"""
PulsoPC - agente del PC.

Lee las estadisticas del PC y las sirve por la red local:
  - GET /stats   -> JSON con CPU, RAM, GPU, temperaturas, red y discos
  - GET /        -> panel web sencillo (sirve tambien en el navegador del movil)
  - Cada 2 s anuncia su presencia por UDP broadcast (puerto 47800) para que
    la app del movil lo encuentre sin escribir la IP.

Requisitos: Python 3.8+ y `pip install psutil`.
GPU NVIDIA: se lee con nvidia-smi si esta instalado (viene con el driver).
Temperaturas en Windows: Windows no las expone de forma fiable. Si tienes
LibreHardwareMonitor abierto con "Remote Web Server" activado (puerto 8085),
el agente lee de ahi temperaturas de CPU/GPU, ventiladores, etc.

Uso:  python pulsopc_agente.py [--puerto 47801]
"""

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psutil

PUERTO_HTTP = 47801
PUERTO_DESCUBRIMIENTO = 47800
LHM_URL = "http://127.0.0.1:8085/data.json"

_ultimo = {"stats": {}, "lock": threading.Lock(), "pedido": 0.0}

# Estado para el icono de la bandeja
estado = {"usb": set(), "avisos": []}


def avisar(texto):
    """Mensaje para el usuario: consola si la hay; si no, notificacion de la bandeja."""
    print(texto)
    estado["avisos"].append(texto)


def movil_conectado():
    """Hay un movil leyendo datos (por USB o Wi-Fi) en los ultimos 5 s."""
    return time.time() - _ultimo["pedido"] < 5


# ---------------------------------------------------------------- lectores --

# ----------------------------------------------------------- GPU NVIDIA --
# nvidia-smi se deja abierto en modo continuo (-lms) en vez de lanzarlo cada
# segundo: gasta mucho menos CPU.

CAMPOS_GPU = ("index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,"
              "fan.speed,clocks.gr,clocks.mem,power.limit")
_gpu = {"datos": {}, "t": 0.0}
SIN_VENTANA = 0x08000000 if platform.system() == "Windows" else 0


def _num(v):
    try:
        return float(v)
    except ValueError:
        return None


def _parsear_gpu(linea):
    p = [x.strip() for x in linea.split(",")]
    if len(p) < 11:
        return None
    return p[0], {
        "nombre": p[1], "uso": _num(p[2]), "vram_usada_mb": _num(p[3]), "vram_total_mb": _num(p[4]),
        "temp": _num(p[5]), "consumo_w": _num(p[6]), "ventilador": _num(p[7]),
        "reloj_mhz": _num(p[8]), "reloj_mem_mhz": _num(p[9]), "limite_w": _num(p[10]),
    }


