# PulsoPC

Mira las estadísticas de tu PC en tiempo real en un móvil antiguo (Android 6.0 o superior), por Wi‑Fi o por cable USB.

```
┌──────────── PC (Windows) ────────────┐        Wi‑Fi         ┌──── Móvil (Android 6+) ────┐
│ agente/pulsopc_agente.py              │  UDP 47800: "estoy aquí" │ App PulsoPC                │
│  · psutil: CPU, RAM, red, discos      │ ───────────────────────▶ │  · encuentra el PC sola    │
│  · nvidia-smi: GPU                    │  HTTP 47801 /stats (JSON) │  · pide /stats cada 1 s    │
│  · LibreHardwareMonitor: temperaturas │ ◀─────────────────────── │  · dibuja el panel         │
│  · adb reverse: conexión por USB      │  o por cable: 127.0.0.1:47801            │
└───────────────────────────────────────┘                         └────────────────────────────┘
```

## 1. Agente del PC

Requiere Python 3.8+.

1. Doble clic en `agente/iniciar_agente.bat` (instala `psutil` y arranca).
2. La primera vez, Windows pedirá permiso en el firewall: permite **redes privadas**.
3. La ventana muestra la dirección, p. ej. `http://192.168.1.20:47801/`. Ese panel web también funciona en el navegador de cualquier móvil.

**Temperaturas en Windows:** Windows no las da directamente. Abre [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) como administrador y activa *Options → Remote Web Server → Run* (puerto 8085). El agente las leerá de ahí.

**GPU:** las tarjetas NVIDIA se leen con `nvidia-smi` (viene con el driver). AMD/Intel: pendiente (se puede sacar de LibreHardwareMonitor).

### Conexión por cable USB

1. En el móvil: *Ajustes → Información del teléfono →* pulsa 7 veces *Número de compilación*. Luego *Opciones de desarrollador → Depuración USB*.
2. Conecta el cable y acepta el aviso de "¿Permitir depuración USB?" en el móvil.
3. El agente detecta el móvil cada 5 s y ejecuta `adb reverse` solo. Necesita `adb`: lo busca en el PATH, en el SDK de Android Studio (`%LOCALAPPDATA%\Android\Sdk\platform-tools`) o en una carpeta `platform-tools` junto al script.

La app prueba primero el USB y, si no hay cable, usa la Wi‑Fi. Arriba se ve por dónde está conectada.

### Móvil fijo dentro del PC

- **Arranque del agente con Windows:** ejecuta una vez `agente/arrancar_con_windows.bat`.
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
