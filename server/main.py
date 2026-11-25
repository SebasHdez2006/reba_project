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
import json
import logging
import time
import zipfile
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
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
    "active_until": None,   # epoch estimado de fin del ciclo de bomba ON
}

# Último contacto del ESP32-CAM. Se actualiza en cada GET /api/device/config
# (poll cada ~60 s) y en cada POST /upload — sirve de heartbeat para saber
# si el dispositivo está en línea sin depender de que tome fotos.
import time as _time

device_state: Dict[str, Any] = {
    "last_seen": None,   # epoch seconds
    "ip": None,
    "polls_total": 0,          # GET /api/device/config recibidos (proceso actual)
    "last_poll_ts": None,      # epoch del último poll de config
    "poll_ts": deque(maxlen=1024),  # epochs de polls p/ ventana deslizante (/h)
}
DEVICE_ONLINE_WINDOW_S = 150.0   # >2 polls perdidos = offline
SERVER_START_TS = _time.time()  # uptime del proceso (métricas /api/perf)


# El firmware manda "User-Agent: REBA-ESP32" en todo request (httpSimple).
# Solo esos cuentan como heartbeat: un curl, el navegador o un bot de
# Internet pegándole a /api/device/config no deben marcar "online".
DEVICE_USER_AGENT = "REBA-ESP32"


def _is_device(request: Request) -> bool:
    return request.headers.get("user-agent", "").startswith(DEVICE_USER_AGENT)


def _touch_device(request: Request) -> bool:
    """
    Registra que el ESP32 contactó al servidor (heartbeat).

    Devuelve True si el request vino del dispositivo (User-Agent
    REBA-ESP32); False para curl, navegador, bots, etc.
    """
    if not _is_device(request):
        return False
    device_state["last_seen"] = _time.time()
    try:
        device_state["ip"] = request.client.host if request.client else None
    except Exception:
        pass
    return True


def _record_poll() -> None:
    """Cuenta un poll de config del dispositivo (GET /api/device/config)."""
    now = _time.time()
    device_state["polls_total"] += 1
    device_state["last_poll_ts"] = now
    device_state["poll_ts"].append(now)