def bucle_gpu():
    exe = shutil.which("nvidia-smi")
    if not exe:
        return
    while True:
        try:
            proc = subprocess.Popen([exe, "--query-gpu=" + CAMPOS_GPU, "--format=csv,noheader,nounits",
                                     "-lms", "1000"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    text=True, creationflags=SIN_VENTANA)
            _procesos.append(proc)
            for linea in proc.stdout:
                r = _parsear_gpu(linea)
                if r:
                    _gpu["datos"][r[0]] = r[1]
                    _gpu["t"] = time.time()
        except Exception as e:
            print("nvidia-smi:", e)
        time.sleep(10)  # si nvidia-smi se cae, reintentar


def leer_gpu_nvidia():
    if time.time() - _gpu["t"] > 5:
        return []
    return [_gpu["datos"][k] for k in sorted(_gpu["datos"])]


# ------------------------------------------- LibreHardwareMonitor (integrado) --
# Se descarga LibreHardwareMonitorLib (build .NET Framework) a agente/lhm/ y se lee
# con lhm_sensores.ps1 en Windows PowerShell. Sin la app de LHM ni su servidor web.

DIR_AGENTE = os.path.dirname(os.path.abspath(__file__))
LHM_DIR = os.path.join(DIR_AGENTE, "lhm")
LHM_API = "https://api.github.com/repos/LibreHardwareMonitor/LibreHardwareMonitor/releases/latest"
_lhm = {"sensores": [], "t": 0.0}
_procesos = []
estado_lhm = {"texto": "Sensores: arrancando"}


def pawnio_instalado():
    """Driver que usa LibreHardwareMonitor (desde 0.9.5) para leer la CPU."""
    if platform.system() != "Windows":
        return True
    try:
        out = subprocess.run(["sc", "query", "PawnIO"], capture_output=True, text=True,
                             timeout=5, creationflags=SIN_VENTANA)
        return out.returncode == 0
    except Exception:
        return False


def es_admin():
    if platform.system() != "Windows":
        return os.geteuid() == 0 if hasattr(os, "geteuid") else False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def asegurar_lhm():
    """Descarga LibreHardwareMonitorLib.dll y sus dependencias si no estan."""
    if os.path.isfile(os.path.join(LHM_DIR, "LibreHardwareMonitorLib.dll")):
        return True
    import io
    import zipfile
    estado_lhm["texto"] = "Sensores: descargando LibreHardwareMonitor..."
    req = urllib.request.Request(LHM_API, headers={"User-Agent": "PulsoPC",
                                                   "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        rel = json.load(r)
    zips = [a for a in rel.get("assets", []) if a["name"].lower().endswith(".zip")]
    if not zips:
        raise RuntimeError("la release %s no trae .zip" % rel.get("tag_name"))
    # Preferimos la build para .NET Framework (la que carga Windows PowerShell 5.1)
    # (en v0.9.6: "LibreHardwareMonitor.zip" es net472 y "LibreHardwareMonitor.NET.10.zip" no sirve)
    zips.sort(key=lambda a: (0 if ("net4" in a["name"].lower() or "framework" in a["name"].lower()) else 1,
                             1 if ".net." in a["name"].lower() else 0, len(a["name"])))
    asset = zips[0]
    with urllib.request.urlopen(urllib.request.Request(asset["browser_download_url"],
                                                       headers={"User-Agent": "PulsoPC"}), timeout=120) as r:
        datos = r.read()
    z = zipfile.ZipFile(io.BytesIO(datos))
    dlls = [n for n in z.namelist() if n.lower().endswith(".dll")]
    # Si el zip trae varias builds en carpetas, nos quedamos con la de .NET Framework
    marcos = [n for n in dlls if "net4" in n.lower()]
    if marcos:
        dlls = marcos
    os.makedirs(LHM_DIR, exist_ok=True)
    for n in dlls:
        destino = os.path.join(LHM_DIR, os.path.basename(n))
        with z.open(n) as fo, open(destino, "wb") as fd:
            fd.write(fo.read())
    with open(os.path.join(LHM_DIR, "VERSION.txt"), "w") as f:
        f.write("%s %s\n" % (rel.get("tag_name"), asset["name"]))
    avisar("LibreHardwareMonitor %s listo para leer sensores." % rel.get("tag_name"))
    return os.path.isfile(os.path.join(LHM_DIR, "LibreHardwareMonitorLib.dll"))


def bucle_lhm():
    if platform.system() != "Windows":
        return
    script = os.path.join(DIR_AGENTE, "lhm_sensores.ps1")
    espera = 5
    while True:
        try:
            if not asegurar_lhm():
                raise RuntimeError("no encuentro LibreHardwareMonitorLib.dll")
            proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", script, "-Dir", LHM_DIR],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                creationflags=SIN_VENTANA)
            _procesos.append(proc)
            for linea in proc.stdout:
                try:
                    j = json.loads(linea.lstrip("\ufeff"))
                except ValueError:
                    continue
                if "error" in j:
                    raise RuntimeError(j["error"])
                _lhm["sensores"] = j.get("sensores") or []
                _lhm["t"] = time.time()
                hay_cpu = any(x["tipo_hw"] == "Cpu" and x["tipo"] == "Temperature" for x in _lhm["sensores"])
                if hay_cpu:
                    estado_lhm["texto"] = "Sensores: OK (LibreHardwareMonitor)"
                elif not es_admin():
                    estado_lhm["texto"] = "Sensores: sin temperatura de CPU (hace falta administrador)"
                elif not pawnio_instalado():
                    estado_lhm["texto"] = "Sensores: falta el driver PawnIO para la CPU"
                else:
                    estado_lhm["texto"] = "Sensores: la CPU no da temperatura"
                espera = 5
            raise RuntimeError("el lector de sensores se ha cerrado")
        except Exception as e:
            estado_lhm["texto"] = "Sensores: error (%s)" % e
            print("LibreHardwareMonitor:", e)
        time.sleep(espera)
        espera = min(espera * 2, 300)


def _cerrar_procesos():
    for p in _procesos:
        try:
            p.kill()
        except Exception:
            pass


import atexit  # noqa: E402
atexit.register(_cerrar_procesos)

PRIORIDAD_CPU_TEMP = ("CPU Package", "Core (Tctl/Tdie)", "Core (Tctl)", "Tctl/Tdie", "Core Average", "Core Max")


def datos_lhm_integrado():
    if time.time() - _lhm["t"] > 5:
        return None
    sens = _lhm["sensores"]
    orden_hw = {"Cpu": 0, "GpuNvidia": 1, "GpuAmd": 1, "GpuIntel": 1, "Motherboard": 2, "SuperIO": 2,
                "Storage": 3, "Memory": 4}
    temps, fans, consumos = [], [], []
    # Discos: una sola temperatura por unidad, llamada "Disco" (o "Disco 1", "Disco 2"...).
    discos = []
    for x in sens:
        if x["tipo_hw"] == "Storage" and x["tipo"] == "Temperature" and x["hw"] not in discos:
            discos.append(x["hw"])
    disco_hecho = set()
    for x in sorted(sens, key=lambda x: orden_hw.get(x["tipo_hw"], 5)):
        if x["tipo"] == "Temperature" and 0 < x["valor"] < 150:
            nombre = x["nombre"]
            # Los NVMe publican sus umbrales ("Warning Temperature", "Critical Temperature")
            # como si fueran sensores: no son lecturas y en el movil parecian alarmas.
            if any(p in nombre for p in ("Warning", "Critical", "Limit", "Threshold")):
                continue
            if x["tipo_hw"] == "Storage":
                if x["hw"] in disco_hecho:
                    continue   # "Temperature #1/#2" repiten la del disco
                disco_hecho.add(x["hw"])
                nombre = "Disco" if len(discos) == 1 else "Disco %d" % (discos.index(x["hw"]) + 1)
            temps.append({"dispositivo": x["hw"], "sensor": nombre, "valor": x["valor"]})
        elif x["tipo"] == "Fan" and x["valor"] > 0:
            fans.append({"dispositivo": x["hw"], "sensor": x["nombre"], "rpm": x["valor"]})
        elif x["tipo"] == "Power" and x["valor"] > 0:
            consumos.append({"dispositivo": x["hw"], "sensor": x["nombre"], "w": x["valor"]})
    cpu_t = [x for x in sens if x["tipo_hw"] == "Cpu" and x["tipo"] == "Temperature"]
    cpu_temp = None
    for nombre in PRIORIDAD_CPU_TEMP:
        m = [x for x in cpu_t if x["nombre"] == nombre]
        if m:
            cpu_temp = m[0]["valor"]
            break
    if cpu_temp is None and cpu_t:
        cpu_temp = max(x["valor"] for x in cpu_t)
    cpu_w = next((x["valor"] for x in sens if x["tipo_hw"] == "Cpu" and x["tipo"] == "Power"
                  and "Package" in x["nombre"]), None)
    # Las temperaturas de la CPU van primero, con la principal en cabeza
    temps.sort(key=lambda t: (0 if t["valor"] == cpu_temp and t["dispositivo"] in [c["hw"] for c in cpu_t] else 1))
    return {"temperaturas": temps, "ventiladores": fans, "consumos": consumos,
            "cpu_temp": cpu_temp, "cpu_w": cpu_w}


def _recorrer_lhm(nodo, ruta, salida):
    texto = nodo.get("Text", "")
    valor = nodo.get("Value", "")
    hijos = nodo.get("Children", [])
    if not hijos and valor and ruta:
        salida.append((ruta + [texto], valor))
    for h in hijos:
        _recorrer_lhm(h, ruta + [texto] if texto else ruta, salida)


def leer_lhm():
    """Temperaturas y ventiladores desde LibreHardwareMonitor (opcional)."""
    try:
        with urllib.request.urlopen(LHM_URL, timeout=0.5) as r:
            datos = json.load(r)
    except Exception:
        return None
    hojas = []
    _recorrer_lhm(datos, [], hojas)
    temps, fans, consumos = [], [], []
    for ruta, valor in hojas:
        grupo = ruta[-2] if len(ruta) >= 2 else ""
        disp = ruta[-3] if len(ruta) >= 3 else ""
        try:
            v = float(valor.split()[0].replace(",", "."))
        except (ValueError, IndexError):
            continue
        if grupo == "Temperatures":
            temps.append({"dispositivo": disp, "sensor": ruta[-1], "valor": v})
        elif grupo == "Fans":
            fans.append({"dispositivo": disp, "sensor": ruta[-1], "rpm": v})
        elif grupo == "Powers":
            consumos.append({"dispositivo": disp, "sensor": ruta[-1], "w": v})
    return {"temperaturas": temps, "ventiladores": fans, "consumos": consumos}


# ---------------------------------------------------------- procesos, bateria --

_procs = {}


def leer_procesos(n=6):
    """Los procesos que mas CPU gastan, agrupados por nombre (p. ej. todos los chrome.exe)."""
    ncpu = psutil.cpu_count() or 1
    vistos = set()
    grupos = {}
    for p in psutil.process_iter(["name", "memory_info"]):
        pid = p.pid
        vistos.add(pid)
        if pid not in _procs:
            _procs[pid] = p
            try:
                p.cpu_percent(None)   # primera lectura: solo prepara la medida
            except Exception:
                pass
            continue
        try:
            cpu = _procs[pid].cpu_percent(None) / ncpu
        except Exception:
            continue
        nombre = (p.info.get("name") or "?")
        if pid == 0 or nombre.lower() in ("system idle process", "idle"):
            continue
        mem = p.info.get("memory_info")
        g = grupos.setdefault(nombre, {"nombre": nombre.rsplit(".exe", 1)[0], "cpu": 0.0, "ram_mb": 0.0})
        g["cpu"] += cpu
        g["ram_mb"] += (mem.rss / 1048576) if mem else 0
    for pid in list(_procs):
        if pid not in vistos:
            del _procs[pid]
    top = sorted(grupos.values(), key=lambda g: (g["cpu"], g["ram_mb"]), reverse=True)[:n]
    for g in top:
        g["cpu"] = round(g["cpu"], 1)
        g["ram_mb"] = round(g["ram_mb"])
    return top


def leer_bateria():
    try:
        b = psutil.sensors_battery()
    except Exception:
        return None
    if b is None:
        return None
    minutos = None
    if b.secsleft not in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN) and b.secsleft > 0:
        minutos = int(b.secsleft / 60)
    return {"porcentaje": round(b.percent), "cargando": bool(b.power_plugged), "minutos": minutos}


# -------------------------------------------------------------- multimedia --
# Lo que suena ahora (titulo/artista) lo lee medios.ps1 con las APIs de Windows.
# Los botones del movil envian teclas multimedia virtuales al PC.

_medios = {"datos": {}, "t": 0.0}

TECLAS = {"play": 0xB3, "siguiente": 0xB0, "anterior": 0xB1,
          "vol_mas": 0xAF, "vol_menos": 0xAE, "silencio": 0xAD}


def pulsar_tecla(accion):
    vk = TECLAS.get(accion)
    if vk is None or platform.system() != "Windows":
        return False
    import ctypes
    u = ctypes.windll.user32
    u.keybd_event(vk, 0, 1, 0)       # KEYEVENTF_EXTENDEDKEY
    u.keybd_event(vk, 0, 1 | 2, 0)   # + KEYEVENTF_KEYUP
    return True


def bucle_medios():
    script = os.path.join(DIR_AGENTE, "medios.ps1")
    if platform.system() != "Windows" or not os.path.isfile(script):
        return
    while True:
        try:
            if not widget_en_uso("multimedia"):
                time.sleep(5)
                continue
            proc = subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                                     "Bypass", "-File", script], stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                                    creationflags=SIN_VENTANA)
            _procesos.append(proc)
            for linea in proc.stdout:
                try:
                    _medios["datos"] = json.loads(linea.lstrip("\ufeff")) or {}
                    _medios["t"] = time.time()
                except ValueError:
                    pass
                if not widget_en_uso("multimedia"):
                    proc.kill()
                    break
        except Exception as e:
            print("Multimedia:", e)
        time.sleep(10)


