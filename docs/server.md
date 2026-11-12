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
| `INFERENCE_CONFIDENCE` | `0.45` | Umbral conf. |
| `INFERENCE_IOU` | `0.45` | NMS IoU |
| `CLASS_NAMES` | `plastico,tela,...` | Orden = class_id |
| `PUMP_TRIGGER_PHOTOS` | `3` | Fotos por ciclo de bomba (**informativo**: = `FOTOS_PARA_BOMBA` del firmware) |
| `PUMP_RUN_SECONDS` | `10` | Duración del ciclo (**informativo**: = `TIEMPO_BOMBA_MS/1000`) |
| `DETECT_MIN_CM` / `DETECT_MAX_CM` | `0` / `23` | Rango ultrasónico del firmware (display) |
| `DATABASE_PATH` | `data/REBA.db` | SQLite |

La carga se hace en `server/config.py` (Pydantic Settings). Tras editar `.env`, reinicia uvicorn.

---

## Módulos

| Archivo | Responsabilidad |
|---------|-----------------|
| `config.py` | Settings |
| `inference.py` | ONNX YOLOv8 local |
| `database.py` | SQLite: captures, detections, counters, pump_events |
| `main.py` | FastAPI, `POST /upload`, REST, WebSocket, static |

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
- Fotos sin detecciones YOLO se guardan igual (`detections: []`) — sirven como dataset real para reentrenar.
- Prueba manual:

```bash
curl -X POST http://localhost:5000/upload -H "Content-Type: image/jpeg" -H "X-Evento: ingreso" --data-binary @foto.jpg
```

---

## API REST

> En producción, el servidor no consulta servicios remotos para inferencia; usa únicamente `models/best.onnx`.

### `GET /api/health`

Estado del motor ONNX y contadores (`uploads_received`, `photos_since_pump`).

### `GET /api/config`

Config **pública**: clases, umbrales de inferencia, constantes de bomba, rango ultrasónico.

### `GET /api/stats`

```json
{
  "counters": {"plastico": 12, "tela": 3, "...": 0},
  "percentages": {"plastico": 60.0, "...": 0},
  "total_objects": 20
}
```

### `GET /api/pump-status`

Progreso inferido hacia el próximo ciclo de bomba: `photos_since_pump`, `pump_trigger_photos`, `uploads_received`, `last_upload_ts`, `last_pump_event`.

### `GET /api/pump-events?limit=50`

Historial de ciclos de bomba inferidos (cada N fotos recibidas).

### `GET /api/detections/latest`

Última inferencia (clases, cajas, ms).

### `GET /api/captures?limit=20`

Historial de fotos clasificadas. Devuelve metadatos y total; no incluye los JPEG en el JSON.

### `GET /api/captures/latest`

Metadatos de la última foto clasificada, o `null` si todavía no hay capturas.

### `GET /api/captures/{id}/image`

JPEG anotado asociado a esa captura. Devuelve 404 si el ID no existe.

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

El servidor **infiere** los ciclos de bomba contando uploads recibidos: el conteo real ocurre en la ESP32 y puede adelantarse si la cola retiene fotos sin Wi-Fi.

---

## Persistencia SQLite

Archivo: `data/REBA.db` (creado al arrancar).

| Tabla | Uso |
|-------|-----|
| `captures` | JPEG anotado de cada foto recibida |
| `detections` | Clase, confianza, caja y vínculo a la foto |
| `counters` | Acumulado por clase |
| `pump_events` | Ciclos de bomba inferidos |

---

## Troubleshooting servidor

| Síntoma | Acción |
|---------|--------|
| ESP32 reintenta siempre | Firewall de Windows: permite el puerto 5000; verifica IP en `SERVIDOR_URL` |
| Inferencia `source: none` | Coloca `models/best.onnx` |
| `400` en /upload | Body no es JPEG decodificable |
| Dashboard sin WS | Abre por `localhost:5000` (no file://) |
| Puerto ocupado | Cambia `PORT` en `.env` **y** `SERVIDOR_URL` en el firmware (reflasheo) |
