"""
PulsoPC en la bandeja de Windows (junto al reloj), sin ventana de terminal.

- Arranca el agente en segundo plano.
- Icono verde: el movil esta leyendo datos. Gris: esperando al movil.
- Menu: personalizar, panel web, arrancar con Windows, salir.
- Avisa con una notificacion al enchufar el movil (y le abre la app).
- Con "Arrancar con Windows" activado, al encender el PC queda esperando
  con muy poco consumo; en cuanto enchufas el movil, abre la app sola.

Requisitos: pip install psutil pystray pillow
Se abre con pythonw (sin consola): doble clic en iniciar_agente.bat.
"""

import logging
import os
import sys
import threading
import time
import webbrowser

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)

logging.basicConfig(filename=os.path.join(DIR, "pulsopc.log"), level=logging.INFO,
                    format="%(asctime)s %(message)s")

# Sin consola, print() no tiene donde escribir: lo mandamos al registro.
class _AlLog:
    def write(self, t):
        t = t.strip()
        if t:
            logging.info(t)

    def flush(self):
        pass

if sys.stdout is None or not sys.stdout.isatty():
    sys.stdout = sys.stderr = _AlLog()

import pystray  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

import pulsopc_agente as agente  # noqa: E402

NOMBRE_RUN = "PulsoPC"
CLAVE_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"


# ------------------------------------------------------------------ icono --

def dibujar_icono(color):
    s = 64
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([2, 2, s - 3, s - 3], radius=12, fill=(14, 17, 22, 255))
    pts = [(9, 35), (21, 35), (27, 21), (35, 45), (40, 35), (55, 35)]
    d.line(pts, fill=color, width=6, joint="curve")
    return im


ICONO_ON = dibujar_icono((62, 199, 160, 255))
ICONO_OFF = dibujar_icono((120, 128, 140, 255))


# --------------------------------------------------- arrancar con Windows --

def comando_arranque():
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        w = exe[:-len("python.exe")] + "pythonw.exe"
        if os.path.isfile(w):
            exe = w
    return '"%s" "%s"' % (exe, os.path.join(DIR, "pulsopc_bandeja.pyw"))


def arranque_activado():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CLAVE_RUN) as k:
            winreg.QueryValueEx(k, NOMBRE_RUN)
            return True
    except OSError:
        return False


def poner_arranque(activar):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CLAVE_RUN, 0, winreg.KEY_SET_VALUE) as k:
        if activar:
            winreg.SetValueEx(k, NOMBRE_RUN, 0, winreg.REG_SZ, comando_arranque())
        else:
            try:
                winreg.DeleteValue(k, NOMBRE_RUN)
            except OSError:
                pass


def quitar_acceso_antiguo():
    """La version 0.2 ponia un acceso directo en la carpeta Inicio; ahora va en el registro."""
    try:
        ruta = os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs\Startup",
                            "PulsoPC agente.lnk")
        if os.path.isfile(ruta):
            os.remove(ruta)
    except Exception:
        pass


# ------------------------------------------------------------------- main --

def main():
    try:
        agente.arrancar()
    except OSError:
        # Ya hay un PulsoPC abierto: abrimos el editor y salimos sin duplicar.
        webbrowser.open("http://localhost:%d/editor" % agente.PUERTO_HTTP)
        return

    es_windows = os.name == "nt"
    if es_windows:
        quitar_acceso_antiguo()
        marca = os.path.join(DIR, ".arranque_configurado")
        if not os.path.exists(marca):
            # Primera vez: se activa solo el arranque con Windows (se puede quitar en el menu).
            try:
                poner_arranque(True)
                open(marca, "w").close()
            except Exception as e:
                logging.info("No pude activar el arranque con Windows: %s", e)

    url = "http://localhost:%d" % agente.PUERTO_HTTP

    def alternar_arranque(icon, item):
        poner_arranque(not arranque_activado())

    def salir(icon, item):
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Personalizar el móvil", lambda i, it: webbrowser.open(url + "/editor"), default=True),
        pystray.MenuItem("Ver panel web", lambda i, it: webbrowser.open(url + "/")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Arrancar con Windows", alternar_arranque,
                         checked=lambda it: arranque_activado(), visible=es_windows),
        pystray.MenuItem("Abrir registro", lambda i, it: os.startfile(os.path.join(DIR, "pulsopc.log"))
                         if es_windows else None, visible=es_windows),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Salir de PulsoPC", salir),
    )
    icon = pystray.Icon("PulsoPC", ICONO_OFF, "PulsoPC · esperando al móvil", menu)

    def vigilar():
        conectado = None
        while True:
            ahora = agente.movil_conectado()
            if ahora != conectado:
                conectado = ahora
                icon.icon = ICONO_ON if ahora else ICONO_OFF
                via = "USB" if agente.estado["usb"] else "Wi-Fi"
                icon.title = ("PulsoPC · móvil conectado (%s)" % via) if ahora else "PulsoPC · esperando al móvil"
            while agente.estado["avisos"]:
                texto = agente.estado["avisos"].pop(0)
                try:
                    icon.notify(texto, "PulsoPC")
                except Exception:
                    pass
            time.sleep(1)

    def al_arrancar(icon):
        icon.visible = True
        threading.Thread(target=vigilar, daemon=True).start()
        logging.info("PulsoPC en marcha en la bandeja.")

    icon.run(setup=al_arrancar)


if __name__ == "__main__":
    main()
