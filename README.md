# PulsoPC

Mira las estadísticas de tu PC en tiempo real en un móvil antiguo (Android 6.0 o superior) por la Wi‑Fi de casa.

```
┌──────────── PC (Windows) ────────────┐        Wi‑Fi         ┌──── Móvil (Android 6+) ────┐
│ agente/pulsopc_agente.py              │  UDP 47800: "estoy aquí" │ App PulsoPC                │
│  · psutil: CPU, RAM, red, discos      │ ───────────────────────▶ │  · encuentra el PC sola    │
│  · nvidia-smi: GPU                    │  HTTP 47801 /stats (JSON) │  · pide /stats cada 1 s    │
│  · LibreHardwareMonitor: temperaturas │ ◀─────────────────────── │  · dibuja el panel         │
└───────────────────────────────────────┘                         └────────────────────────────┘
```

## 1. Agente del PC

Requiere Python 3.8+.

1. Doble clic en `agente/iniciar_agente.bat` (instala `psutil` y arranca).
2. La primera vez, Windows pedirá permiso en el firewall: permite **redes privadas**.
3. La ventana muestra la dirección, p. ej. `http://192.168.1.20:47801/`. Ese panel web también funciona en el navegador de cualquier móvil.

**Temperaturas en Windows:** Windows no las da directamente. Abre [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) como administrador y activa *Options → Remote Web Server → Run* (puerto 8085). El agente las leerá de ahí.

**GPU:** las tarjetas NVIDIA se leen con `nvidia-smi` (viene con el driver). AMD/Intel: pendiente (se puede sacar de LibreHardwareMonitor).

## 2. App Android

- Java puro, sin librerías externas, `minSdk 23` (Android 6.0).
- Abre la carpeta `android/` en Android Studio y pulsa *Run*, o desde terminal: `gradlew assembleDebug` → `android/app/build/outputs/apk/debug/app-debug.apk`.
- Si el móvil no encuentra el PC solo, mantén pulsada la pantalla y escribe la IP.

## API

`GET /stats` devuelve JSON con `cpu`, `ram`, `gpu[]`, `red`, `disco`, `temperaturas[]`, `ventiladores[]`, `equipo`, `so`, `uptime_s`.
