"""
Servidor principal REBA (FastAPI) — modo "push".

La ESP32-CAM ya no actúa como servidor de video: detecta el ingreso de un
objeto con el ultrasónico (0-23 cm), toma una foto con flash y la sube por
HTTP POST a `/upload`. Este servidor:

  - Recibe el JPEG, corre inferencia YOLO/ONNX local y dibuja las cajas.
  - Guarda foto anotada + detecciones en SQLite.
  - Infiere los ciclos de bomba: cada `PUMP_TRIGGER_PHOTOS` fotos recibidas
    el firmware corre la bomba durante `PUMP_RUN_SECONDS` segundos.
  - Emite todo en tiempo real al dashboard por WebSocket `/ws`.

Ejecutar desde la raíz del proyecto:
    uvicorn server.main:app --host 0.0.0.0 --port 5000

Documentación: docs/server.md
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
import zipfile
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any, Dict, List, Set

import cv2

import numpy as np
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from server.config import PROJECT_ROOT, settings
from server.database import Database
from server.inference import InferenceEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("REBA.main")

# ---------------------------------------------------------------------------
# Estado global de la aplicación
# ---------------------------------------------------------------------------

db = Database(settings.database_path_abs)
engine = InferenceEngine(settings)
ws_clients: Set[WebSocket] = set()

latest_capture: Dict[str, Any] | None = None
latest_inference: Dict[str, Any] = {
    "detections": [],
    "source": "none",
    "inference_ms": 0,
    "count": 0,
    "error": None,
}

# Conteo inferido del ciclo de bomba (el conteo real vive en la ESP32;
# aquí solo contamos fotos efectivamente recibidas).
pump_state: Dict[str, Any] = {
    "photos_since_pump": 0,
    "uploads_received": 0,
    "last_upload_ts": None,
    "last_pump_event": None,
}

# Último contacto del ESP32-CAM. Se actualiza en cada GET /api/device/config
# (poll cada ~60 s) y en cada POST /upload — sirve de heartbeat para saber
# si el dispositivo está en línea sin depender de que tome fotos.
import time as _time

device_state: Dict[str, Any] = {
    "last_seen": None,   # epoch seconds
    "ip": None,
}
DEVICE_ONLINE_WINDOW_S = 150.0   # >2 polls perdidos = offline
SERVER_START_TS = _time.time()  # uptime del proceso (métricas /api/perf)


def _touch_device(request: Request) -> None:
    """Registra que el ESP32 contactó al servidor (heartbeat)."""
    device_state["last_seen"] = _time.time()
    try:
        device_state["ip"] = request.client.host if request.client else None
    except Exception:
        pass


def device_status() -> Dict[str, Any]:
    """Estado del dispositivo para /api/health y /api/stats."""
    last = device_state.get("last_seen")
    online = last is not None and (_time.time() - last) < DEVICE_ONLINE_WINDOW_S
    return {
        "online": online,
        "last_seen": last,
        "ip": device_state.get("ip"),
    }


async def broadcast(message: Dict[str, Any]) -> None:
    """Envía un mensaje JSON a todos los clientes WebSocket conectados."""
    dead: List[WebSocket] = []
    for ws in list(ws_clients):
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        ws_clients.discard(ws)


def pump_progress() -> Dict[str, Any]:
    """Progreso inferido hacia el próximo ciclo de bomba."""
    return {
        "photos_since_pump": pump_state["photos_since_pump"],
        "pump_trigger_photos": settings.pump_trigger_photos,
        "pump_run_seconds": settings.pump_run_seconds,
        "uploads_received": pump_state["uploads_received"],
        "last_upload_ts": pump_state["last_upload_ts"],
        "last_pump_event": pump_state["last_pump_event"],
    }


# ---------------------------------------------------------------------------
# Procesamiento de la foto recibida
# ---------------------------------------------------------------------------

def _scale_detections(detections, source_shape, target_shape):
    """Ajusta las cajas del frame normalizado a la resolución de la foto JPEG."""
    scale_x = target_shape[1] / source_shape[1]
    scale_y = target_shape[0] / source_shape[0]
    return [
        replace(
            detection,
            x1=detection.x1 * scale_x,
            y1=detection.y1 * scale_y,
            x2=detection.x2 * scale_x,
            y2=detection.y2 * scale_y,
        )
        for detection in detections
    ]


def _draw_boxes(frame: np.ndarray, detections) -> np.ndarray:
    """Dibuja cajas y etiquetas sobre una copia del frame."""
    out = frame.copy()
    colors = {
        "plastico": (60, 180, 255),
        "unicel": (100, 220, 220),
        "metal": (200, 200, 80),
    }
    for det in detections:
        color = colors.get(det.class_name, (255, 255, 255))
        p1 = (int(det.x1), int(det.y1))
        p2 = (int(det.x2), int(det.y2))
        cv2.rectangle(out, p1, p2, color, 2)
        label = f"{det.class_name} {det.confidence:.2f}"
        cv2.putText(
            out,
            label,
            (p1[0], max(16, p1[1] - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            1,
            cv2.LINE_AA,
        )
    return out


def _normalize(frame_bgr: np.ndarray, size: tuple[int, int] = (640, 640)) -> np.ndarray:
    """Redimensiona al tamaño de entrada del modelo."""
    return cv2.resize(frame_bgr, size, interpolation=cv2.INTER_LINEAR)


async def process_upload(body: bytes, evento: str | None) -> Dict[str, Any]:
    """
    Decodifica el JPEG del ESP32, corre ONNX, persiste y emite al dashboard.

    La ESP32 solo necesita un 2xx para sacar la foto de su cola; cualquier
    error aquí debe reflejarse en el status HTTP para que reintente.
    """
    global latest_capture

    frame = cv2.imdecode(np.frombuffer(body, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise HTTPException(400, "Body no es un JPEG decodificable")

    model_frame = _normalize(frame)
    result = engine.predict(model_frame)
    photo_detections = _scale_detections(
        result.detections, model_frame.shape, frame.shape
    )

    annotated = _draw_boxes(frame, photo_detections)
    ok, buf = cv2.imencode(
        ".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 88]
    )
    if not ok:
        raise HTTPException(500, "JPEG encode failed")

    latest_capture = db.insert_capture(
        buf.tobytes(),
        [d.to_dict() for d in photo_detections],
        inference_ms=result.inference_ms,
        source=result.source,
        image_orig_jpeg=body,   # JPEG crudo del ESP32 p/exportar YOLO
    )

    payload = result.to_dict()
    payload["detections"] = [d.to_dict() for d in photo_detections]
    latest_inference.update(payload)

    # --- Ciclo de bomba inferido ---
    pump_state["uploads_received"] += 1
    pump_state["photos_since_pump"] += 1
    pump_state["last_upload_ts"] = latest_capture["ts"]

    pump_cycle_triggered = False
    if pump_state["photos_since_pump"] >= settings.pump_trigger_photos:
        pump_cycle_triggered = True
        pump_state["photos_since_pump"] = 0
        event = db.insert_pump_event(
            settings.pump_trigger_photos,
            detail=(
                f"Bomba inferida ON ~{settings.pump_run_seconds:.0f} s "
                f"tras {settings.pump_trigger_photos} fotos recibidas"
            ),
        )
        pump_state["last_pump_event"] = event
        logger.info("Pump cycle inferred (event id=%s)", event["id"])

    progress = pump_progress()
    stats_now = db.get_stats()
    stats_now["device"] = device_status()
    await broadcast({"type": "capture", "payload": latest_capture})
    await broadcast(
        {
            "type": "detections",
            "payload": {
                **payload,
                "stats": stats_now,
                "latest_capture": latest_capture,
                "pump": progress,
            },
        }
    )
    if pump_cycle_triggered:
        await broadcast({"type": "pump_event", "payload": progress})

    logger.info(
        "Upload #%d (evento=%s): capture_id=%s, %d detecciones, %d/%d fotos p/bomba",
        pump_state["uploads_received"],
        evento or "-",
        latest_capture["id"],
        len(photo_detections),
        progress["photos_since_pump"],
        settings.pump_trigger_photos,
    )

    return {
        "ok": True,
        "capture_id": latest_capture["id"],
        "detections": payload["detections"],
        "inference_ms": payload["inference_ms"],
        "photos_toward_pump": settings.pump_trigger_photos
        if pump_cycle_triggered
        else progress["photos_since_pump"],
        "pump_cycle_triggered": pump_cycle_triggered,
    }


async def _retention_loop() -> None:
    """
    Poda periódica de capturas viejas (CAPTURE_RETENTION, default 2000).

    Corre cada hora; también una vez al arranque. keep=0 desactiva la poda.
    """
    keep = settings.capture_retention
    # Primera pasada al inicio (asyncio.sleep(0) cede el loop al lifespan)
    await asyncio.sleep(0)
    while True:
        if keep > 0:
            try:
                # to_thread: el DELETE puede tardar si la DB creció mucho;
                # no debe bloquear el event loop (uploads/WebSocket).
                removed = await asyncio.to_thread(db.prune_captures, keep)
                if removed:
                    logger.info("Retención: %d capturas antiguas eliminadas (keep=%d)", removed, keep)
            except Exception as exc:  # nunca tumbar el server por la poda
                logger.warning("Retención falló: %s", exc)
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global latest_capture
    logger.info("Starting REBA server…")
    db.connect()
    db.ensure_classes(settings.class_list)
    latest_capture = db.get_latest_capture()
    pump_state["last_pump_event"] = db.get_latest_pump_event()
    logger.info(
        "Dashboard: http://%s:%s  |  Upload endpoint: POST /upload",
        settings.host,
        settings.port,
    )
    retention_task = asyncio.create_task(_retention_loop())
    try:
        yield
    finally:
        retention_task.cancel()
        logger.info("Shutting down REBA…")
        db.close()


app = FastAPI(
    title="REBA",
    description="Plataforma flotante autónoma — recepción de fotos, visión y métricas",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def no_cache_dashboard(request: Request, call_next):
    """
    HTML/CSS/JS del dashboard con Cache-Control: no-cache.

    El navegador revalida en cada carga (304 si no cambió), lo que evita que
    el tunnel zrok sirva versiones viejas del dashboard por caché heurística.
    Las imágenes de capturas y /api/* no se tocan (los blobs JPEG son
    inmutables por id y sí conviene cachearlos).
    """
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith((".html", ".css", ".js")):
        response.headers["Cache-Control"] = "no-cache"
    return response


# ---------------------------------------------------------------------------
# Endpoint del ESP32 (la aplicación que se conecta)
# ---------------------------------------------------------------------------

@app.post("/upload")
async def upload_photo(request: Request):
    """
    Recibe la foto JPEG del ESP32-CAM.

    Contrato del firmware (fotos_nc.ino):
      POST /upload
      Content-Type: image/jpeg
      X-Evento: ingreso
      Body: JPEG binario

    Cualquier respuesta 2xx saca la foto de la cola del ESP32; un error hace
    que la foto permanezca en la cola y se reintente en ~15 s.

    Validaciones previas al decode (rechazo rápido, sin gastar CPU):
      - body vacío            -> 400
      - magic JPEG inválido   -> 400 (FF D8 FF)
      - tamaño fuera de rango -> 400/413
    """
    body = await request.body()
    _touch_device(request)

    if not body:
        raise HTTPException(400, "Body vacío")
    # JPEG SOI + APP0-ish: FF D8 FF. Rechazo rápido de basura/red corrupta.
    if len(body) < 128 or body[0] != 0xFF or body[1] != 0xD8 or body[2] != 0xFF:
        raise HTTPException(400, "Body no parece un JPEG (magic bytes)")
    if len(body) > 2 * 1024 * 1024:
        raise HTTPException(413, "JPEG demasiado grande (>2 MB)")

    evento = request.headers.get("X-Evento")
    return await process_upload(body, evento)


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    inference_status = engine.status()
    return {
        "ok": inference_status.get("backend") == "onnx",
        "inference": inference_status,
        "pump": pump_progress(),
        "device": device_status(),
    }


@app.get("/api/config")
async def get_config():
    """Config pública (parámetros editables vía `.env`). Sin secretos."""
    return settings.public_dict()


@app.get("/firmware.bin")
async def firmware_bin():
    """
    Binario OTA para la ESP32-CAM (fotos_nc).

    Lo consume el comando serie `OTA` del firmware y la clave remota
    `ota_url` (GET /api/device/config -> {srvBase}/firmware.bin).
    Se compila con partition scheme "Minimal SPIFFS (1.9MB APP with OTA)".
    """
    fw = PROJECT_ROOT / "firmware" / "fotos_nc.bin"
    if not fw.is_file():
        raise HTTPException(status_code=404, detail="Firmware no compilado aun")
    return FileResponse(fw, media_type="application/octet-stream",
                        filename="fotos_nc.bin")


# ---------------------------------------------------------------------------
# Configuración del dispositivo ESP32 (la consulta en su arranque y cada minuto)
# ---------------------------------------------------------------------------

# Comando de bomba manual: "<unix_ts>:<segundos>". Si el ESP32 estuvo
# offline más de esto, el comando expira y no se entrega (evita que una
# orden vieja encienda la bomba al reconectar).
PUMP_CMD_TTL_S = 600


def _pump_cmd_fresh(value: str) -> bool:
    try:
        ts = int(value.split(":", 1)[0])
        return (time.time() - ts) < PUMP_CMD_TTL_S
    except (ValueError, IndexError):
        return False


def _pending_pump_cmd() -> str | None:
    """
    pump_cmd vigente o None. Limpia perezosamente el override expirado:
    si el ESP32 nunca vuelve a consultar, la orden vieja no debe seguir
    reportándose como "en cola" en el dashboard.
    """
    cfg = db.get_device_config()
    cmd = cfg.get("pump_cmd")
    if cmd is not None and not _pump_cmd_fresh(cmd):
        db.delete_device_config("pump_cmd")
        return None
    return cmd


@app.get("/api/device/config", response_class=PlainTextResponse)
async def device_config(request: Request):
    """
    Config que el ESP32-CAM descarga periódicamente.

    Formato `clave=valor` por línea (sin JSON: el firmware lo parsea simple).
    Solo se devuelven las claves con override guardado — el firmware conserva
    sus valores locales para el resto. Cada poll cuenta como heartbeat del
    dispositivo (device_state).
    """
    _touch_device(request)
    cfg = db.get_device_config()
    for key in ("pump_cmd", "capture_cmd"):
        cmd = cfg.get(key)
        if cmd is not None and not _pump_cmd_fresh(cmd):
            db.delete_device_config(key)      # expirado: no entregarlo
            cfg.pop(key, None)
    return "\n".join(f"{k}={v}" for k, v in sorted(cfg.items())) + "\n"


@app.get("/api/admin/device-config")
async def admin_get_device_config():
    """Overrides actuales (los edita el dashboard)."""
    return {"items": db.get_device_config(),
            "allowed_keys": list(db.DEVICE_CONFIG_KEYS)}


@app.post("/api/admin/device-config")
async def admin_set_device_config(payload: Dict[str, Any]):
    """
    Guarda overrides de config del ESP32.

    Body JSON: {"wifi_ssid": "...", "pump_photos": "3", ...}
    Valor vacío o null elimina el override (vuelve al default del firmware).
    """
    items = payload.get("items", payload)
    if not isinstance(items, dict):
        raise HTTPException(400, "Se esperaba un objeto JSON de clave=valor")
    allowed = set(db.DEVICE_CONFIG_KEYS)
    invalid = [k for k in items if k not in allowed]
    if invalid:
        raise HTTPException(400, f"Claves no permitidas: {', '.join(invalid)}")
    applied, removed = [], []
    for key, value in items.items():
        if value is None or str(value).strip() == "":
            db.delete_device_config(key)
            removed.append(key)
        else:
            db.set_device_config(key, str(value).strip())
            applied.append(key)
    logger.info("device_config: aplicadas=%s eliminadas=%s", applied, removed)
    return {"ok": True, "applied": applied, "removed": removed}


@app.post("/api/device/ack")
async def device_ack(payload: Dict[str, Any]):
    """
    ACK del ESP32 tras ejecutar un comando de un solo uso (pump_cmd).

    El firmware hace POST aqui con {"cmd_id": <unix_ts>} despues de correr
    la bomba; el servidor borra el override para que no se re-entregue.
    """
    try:
        cmd_id = int(payload.get("cmd_id", -1))
    except (TypeError, ValueError):
        raise HTTPException(400, "cmd_id inválido")
    cfg = db.get_device_config()
    cleared = False
    pending = cfg.get("pump_cmd", "")
    if pending.startswith(f"{cmd_id}:"):
        db.delete_device_config("pump_cmd")
        cleared = True
    pending_cap = cfg.get("capture_cmd", "")
    if pending_cap == str(cmd_id):
        db.delete_device_config("capture_cmd")
        cleared = True
    if cleared:
        logger.info("cmd %s confirmado por el dispositivo", cmd_id)
        return {"ok": True, "cleared": True}
    return {"ok": True, "cleared": False}


# ---------------------------------------------------------------------------
# Bomba manual (comando servidor -> ESP32 via /api/device/config)
# ---------------------------------------------------------------------------

@app.post("/api/pump/run")
async def pump_run(payload: Dict[str, Any]):
    """
    Encola un ciclo de bomba manual para el ESP32.

    Body: {"seconds": 10}  (1-300 s)
    La orden viaja en el proximo GET /api/device/config del dispositivo
    (<=60 s) con formato pump_cmd="<unix_ts>:<segundos>"; expira a los
    PUMP_CMD_TTL_S si el ESP32 no la recoge. El dispositivo hace ack por
    POST /api/device/ack.
    """
    try:
        seconds = int(payload.get("seconds", settings.pump_run_seconds))
    except (TypeError, ValueError):
        raise HTTPException(400, "seconds inválido")
    if not 1 <= seconds <= 300:
        raise HTTPException(400, "seconds fuera de rango (1-300)")

    cmd_id = int(time.time())
    cmd = f"{cmd_id}:{seconds}"
    db.set_device_config("pump_cmd", cmd)
    event = db.insert_pump_event(
        seconds, detail=f"Bomba manual solicitada desde dashboard (cmd {cmd_id})"
    )
    logger.info("pump_cmd encolado: %s", cmd)
    return {"ok": True, "cmd_id": cmd_id, "seconds": seconds,
            "event_id": event["id"], "device": device_status(),
            "note": "Se entrega en el próximo poll del ESP32 (<=60 s)"}


@app.get("/api/pump/events")
async def pump_events(limit: int = 50):
    limit = max(1, min(limit, 200))
    return {"items": db.get_pump_events(limit),
            "pending_cmd": _pending_pump_cmd()}


@app.post("/api/capture/run")
async def capture_run():
    """
    Encola una foto remota para el ESP32 (equivalente al CAPTURE serie).

    La orden viaja en el próximo GET /api/device/config del dispositivo
    (<=60 s) con formato capture_cmd="<unix_ts>"; expira a los
    PUMP_CMD_TTL_S si el ESP32 no la recoge. El dispositivo hace ack por
    POST /api/device/ack. La foto llega por POST /upload como cualquier
    captura (con detección ONNX y broadcast WS).
    """
    cmd_id = int(time.time())
    db.set_device_config("capture_cmd", str(cmd_id))
    logger.info("capture_cmd encolado: %s", cmd_id)
    return {"ok": True, "cmd_id": cmd_id, "device": device_status(),
            "note": "Se entrega en el próximo poll del ESP32 (<=60 s)"}


# ---------------------------------------------------------------------------
# Reset de datos de prueba + métricas de desempeño + telemetría del dispositivo
# ---------------------------------------------------------------------------

@app.delete("/api/captures")
async def reset_captures(scope: str = "photos"):
    """
    Borra datos de prueba del dashboard.

    scope=photos (default): capturas + detecciones + contadores de
    materiales + progreso de bomba en memoria.
    scope=all: además limpia el historial de eventos de bomba.
    """
    include_pump = scope == "all"
    removed = db.reset_test_data(include_pump=include_pump)
    # Contadores en memoria del ciclo de bomba (se derivan de las fotos)
    pump_state["photos_since_pump"] = 0
    pump_state["uploads_received"] = 0
    pump_state["last_upload_ts"] = None
    pump_state["last_pump_event"] = None
    stats = db.get_stats()
    stats["device"] = device_status()
    await broadcast({"type": "stats", "payload": stats})
    logger.info("Reset de datos de prueba: %s", removed)
    return {"ok": True, "removed": removed, "scope": scope}


@app.get("/api/perf")
async def perf_metrics():
    """
    Métricas de desempeño del software y sus comunicaciones.

    - inference: latencia ONNX de las últimas capturas (avg/p95/min/max)
    - network: RTT medido por el cliente (el dashboard lo calcula aparte)
    - device: heartbeat/edad del último contacto del ESP32
    - server: uptime, clientes WS, uploads recientes
    """
    dev = device_status()
    last = dev.get("last_seen")
    return {
        "inference": db.get_inference_stats(60),
        "uploads_last_hour": db.get_uploads_last_hour(),
        "device": {
            **dev,
            "heartbeat_age_s": round(_time.time() - last, 1) if last else None,
        },
        "server": {
            "uptime_s": round(_time.time() - SERVER_START_TS, 1),
            "ws_clients": len(ws_clients),
            "captures_total": db.count_captures(),
        },
        "telemetry": device_telemetry,
    }


# Telemetría del circuito reportada por el ESP32 (POST /api/device/status).
# La llena el firmware en cada poll de config; sin firmware nuevo queda {}.
device_telemetry: Dict[str, Any] = {}


@app.post("/api/device/status")
async def device_status_report(payload: Dict[str, Any]):
    """Recibe telemetría del ESP32 (distancia, relé, cola, heap, RSSI…)."""
    _touch_device_from_payload(payload)
    device_telemetry.clear()
    device_telemetry.update(payload)
    device_telemetry["_ts"] = _time.time()
    return {"ok": True}


def _touch_device_from_payload(payload: Dict[str, Any]) -> None:
    device_state["last_seen"] = _time.time()


@app.get("/api/device/status")
async def device_status_get():
    """Última telemetría del dispositivo + estado de heartbeat."""
    return {"device": device_status(), "telemetry": device_telemetry}


# ---------------------------------------------------------------------------
# Estadísticas temporales (para gráficas del dashboard)
# ---------------------------------------------------------------------------

@app.get("/api/stats/timeseries")
async def stats_timeseries(days: int = 14):
    days = max(1, min(days, 365))
    return {"days": days, "series": db.get_stats_timeseries(days),
            "class_names": settings.class_list}


# ---------------------------------------------------------------------------
# Exportación del dataset en formato YOLO para reentrenar
# ---------------------------------------------------------------------------

@app.get("/api/export/yolo")
async def export_yolo(min_conf: float = 0.0, limit: int = 500):
    """
    Genera un .zip con images/*.jpg + labels/*.txt (YOLO) + data.yaml.

    Usa el JPEG ORIGINAL del ESP32 (sin cajas dibujadas). Las detecciones se
    convierten de xyxy-píxeles a xywh-normalizado. min_conf filtra labels
    débiles; imágenes sin detecciones quedan con label vacío (background).
    """
    min_conf = max(0.0, min(min_conf, 1.0))
    limit = max(1, min(limit, 2000))
    captures = db.get_captures_for_export(limit=limit,
                                          min_confidence=min_conf)
    class_names = settings.class_list
    class_to_id = {n: i for i, n in enumerate(class_names)}

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        # data.yaml compatible con ultralytics
        names_yaml = "names:\n" + "".join(
            f"  {i}: {n}\n" for i, n in enumerate(class_names)
        )
        z.writestr("data.yaml",
                   "# Exportado desde REBA dashboard (/api/export/yolo)\n"
                   "train: images\nval: images\n" + names_yaml)
        for cap in captures:
            img = cap["image"]
            frame = cv2.imdecode(np.frombuffer(img, np.uint8),
                                 cv2.IMREAD_COLOR)
            if frame is None:
                continue
            h, w = frame.shape[:2]
            stem = f"capture_{cap['id']:05d}"
            z.writestr(f"images/{stem}.jpg", img)
            lines = []
            for d in cap["detections"]:
                cid = class_to_id.get(d["class_name"])
                if cid is None:
                    continue
                cx = ((d["x1"] + d["x2"]) / 2) / w
                cy = ((d["y1"] + d["y2"]) / 2) / h
                bw = (d["x2"] - d["x1"]) / w
                bh = (d["y2"] - d["y1"]) / h
                lines.append(f"{cid} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
            z.writestr(f"labels/{stem}.txt", "\n".join(lines))

    buf.seek(0)
    logger.info("Export YOLO: %d capturas -> zip", len(captures))
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition":
                 "attachment; filename=reba_dataset_yolo.zip"},
    )


@app.get("/api/stats")
async def get_stats():
    stats = db.get_stats()
    stats["device"] = device_status()
    return stats


@app.get("/api/pump-status")
async def get_pump_status():
    return pump_progress()


@app.get("/api/pump-events")
async def pump_events(limit: int = 50):
    limit = max(1, min(limit, 500))
    return {"items": db.get_pump_events(limit)}


@app.get("/api/detections/history")
async def detections_history(limit: int = 100):
    limit = max(1, min(limit, 1000))
    return {"items": db.get_detection_history(limit)}


@app.get("/api/detections/latest")
async def detections_latest():
    return {**latest_inference, "stats": db.get_stats()}


@app.get("/api/captures")
async def captures_history(limit: int = 20, offset: int = 0):
    limit = max(1, min(limit, 100))
    offset = max(0, min(offset, 100000))
    return {"items": db.get_captures(limit, offset),
            "total": db.count_captures(), "offset": offset}


@app.get("/api/captures/latest")
async def latest_capture_metadata():
    return db.get_latest_capture()


@app.get("/api/captures/{capture_id}/image")
async def capture_image(capture_id: int, orig: bool = False):
    """
    JPEG de la captura. Por defecto el ANOTADO (cajas dibujadas) que ve el
    dashboard; ?orig=1 devuelve el JPEG crudo del ESP32 si existe.
    """
    image = (db.get_capture_image_orig(capture_id) if orig
             else db.get_capture_image(capture_id))
    if image is None:
        # orig puede no existir en capturas antiguas -> fallback al anotado
        if orig:
            image = db.get_capture_image(capture_id)
    if image is None:
        raise HTTPException(404, "Capture not found")
    return Response(content=image, media_type="image/jpeg")


@app.get("/api/captures/{capture_id}/detections")
async def capture_detections(capture_id: int):
    """Detecciones detalladas de una captura (para el lightbox del dashboard)."""
    if not db.capture_exists(capture_id):
        raise HTTPException(404, "Capture not found")
    return {"capture_id": capture_id,
            "detections": db.get_capture_detections(capture_id)}


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.add(ws)
    logger.info("WebSocket client connected (%d total)", len(ws_clients))
    # Snapshot inicial
    hello_stats = db.get_stats()
    hello_stats["device"] = device_status()
    try:
        await ws.send_json(
            {
                "type": "hello",
                "payload": {
                    "config": settings.public_dict(),
                    "stats": hello_stats,
                    "detections": latest_inference,
                    "latest_capture": latest_capture,
                    "pump": pump_progress(),
                },
            }
        )
        while True:
            # Mantener vivo; el cliente puede enviar pings
            msg = await ws.receive_json()
            if isinstance(msg, dict) and msg.get("type") == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("WebSocket closed: %s", exc)
    finally:
        ws_clients.discard(ws)
        logger.info("WebSocket client disconnected (%d total)", len(ws_clients))


# ---------------------------------------------------------------------------
# Static dashboard (montar al final para no sombrear /api ni /upload)
# ---------------------------------------------------------------------------

# Binarios para flasheo USB de la ESP32 (bootloader/partitions/fotos_nc.bin).
# La ESP32 los descarga por HTTP plano; tambien sirven para provisioning
# presencial via esptool/web-flasher. Antes del mount "/" para que gane.
firmware_dir = PROJECT_ROOT / "firmware"
if firmware_dir.is_dir():
    app.mount("/firmware", StaticFiles(directory=str(firmware_dir)),
              name="firmware")

static_dir = settings.static_dir
if static_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
else:
    logger.warning("static/ not found at %s", static_dir)


def run() -> None:
    """Entry point opcional: `python -m server.main`."""
    import uvicorn

    uvicorn.run(
        "server.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    run()
