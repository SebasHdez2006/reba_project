# REBA — Dashboard Web

Interfaz servida por FastAPI desde `static/` — abrir por HTTP en **http://localhost:5000** o la IP de la PC (nunca como archivo `file://`).

**Acceso desde el celular:** conectar el teléfono a la misma red que la PC (ej. hotspot `REBA-AP`) y abrir `http://<IP-PC>:5000` — con el hotspot activo suele ser `http://192.168.137.1:5000`. Desde **cualquier red con Internet** abrir `https://rebaserver.tech` (túnel Cloudflare de dirección fija — también sirve a la ESP32 por `http://rebaserver.tech`); respaldo: `https://reba-cam.shares.zrok.io` (zrok). Sin autenticación por decisión: la red `REBA-AP` aísla el sistema en LAN; el dominio expone la URL a Internet — tratarla como secreta durante la prueba.

---

## Módulos

La app está organizada en **6 vistas** con navegación por **sidebar** en escritorio (`≥1024px`) y una **píldora flotante inferior** en móvil (`<1024px`). Cada vista tiene su propio grid de 12 columnas:

| Vista | Módulos |
|-------|---------|
| **01 · Estado** | **Salud del sistema**: estado del dispositivo (nunca conectado / desconectado / datos desactualizados / en línea), último contacto, fotos en archivo, objetos detectados, ciclos de bomba. **Panel de atención**: lista priorizada de anomalías (sin señal, cola de fotos, telemetría incompleta, etc.) |
| **02 · Operación** | **Última captura**: foto 4:3 con badge de clase + confianza, metadatos en mono, tira de 8 miniaturas recientes y botón **"Tomar foto ahora"** (`POST /api/capture/run`). **Bomba**: estado del relé inequívoco (detenida / activa hasta / manual), progreso `fotos/N` + barra segmentada + chips de duración (5/10/20/30 s u "Otro") + "Activar ahora" con bloqueo anti-doble-toque. **Órdenes al dispositivo**: ciclo de vida de cada orden (ver abajo) |
| **03 · Sensores** | **Telemetría del ESP32**: distancia HC-SR04, RSSI, relé GPIO13, heap, cámara, cola de fotos, uptime/boots, versión de firmware, servidor activo (`srv_act`), fotos por ciclo y descanso de bomba — con antigüedad del dato |
| **04 · Analítica** | **Materiales acumulados**: KPIs + donut por clase con leyenda interactiva. **Actividad**: barras apiladas por día/clase, 7/14/30 días, tooltips. **Rendimiento de inferencia**: promedio/p95/mín/máx de ms ONNX sobre las últimas 60 capturas |
| **05 · Archivo** | **Galería**: filtro por clase + Exportar YOLO (ZIP con imágenes + labels + `data.yaml`) + paginación + lightbox con detecciones |
| **06 · Administración** | **Config remota** del ESP32-CAM: Red y servidor / Parámetros y sensores, con estado de entrega de la config (guardada → entregada → aplicada) y fecha de última actualización. **Diagnóstico del servidor**: latencia de red, último contacto, polls/h, uptime, clientes WS. **Mantenimiento**: Reconectar ESP32, vaciar cola de fotos, descarga de respaldo ZIP (`GET /api/export/backup`), programación OTA, y zona de peligro con confirmación modal |

El **topbar sticky** muestra el título/subtítulo de la vista activa, el **toggle de tema claro/oscuro** (switch tipo píldora; persiste en `localStorage` y respeta `prefers-color-scheme`), pill del dispositivo (online/offline por heartbeat), pill WebSocket y último ping relativo. El botón **Exportar YOLO** descarga un ZIP con imágenes + labels YOLO + `data.yaml` para reentrenar.

### Ciclo de vida de las órdenes

Toda orden hacia el ESP32 (`pump_cmd`, `capture_cmd`, `reconnect_cmd`, `queue_clear`) se registra en la tabla `device_commands` y la UI la muestra con su estado real:

`en cola` → `entregada` (servida en un poll de `/api/device/config`) → `ejecutada` (ACK del dispositivo) — o `expirada` (TTL 10 min sin entrega) / `cancelada` (desde el dashboard, solo si aún no fue entregada).

- El endpoint `GET /api/device/commands` alimenta el panel; se refresca cada 10 s y por WebSocket (`type: "orders"`).
- `POST /api/device/commands/{id}/cancel` descarta órdenes aún no entregadas.
- Órdenes repetidas del mismo tipo devuelven **409** (no se encolan duplicados); una orden nueva del mismo tipo cancela la anterior no entregada.
- La telemetría del ESP32 (`pump_on`, `pump_rest_s`, `rele_pin`) corrige el countdown de la bomba entre polls.

### Confirmaciones destructivas