def leer_medios():
    """Lo que suena, sin la ruta local de la portada (la app la pide en /portada)."""
    if time.time() - _medios["t"] >= 5:
        return {}
    d = dict(_medios["datos"])
    d.pop("portada_ruta", None)
    return d


def ruta_portada():
    if time.time() - _medios["t"] >= 5:
        return None
    r = _medios["datos"].get("portada_ruta") or ""
    return r if r and os.path.isfile(r) else None


def widget_en_uso(tipo):
    d = diseno_actual()
    if not d:
        return False
    return any(w.get("tipo") == tipo for p in d.get("paginas", []) for w in p.get("widgets", []))


def leer_temps_psutil():
    f = getattr(psutil, "sensors_temperatures", None)
    if not f:
        return []
    try:
        datos = f()
    except Exception:
        return []
    temps = []
    for disp, lista in datos.items():
        for t in lista:
            temps.append({"dispositivo": disp, "sensor": t.label or disp, "valor": t.current})
    return temps


class Muestreador:
    """Calcula velocidades (red/disco) entre dos lecturas."""

    def __init__(self):
        self.t = time.time()
        self.red = psutil.net_io_counters()
        self.disco = psutil.disk_io_counters()
        psutil.cpu_percent(percpu=True)

    def leer(self):
        ahora = time.time()
        dt = max(ahora - self.t, 0.001)
        red = psutil.net_io_counters()
        disco = psutil.disk_io_counters()

        vm = psutil.virtual_memory()
        freq = psutil.cpu_freq()
        nucleos = psutil.cpu_percent(percpu=True)

        unidades = []
        for p in psutil.disk_partitions(all=False):
            if "cdrom" in p.opts or not p.fstype:
                continue
            try:
                u = psutil.disk_usage(p.mountpoint)
            except Exception:
                continue
            unidades.append({"unidad": p.mountpoint, "usado_gb": u.used / 1e9,
                             "total_gb": u.total / 1e9, "porcentaje": u.percent})

        lhm = datos_lhm_integrado()
        fuente = "LibreHardwareMonitor integrado"
        if not lhm:
            lhm = leer_lhm()   # la app LibreHardwareMonitor con servidor web, si esta abierta
            fuente = "LibreHardwareMonitor (servidor web)"
        if lhm:
            temps, fans, consumos = lhm["temperaturas"], lhm["ventiladores"], lhm["consumos"]
        else:
            temps, fans, consumos = leer_temps_psutil(), [], []
            fuente = "psutil"
        cpu_temp = lhm.get("cpu_temp") if lhm else None
        cpu_w = lhm.get("cpu_w") if lhm else None

        stats = {
            "equipo": socket.gethostname(),
            "so": platform.system() + " " + platform.release(),
            "hora": ahora,
            "uptime_s": ahora - psutil.boot_time(),
            "cpu": {
                "nombre": platform.processor(),
                "uso": sum(nucleos) / len(nucleos) if nucleos else 0,
                "nucleos": nucleos,
                "freq_mhz": freq.current if freq else None,
                "temp": cpu_temp,
                "consumo_w": cpu_w,
            },
            "ram": {"usada_gb": vm.used / 1e9, "total_gb": vm.total / 1e9, "porcentaje": vm.percent},
            "gpu": leer_gpu_nvidia(),
            "red": {
                "bajada_kbs": (red.bytes_recv - self.red.bytes_recv) / dt / 1024,
                "subida_kbs": (red.bytes_sent - self.red.bytes_sent) / dt / 1024,
            },
            "disco": {
                "lectura_kbs": ((disco.read_bytes - self.disco.read_bytes) / dt / 1024) if disco else 0,
                "escritura_kbs": ((disco.write_bytes - self.disco.write_bytes) / dt / 1024) if disco else 0,
                "unidades": unidades,
            },
            "temperaturas": temps,
            "ventiladores": fans,
            "consumos": consumos,
            "fuente_temps": fuente,
            "procesos": leer_procesos() if widget_en_uso("procesos") else [],
            "bateria": leer_bateria(),
            "medios": leer_medios(),
        }
        self.t, self.red, self.disco = ahora, red, disco
        return stats


