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

_ultimo = {"stats": {}, "lock": threading.Lock()}


# ---------------------------------------------------------------- lectores --

def leer_gpu_nvidia():
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    campos = "name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,fan.speed"
    try:
        flags = 0x08000000 if platform.system() == "Windows" else 0  # sin ventana
        out = subprocess.run(
            [exe, "--query-gpu=" + campos, "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3, creationflags=flags,
        ).stdout
    except Exception:
        return []
    gpus = []
    for linea in out.strip().splitlines():
        p = [x.strip() for x in linea.split(",")]
        if len(p) < 7:
            continue

        def num(v):
            try:
                return float(v)
            except ValueError:
                return None

        gpus.append({
            "nombre": p[0],
            "uso": num(p[1]),
            "vram_usada_mb": num(p[2]),
            "vram_total_mb": num(p[3]),
            "temp": num(p[4]),
            "consumo_w": num(p[5]),
            "ventilador": num(p[6]),
        })
    return gpus


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
    temps, fans = [], []
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
    return {"temperaturas": temps, "ventiladores": fans}


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

        lhm = leer_lhm()
        if lhm:
            temps, fans = lhm["temperaturas"], lhm["ventiladores"]
        else:
            temps, fans = leer_temps_psutil(), []

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
            "fuente_temps": "LibreHardwareMonitor" if lhm else "psutil",
        }
        self.t, self.red, self.disco = ahora, red, disco
        return stats


def bucle_muestreo(intervalo):
    m = Muestreador()
    time.sleep(intervalo)
    while True:
        try:
            s = m.leer()
            with _ultimo["lock"]:
                _ultimo["stats"] = s
        except Exception as e:  # nunca dejar morir el hilo
            print("Error leyendo estadisticas:", e)
        time.sleep(intervalo)


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
    def do_GET(self):
        if self.path.startswith("/stats"):
            with _ultimo["lock"]:
                cuerpo = json.dumps(_ultimo["stats"]).encode()
            tipo = "application/json"
        elif self.path in ("/", "/index.html"):
            cuerpo, tipo = PANEL_HTML.encode(), "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

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


def main():
    ap = argparse.ArgumentParser(description="Agente PulsoPC")
    ap.add_argument("--puerto", type=int, default=PUERTO_HTTP)
    ap.add_argument("--intervalo", type=float, default=1.0, help="segundos entre lecturas")
    a = ap.parse_args()

    threading.Thread(target=bucle_muestreo, args=(a.intervalo,), daemon=True).start()
    threading.Thread(target=bucle_anuncio, args=(a.puerto,), daemon=True).start()

    srv = ThreadingHTTPServer(("0.0.0.0", a.puerto), Manejador)
    print("PulsoPC agente en marcha.")
    print("  Panel web: http://%s:%d/" % (ip_local(), a.puerto))
    print("  La app del movil lo encontrara sola si estais en la misma Wi-Fi.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
