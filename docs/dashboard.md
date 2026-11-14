# REBA — Dashboard Web

Interfaz servida por FastAPI desde `static/` — abrir por HTTP en **http://localhost:5000** o la IP de la PC (nunca como archivo `file://`).

**Acceso desde el celular:** conectar el teléfono a la misma red que la PC (ej. hotspot `REBA-AP`) y abrir `http://<IP-PC>:5000` — con el hotspot activo suele ser `http://192.168.137.1:5000`. Sin autenticación por decisión: la red `REBA-AP` aísla el sistema; no exponer el puerto a redes no confiables.

---

## Módulos

La app está organizada en **3 vistas** con navegación inferior fija en móvil (`<1024px`) y un grid de 12 columnas que muestra todo en escritorio (`≥1024px`):

| Vista | Módulos |
|-------|---------|
| **En vivo** | **Última captura**: foto 4:3 con badge de clase + confianza, metadatos en mono (fecha, detecciones, inferencia ms, motor) y tira de 8 miniaturas recientes (tocable → actualiza la principal). **Panel Bomba**: progreso `fotos/N` en 2 columnas + barra segmentada + chips de duración manual (5/10/20/30 s u "Otro") + "Activar ahora" + acordeón "Registro de eventos" |
| **Analítica** | **KPIs** (total objetos, fotos recibidas) + **donut** por clase con leyenda interactiva + **actividad** (barras apiladas por día/clase, 7/14/30 días, tooltips, rejilla visible) + **galería unificada** (filtro por clase + Exportar YOLO + paginación + lightbox) |
| **Dispositivo** | **Config remota** del ESP32-CAM en 2 grupos (Red y servidor / Parámetros y sensores), unidades integradas en los inputs, mostrar/ocultar contraseña, ayuda desplegable y feedback "Guardado ✓" |

El **header sticky** muestra marca REBA, pill del dispositivo (online/offline por heartbeat), pill WebSocket y último ping relativo. El botón **Exportar YOLO** descarga un ZIP con imágenes + labels YOLO + `data.yaml` para reentrenar.

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

- `static/index.html` — estructura: header, 3 vistas (`view-live`, `view-analytics`, `view-device`), bottom nav y lightbox
- `static/styles.css` — design system oscuro plano (`#090D12`/`#111720`/`#18212E`), Space Grotesk + JetBrains Mono auto-hospedadas en `static/fonts/`, responsive móvil-first
- `static/app.js` — WS, canvas donut/gráfica de actividad con tooltips, galería, lightbox, bomba manual (chips + barra segmentada), config remota

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

El dashboard usa Space Grotesk (interfaz) y JetBrains Mono (datos técnicos) servidas como woff2 locales desde `static/fonts/` — no descarga nada de Internet y funciona sin conexión externa.