def bucle_muestreo(intervalo):
    """Lee el PC cada 'intervalo' s. Si nadie pide datos (movil desconectado),
    baja a una lectura cada 5 s para no gastar CPU en segundo plano."""
    m = Muestreador()
    time.sleep(intervalo)
    while True:
        try:
            s = m.leer()
            with _ultimo["lock"]:
                _ultimo["stats"] = s
        except Exception as e:  # nunca dejar morir el hilo
            print("Error leyendo estadisticas:", e)
        reposo = time.time() - _ultimo["pedido"] > 10
        time.sleep(max(intervalo, 5.0) if reposo else intervalo)


def bucle_anuncio(puerto_http):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    msg = json.dumps({"app": "pulsopc", "equipo": socket.gethostname(), "puerto": puerto_http}).encode()
    while True:
        try:
            s.sendto(msg, ("255.255.255.255", PUERTO_DESCUBRIMIENTO))
        except Exception:
            pass
        time.sleep(2)


def buscar_adb():
    exe = shutil.which("adb")
    if exe:
        return exe
    for base in (os.environ.get("LOCALAPPDATA"), os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT")):
        if not base:
            continue
        for sub in (("Android", "Sdk", "platform-tools"), ("platform-tools",)):
            c = os.path.join(base, *sub, "adb.exe" if platform.system() == "Windows" else "adb")
            if os.path.isfile(c):
                return c
    bw = os.path.join(os.path.expanduser("~"), ".bubblewrap", "android_sdk", "platform-tools", "adb.exe")
    if os.path.isfile(bw):
        return bw
    import glob
    casa = os.path.expanduser("~")
    for patron in (os.path.join(casa, "scrcpy*", "adb.exe"), os.path.join(casa, "Downloads", "scrcpy*", "adb.exe"),
                   os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "scrcpy*", "adb.exe")):
        hallados = glob.glob(patron)
        if hallados:
            return hallados[0]
    junto = os.path.join(os.path.dirname(os.path.abspath(__file__)), "platform-tools", "adb.exe")
    return junto if os.path.isfile(junto) else None


