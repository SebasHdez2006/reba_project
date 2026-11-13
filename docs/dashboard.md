# REBA — Dashboard Web

Interfaz servida por FastAPI desde `static/` — abrir por HTTP en **http://localhost:5000** o la IP de la PC (nunca como archivo `file://`).

**Acceso desde el celular:** conectar el teléfono a la misma red que la PC (ej. hotspot `REBA-AP`) y abrir `http://<IP-PC>:5000` — con el hotspot activo suele ser `http://192.168.137.1:5000`. Sin autenticación por decisión: la red `REBA-AP` aísla el sistema; no exponer el puerto a redes no confiables.

---

## Módulos

| Módulo | Qué hace |
|--------|----------|
| **Header** | Marca REBA, estado WebSocket (En vivo / Reconectando), pill del dispositivo (online/offline por heartbeat) |
| **Última captura** | Foto anotada con cajas YOLO + conteo de detecciones, ms de inferencia y fuente del motor |
| **Donut + leyenda** | % y conteo por clase (plastico, tela, organico, unicel, metal) + total acumulado |
| **Galería completa** | Grid paginado (24/pág) de todas las capturas con filtro por clase y lightbox (imagen, hora, detecciones con confianza) |
| **Panel Bomba** | Progreso automático `fotos/N` + control manual (segundos elegibles → "Activar ahora") + eventos recientes + orden pendiente (`pending_cmd`) |
| **Panel Actividad** | Gráfica de barras apiladas por día/clase (7/14/30 días), auto-refresh |
| **Exportar YOLO** | Descarga ZIP con imágenes originales + labels YOLO + `data.yaml` para reentrenar |
| **Dispositivo ESP32-CAM** | Config remota clave=valor (WiFi, server_url, bomba, distancias) — se aplica en el próximo poll del firmware (≤60 s) |

---

## Cómo se actualiza

1. Al cargar, `app.js` pide snapshot REST (`/api/config`, `/api/stats`, `/api/stats/timeseries`, `/api/pump/events`, `/api/captures`) e historial de capturas.
2. Abre WebSocket `ws://<host>/ws`.
3. El servidor empuja:
   - `capture` → foto JPEG guardada, clases y hora
   - `detections` → contadores, ms de inferencia, fuente (`onnx`/`none`), progreso de bomba
   - `pump_event` → banner "Bomba activada"
   - estado del dispositivo → actualiza el pill online/offline
4. Poll ligero de `/api/health` cada 30 s para el estado del dispositivo.
5. Ping cada 25 s para keepalive.

Archivos:

- `static/index.html` — estructura y secciones
- `static/styles.css` — tema acuático oscuro, responsive móvil (grid colapsable, lightbox, táctil)
- `static/app.js` — WS, canvas donut/gráficas, galería, lightbox, bomba manual, export YOLO, config remota

---

## Interpretar el panel Bomba

- El ciclo **automático** es autónomo en la ESP32: cada `pump_photos` fotos corre `pump_seconds`.
- El servidor **infiere** el progreso contando uploads recibidos; si la cola del ESP32 retiene fotos sin Wi-Fi, el conteo mostrado se atrasa.
- La **bomba manual** encola `pump_cmd=<unix_ts>:<s>` que el ESP32 recoge en su poll de config (≤60 s), ejecuta no-bloqueante y confirma por `/api/device/ack`. El comando expira a los 10 min — si el dispositivo estuvo offline, no hay bomba fantasma.
- `pending_cmd` muestra la orden encolada sin confirmar; `pump_events` mezcla ciclos inferidos y manuales.

---

## Requisitos de red

- Abrir por HTTP del servidor (IP de la PC), no por `file://`.
- La PC debe ser alcanzable por el ESP32 (`server_url` del firmware → mismo Wi-Fi 2.4 GHz).
- La red `REBA-AP` (hotspot Windows de la PC) no requiere Internet: PC y ESP32 se comunican por LAN.
- Windows Firewall debe permitir entrada TCP 5000.
- El dashboard y el ESP32 pueden usar un punto de acceso sin Internet; el modelo y la base de datos residen en la PC.

---

## Personalización rápida

| Qué | Dónde |
|-----|-------|
| Colores de clases | `CLASS_COLORS` en `app.js` + variables CSS |
| Fotos por ciclo / duración | `.env` (informativo) + config remota `pump_photos`/`pump_seconds` del dispositivo |
| Texto / layout / tema | `index.html` / `styles.css` |
| Rango de días de la gráfica | selector en el panel Actividad (7/14/30) |

El dashboard usa fuentes del sistema y recursos servidos por la PC; no descarga contenido de Internet — funciona sin conexión externa.
