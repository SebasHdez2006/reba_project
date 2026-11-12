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

import logging
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any, Dict, List, Set

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
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
        "tela": (180, 120, 255),
        "organico": (80, 200, 120),
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
    await broadcast({"type": "capture", "payload": latest_capture})
    await broadcast(
        {
            "type": "detections",
            "payload": {
                **payload,
                "stats": db.get_stats(),
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
    yield
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
    """
    body = await request.body()
    if not body:
        raise HTTPException(400, "Body vacío")
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
    }


@app.get("/api/config")
async def get_config():
    """Config pública (parámetros editables vía `.env`). Sin secretos."""
    return settings.public_dict()


@app.get("/api/stats")
async def get_stats():
    return db.get_stats()


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
async def captures_history(limit: int = 20):
    limit = max(1, min(limit, 100))
    return {"items": db.get_captures(limit), "total": db.count_captures()}


@app.get("/api/captures/latest")
async def latest_capture_metadata():
    return db.get_latest_capture()


@app.get("/api/captures/{capture_id}/image")
async def capture_image(capture_id: int):
    image = db.get_capture_image(capture_id)
    if image is None:
        raise HTTPException(404, "Capture not found")
    return Response(content=image, media_type="image/jpeg")


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.add(ws)
    logger.info("WebSocket client connected (%d total)", len(ws_clients))
    # Snapshot inicial
    try:
        await ws.send_json(
            {
                "type": "hello",
                "payload": {
                    "config": settings.public_dict(),
                    "stats": db.get_stats(),
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