APP_ACTIVIDAD = "com.pulsopc.app/.MainActivity"


def bucle_usb(puerto_http, mantener_app=True):
    """Conexion por cable: con 'adb reverse' el movil ve el agente en 127.0.0.1.

    Requiere la depuracion USB activada en el movil. Se repite cada 5 s para
    cubrir conexiones y desconexiones del cable. Al conectar un movil:
    enciende la pantalla, la deja encendida mientras haya USB y abre la app.
    Con mantener_app, si la app deja de estar delante, la vuelve a abrir.
    """
    adb = buscar_adb()
    if not adb:
        avisar("USB: no encuentro adb (instala Android Platform Tools); solo Wi-Fi.")
        return
    flags = 0x08000000 if platform.system() == "Windows" else 0
    regla = "tcp:%d" % puerto_http

    def sh(sn, *args):
        return subprocess.run([adb, "-s", sn] + list(args), capture_output=True, text=True,
                              timeout=5, creationflags=flags).stdout

    def abrir_app(sn):
        sh(sn, "shell", "input", "keyevent", "KEYCODE_WAKEUP")
        sh(sn, "shell", "wm", "dismiss-keyguard")
        sh(sn, "shell", "am", "start", "-n", APP_ACTIVIDAD)

    def app_delante(sn):
        out = sh(sn, "shell", "dumpsys", "window", "windows")
        for linea in out.splitlines():
            if "mCurrentFocus" in linea or "mFocusedApp" in linea:
                return "com.pulsopc.app" in linea
        return True  # si no se puede saber, no molestar

    conocidos = set()
    while True:
        try:
            out = subprocess.run([adb, "devices"], capture_output=True, text=True,
                                 timeout=5, creationflags=flags).stdout
            lineas = out.splitlines()[1:]
            if any(l.strip().endswith("offline") for l in lineas):
                # El movil se queda "offline" a veces tras un microcorte del cable.
                subprocess.run([adb, "reconnect", "offline"], capture_output=True,
                               timeout=5, creationflags=flags)
            serials = [l.split()[0] for l in lineas if l.strip().endswith("device")]
            for sn in serials:
                sh(sn, "reverse", regla, regla)
                if sn not in conocidos:
                    avisar("Movil conectado por USB. Abriendo PulsoPC...")
                    sh(sn, "shell", "svc", "power", "stayon", "usb")  # pantalla encendida con USB
                    abrir_app(sn)
                    conocidos.add(sn)
                elif mantener_app and not app_delante(sn):
                    abrir_app(sn)
            conocidos &= set(serials)
            estado["usb"] = set(serials)
        except Exception:
            pass
        time.sleep(5)


