"""
PulsePC - agente del PC.

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
import ipaddress
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psutil

PUERTO_HTTP = 47801
PUERTO_DESCUBRIMIENTO = 47800
LHM_URL = "http://127.0.0.1:8085/data.json"

_ultimo = {"stats": {}, "lock": threading.Lock(), "pedido": 0.0, "via": ""}

# Estado para el icono de la bandeja
estado = {"usb": set(), "avisos": []}


def _dir_datos():
    """Carpeta del agente: junto al script, o en %LOCALAPPDATA%\\PulsePC si es PulsePC.exe."""
    if getattr(sys, "frozen", False):
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "PulsePC")
    return os.path.dirname(os.path.abspath(__file__))


DIR_AGENTE = _dir_datos()

# Carpeta de los archivos del programa (scripts de PowerShell, paginas, LibreHardwareMonitor):
# junto al .exe que se esta ejecutando. Con permisos de administrador PulsePC.exe se instala
# en C:\Program Files\PulsePC, que solo puede cambiar un administrador; los datos (diseno,
# ajustes, registro) siguen en %LOCALAPPDATA%\PulsePC.
CONGELADO = getattr(sys, "frozen", False)
DIR_RECURSOS = os.path.dirname(os.path.abspath(sys.executable if CONGELADO else __file__))
SYSTEM32 = os.path.join(os.environ.get("SystemRoot") or r"C:\Windows", "System32")
POWERSHELL = os.path.join(SYSTEM32, "WindowsPowerShell", "v1.0", "powershell.exe")
if platform.system() != "Windows":
    POWERSHELL = "powershell"


def ruta_protegida(ruta):
    """La ruta esta dentro de Windows o de Program Files (solo la cambia un administrador)."""
    if platform.system() != "Windows" or not ruta:
        return False
    r = os.path.normcase(os.path.abspath(ruta))
    for k in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        b = os.environ.get(k)
        if b and r.startswith(os.path.normcase(os.path.abspath(b)).rstrip("\\/") + os.sep):
            return True
    return False


def modo_estricto():
    """PulsePC.exe con permisos de administrador: solo ejecuta programas de carpetas protegidas.
    Si ejecutara algo de una carpeta que puede cambiar cualquier programa del usuario (Descargas,
    AppData, el PATH del usuario...), un programa malicioso podria colarse ahi y conseguir
    permisos de administrador sin aviso. (Con Python a mano no aplica: los propios .py estan
    en una carpeta del usuario.)"""
    return CONGELADO and platform.system() == "Windows" and es_admin()


# ------------------------------------------------------------------ idioma --
# Idioma de PulsePC (espanol o ingles): lo usan la bandeja, el editor, el Nest Hub y
# la app del movil (llega con /stats). Se guarda en agente/pulsopc_ajustes.json.

RUTA_AJUSTES = os.path.join(DIR_AGENTE, "pulsopc_ajustes.json")
IDIOMAS = ("es", "en")
_ajustes = {}


def _idioma_sistema():
    try:
        import locale
        nombre = (locale.getlocale()[0] or "").lower()
    except Exception:
        nombre = ""
    if not nombre and platform.system() == "Windows":
        try:
            import ctypes
            import locale
            nombre = locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage(), "").lower()
        except Exception:
            nombre = ""
    return "es" if nombre.startswith(("es", "spanish", "catalan", "galician", "basque", "ca", "gl", "eu")) or not nombre else "en"


def cargar_ajustes():
    try:
        with open(RUTA_AJUSTES, encoding="utf-8") as f:
            _ajustes.update(json.load(f))
    except Exception:
        pass
    if _ajustes.get("idioma") not in IDIOMAS:
        _ajustes["idioma"] = _idioma_sistema()


def idioma():
    if "idioma" not in _ajustes:
        cargar_ajustes()
    return _ajustes["idioma"]


def poner_idioma(valor):
    if valor not in IDIOMAS:
        raise ValueError("idioma no valido")
    _ajustes["idioma"] = valor
    tmp = RUTA_AJUSTES + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_ajustes, f, ensure_ascii=False, indent=2)
    os.replace(tmp, RUTA_AJUSTES)


def tr(es, en):
    """Texto en el idioma elegido."""
    return en if idioma() == "en" else es


def avisar(texto):
    """Mensaje para el usuario: consola si la hay; si no, notificacion de la bandeja."""
    print(texto)
    estado["avisos"].append(texto)


def movil_conectado():
    """Hay un movil leyendo datos (por USB o Wi-Fi) en los ultimos 5 s."""
    return time.time() - _ultimo["pedido"] < 5


def via_movil():
    """Por donde lee el movil ahora mismo: "USB" o "Wi-Fi" ("" si no hay movil)."""
    return _ultimo["via"] if movil_conectado() else ""


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


def buscar_nvidia_smi():
    exe = shutil.which("nvidia-smi")
    if not modo_estricto():
        return exe
    for c in (exe, os.path.join(SYSTEM32, "nvidia-smi.exe"),
              os.path.join(os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles") or r"C:\Program Files",
                           "NVIDIA Corporation", "NVSMI", "nvidia-smi.exe")):
        if c and ruta_protegida(c) and os.path.isfile(c):
            return c
    return None


def bucle_gpu():
    exe = buscar_nvidia_smi()
    if not exe:
        return
    while True:
        try:
            proc = subprocess.Popen([exe, "--query-gpu=" + CAMPOS_GPU, "--format=csv,noheader,nounits",
                                     "-lms", "1000"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    text=True, errors="replace", creationflags=SIN_VENTANA)
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

LHM_DIR = os.path.join(DIR_AGENTE, "lhm")   # se decide en bucle_lhm() (ver dir_lhm)


def dir_lhm():
    """Donde va LibreHardwareMonitor: junto al programa si esta en Program Files (y se puede
    escribir ahi o ya esta descargado); si no, en la carpeta de datos, como antes."""
    propio = os.path.join(DIR_RECURSOS, "lhm")
    if ruta_protegida(DIR_RECURSOS) and (es_admin() or os.path.isfile(os.path.join(propio, "LibreHardwareMonitorLib.dll"))):
        return propio
    return os.path.join(DIR_AGENTE, "lhm")


LHM_API = "https://api.github.com/repos/LibreHardwareMonitor/LibreHardwareMonitor/releases/latest"
_lhm = {"sensores": [], "t": 0.0}
_procesos = []
estado_lhm = {"texto": "Sensores: arrancando"}


def pawnio_instalado():
    """Driver que usa LibreHardwareMonitor (desde 0.9.5) para leer la CPU."""
    if platform.system() != "Windows":
        return True
    try:
        out = subprocess.run([os.path.join(SYSTEM32, "sc.exe"), "query", "PawnIO"], capture_output=True, text=True, errors="replace",
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
    estado_lhm["texto"] = tr("Sensores: descargando LibreHardwareMonitor...", "Sensors: downloading LibreHardwareMonitor...")
    req = urllib.request.Request(LHM_API, headers={"User-Agent": "PulsePC",
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
                                                       headers={"User-Agent": "PulsePC"}), timeout=120) as r:
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
    avisar(tr("LibreHardwareMonitor %s listo para leer sensores.", "LibreHardwareMonitor %s ready to read sensors.") % rel.get("tag_name"))
    return os.path.isfile(os.path.join(LHM_DIR, "LibreHardwareMonitorLib.dll"))


def bucle_lhm():
    global LHM_DIR
    if platform.system() != "Windows":
        return
    if modo_estricto() and not ruta_protegida(DIR_RECURSOS):
        # Elevado pero fuera de Program Files: no se ejecuta nada de una carpeta del usuario.
        estado_lhm["texto"] = tr("Sensores: PulsePC no esta instalado en Program Files",
                                 "Sensors: PulsePC is not installed in Program Files")
        return
    LHM_DIR = dir_lhm()
    script = os.path.join(DIR_RECURSOS, "lhm_sensores.ps1")
    espera = 5
    while True:
        try:
            if not asegurar_lhm():
                raise RuntimeError("no encuentro LibreHardwareMonitorLib.dll")
            proc = subprocess.Popen(
                [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", script, "-Dir", LHM_DIR],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, errors="replace", encoding="utf-8",
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
                    estado_lhm["texto"] = tr("Sensores: OK (LibreHardwareMonitor)", "Sensors: OK (LibreHardwareMonitor)")
                elif not es_admin():
                    estado_lhm["texto"] = tr("Sensores: sin temperatura de CPU (hace falta administrador)", "Sensors: no CPU temperature (needs administrator)")
                elif not pawnio_instalado():
                    estado_lhm["texto"] = tr("Sensores: falta el driver PawnIO para la CPU", "Sensors: the PawnIO driver is missing for the CPU")
                else:
                    estado_lhm["texto"] = tr("Sensores: la CPU no da temperatura", "Sensors: the CPU reports no temperature")
                espera = 5
            raise RuntimeError("el lector de sensores se ha cerrado")
        except Exception as e:
            estado_lhm["texto"] = tr("Sensores: error (%s)", "Sensors: error (%s)") % e
            print("LibreHardwareMonitor:", e)
        time.sleep(espera)
        espera = min(espera * 2, 300)


_tunel_usb = {"adb": None, "regla": None, "serials": set()}


def _cerrar_procesos():
    for p in _procesos:
        try:
            p.kill()
        except Exception:
            pass
    # Quitar el tunel "adb reverse" al cerrar. Si se queda puesto con el agente cerrado,
    # la app sigue intentando conectar por el cable, cada intento hace que el servidor de
    # adb del PC intente abrir el puerto cerrado (en Windows tarda ~2 s en fallar) y el
    # servidor de adb se queda bloqueado: hasta "adb devices" deja de responder.
    adb, regla = _tunel_usb["adb"], _tunel_usb["regla"]
    for sn in list(_tunel_usb["serials"]):
        try:
            subprocess.run([adb, "-s", sn, "reverse", "--remove", regla], capture_output=True,
                           timeout=3, creationflags=0x08000000 if platform.system() == "Windows" else 0)
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
            # Igual con "Distance to TjMax" de Intel: es el margen hasta el limite termico
            # (55 = faltan 55 °C), no una temperatura, y se colaba en la lista como si lo fuera.
            if any(p in nombre for p in ("Warning", "Critical", "Limit", "Threshold", "Distance")):
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
    script = os.path.join(DIR_RECURSOS, "medios.ps1")
    if platform.system() != "Windows" or not os.path.isfile(script):
        return
    if modo_estricto() and not ruta_protegida(DIR_RECURSOS):
        return   # elevado fuera de Program Files: no se ejecuta nada de una carpeta del usuario
    while True:
        try:
            if not widget_en_uso("multimedia"):
                time.sleep(5)
                continue
            proc = subprocess.Popen([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                                     "Bypass", "-File", script], stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, text=True, errors="replace", encoding="utf-8",
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
    # La posicion se leyo hace hasta un segundo: se adelanta a "ahora" si esta sonando.
    try:
        if d.get("estado") == "reproduciendo" and d.get("duracion"):
            d["posicion"] = round(min(float(d.get("posicion") or 0) + time.time() - _medios["t"],
                                      float(d["duracion"])), 1)
    except (TypeError, ValueError):
        pass
    return d


def ruta_portada():
    if time.time() - _medios["t"] >= 5:
        return None
    r = _medios["datos"].get("portada_ruta") or ""
    return r if r and os.path.isfile(r) else None


def widget_en_uso(tipo):
    for d in (diseno_actual(), diseno_actual("pantalla")):
        if d and any(w.get("tipo") == tipo for p in d.get("paginas", []) for w in p.get("widgets", [])):
            return True
    return False


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


URL_PLATFORM_TOOLS = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"


def descargar_adb(destino):
    """Descarga adb de Google (Android Platform Tools) a una carpeta de Program Files."""
    import io
    import zipfile
    req = urllib.request.Request(URL_PLATFORM_TOOLS, headers={"User-Agent": "PulsePC"})
    with urllib.request.urlopen(req, timeout=180) as r:
        z = zipfile.ZipFile(io.BytesIO(r.read()))
    os.makedirs(destino, exist_ok=True)
    for n in z.namelist():
        base = os.path.basename(n)
        if n.count("/") == 1 and n.startswith("platform-tools/") and \
                (base.lower() == "adb.exe" or base.lower().endswith(".dll")):
            with z.open(n) as fo, open(os.path.join(destino, base), "wb") as fd:
                fd.write(fo.read())
    exe = os.path.join(destino, "adb.exe")
    return exe if os.path.isfile(exe) else None


def buscar_adb():
    if modo_estricto():
        # Elevado: el adb de Android Studio, scrcpy, etc. esta en carpetas del usuario y no se
        # ejecuta con permisos de administrador. Se usa uno en carpeta protegida o se descarga.
        exe = shutil.which("adb")
        if exe and ruta_protegida(exe):
            return exe
        junto = os.path.join(DIR_RECURSOS, "platform-tools", "adb.exe")
        if ruta_protegida(junto) and os.path.isfile(junto):
            return junto
        if not ruta_protegida(DIR_RECURSOS):
            return None
        try:
            return descargar_adb(os.path.dirname(junto))
        except Exception as e:
            print("No pude descargar adb:", e)
            return None
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
    junto = os.path.join(DIR_RECURSOS, "platform-tools", "adb.exe")
    return junto if os.path.isfile(junto) else None


APP_ACTIVIDAD = "com.pulsopc.app/.MainActivity"
APP_PAQUETE = "com.pulsopc.app"

# La app la compila GitHub en cada cambio (ver .github/workflows/apk.yml) y la deja aqui:
URL_APK = "https://github.com/Numial/pulsepc/releases/download/apk/"
DIR_APK = os.path.join(DIR_AGENTE, "apk")
CADA_CUANTO_ACTUALIZAR = 30 * 60   # con el movil conectado, se vuelve a mirar cada 30 min


def actualizar_app_activado():
    idioma()   # carga los ajustes
    return _ajustes.get("actualizar_app", True)


def poner_actualizar_app(activar):
    _ajustes["actualizar_app"] = bool(activar)
    poner_idioma(idioma())   # guarda los ajustes


def version_disponible():
    """Ultima version de la app publicada en GitHub: {"versionCode", "versionName"} o None."""
    try:
        req = urllib.request.Request(URL_APK + "version.json", headers={"User-Agent": "PulsePC"})
        with urllib.request.urlopen(req, timeout=10) as r:
            v = json.loads(r.read().decode("utf-8"))
        return v if int(v.get("versionCode", 0)) > 0 else None
    except Exception:
        return None


def descargar_apk(v):
    """Descarga la APK de esa version (si no estaba ya descargada) y devuelve su ruta."""
    os.makedirs(DIR_APK, exist_ok=True)
    ruta = os.path.join(DIR_APK, "PulsePC-%d.apk" % int(v["versionCode"]))
    if os.path.isfile(ruta) and os.path.getsize(ruta) > 50000:
        return ruta
    req = urllib.request.Request(URL_APK + "PulsePC.apk", headers={"User-Agent": "PulsePC"})
    with urllib.request.urlopen(req, timeout=120) as r:
        datos = r.read()
    if datos[:2] != b"PK" or len(datos) < 50000:
        raise RuntimeError("la descarga no es una APK valida")
    with open(ruta + ".tmp", "wb") as f:
        f.write(datos)
    os.replace(ruta + ".tmp", ruta)
    # Se guarda solo la ultima: las anteriores se borran.
    for n in os.listdir(DIR_APK):
        if n.endswith(".apk") and n != os.path.basename(ruta):
            try:
                os.remove(os.path.join(DIR_APK, n))
            except OSError:
                pass
    return ruta


def bucle_usb(puerto_http, mantener_app=True):
    """Conexion por cable: con 'adb reverse' el movil ve el agente en 127.0.0.1.

    Requiere la depuracion USB activada en el movil. Se repite cada 5 s para
    cubrir conexiones y desconexiones del cable. Al conectar un movil:
    enciende la pantalla, la deja encendida mientras haya USB y abre la app.
    Con mantener_app, si la app deja de estar delante, la vuelve a abrir.
    """
    adb = buscar_adb()
    if not adb:
        avisar(tr("USB: no encuentro adb (instala Android Platform Tools); solo Wi-Fi.",
                  "USB: adb not found (install Android Platform Tools); Wi-Fi only."))
        return
    flags = 0x08000000 if platform.system() == "Windows" else 0
    regla = "tcp:%d" % puerto_http

    def sh(sn, *args):
        return subprocess.run([adb, "-s", sn] + list(args), capture_output=True, text=True, errors="replace",
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

    def version_instalada(sn):
        out = sh(sn, "shell", "dumpsys", "package", APP_PAQUETE)
        for trozo in out.split():
            if trozo.startswith("versionCode="):
                try:
                    return int(trozo.split("=", 1)[1])
                except ValueError:
                    pass
        return 0   # no instalada

    def instalar(sn, ruta):
        r = subprocess.run([adb, "-s", sn, "install", "-r", ruta], capture_output=True, text=True,
                           errors="replace", timeout=300, creationflags=flags)
        salida = (r.stdout or "") + (r.stderr or "")
        if "Success" in salida:
            return True, ""
        if "UPDATE_INCOMPATIBLE" in salida or "signatures do not match" in salida.lower():
            # La version instalada se compilo con otra firma (en otro PC): hay que quitarla
            # una vez. Se pierde solo la IP guardada a mano; el diseno vive en el PC.
            sh(sn, "uninstall", APP_PAQUETE)
            r = subprocess.run([adb, "-s", sn, "install", ruta], capture_output=True, text=True,
                               errors="replace", timeout=300, creationflags=flags)
            salida = (r.stdout or "") + (r.stderr or "")
            if "Success" in salida:
                return True, ""
        return False, salida.strip().splitlines()[-1] if salida.strip() else "?"

    revisado = {}

    def actualizar_si_hace_falta(sn):
        """Si GitHub tiene una version mas nueva de la app que la del movil, la instala."""
        revisado[sn] = time.time()
        if not actualizar_app_activado():
            return False
        v = version_disponible()
        if not v:
            return False
        tiene = version_instalada(sn)
        if tiene >= int(v["versionCode"]):
            return False
        nombre = v.get("versionName", str(v["versionCode"]))
        avisar(tr("Actualizando la app del móvil a la versión %s...", "Updating the phone app to version %s...") % nombre)
        try:
            ok, error = instalar(sn, descargar_apk(v))
        except Exception as e:
            ok, error = False, str(e)
        if ok:
            avisar(tr("App del móvil actualizada a la versión %s.", "Phone app updated to version %s.") % nombre)
        else:
            avisar(tr("No pude actualizar la app del móvil: %s", "Could not update the phone app: %s") % error)
        return ok

    conocidos = set()
    while True:
        try:
            out = subprocess.run([adb, "devices"], capture_output=True, text=True, errors="replace",
                                 timeout=5, creationflags=flags).stdout
            lineas = out.splitlines()[1:]
            if any(l.strip().endswith("offline") for l in lineas):
                # El movil se queda "offline" a veces tras un microcorte del cable.
                subprocess.run([adb, "reconnect", "offline"], capture_output=True,
                               timeout=5, creationflags=flags)
            serials = [l.split()[0] for l in lineas if l.strip().endswith("device")]
            _tunel_usb["adb"], _tunel_usb["regla"] = adb, regla
            for sn in serials:
                sh(sn, "reverse", regla, regla)
                _tunel_usb["serials"].add(sn)
                if sn not in conocidos:
                    avisar(tr("Móvil conectado por USB. Abriendo PulsePC...", "Phone connected over USB. Opening PulsePC..."))
                    sh(sn, "shell", "svc", "power", "stayon", "usb")  # pantalla encendida con USB
                    conocidos.add(sn)
                    try:
                        actualizar_si_hace_falta(sn)   # antes de abrirla, por si hay version nueva
                    except Exception as e:
                        print("Actualizar app:", e)
                    abrir_app(sn)
                elif time.time() - revisado.get(sn, 0) > CADA_CUANTO_ACTUALIZAR:
                    try:
                        if actualizar_si_hace_falta(sn):
                            abrir_app(sn)
                    except Exception as e:
                        print("Actualizar app:", e)
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
                "gpu_detalle", "ventiladores", "reloj", "procesos", "bateria", "multimedia", "grafica", "imagen"]
METRICAS_GRAFICA = ["cpu", "gpu", "ram", "temp_cpu", "temp_gpu", "red"]

DISENO_POR_DEFECTO = {
    "version": 1,
    "tema": {"fondo": "#0E1116", "tarjeta": "#171B22", "acento": "#3EC7A0",
             "secundario": "#5B9BF0", "texto": "#E6E6E6", "numeros": "#E6E6E6", "tarjeta2": "#232A35"},
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

# Diseno propio para el Nest Hub (pantalla 1024x600, mas ancha que el movil y sin tacto).
DISENO_PANTALLA_POR_DEFECTO = {
    "version": 1,
    "tema": dict(DISENO_POR_DEFECTO["tema"]),
    "cabecera": True,
    "alerta_temp": 90,
    "pagina_fija": -1,      # -1: las paginas se pasan solas; 0, 1...: se queda fija en esa
    "segundos": 15,         # cada cuanto se pasa de pagina si no hay una fija
    "paginas": [
        {"nombre": "Resumen", "columnas": 4, "filas": 2, "widgets": [
            {"tipo": "cpu", "ancho": 1, "alto": 1},
            {"tipo": "gpu", "ancho": 1, "alto": 1},
            {"tipo": "ram", "ancho": 1, "alto": 1},
            {"tipo": "reloj", "ancho": 1, "alto": 1},
            {"tipo": "grafica", "ancho": 2, "alto": 1, "metrica": "cpu"},
            {"tipo": "temps", "ancho": 1, "alto": 1},
            {"tipo": "red", "ancho": 1, "alto": 1},
        ]},
        {"nombre": "Detalle", "columnas": 3, "filas": 1, "widgets": [
            {"tipo": "nucleos", "ancho": 1, "alto": 1},
            {"tipo": "gpu_detalle", "ancho": 1, "alto": 1},
            {"tipo": "ventiladores", "ancho": 1, "alto": 1},
        ]},
    ],
}

RUTA_CONFIG = os.path.join(DIR_AGENTE, "pulsopc_config.json")
RUTA_CONFIG_PANTALLA = os.path.join(DIR_AGENTE, "pulsopc_pantalla.json")
RUTA_EDITOR = os.path.join(DIR_RECURSOS, "editor.html")
RUTA_PANTALLA = os.path.join(DIR_RECURSOS, "pantalla.html")
_diseno = {"valor": None, "pantalla": None, "lock": threading.Lock()}
DESTINOS = {
    "movil": (RUTA_CONFIG, DISENO_POR_DEFECTO),
    "pantalla": (RUTA_CONFIG_PANTALLA, DISENO_PANTALLA_POR_DEFECTO),
}


def _color(v, defecto):
    v = str(v or "")
    if len(v) == 7 and v.startswith("#"):
        try:
            int(v[1:], 16)
            return v.upper()
        except ValueError:
            pass
    return defecto


FUENTES = ["condensada", "moderna", "fina", "gruesa", "serif", "mono", "manuscrita", "informal"]
DEGRADADOS = ["no", "vertical", "horizontal", "diagonal"]


def validar_diseno(d, destino="movil"):
    """Devuelve un diseno limpio; lanza ValueError si no sirve."""
    if not isinstance(d, dict):
        raise ValueError("el diseno debe ser un objeto")
    base = DISENO_POR_DEFECTO
    tema_in = d.get("tema") or {}
    tema = {k: _color(tema_in.get(k), v) for k, v in base["tema"].items()}
    if not tema_in.get("numeros"):
        tema["numeros"] = tema["texto"]   # disenos antiguos: numeros del mismo color que el texto
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
            if w["tipo"] == "imagen":
                img = str(w.get("imagen", ""))
                limpio["imagen"] = img if RE_IMAGEN.match(img) else ""
                limpio["ajuste"] = "contener" if w.get("ajuste") == "contener" else "cubrir"
            ws.append(limpio)
        paginas.append({"nombre": str(p.get("nombre", "Pagina"))[:20], "columnas": cols,
                        "filas": filas, "widgets": ws})
    if not paginas:
        raise ValueError("hace falta al menos una pagina")
    try:
        alerta = max(50, min(110, int(d.get("alerta_temp", 90))))
    except (TypeError, ValueError):
        alerta = 90
    limpio = {"version": int(time.time()), "tema": tema, "cabecera": bool(d.get("cabecera", True)),
              "alerta_temp": alerta, "paginas": paginas,
              "fuente": d.get("fuente") if d.get("fuente") in FUENTES else "condensada",
              "degradado": d.get("degradado") if d.get("degradado") in DEGRADADOS else "no"}
    if destino == "pantalla":
        try:
            fija = int(d.get("pagina_fija", -1))
        except (TypeError, ValueError):
            fija = -1
        limpio["pagina_fija"] = fija if 0 <= fija < len(paginas) else -1
        try:
            limpio["segundos"] = max(5, min(600, int(d.get("segundos", 15))))
        except (TypeError, ValueError):
            limpio["segundos"] = 15
    return limpio


def _clave(destino):
    return "pantalla" if destino == "pantalla" else "valor"


# ---------------------------------------------------------------- imagenes --
# Fotos y GIF del widget "imagen": se suben desde el editor (solo desde el PC) y se
# guardan en agente/imagenes/ con un nombre sacado de su contenido.

import hashlib  # noqa: E402
import re  # noqa: E402

DIR_IMAGENES = os.path.join(DIR_AGENTE, "imagenes")
RE_IMAGEN = re.compile(r"^[0-9a-f]{16}\.(png|jpg|gif|webp)$")
MAX_IMAGEN = 15 * 1024 * 1024
TIPOS_IMAGEN = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}


def tipo_imagen(datos):
    if datos[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if datos[:3] == b"\xff\xd8\xff":
        return "jpg"
    if datos[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if datos[:4] == b"RIFF" and datos[8:12] == b"WEBP":
        return "webp"
    return None


def guardar_imagen(datos):
    ext = tipo_imagen(datos)
    if not ext:
        raise ValueError("solo se admiten fotos PNG, JPG, WEBP o GIF")
    nombre = hashlib.sha1(datos).hexdigest()[:16] + "." + ext
    os.makedirs(DIR_IMAGENES, exist_ok=True)
    ruta = os.path.join(DIR_IMAGENES, nombre)
    if not os.path.exists(ruta):
        with open(ruta + ".tmp", "wb") as f:
            f.write(datos)
        os.replace(ruta + ".tmp", ruta)
    return nombre


def limpiar_imagenes():
    """Borra las imagenes que ya no usa ningun diseno (y que no se acaban de subir)."""
    usadas = set()
    for d in (diseno_actual(), diseno_actual("pantalla")):
        for pg in (d or {}).get("paginas", []):
            for w in pg.get("widgets", []):
                if w.get("imagen"):
                    usadas.add(w["imagen"])
    try:
        for n in os.listdir(DIR_IMAGENES):
            ruta = os.path.join(DIR_IMAGENES, n)
            if RE_IMAGEN.match(n) and n not in usadas and time.time() - os.path.getmtime(ruta) > 3600:
                os.remove(ruta)
    except OSError:
        pass


def cargar_diseno():
    for destino, (ruta, defecto) in DESTINOS.items():
        try:
            with open(ruta, encoding="utf-8") as f:
                d = validar_diseno(json.load(f), destino)
        except FileNotFoundError:
            d = validar_diseno(defecto, destino)
        except Exception as e:
            print("  Diseno: %s no es valido (%s); uso el de por defecto." % (os.path.basename(ruta), e))
            d = validar_diseno(defecto, destino)
        with _diseno["lock"]:
            _diseno[_clave(destino)] = d


def guardar_diseno(d, destino="movil"):
    d = validar_diseno(d, destino)
    ruta = DESTINOS[destino][0]
    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ruta)
    with _diseno["lock"]:
        _diseno[_clave(destino)] = d
    limpiar_imagenes()
    return d


def diseno_actual(destino="movil"):
    with _diseno["lock"]:
        return _diseno[_clave(destino)]


# ------------------------------------------------------------------- HTTP --

PANEL_HTML = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PulsePC</title><style>
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

    def _destino(self):
        return "pantalla" if "destino=pantalla" in self.path else "movil"

    def _es_local(self):
        ip = self.client_address[0]
        return ip.startswith("127.") or ip == "::1" or ip == ip_local()

    # Proteccion frente a paginas web maliciosas abiertas en el navegador del PC.
    # Una web cualquiera puede mandar peticiones a http://127.0.0.1:47801 (CSRF) o usar un
    # dominio suyo que apunte a 127.0.0.1 (DNS rebinding) y, como llegan desde el propio PC,
    # pasarian la comprobacion de _es_local. Por eso:
    #  - solo se aceptan peticiones dirigidas a una IP, a "localhost" o al nombre del PC
    #    (la app, el editor y el Nest Hub siempre usan uno de esos; un dominio ajeno, no);
    #  - un POST que venga de un navegador tiene que venir de una pagina del propio agente.
    def _host_valido(self):
        host = (self.headers.get("Host") or "").strip().lower()
        if not host:
            return True   # clientes HTTP/1.0 sin cabecera Host (no son navegadores)
        if host.startswith("["):
            nombre = host[1:host.find("]")] if "]" in host else host[1:]
        else:
            nombre = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        nombre = nombre.rstrip(".")
        equipo = socket.gethostname().lower()
        if nombre in ("localhost", equipo, equipo + ".local"):
            return True
        try:
            ipaddress.ip_address(nombre)
            return True
        except ValueError:
            return False

    def _origen_valido(self):
        if self.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
            return False
        origen = self.headers.get("Origin")
        if origen is None:
            return True   # no es un navegador (la app del movil, la bandeja, curl...)
        return origen.strip().lower() == "http://" + (self.headers.get("Host") or "").strip().lower()

    def _rechazar(self, post=False):
        if not self._host_valido() or (post and not self._origen_valido()):
            self._json({"error": "peticion no permitida"}, 403)
            return True
        return False

    def do_GET(self):
        if self._rechazar():
            return
        ruta = self.path.split("?")[0]
        if ruta == "/stats":
            # Por USB (adb reverse) el movil llega como 127.0.0.1; por Wi-Fi, con su IP.
            # El editor y el panel web abiertos en el propio PC tambien piden /stats desde
            # 127.0.0.1, pero son navegadores ("Mozilla" en el User-Agent; la app usa el
            # cliente de Android, "Dalvik"): esos no cuentan como movil conectado.
            ip = self.client_address[0]
            local = ip.startswith("127.") or ip == "::1"
            del_pc = local or ip == ip_local()
            # La pantalla del Nest Hub tampoco es el movil: pide /stats?pantalla=1.
            pantalla = "pantalla=1" in self.path
            if not pantalla and not (del_pc and "Mozilla" in self.headers.get("User-Agent", "")):
                _ultimo["pedido"] = time.time()
                _ultimo["via"] = "USB" if local else "Wi-Fi"
            with _ultimo["lock"]:
                datos = dict(_ultimo["stats"])
            datos["diseno"] = diseno_actual("pantalla" if pantalla else "movil")
            datos["idioma"] = idioma()
            if pantalla:
                # Version de pantalla.html: si cambia (PulsePC actualizado), la pagina abierta
                # en el Nest Hub se recarga sola en vez de seguir con el codigo antiguo.
                try:
                    datos["pagina_v"] = int(os.path.getmtime(RUTA_PANTALLA))
                except OSError:
                    pass
            self._json(datos)
        elif ruta == "/config":
            destino = self._destino()
            self._json({"diseno": diseno_actual(destino), "tipos": TIPOS_WIDGET, "metricas": METRICAS_GRAFICA,
                        "destino": destino,
                        "por_defecto": validar_diseno(DESTINOS[destino][1], destino)})
        elif ruta == "/portada":
            r = ruta_portada()
            if not r:
                self.send_error(404)
                return
            with open(r, "rb") as f:
                datos = f.read()
            tipo = "image/png" if datos[:4] == b"\x89PNG" else "image/jpeg"
            self._enviar(200, datos, tipo)
        elif ruta == "/ajustes":
            self._json({"idioma": idioma()})
        elif ruta == "/imagen":
            nombre = self.path.split("id=", 1)[-1].split("&")[0] if "id=" in self.path else ""
            if not RE_IMAGEN.match(nombre) or not os.path.isfile(os.path.join(DIR_IMAGENES, nombre)):
                self.send_error(404)
                return
            with open(os.path.join(DIR_IMAGENES, nombre), "rb") as f:
                datos = f.read()
            self.send_response(200)
            self.send_header("Content-Type", TIPOS_IMAGEN[nombre.rsplit(".", 1)[1]])
            self.send_header("Content-Length", str(len(datos)))
            # El nombre sale del contenido: si cambia la foto, cambia el nombre. Se puede guardar.
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
            self.end_headers()
            self.wfile.write(datos)
        elif ruta == "/editor":
            try:
                with open(RUTA_EDITOR, "rb") as f:
                    self._enviar(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                self.send_error(404, "No encuentro editor.html junto al agente")
        elif ruta == "/pantalla":
            try:
                with open(RUTA_PANTALLA, "rb") as f:
                    self._enviar(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                self.send_error(404, "No encuentro pantalla.html junto al agente")
        elif ruta in ("/", "/index.html"):
            self._enviar(200, PANEL_HTML.encode(), "text/html; charset=utf-8")
        else:
            self.send_error(404)

    def do_POST(self):
        if self._rechazar(post=True):
            return
        if self.path.split("?")[0] == "/salir":
            # Cierra el agente (solo desde el propio PC). Sirve para actualizarlo sin
            # tener que usar el menu del icono; luego se relanza con: schtasks /Run /TN PulsePC
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
        if self.path.split("?")[0] == "/ajustes":
            # Idioma de PulsePC: solo desde el propio PC (editor).
            if not self._es_local():
                self._json({"error": "solo desde el PC"}, 403)
                return
            try:
                n = int(self.headers.get("Content-Length", "0"))
                poner_idioma(json.loads(self.rfile.read(min(n, 1000)).decode("utf-8")).get("idioma"))
                self._json({"ok": True, "idioma": idioma()})
            except Exception as e:
                self._json({"error": str(e)}, 400)
            return
        if self.path.split("?")[0] == "/imagen":
            # Subir una foto o GIF para el widget Imagen: solo desde el propio PC (el editor).
            if not self._es_local():
                self._json({"error": "Las imagenes solo se pueden subir desde el PC"}, 403)
                return
            try:
                n = int(self.headers.get("Content-Length", "0"))
                if n <= 0 or n > MAX_IMAGEN:
                    raise ValueError("la imagen tiene que ocupar menos de %d MB" % (MAX_IMAGEN // 1048576))
                datos = b""
                while len(datos) < n:
                    trozo = self.rfile.read(n - len(datos))
                    if not trozo:
                        break
                    datos += trozo
                self._json({"ok": True, "id": guardar_imagen(datos)})
            except Exception as e:
                self._json({"error": str(e)}, 400)
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
            d = guardar_diseno(json.loads(self.rfile.read(n).decode("utf-8")), self._destino())
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
    # para detectar que PulsePC ya esta abierto.
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
    cargar_ajustes()
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
    ap = argparse.ArgumentParser(description="Agente PulsePC (modo consola)")
    ap.add_argument("--puerto", type=int, default=PUERTO_HTTP)
    ap.add_argument("--intervalo", type=float, default=1.0, help="segundos entre lecturas")
    ap.add_argument("--no-mantener-app", action="store_true",
                    help="no volver a abrir la app del movil si se cierra (por USB)")
    a = ap.parse_args()

    try:
        arrancar(a.puerto, a.intervalo, not a.no_mantener_app)
    except OSError:
        print("El puerto %d ya esta en uso: seguramente PulsePC ya esta abierto "
              "(mira el icono junto al reloj)." % a.puerto)
        return
    print("PulsePC agente en marcha.")
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
