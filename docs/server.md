# REBA — Servidor PC (FastAPI)

El servidor corre en la misma red Wi-Fi que el ESP32-CAM. Recibe las fotos que el nodo sube por `POST /upload`, las clasifica con ONNX local, persiste en SQLite y actualiza el dashboard por WebSocket.

---

## Instalación

```bash
cd REBA
python -m venv .venv

# Windows PowerShell
.venv\Scripts\Activate.ps1

# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env   # Windows
# cp .env.example .env   # Unix
```

Edita `.env` si necesitas cambiar el puerto o umbrales.

### Arranque

```bash
uvicorn server.main:app --host 0.0.0.0 --port 5000
```

O:

```bash
python -m server.main
```

- Dashboard: http://localhost:5000
- OpenAPI: http://localhost:5000/docs

> El puerto **debe coincidir** con `SERVIDOR_URL` del firmware (`http://<IP_PC>:<PORT>/upload`).

### Acceso remoto (túnel Cloudflare `rebaserver.tech`)

El acceso remoto definitivo es un **Cloudflare named tunnel** sobre el
dominio propio `rebaserver.tech` (.tech gratis del GitHub Student Pack):

- **`https://rebaserver.tech`** → dashboard remoto (HTTPS + `wss://`
  automáticos) — uso principal.
- **`http://rebaserver.tech`** → HTTP plano puerto 80 → **lo usa la
  ESP32** como `server_url_alt` (failover automático del firmware).
- `cloudflared` corre oculto vía `reba-cloudflared.vbs` en Startup →
  `tools/run_cloudflared.bat` (`--protocol http2` — QUIC/UDP hace
  timeout en la red institucional). Log: `logs/cloudflared.log`.
  Reconecta solo; config: `tools/cloudflared_config.yml`, credenciales
  en `~/.cloudflared/` (no commitear).
- Verificación: `http://rebaserver.tech/api/health` y
  `https://rebaserver.tech/api/health` → 200.

**Respaldo — túnel zrok** (`tools/zrok_tunnel.ps1`, invocado por
`campo.bat`/`retomar.bat`): share público `https://reba-cam.shares.zrok.io`
(reservado, URL fija). Solo HTTPS → dashboard/simulador, **no la ESP32**.

```bat
setup_zrok.bat   :: una sola vez: cuenta + reserva de nombre
campo.bat        :: levanta uvicorn + tunel zrok (ventana REBA-Tunnel)
```

- Con `ZROK_RESERVED_NAME` en `.env` → share **reservado**: URL fija
  `https://reba-cam.shares.zrok.io`. Sin reserva → share **efímero**
  (`logs/tunnel.log`, `logs/zrok_url.txt`), fallback automático.
- Detecta `zrok` (v1) o `zrok2` (v2) en el PATH.
- HTTPS + `wss://` automáticos; uvicorn corre con
  `--proxy-headers --forwarded-allow-ips=*` para aceptar los headers del
  proxy (IP real del cliente en `device.ip`).
- **Ojo**: `device.online`/`device.ip` en `/api/health` reflejan al
  **último cliente que consultó `/api/device/config`** — el simulador web
  (`/simulador_esp32.html`) usa los mismos endpoints, así que su polling
  también "enciende" el heartbeat. Un `EN LÍNEA` puede ser el simulador,
  no necesariamente la ESP32 física.
- **La ESP32 usa el túnel Cloudflare** (`http://rebaserver.tech` como
  `server_url_alt`) o la LAN directa — **no zrok** (HTTPS-only).
- Sin auth → tratar las URLs como secretas. zrok free tier: 5 GB/día.
- *Interstitial page* zrok: ~1 vez/semana solo a navegadores.
- Detalles y alternativas evaluadas: `docs/manual_uso.md` §1,
  `docs/componentes.md` §Acceso, `docs/plan_tunel_fijo.md`.

---

## Variables de entorno (`.env`)