# ----------------------------------------------------------------- diseno --
# El diseno (paginas, widgets y colores) se edita en http://localhost:47801/editor
# y se guarda en pulsopc_config.json. Va dentro de cada respuesta de /stats,
# asi que el movil lo aplica en el siguiente segundo sin reiniciar nada.

TIPOS_WIDGET = ["cpu", "ram", "gpu", "temps", "red", "disco", "nucleos",
                "gpu_detalle", "ventiladores", "reloj", "procesos", "bateria", "multimedia", "grafica"]
METRICAS_GRAFICA = ["cpu", "gpu", "ram", "temp_cpu", "temp_gpu", "red"]

DISENO_POR_DEFECTO = {
    "version": 1,
    "tema": {"fondo": "#0E1116", "tarjeta": "#171B22", "acento": "#3EC7A0",
             "secundario": "#5B9BF0", "texto": "#E6E6E6"},
    "cabecera": True,
    "alerta_temp": 90,
    "paginas": [
        {"nombre": "Resumen", "columnas": 3, "filas": 2, "widgets": [
            {"tipo": "cpu", "ancho": 1, "alto": 1},
            {"tipo": "ram", "ancho": 1, "alto": 1},
            {"tipo": "gpu", "ancho": 1, "alto": 1},
            {"tipo": "temps", "ancho": 1, "alto": 1},
            {"tipo": "red", "ancho": 1, "alto": 1},
            {"tipo": "disco", "ancho": 1, "alto": 1},
        ]},
        {"nombre": "Detalle", "columnas": 3, "filas": 1, "widgets": [
            {"tipo": "nucleos", "ancho": 1, "alto": 1},
            {"tipo": "gpu_detalle", "ancho": 1, "alto": 1},
            {"tipo": "ventiladores", "ancho": 1, "alto": 1},
        ]},
    ],
}

RUTA_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pulsopc_config.json")
RUTA_EDITOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "editor.html")
_diseno = {"valor": None, "lock": threading.Lock()}


def _color(v, defecto):
    v = str(v or "")
    if len(v) == 7 and v.startswith("#"):
        try:
            int(v[1:], 16)
            return v.upper()
        except ValueError:
            pass
    return defecto


