# PulsePC

Mira las estadísticas de tu PC en tiempo real en un móvil antiguo (Android 6.0 o superior), por Wi‑Fi o por cable USB.

```
┌──────────── PC (Windows) ────────────┐        Wi‑Fi         ┌──── Móvil (Android 6+) ────┐
│ agente/pulsopc_agente.py              │  UDP 47800: "estoy aquí" │ App PulsePC                │
│  · psutil: CPU, RAM, red, discos      │ ───────────────────────▶ │  · encuentra el PC sola    │
│  · nvidia-smi: GPU                    │  HTTP 47801 /stats (JSON) │  · pide /stats cada 1 s    │
│  · LibreHardwareMonitor: temperaturas │ ◀─────────────────────── │  · dibuja el panel         │
│  · adb reverse: conexión por USB      │  o por cable: 127.0.0.1:47801            │
└───────────────────────────────────────┘                         └────────────────────────────┘
```

## 1. Agente del PC

Requiere Python 3.8+.

1. Doble clic en `agente/iniciar_agente.bat`. Instala lo necesario (`psutil`, `pystray`, `pillow`), cierra su ventana y deja **PulsePC en la bandeja**, junto al reloj (puede estar dentro de la flechita ^).
2. La primera vez, Windows pedirá permiso en el firewall: permite **redes privadas**.
3. Desde el icono: *Personalizar el móvil*, *Ver panel web*, *Arrancar con Windows* y *Salir*. El icono se pone verde cuando el móvil está leyendo datos.

**Arranque automático:** la primera vez se activa *Arrancar con Windows*. PulsePC queda esperando con muy poco consumo (lee el PC cada 5 s mientras no hay móvil) y, en cuanto enchufas el móvil por USB, le abre la app y te avisa con una notificación. Se puede desactivar desde el menú del icono.

¿Prefieres verlo en una ventana de consola para depurar? Usa `agente/iniciar_agente_consola.bat`. El registro del modo bandeja está en `agente/pulsopc.log`.

**Temperaturas, ventiladores y consumos:** PulsePC lleva [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) integrado. La primera vez descarga su librería oficial en `agente/lhm/` y la lee en segundo plano, sin abrir ninguna app. Para la temperatura de la CPU:
- PulsePC tiene que correr **como administrador**. Al abrir `iniciar_agente.bat`, Windows pide permiso una vez y PulsePC crea una tarea programada que, desde entonces, lo arranca elevado al iniciar sesión **sin más avisos**.
- LibreHardwareMonitor 0.9.5+ usa el driver **PawnIO**. Si falta, el menú del icono muestra *Instalar driver de sensores (PawnIO)* (o `winget install namazso.PawnIO`).
- El estado de los sensores aparece en la primera línea del menú del icono.

**GPU:** las tarjetas NVIDIA se leen con `nvidia-smi` en modo continuo (viene con el driver). AMD/Intel: pendiente (se puede sacar de LibreHardwareMonitor).

### Conexión por cable USB

1. En el móvil: *Ajustes → Información del teléfono →* pulsa 7 veces *Número de compilación*. Luego *Opciones de desarrollador → Depuración USB*.
2. Conecta el cable y acepta el aviso de "¿Permitir depuración USB?" en el móvil.
3. El agente detecta el móvil cada 5 s y ejecuta `adb reverse` solo. Necesita `adb`: lo busca en el PATH, en el SDK de Android Studio (`%LOCALAPPDATA%\Android\Sdk\platform-tools`) o en una carpeta `platform-tools` junto al script.

La app prueba primero el USB y, si no hay cable, usa la Wi‑Fi. Arriba se ve por dónde está conectada.

### Personalizar lo que se ve en el móvil

Con el agente abierto, abre **`agente/personalizar.bat`** (o `http://localhost:47801/editor` en el navegador del PC).

- Crea páginas y elige cuántas columnas y filas tiene cada una (hasta 4×4).
- Añade widgets, arrástralos para ordenarlos y cambia su tamaño y título:
  CPU (con temperatura), memoria, GPU, GPU detalle, temperaturas, núcleos, red, discos, ventiladores y consumo, reloj,
  **procesos** (los que más CPU gastan), **batería** (portátiles), **gráfica** grande (CPU, GPU, memoria, temperatura de CPU o GPU, red)
  y **multimedia**: botones tocables en el móvil para anterior / reproducir-pausa / siguiente y volumen, con lo que suena ahora.
- **Alerta de temperatura:** si la CPU o la GPU pasan del límite (90 °C por defecto), su tarjeta parpadea en rojo.
- Elige un tema de colores o ajusta cada color.
- Se guarda solo en `agente/pulsopc_config.json` y el móvil lo aplica en un segundo, sin reiniciar nada.
- Por seguridad, el diseño solo se puede cambiar desde el propio PC.

### Enviar a un Google Nest Hub

PulsePC también se puede ver en un **Nest Hub** (o cualquier pantalla con Chromecast), con el mismo diseño que el móvil.
No se envía solo: tú eliges cuándo desde el icono de la bandeja, en *Enviar al Nest Hub → Enviar a …*, y lo quitas con *Dejar de enviar*.

- El Nest Hub tiene que estar en la misma Wi‑Fi que el PC (y el firewall debe permitir redes privadas).
- Tiene **su propio diseño**, aparte del del móvil: en el editor (*Personalizar el Nest Hub* en el icono, o la pestaña *Nest Hub* del editor) eliges páginas, widgets y colores igual que para el móvil.
- Como el Nest Hub no deja tocar las páginas enviadas, en el editor eliges si se **queda fija una página** o si se pasan solas (y cada cuántos segundos).
- Si el Nest Hub la cierra por su cuenta, PulsePC la vuelve a abrir mientras no pulses *Dejar de enviar*.
- La misma pantalla se ve en el navegador en `http://localhost:47801/pantalla`.

### Móvil fijo dentro del PC

- **Anti-quemado:** el contenido se desplaza un par de píxeles cada minuto.
- **PC apagado:** tras 30 s sin datos, el móvil baja el brillo al mínimo y muestra solo un texto discreto que cambia de sitio. Vuelve solo cuando el PC arranca.

- **Arranque del agente con Windows:** activado por defecto desde el icono de la bandeja.
- **La app se abre sola** al encender el móvil y al recibir corriente por el cable (al encender el PC).
- **Por USB, el agente además** enciende la pantalla, la mantiene encendida mientras haya cable y vuelve a abrir la app si se cierra. Para desactivar esto último: `pulsopc_agente.py --no-mantener-app`.
- Quita el PIN o patrón de bloqueo del móvil (pon *Ninguno* o *Deslizar*) para que la app se vea sin desbloquear.

## 2. App Android

- Java puro, sin librerías externas, `minSdk 23` (Android 6.0).
- Abre la carpeta `android/` en Android Studio y pulsa *Run*, o desde terminal: `gradlew assembleDebug` → `android/app/build/outputs/apk/debug/app-debug.apk`.
- **Toca** la pantalla para cambiar de página: resumen (CPU, memoria, GPU, temperaturas, red, discos) y detalle (cada núcleo, relojes y consumo de la GPU, ventiladores).
- Si el móvil no encuentra el PC solo, **mantén pulsada** la pantalla y escribe la IP.

## API

`GET /stats` devuelve JSON con `cpu`, `ram`, `gpu[]`, `red`, `disco`, `temperaturas[]`, `ventiladores[]`, `consumos[]`, `equipo`, `so`, `uptime_s`.