"Vaciar álbum", "Borrar todos los datos" y "Vaciar cola del ESP32" ya no usan `confirm()` nativo: abren un **modal contextual** que lista el alcance exacto de lo que se pierde. "Borrar todos los datos" exige **escribir la frase `BORRAR TODO`** para habilitar el botón. Antes de borrar conviene usar **Descargar respaldo** (`/api/export/backup` → ZIP con snapshot JSON + todas las fotos).

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

- `static/index.html` — estructura: sidebar, topbar con toggle de tema, **6 vistas** (`view-overview`, `view-operation`, `view-sensors`, `view-analytics`, `view-archive`, `view-admin`), bottom-nav móvil, lightbox y modal de confirmación
- `static/styles.css` — design system con **tokens por tema** (`:root` oscuro + `[data-theme="light"]`): superficies, líneas, textos, acentos y colores de gráficos todos son variables; Space Grotesk + JetBrains Mono auto-hospedadas en `static/fonts/`, responsive móvil-first
- `static/app.js` — WS, canvas donut/gráfica de actividad con tooltips (colores leídos de las variables CSS al repintar), galería, lightbox, bomba manual (chips + barra segmentada + bloqueo anti-doble), panel de órdenes, estado general con panel de atención, telemetría extendida, config remota con estado de entrega, OTA, modal de confirmación destructiva, toggle de tema

**Tema claro/oscuro:** el switch del topbar conmuta `data-theme` en `<html>` y lo guarda en `localStorage` (`reba-theme`); un mini-script en `<head>` aplica el tema guardado antes de pintar (sin flash). Los canvas se repintan leyendo `--chart-*` del tema activo.

**Caché:** el servidor envía `Cache-Control: no-cache` en `/`, `.html`, `.css` y `.js` (middleware en `server/main.py`) — el navegador revalida en cada carga, así las actualizaciones se propagan por el túnel sin recarga dura. Los assets llevan además `?v=<fecha>` en el HTML como rompe-caché extra.

---

## Interpretar el panel Bomba

- El ciclo **automático** es autónomo en la ESP32: cada `pump_photos` fotos corre `pump_seconds`.
- El servidor **infiere** el progreso contando uploads recibidos; si la cola del ESP32 retiene fotos sin Wi-Fi, el conteo mostrado se atrasa.
- La **bomba manual** encola `pump_cmd=<unix_ts>:<s>` que el ESP32 recoge en su poll de config (≤60 s), ejecuta no-bloqueante y confirma por `/api/device/ack`. El comando expira a los 10 min — si el dispositivo estuvo offline, no hay bomba fantasma.
- El estado del relé se deriva de la telemetría real (`pump_on`, `pump_rest_s`, `rele_pin`), no solo del comando encolado.
- `pump_events` mezcla ciclos inferidos y manuales; el panel **Órdenes al dispositivo** muestra el ciclo de vida completo de cada orden.

---

## Requisitos de red

- Abrir por HTTP del servidor (IP de la PC), no por `file://`.
- La PC debe ser alcanzable por el ESP32 (`server_url` del firmware → mismo Wi-Fi 2.4 GHz).
- La red `REBA-AP` (hotspot Windows de la PC) no requiere Internet: PC y ESP32 se comunican por LAN.
- Windows Firewall debe permitir entrada TCP 5000.
- El dashboard y el ESP32 pueden usar un punto de acceso sin Internet; el modelo y la base de datos residen en la PC.

### Acceso remoto — dominio `rebaserver.tech` (principal) + zrok (respaldo)

Para operar desde una red distinta a la LAN (datos móviles, otro sitio):

- **`https://rebaserver.tech`** — túnel Cloudflare nombrado de dirección fija (autostart vía `reba-cloudflared.vbs` → `tools/run_cloudflared.bat`, `--protocol http2`). HTTPS + `wss://` automáticos. Es la URL principal y **también la usa la ESP32** por `http://rebaserver.tech` cuando está fuera de la LAN.
- **`https://reba-cam.shares.zrok.io`** — respaldo zrok (reservado, URL fija; se levanta con `campo.bat`/`retomar.bat` → `tools/zrok_tunnel.ps1`, log en `logs/tunnel.log`).

Detalles del failover ESP32 y del túnel: `docs/plan_tunel_fijo.md`, `docs/server.md` §Acceso remoto.

---

## Personalización rápida

| Qué | Dónde |
|-----|-------|
| Colores de clases | `CLASS_COLORS` en `app.js` + variables CSS |
| Fotos por ciclo / duración | `.env` (informativo) + config remota `pump_photos`/`pump_seconds` del dispositivo |
| Texto / layout / tema | `index.html` / `styles.css` |
| Rango de días de la gráfica | selector en el panel Actividad (7/14/30) |

El dashboard usa Space Grotesk (interfaz) y JetBrains Mono (datos técnicos) servidas como woff2 locales desde `static/fonts/` — no descarga nada de Internet y funciona sin conexión externa.
