# REBA — Dashboard Web

Interfaz servida por FastAPI desde `static/` en **http://localhost:5000**.

---

## Qué muestra

| Elemento | Descripción |
|----------|-------------|
| Marca **REBA** | Identidad del producto en el header |
| Estado WebSocket | Píldora En vivo / Reconectando |
| Capturas clasificadas | Última foto (con cajas YOLO) y galería local de fotos guardadas en SQLite |
| Detecciones foto / Inferencia / Motor | Conteo de objetos en la última foto, ms de inferencia y fuente (`onnx`/`none`) |
| Total objetos | Contador acumulado SQLite |
| Fotos recibidas | Uploads totales procesados por `/upload` |
| Donut chart | % plastico, tela, organico, unicel, metal |
| Leyenda | Conteo y % por clase |
| Panel Bomba | Progreso `X / 3` fotos hacia el ciclo, duración del ciclo y hora del último ciclo inferido |
| Banner ALERTA | Visible unos segundos cuando se completa un ciclo de bomba |

---

## Cómo se actualiza

1. Al cargar, `app.js` pide snapshot REST (`/api/config`, `/api/stats`, `/api/pump-status`) e historial local de fotos (`/api/captures`).
2. Abre WebSocket `ws://<host>/ws`.
3. El servidor empuja:
   - `capture` → foto JPEG guardada, clases y hora
   - `detections` → contadores, ms de inferencia, fuente (`onnx` o `none`), progreso de bomba
   - `pump_event` → banner "Bomba activada" durante ~`PUMP_RUN_SECONDS` segundos
4. Ping cada 25 s para keepalive.

Archivos:

- `static/index.html` — estructura
- `static/styles.css` — tema acuático con fuentes del sistema
- `static/app.js` — WS, chart canvas, progreso de bomba

---

## Interpretar el progreso de bomba

- La bomba es **autónoma en la ESP32**: cada `FOTOS_PARA_BOMBA` fotos tomadas corre `TIEMPO_BOMBA_MS`.
- El servidor **infiere** el ciclo contando fotos efectivamente recibidas por `/upload`. Si el ESP32 retiene fotos en su cola (sin Wi-Fi), el progreso mostrado se atrasa respecto al conteo real.
- `last_pump_event` es la hora del último ciclo **inferido por el servidor**, no confirmado por el hardware.

---

## Requisitos de red en el navegador

- Abre el dashboard por HTTP del servidor (`localhost:5000` o la IP del PC), **no** abriendo el HTML como archivo.
- El PC debe ser alcanzable por el ESP32 (`SERVIDOR_URL` del firmware → mismo Wi-Fi 2.4 GHz).
- La ESP32 y la PC pueden usar un punto de acceso sin Internet; el modelo y la base de datos residen en la PC.

---

## Personalización rápida

| Qué | Dónde |
|-----|-------|
| Colores de clases | `CLASS_COLORS` en `app.js` + variables CSS |
| Fotos por ciclo / duración | `.env` (`PUMP_TRIGGER_PHOTOS`/`PUMP_RUN_SECONDS`) + constantes del firmware (deben coincidir) |
| Texto / layout | `index.html` / `styles.css` |

El dashboard usa fuentes del sistema y recursos servidos por la PC; no descarga contenido de Internet.
