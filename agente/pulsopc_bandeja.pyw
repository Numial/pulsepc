"""
PulsePC en la bandeja de Windows (junto al reloj), sin ventana de terminal.

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
import pulsopc_cast as cast  # noqa: E402

NOMBRE_RUN = "PulsePC"
NOMBRE_ANTIGUO = "PulsoPC"   # nombre anterior de la app (tarea y clave Run)
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
# Con permisos de administrador se usa una tarea programada "al iniciar sesion"
# con privilegios altos: arranca elevado SIN aviso de UAC, y asi
# LibreHardwareMonitor puede leer la temperatura de la CPU.
# Sin permisos, se usa la clave Run del registro (sin temperatura de CPU).

SIN_VENTANA = 0x08000000


def pythonw():
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        w = exe[:-len("python.exe")] + "pythonw.exe"
        if os.path.isfile(w):
            exe = w
    return exe


def comando_arranque():
    return '"%s" "%s"' % (pythonw(), os.path.join(DIR, "pulsopc_bandeja.pyw"))


def _ps(script):
    import subprocess
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, errors="replace", timeout=30, creationflags=SIN_VENTANA)


def tarea_existe():
    import subprocess
    r = subprocess.run(["schtasks", "/Query", "/TN", NOMBRE_RUN], capture_output=True,
                       creationflags=SIN_VENTANA)
    return r.returncode == 0


def crear_tarea():
    exe = pythonw().replace("'", "''")
    arg = ('"%s"' % os.path.join(DIR, "pulsopc_bandeja.pyw")).replace("'", "''")
    d = DIR.replace("'", "''")
    r = _ps(
        "$a = New-ScheduledTaskAction -Execute '%s' -Argument '%s' -WorkingDirectory '%s';"
        "$t = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME;"
        "$p = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest;"
        "$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
        "-ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew;"
        "Register-ScheduledTask -TaskName '%s' -Action $a -Trigger $t -Principal $p -Settings $s -Force | Out-Null"
        % (exe, arg, d, NOMBRE_RUN))
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:200])


def borrar_tarea():
    import subprocess
    subprocess.run(["schtasks", "/Delete", "/TN", NOMBRE_RUN, "/F"], capture_output=True,
                   creationflags=SIN_VENTANA)


def run_existe():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CLAVE_RUN) as k:
            winreg.QueryValueEx(k, NOMBRE_RUN)
            return True
    except OSError:
        return False


def poner_run(activar):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CLAVE_RUN, 0, winreg.KEY_SET_VALUE) as k:
        if activar:
            winreg.SetValueEx(k, NOMBRE_RUN, 0, winreg.REG_SZ, comando_arranque())
        else:
            try:
                winreg.DeleteValue(k, NOMBRE_RUN)
            except OSError:
                pass


def arranque_activado():
    return tarea_existe() or run_existe()


def poner_arranque(activar):
    if not activar:
        poner_run(False)
        if tarea_existe():
            if agente.es_admin():
                borrar_tarea()
            else:
                ejecutar_elevado(["schtasks", "/Delete", "/TN", NOMBRE_RUN, "/F"])
        return
    if agente.es_admin():
        crear_tarea()
        poner_run(False)   # la tarea sustituye a la clave Run
    else:
        poner_run(True)


def ejecutar_elevado(cmd, args_lista=None):
    """Lanza un programa pidiendo permisos de administrador (aviso de UAC)."""
    import ctypes
    import subprocess
    exe = cmd[0]
    params = subprocess.list2cmdline(cmd[1:])
    return ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, DIR, 1) > 32


def migrar_nombre_antiguo():
    """La app se llamaba PulsoPC: se borra la tarea/clave Run con ese nombre y se crea la nueva."""
    import subprocess
    habia = False
    r = subprocess.run(["schtasks", "/Query", "/TN", NOMBRE_ANTIGUO], capture_output=True,
                       creationflags=SIN_VENTANA)
    if r.returncode == 0 and agente.es_admin():
        subprocess.run(["schtasks", "/Delete", "/TN", NOMBRE_ANTIGUO, "/F"], capture_output=True,
                       creationflags=SIN_VENTANA)
        habia = True
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CLAVE_RUN, 0, winreg.KEY_ALL_ACCESS) as k:
            winreg.DeleteValue(k, NOMBRE_ANTIGUO)
            habia = True
    except OSError:
        pass
    if habia:
        poner_arranque(True)


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
    # Lanzado desde iniciar_agente.bat: pedimos permisos de administrador una vez
    # (para la temperatura de la CPU). Si se rechaza el aviso, sigue sin ellos.
    if "--elevar" in sys.argv and os.name == "nt" and not agente.es_admin():
        if ejecutar_elevado([pythonw(), os.path.join(DIR, "pulsopc_bandeja.pyw"), "--esperar"]):
            return
    intentos = 20 if "--esperar" in sys.argv else 1
    for i in range(intentos):
        try:
            agente.arrancar()
            break
        except OSError:
            if i < intentos - 1:
                time.sleep(0.5)
                continue
            # Ya hay un PulsePC abierto: abrimos el editor y salimos sin duplicar.
            webbrowser.open("http://localhost:%d/editor" % agente.PUERTO_HTTP)
            return

    es_windows = os.name == "nt"
    if es_windows:
        quitar_acceso_antiguo()
        try:
            migrar_nombre_antiguo()
        except Exception as e:
            logging.info("No pude migrar el arranque antiguo: %s", e)
        marca = os.path.join(DIR, ".arranque_configurado")
        try:
            if not os.path.exists(marca):
                # Primera vez: se activa solo el arranque con Windows (se puede quitar en el menu).
                poner_arranque(True)
                open(marca, "w").close()
            elif agente.es_admin() and run_existe() and not tarea_existe():
                # Ya estaba activado sin permisos: lo pasamos a tarea elevada.
                poner_arranque(True)
        except Exception as e:
            logging.info("No pude configurar el arranque con Windows: %s", e)

    url = "http://localhost:%d" % agente.PUERTO_HTTP

    def alternar_arranque(icon, item):
        poner_arranque(not arranque_activado())

    def salir(icon, item):
        try:
            cast.parar()
        except Exception:
            pass
        agente._cerrar_procesos()
        icon.stop()

    def reiniciar_admin(icon, item):
        # Se lanza otra copia elevada; esta se cierra para liberar el puerto.
        if ejecutar_elevado([pythonw(), os.path.join(DIR, "pulsopc_bandeja.pyw"), "--esperar"]):
            salir(icon, item)

    def instalar_pawnio(icon, item):
        ejecutar_elevado(["winget", "install", "--id", "namazso.PawnIO", "-e",
                          "--accept-package-agreements", "--accept-source-agreements"])
        avisar_local(tr("Instalando el driver PawnIO. Cuando termine, reinicia PulsePC.",
                        "Installing the PawnIO driver. When it finishes, restart PulsePC."))

    tr = agente.tr

    def L(es, en):
        """Texto de menu que cambia con el idioma."""
        return lambda it: tr(es, en)

    def poner_idioma(valor):
        def _accion(icon, item):
            agente.poner_idioma(valor)
            icon.update_menu()
        return _accion

    def avisar_local(t):
        agente.estado["avisos"].append(t)

    # ---- Enviar a una pantalla (Nest Hub): solo cuando el usuario lo pide ----
    def buscar_pantallas(icon=None, item=None):
        def _b():
            try:
                cast.buscar()
                # Si una pantalla ya estaba mostrando PulsePC (enviada antes de reiniciar el
                # agente), se le vuelve a enviar: asi carga la version actual de la pagina
                # (DashCast no la recarga sola y se quedaba con la antigua, sin pagina fija)
                # y este agente la vigila y la puede cerrar con "Dejar de enviar".
                time.sleep(2)
                uuid = cast.uuid_mostrando()
                if uuid and not cast.estado["enviando"]:
                    cast.enviar(uuid, "http://%s:%d/pantalla" % (agente.ip_local(), agente.PUERTO_HTTP))
                    logging.info("Pantalla que ya mostraba PulsePC recargada con la version actual.")
            except Exception as e:
                logging.info("Buscar pantallas: %s", e)
                cast.estado["error"] = str(e)
            ic.update_menu()
        threading.Thread(target=_b, daemon=True).start()
        if icon is not None:
            ic.update_menu()

    def enviar_a(uuid):
        def _accion(icon, item):
            def _e():
                try:
                    cast.enviar(uuid, "http://%s:%d/pantalla" % (agente.ip_local(), agente.PUERTO_HTTP))
                    avisar_local(tr("Enviando PulsePC a %s.", "Sending PulsePC to %s.") % cast.estado["enviando"])
                except Exception as e:
                    logging.info("Enviar a pantalla: %s", e)
                    avisar_local(tr("No pude enviar a la pantalla: %s", "Could not send to the display: %s") % e)
                icon.update_menu()
            threading.Thread(target=_e, daemon=True).start()
        return _accion

    def dejar_de_enviar(icon, item):
        def _p():
            cast.parar()
            icon.update_menu()
        threading.Thread(target=_p, daemon=True).start()

    def opciones_pantalla():
        est = cast.estado
        if not cast.disponible():
            yield pystray.MenuItem(tr("Falta instalar pychromecast (abre iniciar_agente.bat)",
                                      "pychromecast is not installed (open iniciar_agente.bat)"), None, enabled=False)
            return
        actual = cast.mostrando()
        if actual:
            yield pystray.MenuItem(tr("Dejar de enviar a %s", "Stop sending to %s") % actual, dejar_de_enviar)
            yield pystray.Menu.SEPARATOR
        for p in est["pantallas"]:
            yield pystray.MenuItem(tr("Enviar a %s", "Send to %s") % p["nombre"], enviar_a(p["uuid"]),
                                   checked=(lambda it, n=p["nombre"]: cast.estado["enviando"] == n), radio=True)
        if est["buscando"]:
            yield pystray.MenuItem(tr("Buscando pantallas…", "Looking for displays…"), None, enabled=False)
        elif est["buscado"] and not est["pantallas"]:
            yield pystray.MenuItem(tr("No encuentro ninguna pantalla en tu Wi-Fi", "No displays found on your Wi-Fi"), None, enabled=False)
        yield pystray.MenuItem(tr("Buscar pantallas otra vez", "Search for displays again"), buscar_pantallas, enabled=not est["buscando"])

    menu = pystray.Menu(
        pystray.MenuItem(lambda it: agente.estado_lhm["texto"], None, enabled=False),
        pystray.MenuItem(L("Reiniciar como administrador (temperatura de CPU)",
                           "Restart as administrator (CPU temperature)"), reiniciar_admin,
                         visible=lambda it: es_windows and not agente.es_admin()),
        pystray.MenuItem(L("Instalar driver de sensores (PawnIO)", "Install sensor driver (PawnIO)"), instalar_pawnio,
                         visible=lambda it: es_windows and agente.es_admin() and not agente.pawnio_instalado()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(L("Personalizar el móvil", "Customize the phone"), lambda i, it: webbrowser.open(url + "/editor"), default=True),
        pystray.MenuItem(L("Personalizar el Nest Hub", "Customize the Nest Hub"), lambda i, it: webbrowser.open(url + "/editor#pantalla")),
        pystray.MenuItem(L("Ver panel web", "Open web panel"), lambda i, it: webbrowser.open(url + "/pantalla")),
        pystray.MenuItem(lambda it: (tr("Nest Hub: enviando a %s", "Nest Hub: sending to %s") % cast.mostrando())
                         if cast.mostrando() else tr("Enviar al Nest Hub", "Send to Nest Hub"),
                         pystray.Menu(opciones_pantalla)),
        pystray.MenuItem("Idioma / Language", pystray.Menu(
            pystray.MenuItem("Español", poner_idioma("es"), checked=lambda it: agente.idioma() == "es", radio=True),
            pystray.MenuItem("English", poner_idioma("en"), checked=lambda it: agente.idioma() == "en", radio=True),
        )),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(L("Arrancar con Windows", "Start with Windows"), alternar_arranque,
                         checked=lambda it: arranque_activado(), visible=es_windows),
        pystray.MenuItem(L("Abrir registro", "Open log"), lambda i, it: os.startfile(os.path.join(DIR, "pulsopc.log"))
                         if es_windows else None, visible=es_windows),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(L("Salir de PulsePC", "Quit PulsePC"), salir),
    )
    icon = pystray.Icon("PulsePC", ICONO_OFF, tr("PulsePC · esperando al móvil", "PulsePC · waiting for the phone"), menu)
    ic = icon

    def vigilar():
        conectado = None
        while True:
            # El metodo sale de por donde llegan de verdad las peticiones del movil, y se
            # revisa cada segundo: antes se decidia una sola vez al conectar (con la lista
            # de adb, que aun podia estar vacia) y se quedaba en "Wi-Fi" aunque fuera por cable.
            via = agente.via_movil()
            if (via, agente.idioma()) != conectado:
                conectado = (via, agente.idioma())
                icon.icon = ICONO_ON if via else ICONO_OFF
                icon.title = ((tr("PulsePC · móvil conectado (%s)", "PulsePC · phone connected (%s)") % via) if via
                              else tr("PulsePC · esperando al móvil", "PulsePC · waiting for the phone"))
                logging.info(icon.title)
            while agente.estado["avisos"]:
                texto = agente.estado["avisos"].pop(0)
                try:
                    icon.notify(texto, "PulsePC")
                except Exception:
                    pass
            time.sleep(1)

    def al_arrancar(icon):
        icon.visible = True
        threading.Thread(target=vigilar, daemon=True).start()
        # Solo busca qué pantallas hay (no envía nada) para tener la lista lista en el menú.
        if cast.disponible():
            buscar_pantallas()
        logging.info("PulsePC en marcha en la bandeja.")

    icon.run(setup=al_arrancar)


if __name__ == "__main__":
    main()
