"""Enviar la pantalla de PulsePC a un Google Nest Hub (o cualquier pantalla con Chromecast).

Solo se envia cuando el usuario lo pide desde el menu de la bandeja; nunca solo.
Usa pychromecast y la app "DashCast" de Google, que abre una pagina web en la pantalla.
La pagina es http://<IP del PC>:47801/pantalla, asi que el Nest Hub tiene que estar
en la misma Wi-Fi y el firewall de Windows tiene que dejar entrar en redes privadas.

Mientras se esta enviando, se comprueba cada 30 s que la pantalla sigue mostrando
PulsePC y, si el Nest Hub la ha cerrado por su cuenta, se vuelve a abrir. Al pulsar
"Dejar de enviar" (o al salir de PulsePC) se cierra.
"""

import logging
import threading
import time

APP_DASHCAST = "84912283"

estado = {
    "pantallas": [],        # [{"nombre", "modelo", "uuid"}] con pantalla (no altavoces)
    "buscando": False,
    "buscado": False,
    "enviando": None,       # nombre de la pantalla a la que se envia, o None
    "error": "",
}

_lock = threading.Lock()
_casts = {}                 # uuid -> objeto Chromecast
_browser = {"b": None}
_url = {"v": ""}
_parar = threading.Event()


def disponible():
    try:
        import pychromecast  # noqa: F401
        return True
    except Exception:
        return False


def buscar(espera=6):
    """Busca pantallas Chromecast en la red local. Devuelve la lista de pantallas."""
    import pychromecast
    with _lock:
        if estado["buscando"]:
            return estado["pantallas"]
        estado["buscando"] = True
    try:
        casts, browser = pychromecast.get_chromecasts(timeout=espera)
        viejo = _browser["b"]
        _browser["b"] = browser
        if viejo is not None and estado["enviando"] is None:
            try:
                viejo.stop_discovery()
            except Exception:
                pass
        pantallas = []
        for c in casts:
            info = c.cast_info
            # Los altavoces (cast_type "audio") y los grupos no tienen pantalla.
            if info.cast_type and info.cast_type != "cast":
                continue
            uuid = str(info.uuid)
            _casts[uuid] = c
            # Conectar en segundo plano para saber si ya muestra PulsePC (p. ej. tras reiniciar
            # el agente) y poder ofrecer "Dejar de enviar". No envia nada.
            try:
                if not c.socket_client.is_alive():
                    c.start()
            except Exception:
                pass
            pantallas.append({"nombre": info.friendly_name or "Pantalla", "modelo": info.model_name or "",
                              "uuid": uuid})
        # Primero los Nest Hub, luego el resto (Chromecast con tele, etc.).
        pantallas.sort(key=lambda p: (0 if "hub" in p["modelo"].lower() else 1, p["nombre"].lower()))
        estado["pantallas"] = pantallas
        estado["error"] = ""
        logging.info("Pantallas encontradas: %s", ", ".join("%s (%s)" % (p["nombre"], p["modelo"])
                                                            for p in pantallas) or "ninguna")
        return pantallas
    finally:
        estado["buscando"] = False
        estado["buscado"] = True


def _abrir(cast, url):
    from pychromecast.controllers.dashcast import DashCastController
    cast.wait(timeout=10)
    dash = DashCastController()
    cast.register_handler(dash)
    # force=True: la pagina se abre directamente (no dentro de un marco).
    dash.load_url(url, force=True)


def enviar(uuid, url):
    """Empieza a enviar la pantalla. Lanza excepcion si no se puede."""
    cast = _casts.get(uuid)
    if cast is None:
        raise RuntimeError("No encuentro esa pantalla; vuelve a buscar.")
    parar()
    _url["v"] = url
    try:
        _abrir(cast, url)
    except Exception as e:
        logging.info("Enviar a %s: %r", cast.cast_info.friendly_name, e)
        if "Timeout" in type(e).__name__ or "timed out" in str(e).lower():
            raise RuntimeError("%s no responde. Reinícialo (desenchúfalo 10 s) y vuelve a probar."
                               % (cast.cast_info.friendly_name or "La pantalla"))
        raise
    estado["enviando"] = cast.cast_info.friendly_name or "Pantalla"
    estado["error"] = ""
    _parar.clear()
    threading.Thread(target=_vigilar, args=(cast,), daemon=True).start()
    logging.info("Enviando %s a %s", url, estado["enviando"])


def _vigilar(cast):
    # Si el Nest Hub cierra la pagina (pasa a veces tras un rato, o si alguien le pide
    # otra cosa con la voz), se vuelve a abrir mientras siga activado "enviar".
    fallos = 0
    while not _parar.wait(30):
        try:
            app = cast.status.app_id if cast.status else None
            if app != APP_DASHCAST:
                logging.info("La pantalla cerro PulsePC (app %s); la vuelvo a abrir.", app)
                _abrir(cast, _url["v"])
            fallos = 0
        except Exception as e:
            fallos += 1
            logging.info("Envio a pantalla: %s", e)
            if fallos >= 10:
                estado["error"] = "Se perdio la conexion con %s" % estado["enviando"]
                estado["enviando"] = None
                return


def mostrando():
    """Nombre de la pantalla que muestra PulsePC ahora (aunque lo haya enviado otro programa)."""
    if estado["enviando"]:
        return estado["enviando"]
    for c in list(_casts.values()):
        try:
            if c.status and c.status.app_id == APP_DASHCAST:
                return c.cast_info.friendly_name or "Pantalla"
        except Exception:
            pass
    return None


def uuid_mostrando():
    """uuid de la pantalla que ya muestra PulsePC (DashCast) aunque no la haya enviado este agente."""
    for uuid, c in list(_casts.items()):
        try:
            if c.status and c.status.app_id == APP_DASHCAST:
                return uuid
        except Exception:
            pass
    return None


def parar():
    """Deja de enviar y cierra PulsePC en la pantalla (en todas las que lo muestren)."""
    _parar.set()
    nombre = estado["enviando"]
    estado["enviando"] = None
    for c in list(_casts.values()):
        try:
            if c.status and c.status.app_id == APP_DASHCAST:
                c.quit_app()
                logging.info("Dejo de enviar a %s", c.cast_info.friendly_name)
        except Exception as e:
            logging.info("No pude cerrar la pantalla %s: %s", nombre or "", e)