| Variable | Default | Descripción |
|----------|---------|-------------|
| `HOST` / `PORT` | `0.0.0.0` / `5000` | Bind HTTP — `PORT` = puerto de `SERVIDOR_URL` |
| `MODEL_PATH` | `models/best.onnx` | Modelo ONNX |
| `INFERENCE_CONFIDENCE` | `0.35` | Umbral conf. (afinado por `tune_confidence.py` = máx F1) |
| `INFERENCE_IOU` | `0.45` | NMS IoU |
| `CLASS_NAMES` | `plastico,unicel,metal` | Orden = class_id |
| `PUMP_TRIGGER_PHOTOS` | `3` | Fotos por ciclo de bomba (**informativo**: = `pump_photos` del dispositivo) |
| `PUMP_RUN_SECONDS` | `10` | Duración del ciclo (**informativo**: = `pump_seconds` del dispositivo) |
| `DETECT_MIN_CM` / `DETECT_MAX_CM` | `0` / `23` | Rango ultrasónico del firmware (display) |
| `CAPTURE_RETENTION` | `2000` | Máx. capturas en SQLite; poda horaria de las más viejas (`0` = sin poda) |
| `DATABASE_PATH` | `data/REBA.db` | SQLite |
| `ZROK_RESERVED_NAME` | *(vacío)* | Nombre fijo del share zrok (`https://<nombre>.shares.zrok.io`); lo escribe `setup_zrok.bat`. Vacío = share efímero |
| `ZROK_TARGET_URL` | `http://127.0.0.1:5000` | Objetivo local del túnel (informativo para `tools/zrok_tunnel.ps1`) |

### Firmware OTA

- `GET /firmware.bin` → sirve `firmware/fotos_nc.bin` (imagen de la app
  para la partición OTA; 404 si no está compilada).
- `GET /firmware/*` → mount estático del directorio `firmware/` con los
  artefactos de flasheo USB (`bootloader.bin`, `partitions.bin`,
  `fotos_nc.bin`) — descargables por HTTP plano para provisioning.
- Clave remota `ota_url` (en `DEVICE_CONFIG_KEYS`): el ESP32 la recoge en
  `/api/device/config` y auto-actualiza (ver `docs/firmware.md` → OTA).

La carga se hace en `server/config.py` (Pydantic Settings). Tras editar `.env`, reinicia uvicorn.

---

## Módulos

| Archivo | Responsabilidad |
|---------|-----------------|
| `config.py` | Settings |
| `inference.py` | ONNX YOLOv8 local |
| `database.py` | SQLite: captures, detections, counters, pump_events, device_config |
| `main.py` | FastAPI, `POST /upload`, REST, WebSocket, static, device config, pump_cmd, export |

---

## Endpoint del ESP32

### `POST /upload`

Recibe la foto JPEG del ESP32-CAM:

```http
POST /upload
Content-Type: image/jpeg
X-Evento: ingreso        (opcional)

<JPEG binario>
```

Respuesta `200`:

```json
{
  "ok": true,
  "capture_id": 7,
  "detections": [{"class_name": "plastico", "confidence": 0.81, "x1": 120.0, "..." : "..."}],
  "inference_ms": 42.5,
  "photos_toward_pump": 2,
  "pump_cycle_triggered": false
}
```

- **2xx** → el ESP32 saca la foto de su cola. Cualquier error → reintento en ~15 s.
- Validación previa al decode: body vacío → 400, magic JPEG inválido (`FF D8 FF`) → 400, >2 MB → 413, JPEG no decodificable → 400.
- Cada upload cuenta como **heartbeat** del dispositivo (`device.online` en `/api/health` y `/api/stats`, <150 s).
- Se guardan DOS JPEG por captura: `image_jpeg` **anotado** (cajas, para el dashboard) e `image_orig_jpeg` **crudo** del ESP32 (para exportar dataset limpio).
- Fotos sin detecciones YOLO se guardan igual (`detections: []`) — sirven como dataset real para reentrenar.
- Prueba manual:

```bash
curl -X POST http://localhost:5000/upload -H "Content-Type: image/jpeg" -H "X-Evento: ingreso" --data-binary @foto.jpg
```

---

## API REST

> En producción, el servidor no consulta servicios remotos para inferencia; usa únicamente `models/best.onnx`.

### `GET /api/health`

Estado del motor ONNX, contadores (`uploads_received`, `photos_since_pump`) y **`device`**: `{online, last_seen, ip, polls_total, polls_last_hour, last_poll_ts}` — heartbeat del ESP32 (<150 s = online; se actualiza en cada poll de config y cada upload). Los campos `polls_*` cuentan los `GET /api/device/config` recibidos del dispositivo (ventana deslizante de 1 h ≈ 60 polls/h esperados con `POLL_CONFIG_MS=60 s`).

### `GET /api/config`

Config **pública**: clases, umbrales de inferencia, constantes de bomba, rango ultrasónico.

### `GET /api/stats`

```json
{
  "counters": {"plastico": 12, "unicel": 3, "...": 0},
  "percentages": {"plastico": 60.0, "...": 0},
  "total_objects": 20
}
```

