# REBA — Dashboard Web

Interfaz servida por FastAPI desde `static/` — abrir por HTTP en **http://localhost:5000** o la IP de la PC (nunca como archivo `file://`).

**Acceso desde el celular:** conectar el teléfono a la misma red que la PC (ej. hotspot `REBA-AP`) y abrir `http://<IP-PC>:5000` — con el hotspot activo suele ser `http://192.168.137.1:5000`. También es posible abrirlo desde **cualquier red con Internet** mediante el túnel zrok (`https://<nombre>.shares.zrok.io`, URL fija si se reservó con `setup_zrok.bat`, ver "Requisitos de red"). Sin autenticación por decisión: la red `REBA-AP` aísla el sistema en LAN; el túnel expone la URL a Internet — tratarla como secreta durante la prueba.

---

## Módulos

La app está organizada en **3 vistas** con navegación por **sidebar** en escritorio (`≥1024px`) y una **píldora flotante inferior** en móvil (`<1024px`). Cada vista tiene su propio grid de 12 columnas:

| Vista | Módulos |
|-------|---------|
| **En vivo** | **Última captura** (7/12 × 2 filas): foto 4:3 con badge de clase + confianza, metadatos en mono (fecha, detecciones, inferencia ms, motor) y tira de 8 miniaturas recientes (tocable → actualiza la principal). **Bomba** (5/12): progreso `fotos/N` + barra segmentada + chips de duración manual (5/10/20/30 s u "Otro") + "Activar ahora" + acordeón "Registro de eventos". **Rendimiento** (5/12, bajo Bomba): RTT de red, latencia ONNX (avg/p95), heartbeat, uploads/h, uptime del servidor, clientes WS |
| **Analítica** | **Materiales acumulados** (5/12): KPIs + donut por clase con leyenda interactiva. **Actividad** (7/12): barras apiladas por día/clase, 7/14/30 días, tooltips. **Galería** (12/12): filtro por clase + Exportar YOLO + paginación + lightbox |
| **Dispositivo** | **Config remota** del ESP32-CAM (5/12): Red y servidor / Parámetros y sensores, unidades en los inputs, mostrar/ocultar contraseña, ayuda desplegable y feedback "Guardado ✓". **Estado del circuito** (7/12): sensor HC-SR04, relé GPIO13, cola de fotos, cámara, heap, RSSI + acciones de reset |

El **topbar sticky** muestra el título/subtítulo de la vista activa, el **toggle de tema claro/oscuro** (switch tipo píldora; persiste en `localStorage` y respeta `prefers-color-scheme`), pill del dispositivo (online/offline por heartbeat), pill WebSocket y último ping relativo. El botón **Exportar YOLO** descarga un ZIP con imágenes + labels YOLO + `data.yaml` para reentrenar.

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

- `static/index.html` — estructura: sidebar, topbar con toggle de tema, 3 vistas (`view-live`, `view-analytics`, `view-device`), bottom-nav móvil y lightbox
- `static/styles.css` — design system con **tokens por tema** (`:root` oscuro + `[data-theme="light"]`): superficies, líneas, textos, acentos y colores de gráficos todos son variables; Space Grotesk + JetBrains Mono auto-hospedadas en `static/fonts/`, responsive móvil-first
- `static/app.js` — WS, canvas donut/gráfica de actividad con tooltips (colores leídos de las variables CSS al repintar), galería, lightbox, bomba manual (chips + barra segmentada), config remota, toggle de tema

**Tema claro/oscuro:** el switch del topbar conmuta `data-theme` en `<html>` y lo guarda en `localStorage` (`reba-theme`); un mini-script en `<head>` aplica el tema guardado antes de pintar (sin flash). Los canvas se repintan leyendo `--chart-*` del tema activo.

**Caché:** el servidor envía `Cache-Control: no-cache` en `/`, `.html`, `.css` y `.js` (middleware en `server/main.py`) — el navegador revalida en cada carga, así las actualizaciones se propagan por el túnel zrok sin recarga dura. Los assets llevan además `?v=<fecha>` en el HTML como rompe-caché extra.

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

### Acceso remoto — túnel zrok

Para operar desde una red distinta a la LAN (datos móviles, otro sitio):

```bat
setup_zrok.bat   :: una vez: reserva https://<nombre>.shares.zrok.io (gratis, fijo)
campo.bat        :: levanta el tunel en la ventana REBA-Tunnel
```

- Usa la URL `https://<nombre>.shares.zrok.io` en el navegador (HTTPS + `wss://` automáticos). Con reserva es **siempre la misma**; sin reserva cambia por arranque del túnel (`logs/tunnel.log`, `logs/zrok_url.txt`).
- **El ESP32 no usa el túnel** (zrok solo HTTPS; el firmware habla HTTP plano): va por la LAN/hotspot con la PC — `server_url=http://<IP-PC>:5000`.
- El servidor puede reiniciarse sin afectar al túnel; `campo.bat`/`retomar.bat` relanzan ambos.

---

## Personalización rápida

| Qué | Dónde |
|-----|-------|
| Colores de clases | `CLASS_COLORS` en `app.js` + variables CSS |
| Fotos por ciclo / duración | `.env` (informativo) + config remota `pump_photos`/`pump_seconds` del dispositivo |
| Texto / layout / tema | `index.html` / `styles.css` |
| Rango de días de la gráfica | selector en el panel Actividad (7/14/30) |

El dashboard usa Space Grotesk (interfaz) y JetBrains Mono (datos técnicos) servidas como woff2 locales desde `static/fonts/` — no descarga nada de Internet y funciona sin conexión externa.
