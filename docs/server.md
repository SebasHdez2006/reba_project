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

Estado del motor ONNX, contadores (`uploads_received`, `photos_since_pump`) y **`device`**: `{online, last_seen, ip}` — heartbeat del ESP32 (<150 s = online; se actualiza en cada poll de config y cada upload).

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

Encola un ciclo de bomba **manual** para el ESP32. Body `{"seconds": 10}` (1-300). El comando viaja como `pump_cmd=<unix_ts>:<s>` en el próximo `GET /api/device/config` del dispositivo (≤60 s), expira a los 10 min y se borra cuando el ESP32 hace `POST /api/device/ack`. Respuesta incluye `cmd_id` y `event_id`.

### `GET /api/pump/events?limit=50`

Igual que `/api/pump-events` + `pending_cmd` (la orden encolada sin confirmar, o `null`).

### `POST /api/device/ack`

Confirmación del ESP32 tras ejecutar `pump_cmd`. Body `{"cmd_id": <unix_ts>}`. Si coincide con el pendiente, borra el override → `{"ok": true, "cleared": true}`.

### `GET /api/device/config`

Texto plano `clave=valor` que el ESP32 descarga cada 60 s (heartbeat incluido). Solo overrides guardados; `pump_cmd` se sirve únicamente si tiene <10 min.

### `GET/POST /api/admin/device-config`

GET: overrides actuales + claves permitidas. POST: body JSON `{clave: valor}` — valor vacío/null borra el override. Claves: `wifi_ssid`, `wifi_pass`, `server_url`, `pump_photos`, `pump_seconds`, `dist_min_cm`, `dist_max_cm`, `pump_cmd`. El firmware re-valida todo al aplicarlo.

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
| Inferencia `source: none` | Coloca `models/best.onnx` |
| `400` en /upload | Body no es JPEG decodificable |
| Dashboard sin WS | Abre por `localhost:5000` (no file://) |
| Puerto ocupado | Cambia `PORT` en `.env` **y** `SERVIDOR_URL` en el firmware (reflasheo) |