def device_status() -> Dict[str, Any]:
    """Estado del dispositivo para /api/health y /api/stats."""
    last = device_state.get("last_seen")
    online = last is not None and (_time.time() - last) < DEVICE_ONLINE_WINDOW_S
    # Ventana deslizante de polls (última hora): con el firmware actual
    # (POLL_CONFIG_MS=60 s) un dispositivo sano ronda ~60 polls/h.
    polls_hour = 0
    poll_ts = device_state.get("poll_ts")
    if poll_ts:
        cutoff = _time.time() - 3600
        while poll_ts and poll_ts[0] < cutoff:
            poll_ts.popleft()
        polls_hour = len(poll_ts)
    return {
        "online": online,
        "last_seen": last,
        "ip": device_state.get("ip"),
        "polls_total": device_state.get("polls_total", 0),
        "polls_last_hour": polls_hour,
        "last_poll_ts": device_state.get("last_poll_ts"),
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
    active_until = pump_state.get("active_until")
    rest = max(0.0, active_until - _time.time()) if active_until else 0.0
    return {
        "photos_since_pump": pump_state["photos_since_pump"],
        "pump_trigger_photos": settings.pump_trigger_photos,
        "pump_run_seconds": settings.pump_run_seconds,
        "uploads_received": pump_state["uploads_received"],
        "last_upload_ts": pump_state["last_upload_ts"],
        "last_pump_event": pump_state["last_pump_event"],
        "pump_active_until": active_until,
        "pump_rest_s": rest,
    }


# ---------------------------------------------------------------------------
# Procesamiento de la foto recibida
# ---------------------------------------------------------------------------

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

    # Foto original: el motor letterboxea (detector) y recorta al centro
    # (clasificador) sin deformar el 4:3 de la ESP32.
    result = engine.predict(frame)
    photo_detections = result.detections

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
        # La ESP32 enciende la bomba al tomar la N-ésima foto; este upload
        # llega ~1 s después -> estimación del fin del ciclo para la UI.
        pump_state["active_until"] = _time.time() + settings.pump_run_seconds
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


def _pending_cmd(key: str) -> str | None:
    """
    Valor vigente de una orden de un solo uso o None.

    Limpia perezosamente el override expirado: si el ESP32 nunca vuelve a
    consultar, la orden vieja no debe seguir reportándose como "en cola".
    """
    cfg = db.get_device_config()
    cmd = cfg.get(key)
    if cmd is not None and not _pump_cmd_fresh(cmd):
        db.delete_device_config(key)
        return None
    return cmd


def _pending_pump_cmd() -> str | None:
    return _pending_cmd("pump_cmd")


# Claves de órdenes de un solo uso (las registra device_commands).
COMMAND_CONFIG_KEYS = ("pump_cmd", "capture_cmd", "reconnect_cmd", "queue_clear")


@app.get("/api/device/config", response_class=PlainTextResponse)
async def device_config(request: Request):
    """
    Config que el ESP32-CAM descarga periódicamente.

    Formato `clave=valor` por línea (sin JSON: el firmware lo parsea simple).
    Solo se devuelven las claves con override guardado — el firmware conserva
    sus valores locales para el resto. Cada poll cuenta como heartbeat del
    dispositivo (device_state) y se acumula en el contador de polls.
    """
    is_device = _touch_device(request)
    if is_device:
        _record_poll()
    cfg = db.get_device_config()
    if not is_device:
        # Terceros (curl, bots): sin credenciales WiFi y sin visibilidad de
        # las órdenes de un solo uso destinadas a la placa (ni consumen la
        # orden WiFi, que es de un solo uso).
        for key in ("wifi_ssid", "wifi_pass", *COMMAND_CONFIG_KEYS):
            cfg.pop(key, None)
    for key in COMMAND_CONFIG_KEYS:
        cmd = cfg.get(key)
        if cmd is not None and not _pump_cmd_fresh(cmd):
            db.delete_device_config(key)      # expirado: no entregarlo
            cfg.pop(key, None)
    # WiFi = orden de un solo uso: si quedara persistente, una ESP32 en otra
    # red (hotspot del celular en campo) recibiría REBA-AP en cada poll,
    # saltaría a una red inexistente, revertiría a los 90 s y repetiría
    # el ciclo sin fin (offline intermitente).
    if "wifi_ssid" in cfg and "wifi_pass" in cfg:
        db.delete_device_config("wifi_ssid")
        db.delete_device_config("wifi_pass")
        logger.info("wifi_ssid=%s entregado al ESP32 (un solo uso)", cfg["wifi_ssid"])
    if is_device:
        # Las órdenes presentes en esta respuesta quedaron entregadas:
        # el dashboard las muestra como "entregada" hasta el ACK.
        for key in COMMAND_CONFIG_KEYS:
            if key in cfg:
                db.mark_device_command_delivered(key)
    return "\n".join(f"{k}={v}" for k, v in sorted(cfg.items())) + "\n"


@app.get("/api/admin/device-config")
async def admin_get_device_config():
    """Overrides actuales (los edita el dashboard) + marca de tiempo."""
    meta = db.get_device_config_meta()
    return {"items": {k: m["value"] for k, m in meta.items()},
            "updated": {k: m["updated_ts"] for k, m in meta.items()},
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
    # queue_clear también es una orden de un solo uso: registrarla para
    # que aparezca en el ciclo de vida de órdenes del dashboard.
    if "queue_clear" in applied:
        try:
            db.upsert_device_command(
                int(str(items["queue_clear"]).strip()),
                "queue_clear", "queue_clear", "vaciar cola de fotos",
            )
        except (TypeError, ValueError):
            pass
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
    pump_acked = False
    pending = cfg.get("pump_cmd", "")
    if pending.startswith(f"{cmd_id}:"):
        # pump_cmd="<unix_ts>:<segundos>": el ACK llega justo tras
        # bombaArrancar -> el countdown corre desde ahora + seg.
        try:
            secs = int(pending.split(":", 1)[1])
            pump_state["active_until"] = _time.time() + secs
        except (ValueError, IndexError):
            pass
        db.delete_device_config("pump_cmd")
        db.mark_device_command_acked("pump_cmd")
        pump_acked = True
        cleared = True
    pending_cap = cfg.get("capture_cmd", "")
    if pending_cap == str(cmd_id):
        db.delete_device_config("capture_cmd")
        db.mark_device_command_acked("capture_cmd")
        cleared = True
    pending_rec = cfg.get("reconnect_cmd", "")
    if pending_rec == str(cmd_id):
        db.delete_device_config("reconnect_cmd")
        db.mark_device_command_acked("reconnect_cmd")
        cleared = True
    pending_qc = cfg.get("queue_clear", "")
    if pending_qc == str(cmd_id):
        db.delete_device_config("queue_clear")
        db.mark_device_command_acked("queue_clear")
        cleared = True
    if cleared:
        logger.info("cmd %s confirmado por el dispositivo", cmd_id)
        # Empujar el nuevo estado al dashboard sin esperar el próximo poll:
        # la lista de órdenes cambia y, si era la bomba, arranca el countdown.
        await broadcast({"type": "orders", "payload": {}})
        if pump_acked:
            await broadcast({"type": "pump", "payload": pump_progress()})
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
    if _pending_pump_cmd() is not None:
        raise HTTPException(
            409, "Ya hay una orden de bomba en cola; cancélala o espera a que expire")

    cmd_id = int(time.time())
    cmd = f"{cmd_id}:{seconds}"
    db.set_device_config("pump_cmd", cmd)
    db.upsert_device_command(cmd_id, "pump", "pump_cmd", f"{seconds} s")
    # La orden manual cuenta como ciclo de bomba realizado: el contador
    # de fotos para el disparo automático vuelve a cero (el firmware
    # hace lo propio al ejecutar el comando — evita doble bombeo).
    pump_state["photos_since_pump"] = 0
    event = db.insert_pump_event(
        seconds,
        detail=f"Bomba manual solicitada desde dashboard (cmd {cmd_id}) · contador reiniciado",
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
    if _pending_cmd("capture_cmd") is not None:
        raise HTTPException(
            409, "Ya hay una orden de foto en cola; cancélala o espera a que expire")
    db.set_device_config("capture_cmd", str(cmd_id))
    db.upsert_device_command(cmd_id, "capture", "capture_cmd", "fotografía")
    logger.info("capture_cmd encolado: %s", cmd_id)
    return {"ok": True, "cmd_id": cmd_id, "device": device_status(),
            "note": "Se entrega en el próximo poll del ESP32 (<=60 s)"}


@app.post("/api/device/reconnect")
async def device_reconnect():
    """
    Encola una solicitud de reconexión para el ESP32-CAM.

    Botón "Reconectar ESP32" del dashboard: la orden viaja como
    `reconnect_cmd="<unix_ts>"` en el próximo GET /api/device/config del
    dispositivo (<=60 s); el firmware fuerza WiFi.disconnect + un poll de
    config inmediato al recibirla. Mismo TTL de PUMP_CMD_TTL_S que el
    resto de comandos de un solo uso y ack por POST /api/device/ack.

    OJO: la arquitectura es "push" — el servidor no puede iniciar la
    conexión. Si el ESP32 está realmente offline (no alcanza el servidor),
    esta orden no puede entregarse y expira; el dispositivo debe volver
    a pollar por sus propios reintentos (firmware: REINTENTO_WIFI_MS,
    failover srv_alt, reinicio a los 30 min sin config).
    """
    cmd_id = int(time.time())
    if _pending_cmd("reconnect_cmd") is not None:
        raise HTTPException(
            409, "Ya hay una orden de reconexión en cola; cancélala o espera a que expire")
    db.set_device_config("reconnect_cmd", str(cmd_id))
    db.upsert_device_command(cmd_id, "reconnect", "reconnect_cmd",
                             "forzar reconexión WiFi")
    logger.info("reconnect_cmd encolado: %s", cmd_id)
    return {"ok": True, "cmd_id": cmd_id, "device": device_status(),
            "note": "Se entrega en el próximo poll del ESP32 (<=60 s)"}


# ---------------------------------------------------------------------------
# Ciclo de vida de las órdenes al dispositivo (dashboard "Órdenes")
# ---------------------------------------------------------------------------

def _command_state(row: Dict[str, Any], now: float) -> str:
    """
    Estado visible de una orden:
      queued     creada, aún no entregada al ESP32
      delivered  servida en un poll de config, esperando ACK
      acked      el dispositivo confirmó su ejecución
      expired    el TTL pasó sin que el ESP32 la recibiera
      cancelled  descartada desde el dashboard antes de entregarse
    """
    if row.get("cancelled_ts"):
        return "cancelled"
    if row.get("acked_ts"):
        return "acked"
    if row.get("delivered_ts"):
        return "delivered"
    if now - float(row["created_ts"]) > PUMP_CMD_TTL_S:
        return "expired"
    return "queued"


@app.get("/api/device/commands")
async def device_commands(limit: int = 40):
    """
    Órdenes recientes al ESP32 con su estado de ciclo de vida.

    El dashboard las muestra en Operación → "Órdenes al dispositivo":
    en cola → entregada → ejecutada (ACK) / expirada / cancelada.
    """
    limit = max(1, min(limit, 200))
    now = time.time()
    items = []
    for row in db.get_device_commands(limit):
        items.append({
            "id": row["id"],
            "kind": row["kind"],
            "detail": row["detail"],
            "uid": row["uid"],
            "created_ts": row["created_ts"],
            "delivered_ts": row["delivered_ts"],
            "acked_ts": row["acked_ts"],
            "expires_ts": float(row["created_ts"]) + PUMP_CMD_TTL_S,
            "state": _command_state(row, now),
        })
    return {
        "items": items,
        "pending": db.count_pending_device_commands(now - PUMP_CMD_TTL_S),
        "ttl_s": PUMP_CMD_TTL_S,
        "device": device_status(),
    }


@app.post("/api/device/commands/{cid}/cancel")
async def cancel_device_command(cid: int):
    """
    Cancela una orden que aún no fue entregada al ESP32.

    Solo procede en estado "queued": una vez entregada, el firmware la
    ejecuta al recibirla y cancelarla en el servidor no la retiraría.
    """
    row = db.get_device_command(cid)
    if row is None:
        raise HTTPException(404, "Orden no encontrada")
    if row["acked_ts"] or row["cancelled_ts"]:
        raise HTTPException(409, "La orden ya terminó")
    if row["delivered_ts"]:
        raise HTTPException(
            409, "La orden ya fue entregada al dispositivo; no se puede cancelar")
    # Borrar la clave solo si todavía contiene ESTA orden: pudo ser
    # reemplazada por una más reciente del mismo tipo.
    current = db.get_device_config().get(row["config_key"], "")
    uid = str(row["uid"])
    still_current = (current.startswith(f"{uid}:")
                     if row["config_key"] == "pump_cmd" else current == uid)
    if still_current:
        db.delete_device_config(row["config_key"])
    if not db.cancel_device_command(cid):
        raise HTTPException(409, "La orden ya no es cancelable")
    logger.info("Orden #%s (%s) cancelada desde el dashboard", cid, row["kind"])
    await broadcast({"type": "orders", "payload": {}})
    return {"ok": True, "id": cid}


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
    pump_state["active_until"] = None
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
async def device_status_report(payload: Dict[str, Any], request: Request):
    """Recibe telemetría del ESP32 (distancia, relé, cola, heap, RSSI…)."""
    if not _is_device(request):
        raise HTTPException(403, "Solo el dispositivo reporta telemetría")
    _touch_device(request)
    device_telemetry.clear()
    device_telemetry.update(payload)
    device_telemetry["_ts"] = _time.time()
    return {"ok": True}


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


@app.get("/api/export/backup")
async def export_backup():
    """
    Respaldo completo del estado del servidor en un .zip.

    Contenido:
      - manifest.json: contadores, eventos de bomba, órdenes, config del
        dispositivo (sin credenciales WiFi) e historial de detecciones.
      - images/capture_XXXXX.jpg: JPEG de cada captura (original si existe).

    Pensado para descargar antes de una operación destructiva desde la
    sección Mantenimiento del dashboard.
    """
    captures = db.get_captures_for_export(limit=5000, min_confidence=0.0)
    cfg_safe = {
        k: v for k, v in db.get_device_config().items()
        if k not in ("wifi_ssid", "wifi_pass")
    }
    manifest = {
        "app": "REBA",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "captures": len(captures),
        "counters": db.get_counters(),
        "pump_events": db.get_pump_events(10000),
        "device_commands": db.get_device_commands(500),
        "device_config": cfg_safe,
        "detections": db.get_detection_history(100000),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json",
                   json.dumps(manifest, indent=2, default=str))
        for cap in captures:
            z.writestr(f"images/capture_{cap['id']:05d}.jpg", cap["image"])
    buf.seek(0)
    logger.info("Backup exportado: %d capturas", len(captures))
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition":
                 "attachment; filename=reba_backup.zip"},
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