### `GET /api/pump-status`

Progreso inferido hacia el próximo ciclo de bomba: `photos_since_pump`, `pump_trigger_photos`, `uploads_received`, `last_upload_ts`, `last_pump_event`.

### `GET /api/pump-events?limit=50`

Historial de ciclos de bomba (inferidos por fotos y **manuales** del dashboard).

### `POST /api/pump/run`

Encola un ciclo de bomba **manual** para el ESP32. Body `{"seconds": 10}` (1-300). El comando viaja como `pump_cmd=<unix_ts>:<s>` en el próximo `GET /api/device/config` del dispositivo (≤60 s), expira a los 10 min y se borra cuando el ESP32 hace `POST /api/device/ack`. Respuesta incluye `cmd_id`, `event_id` y `device` (estado online/last_seen/ip del ESP32, para que el dashboard avise si está sin señal).

### `GET /api/pump/events?limit=50`

Igual que `/api/pump-events` + `pending_cmd` (la orden encolada vigente — `null` si expiró o ya fue confirmada).

### `POST /api/capture/run`

Encola una **foto remota** para el ESP32 — el botón "Tomar foto ahora" del dashboard (equivalente al `CAPTURE` serie). El comando viaja como `capture_cmd=<unix_ts>` en el próximo poll de config (≤60 s), expira a los 10 min y se borra con el `POST /api/device/ack`. La foto llega por `POST /upload` con detección ONNX y broadcast WS.

### `POST /api/device/reconnect`

Encola una **solicitud de reconexión** para el ESP32 — el botón "Reconectar ESP32" del dashboard. La orden viaja como `reconnect_cmd=<unix_ts>` en el próximo poll de config (≤60 s), expira a los 10 min y se borra con el `POST /api/device/ack`. Al recibirla, el firmware hace `WiFi.disconnect()` + un poll de config inmediato (acelera el failover `server_url`↔`server_url_alt` y cura enlaces WiFi "zombie").

**Limitación de la arquitectura push**: el servidor no puede iniciar la conexión — si el ESP32 está realmente offline (no alcanza el servidor), la orden no puede entregarse y expira; el dispositivo debe volver a pollar por sus propios reintentos (`REINTENTO_WIFI_MS`, failover, reinicio a los 30 min sin config).

### `POST /api/device/ack`

Confirmación del ESP32 tras ejecutar `pump_cmd`, `capture_cmd` o `reconnect_cmd`. Body `{"cmd_id": <unix_ts>}`. Si coincide con el pendiente, borra el override → `{"ok": true, "cleared": true}`.

### `GET /api/device/config`

Texto plano `clave=valor` que el ESP32 descarga cada 60 s (heartbeat incluido). Solo overrides guardados; `pump_cmd`, `capture_cmd` y `reconnect_cmd` se sirven únicamente si tienen <10 min. Cada poll del dispositivo (`User-Agent: REBA-ESP32`) incrementa el contador de polls (`device.polls_total`/`polls_last_hour`).

### `GET/POST /api/admin/device-config`

GET: overrides actuales + claves permitidas. POST: body JSON `{clave: valor}` — valor vacío/null borra el override. Claves: `wifi_ssid`, `wifi_pass`, `server_url`, `server_url_alt`, `pump_photos`, `pump_seconds`, `dist_min_cm`, `dist_max_cm`, `pump_cmd`, `ota_url`, `queue_clear`, `capture_cmd`, `reconnect_cmd`. El firmware re-valida todo al aplicarlo.

### Vista en vivo

El stream MJPEG (`:8080` en la placa + proxy `/api/device/stream` y `/snapshot`) se **retiró** por térmica — la ESP32-CAM se calienta mucho sirviendo frames continuos. La pestaña "En vivo" ahora solo muestra la última captura + eventos WS.

### `GET /api/stats/timeseries?days=14`

Serie temporal para la gráfica del dashboard: `{days, series: [{date, detections: {clase: n}, captures, pump_events}], class_names}`.

### `GET /api/export/yolo?min_conf=0&limit=500`

Descarga `reba_dataset_yolo.zip`: `images/*.jpg` (JPEG **original** del ESP32, sin cajas), `labels/*.txt` en formato YOLO (clase cx cy w h normalizado, desde `detections`), `data.yaml` con las 3 clases. `min_conf` filtra detecciones débiles; capturas sin detección quedan como background.

### `GET /api/detections/latest`

