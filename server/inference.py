"""
Motor de inferencia REBA.

Modo final: YOLOv8 exportado a ONNX (`models/best.onnx`) vía onnxruntime.
Sin fallback remoto: si no hay modelo, el sistema reporta error y sigue en
modo local/offline.

Clases (class_id 0..N-1, configurables vía CLASS_NAMES):
    plastico, unicel, metal

Uso:
    engine = InferenceEngine(settings)
    result = engine.predict(frame_bgr_640)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import cv2
import numpy as np

from server.config import Settings

logger = logging.getLogger("REBA.inference")


@dataclass
class Detection:
    class_id: int
    class_name: str
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "class_id": self.class_id,
            "class_name": self.class_name,
            "confidence": round(self.confidence, 4),
            "x1": round(self.x1, 2),
            "y1": round(self.y1, 2),
            "x2": round(self.x2, 2),
            "y2": round(self.y2, 2),
        }


@dataclass
class InferenceResult:
    detections: List[Detection] = field(default_factory=list)
    source: str = "none"  # onnx | none
    inference_ms: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "detections": [d.to_dict() for d in self.detections],
            "source": self.source,
            "inference_ms": round(self.inference_ms, 2),
            "error": self.error,
            "count": len(self.detections),
        }


class InferenceEngine:
    """Encapsula únicamente ONNX local."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.class_names: Sequence[str] = settings.class_list
        self.session = None
        self.input_name: Optional[str] = None
        self.input_shape: Optional[tuple] = None
        self.backend = "none"
        self._init_onnx()

    def _init_onnx(self) -> None:
        model_path: Path = self.settings.model_path_abs
        if not model_path.is_file():
            logger.warning("ONNX model not found at %s", model_path)
            self.backend = "none"
            return

        try:
            import onnxruntime as ort

            opts = ort.SessionOptions()
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self.session = ort.InferenceSession(
                str(model_path),
                sess_options=opts,
                providers=["CPUExecutionProvider"],
            )
            inp = self.session.get_inputs()[0]
            self.input_name = inp.name
            self.input_shape = tuple(inp.shape)
            self.backend = "onnx"
            logger.info("ONNX loaded: %s input=%s", model_path.name, self.input_shape)
        except Exception as exc:
            logger.error("Failed to load ONNX: %s", exc)
            self.session = None
            self.backend = "none"

    def predict(self, frame_bgr: np.ndarray) -> InferenceResult:
        """Ejecuta inferencia sobre un frame BGR (idealmente 640×640)."""
        import time

        t0 = time.perf_counter()
        if self.session is not None:
            try:
                dets = self._predict_onnx(frame_bgr)
                ms = (time.perf_counter() - t0) * 1000.0
                return InferenceResult(detections=dets, source="onnx", inference_ms=ms)
            except Exception as exc:
                logger.exception("ONNX inference failed: %s", exc)
                ms = (time.perf_counter() - t0) * 1000.0
                return InferenceResult(
                    detections=[], source="none", inference_ms=ms, error=str(exc)
                )

        ms = (time.perf_counter() - t0) * 1000.0
        return InferenceResult(
            detections=[],
            source="none",
            inference_ms=ms,
            error="No ONNX model available",
        )

    # ------------------------------------------------------------------ ONNX

    def _letterbox(
        self, img: np.ndarray, new_shape: tuple[int, int] = (640, 640)
    ) -> tuple[np.ndarray, float, tuple[float, float]]:
        """Resize con padding gris (estilo YOLO)."""
        shape = img.shape[:2]  # h, w
        r = min(new_shape[0] / shape[0], new_shape[1] / shape[1])
        new_unpad = (int(round(shape[1] * r)), int(round(shape[0] * r)))
        dw = new_shape[1] - new_unpad[0]
        dh = new_shape[0] - new_unpad[1]
        dw /= 2
        dh /= 2
        if shape[::-1] != new_unpad:
            img = cv2.resize(img, new_unpad, interpolation=cv2.INTER_LINEAR)
        top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
        left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
        img = cv2.copyMakeBorder(
            img, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114, 114, 114)
        )
        return img, r, (dw, dh)

    def _preprocess(
        self, frame_bgr: np.ndarray
    ) -> tuple[np.ndarray, float, tuple[float, float]]:
        img, ratio, pad = self._letterbox(frame_bgr, (640, 640))
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img = img.astype(np.float32) / 255.0
        img = np.transpose(img, (2, 0, 1))[None, ...]  # 1x3x640x640
        return img, ratio, pad

    def _predict_onnx(self, frame_bgr: np.ndarray) -> List[Detection]:
        assert self.session is not None and self.input_name is not None
        blob, ratio, pad = self._preprocess(frame_bgr)
        outputs = self.session.run(None, {self.input_name: blob})
        return self._postprocess_yolov8(outputs[0], frame_bgr.shape, ratio, pad)

    def _postprocess_yolov8(
        self,
        output: np.ndarray,
        orig_shape: tuple,
        ratio: float,
        pad: tuple[float, float],
    ) -> List[Detection]:
        """
        Postproceso YOLOv8 ONNX.

        Salida típica: (1, 4+nc, N) → se transpone a (N, 4+nc)
        con [x, y, w, h, class_scores...]
        """
        pred = output[0]
        if pred.shape[0] < pred.shape[1]:
            # (4+nc, N) → (N, 4+nc)
            pred = pred.T

        conf_thres = self.settings.inference_confidence
        iou_thres = self.settings.inference_iou
        nc = len(self.class_names)

        boxes_xywh = pred[:, :4]
        scores_all = pred[:, 4 : 4 + nc] if pred.shape[1] >= 4 + nc else pred[:, 4:]

        class_ids = np.argmax(scores_all, axis=1)
        confidences = scores_all[np.arange(scores_all.shape[0]), class_ids]

        mask = confidences >= conf_thres
        boxes_xywh = boxes_xywh[mask]
        confidences = confidences[mask]
        class_ids = class_ids[mask]

        if len(boxes_xywh) == 0:
            return []

        # xywh (centro) → xyxy en espacio letterbox
        boxes_xyxy = np.zeros_like(boxes_xywh)
        boxes_xyxy[:, 0] = boxes_xywh[:, 0] - boxes_xywh[:, 2] / 2
        boxes_xyxy[:, 1] = boxes_xywh[:, 1] - boxes_xywh[:, 3] / 2
        boxes_xyxy[:, 2] = boxes_xywh[:, 0] + boxes_xywh[:, 2] / 2
        boxes_xyxy[:, 3] = boxes_xywh[:, 1] + boxes_xywh[:, 3] / 2

        # Quitar padding y escalar a imagen original
        pad_w, pad_h = pad
        boxes_xyxy[:, [0, 2]] -= pad_w
        boxes_xyxy[:, [1, 3]] -= pad_h
        boxes_xyxy /= ratio

        h0, w0 = orig_shape[:2]
        boxes_xyxy[:, [0, 2]] = boxes_xyxy[:, [0, 2]].clip(0, w0)
        boxes_xyxy[:, [1, 3]] = boxes_xyxy[:, [1, 3]].clip(0, h0)

        # NMS OpenCV
        # OpenCV espera cajas [x, y, width, height], no coordenadas xyxy.
        # Mantener xyxy para la salida, pero convertir solo para NMS.
        nms_boxes = np.column_stack(
            (
                boxes_xyxy[:, 0],
                boxes_xyxy[:, 1],
                boxes_xyxy[:, 2] - boxes_xyxy[:, 0],
                boxes_xyxy[:, 3] - boxes_xyxy[:, 1],
            )
        ).tolist()
        indices = cv2.dnn.NMSBoxes(
            bboxes=nms_boxes,
            scores=confidences.tolist(),
            score_threshold=conf_thres,
            nms_threshold=iou_thres,
        )

        detections: List[Detection] = []
        if len(indices) == 0:
            return detections

        for i in np.array(indices).flatten():
            cid = int(class_ids[i])
            name = (
                self.class_names[cid]
                if 0 <= cid < len(self.class_names)
                else f"class_{cid}"
            )
            x1, y1, x2, y2 = boxes_xyxy[i]
            detections.append(
                Detection(
                    class_id=cid,
                    class_name=name,
                    confidence=float(confidences[i]),
                    x1=float(x1),
                    y1=float(y1),
                    x2=float(x2),
                    y2=float(y2),
                )
            )
        return detections

    def status(self) -> dict:
        return {
            "backend": self.backend,
            "model_path": str(self.settings.model_path_abs),
            "model_exists": self.settings.model_path_abs.is_file(),
            "class_names": list(self.class_names),
            "input_shape": list(self.input_shape) if self.input_shape else None,
        }