def validar_diseno(d):
    """Devuelve un diseno limpio; lanza ValueError si no sirve."""
    if not isinstance(d, dict):
        raise ValueError("el diseno debe ser un objeto")
    base = DISENO_POR_DEFECTO
    tema_in = d.get("tema") or {}
    tema = {k: _color(tema_in.get(k), v) for k, v in base["tema"].items()}
    paginas = []
    for p in (d.get("paginas") or [])[:8]:
        cols = max(1, min(4, int(p.get("columnas", 3))))
        filas = max(1, min(4, int(p.get("filas", 2))))
        ws = []
        for w in (p.get("widgets") or [])[:16]:
            if w.get("tipo") not in TIPOS_WIDGET:
                continue
            limpio = {
                "tipo": w["tipo"],
                "ancho": max(1, min(cols, int(w.get("ancho", 1)))),
                "alto": max(1, min(filas, int(w.get("alto", 1)))),
                "titulo": str(w.get("titulo", ""))[:30],
            }
            if w["tipo"] == "grafica":
                m = w.get("metrica", "cpu")
                limpio["metrica"] = m if m in METRICAS_GRAFICA else "cpu"
            ws.append(limpio)
        paginas.append({"nombre": str(p.get("nombre", "Pagina"))[:20], "columnas": cols,
                        "filas": filas, "widgets": ws})
    if not paginas:
        raise ValueError("hace falta al menos una pagina")
    try:
        alerta = max(50, min(110, int(d.get("alerta_temp", 90))))
    except (TypeError, ValueError):
        alerta = 90
    return {"version": int(time.time()), "tema": tema, "cabecera": bool(d.get("cabecera", True)),
            "alerta_temp": alerta, "paginas": paginas}


def cargar_diseno():
    try:
        with open(RUTA_CONFIG, encoding="utf-8") as f:
            d = validar_diseno(json.load(f))
    except FileNotFoundError:
        d = validar_diseno(DISENO_POR_DEFECTO)
    except Exception as e:
        print("  Diseno: pulsopc_config.json no es valido (%s); uso el de por defecto." % e)
        d = validar_diseno(DISENO_POR_DEFECTO)
    with _diseno["lock"]:
        _diseno["valor"] = d


def guardar_diseno(d):
    d = validar_diseno(d)
    tmp = RUTA_CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, RUTA_CONFIG)
    with _diseno["lock"]:
        _diseno["valor"] = d
    return d


def diseno_actual():
    with _diseno["lock"]:
        return _diseno["valor"]


# ------------------------------------------------------------------- HTTP --