Última inferencia (clases, cajas, ms).

### `GET /api/captures?limit=20&offset=0`

Historial de fotos clasificadas (paginado). Devuelve metadatos y total; no incluye los JPEG en el JSON.

### `GET /api/captures/latest`

Metadatos de la última foto clasificada, o `null` si todavía no hay capturas.

### `GET /api/captures/{id}/image?orig=0`

JPEG de la captura: por defecto el **anotado** (cajas, para el dashboard); `?orig=1` devuelve el JPEG crudo del ESP32 (fallback al anotado en capturas antiguas sin `image_orig_jpeg`).

### `GET /api/captures/{id}/detections`

Detecciones detalladas de una captura (clase, confianza, caja xyxy) — alimenta el lightbox del dashboard. 404 si el ID no existe.

### `GET /api/detections/history?limit=100`

Historial SQLite.

---

## WebSocket `/ws`

### Mensaje inicial (`hello`)

```json
{
  "type": "hello",
  "payload": {
    "config": { "...": "..." },
    "stats": { "...": "..." },
    "detections": { "...": "..." },
    "latest_capture": { "...": "..." },
    "pump": { "photos_since_pump": 1, "pump_trigger_photos": 3 }
  }
}
```

### Eventos push

| `type` | Contenido |
|--------|-----------|
| `capture` | Metadatos de una nueva foto JPEG clasificada |
| `detections` | Detecciones + `stats` + `pump` (progreso) |
| `pump_event` | Se completó el conteo de fotos → ciclo de bomba inferido |
| `pong` | Respuesta a `{"type":"ping"}` |

### Comandos del cliente

```json
{"type": "ping"}
```

---

## Flujo de procesamiento

```
ESP32: objeto en rango → foto con flash → POST /upload
        │
        ▼
PC: decodifica JPEG → ONNX YOLOv8 → cajas dibujadas
        │
        ▼
SQLite: captures + detections + counters
        │
        ├─ cada N fotos → pump_events + broadcast pump_event
        ▼
WebSocket → dashboard (foto, contadores, progreso bomba)
```

El servidor **infiere** los ciclos automáticos de bomba contando uploads; los ciclos **manuales** los ordena desde el dashboard y el ESP32 los ejecuta/ack. El conteo real del firmware puede adelantarse si la cola retiene fotos sin Wi-Fi.

---

## Persistencia SQLite

Archivo: `data/REBA.db` (creado al arrancar, WAL, FK ON).

| Tabla | Uso |
|-------|-----|
| `captures` | Dos JPEG por foto: `image_jpeg` anotado + `image_orig_jpeg` crudo |
| `detections` | Clase, confianza, caja y vínculo a la captura (`capture_id`, SET NULL al purgar) |
| `counters` | Acumulado por clase |
| `pump_events` | Ciclos de bomba inferidos **y manuales** |
| `device_config` | Overrides servidor→ESP32 + `pump_cmd` pendiente |

La retención (`CAPTURE_RETENTION`, default 2000) poda las capturas más viejas cada hora; las detecciones y eventos sobreviven.

---

## Troubleshooting servidor

| Síntoma | Acción |
|---------|--------|
| ESP32 reintenta siempre | Firewall de Windows: permite el puerto 5000; verifica IP en `SERVIDOR_URL` |
| Inferencia `source: none` | Coloca `models/best.onnx` válido para `CLASS_NAMES` (el engine rechaza ONNX con nº de clases distinto) |
| `400` en /upload | Body no es JPEG decodificable |
| Dashboard sin WS | Abre por `localhost:5000` (no file://) |
| Puerto ocupado | Cambia `PORT` en `.env` **y** `SERVIDOR_URL` en el firmware (reflasheo o config remota) |
| ESP32 inalcanzable tras reiniciar PC | Verificar IP LAN / `server_url` del ESP32 (el túnel zrok no aplica: es solo HTTPS para el dashboard). Si es remota: revisar agente **Portwarp** (`93togsum.free.tryportwarp.cc:16404`, app Store — alerta "conexión inestable" = nodo HK + PC en WiFi) |
| Dashboard no carga por la URL zrok | Comprobar que `REBA-Tunnel` corre y `logs/zrok_url.txt` tiene la URL; share efímero → URL nueva en cada arranque del túnel |
| `device.online=false` con túnel activo | El ESP32 perdió el server_url viejo o no tiene datos/senal — el ESP32 va por LAN/hotspot, o por Portwarp si es remota (ver `docs/plan_tunel_fijo.md`) |