PANEL_HTML = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PulsoPC</title><style>
body{background:#0e1116;color:#e6e6e6;font-family:sans-serif;margin:12px}
.c{background:#171b22;border-radius:10px;padding:10px;margin:8px 0}
.b{height:10px;background:#2a303a;border-radius:5px;overflow:hidden}
.b i{display:block;height:100%;background:#3ec7a0}
h3{margin:0 0 6px;font-size:14px;color:#9aa4b2}
.v{font-size:22px}</style></head><body><div id="p">Cargando...</div><script>
function bar(p){return '<div class="b"><i style="width:'+Math.min(100,p||0)+'%"></i></div>'}
function card(t,v,p){return '<div class="c"><h3>'+t+'</h3><div class="v">'+v+'</div>'+(p!=null?bar(p):'')+'</div>'}
function f(x,d){return x==null?'-':x.toFixed(d||0)}
function tick(){var r=new XMLHttpRequest();r.open('GET','/stats');r.onload=function(){
var s=JSON.parse(r.responseText);if(!s.cpu){return}var h='';
h+=card('CPU',f(s.cpu.uso)+' %',s.cpu.uso);
h+=card('RAM',f(s.ram.usada_gb,1)+' / '+f(s.ram.total_gb,1)+' GB',s.ram.porcentaje);
for(var i=0;i<s.gpu.length;i++){var g=s.gpu[i];h+=card('GPU '+g.nombre,f(g.uso)+' % · '+f(g.temp)+' °C',g.uso)}
h+=card('Red','↓ '+f(s.red.bajada_kbs)+' KB/s  ↑ '+f(s.red.subida_kbs)+' KB/s');
for(var j=0;j<s.temperaturas.length&&j<6;j++){var t=s.temperaturas[j];h+=card(t.dispositivo+' · '+t.sensor,f(t.valor)+' °C')}
document.getElementById('p').innerHTML=h};r.send()}
setInterval(tick,1000);tick();</script></body></html>"""


class Manejador(BaseHTTPRequestHandler):
    def _enviar(self, codigo, cuerpo, tipo):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def _json(self, obj, codigo=200):
        self._enviar(codigo, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                     "application/json; charset=utf-8")

    def _es_local(self):
        ip = self.client_address[0]
        return ip.startswith("127.") or ip == "::1" or ip == ip_local()

    def do_GET(self):
        ruta = self.path.split("?")[0]
        if ruta == "/stats":
            _ultimo["pedido"] = time.time()
            with _ultimo["lock"]:
                datos = dict(_ultimo["stats"])
            datos["diseno"] = diseno_actual()
            self._json(datos)
        elif ruta == "/config":
            self._json({"diseno": diseno_actual(), "tipos": TIPOS_WIDGET, "metricas": METRICAS_GRAFICA,
                        "por_defecto": validar_diseno(DISENO_POR_DEFECTO)})
        elif ruta == "/portada":
            r = ruta_portada()
            if not r:
                self.send_error(404)
                return
            with open(r, "rb") as f:
                datos = f.read()
            tipo = "image/png" if datos[:4] == b"\x89PNG" else "image/jpeg"
            self._enviar(200, datos, tipo)
        elif ruta == "/editor":
            try:
                with open(RUTA_EDITOR, "rb") as f:
                    self._enviar(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                self.send_error(404, "No encuentro editor.html junto al agente")
        elif ruta in ("/", "/index.html"):
            self._enviar(200, PANEL_HTML.encode(), "text/html; charset=utf-8")
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path.split("?")[0] == "/salir":
            # Cierra el agente (solo desde el propio PC). Sirve para actualizarlo sin
            # tener que usar el menu del icono; luego se relanza con: schtasks /Run /TN PulsoPC
            # Por USB el movil tambien llega como 127.0.0.1, asi que se exige ademas
            # el token que el agente guarda en agente/.token (solo legible en el PC).
            if (not self.client_address[0].startswith("127.")
                    or self.headers.get("X-Token", "") != TOKEN_LOCAL):
                self._json({"error": "solo desde el PC"}, 403)
                return
            self._json({"ok": True})
            def _salir():
                time.sleep(0.3)
                _cerrar_procesos()
                os._exit(0)
            threading.Thread(target=_salir, daemon=True).start()
            return
        if self.path.split("?")[0] == "/accion":
            # Botones multimedia del movil: solo pulsan teclas multimedia, nada mas.
            try:
                n = int(self.headers.get("Content-Length", "0"))
                accion = json.loads(self.rfile.read(min(n, 1000)).decode("utf-8")).get("accion")
                self._json({"ok": pulsar_tecla(accion)})
            except Exception as e:
                self._json({"error": str(e)}, 400)
            return
        if self.path.split("?")[0] != "/config":
            self.send_error(404)
            return
        # Solo se puede cambiar el diseno desde el propio PC.
        if not self._es_local():
            self._json({"error": "El diseno solo se puede cambiar desde el PC"}, 403)
            return
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if n > 200000:
                raise ValueError("demasiado grande")
            d = guardar_diseno(json.loads(self.rfile.read(n).decode("utf-8")))
            self._json({"ok": True, "diseno": d})
        except Exception as e:
            self._json({"error": str(e)}, 400)

    def log_message(self, *a):
        pass


def ip_local():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


class Servidor(ThreadingHTTPServer):
    daemon_threads = True
    # En Windows, SO_REUSEADDR deja abrir dos agentes en el mismo puerto: lo evitamos
    # para detectar que PulsoPC ya esta abierto.
    allow_reuse_address = platform.system() != "Windows"

    def server_bind(self):
        if platform.system() == "Windows" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


TOKEN_LOCAL = ""


def _crear_token():
    global TOKEN_LOCAL
    import secrets
    TOKEN_LOCAL = secrets.token_hex(16)
    try:
        with open(os.path.join(DIR_AGENTE, ".token"), "w") as f:
            f.write(TOKEN_LOCAL)
    except OSError:
        pass


def arrancar(puerto=PUERTO_HTTP, intervalo=1.0, mantener_app=True):
    """Arranca todo en hilos y devuelve el servidor HTTP (sin bloquear).
    Lanza OSError si el puerto ya esta en uso (otro agente abierto)."""
    srv = Servidor(("0.0.0.0", puerto), Manejador)
    _crear_token()
    cargar_diseno()
    threading.Thread(target=bucle_gpu, daemon=True).start()
    threading.Thread(target=bucle_lhm, daemon=True).start()
    threading.Thread(target=bucle_medios, daemon=True).start()
    threading.Thread(target=bucle_muestreo, args=(intervalo,), daemon=True).start()
    threading.Thread(target=bucle_anuncio, args=(puerto,), daemon=True).start()
    threading.Thread(target=bucle_usb, args=(puerto, mantener_app), daemon=True).start()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    ap = argparse.ArgumentParser(description="Agente PulsoPC (modo consola)")
    ap.add_argument("--puerto", type=int, default=PUERTO_HTTP)
    ap.add_argument("--intervalo", type=float, default=1.0, help="segundos entre lecturas")
    ap.add_argument("--no-mantener-app", action="store_true",
                    help="no volver a abrir la app del movil si se cierra (por USB)")
    a = ap.parse_args()

    try:
        arrancar(a.puerto, a.intervalo, not a.no_mantener_app)
    except OSError:
        print("El puerto %d ya esta en uso: seguramente PulsoPC ya esta abierto "
              "(mira el icono junto al reloj)." % a.puerto)
        return
    print("PulsoPC agente en marcha.")
    print("  Panel web: http://%s:%d/" % (ip_local(), a.puerto))
    print("  Personalizar el movil: http://localhost:%d/editor" % a.puerto)
    print("  Wi-Fi: la app del movil lo encontrara sola en la misma red.")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
