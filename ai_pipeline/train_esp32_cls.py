#!/usr/bin/env python3
"""
REBA — Entrena el clasificador ESP32 (YOLO-cls) y exporta ONNX para el server.

Dataset: datasets/REBA_cls_esp32 (build_esp32_cls_dataset.py).
Salida:  runs/classify/<name>/weights/best.pt  ->  --export copia el ONNX a
         models/cls_esp32.onnx (el server lo carga al arrancar, CLS_MODEL_PATH).

Uso (CPU i5, ~6-8 min/época a 160 px con freeze=4):
    python ai_pipeline/train_esp32_cls.py --model models/yolov8n-cls.pt --name REBA_cls_v1 --export
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from ultralytics import YOLO

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=str(ROOT / "models" / "yolov8n-cls.pt"))
    ap.add_argument("--data", default=str(ROOT / "datasets" / "REBA_cls_esp32"))
    ap.add_argument("--name", default="REBA_cls_v1")
    ap.add_argument("--epochs", type=int, default=12)
    ap.add_argument("--imgsz", type=int, default=160)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--patience", type=int, default=4)
    ap.add_argument("--freeze", type=int, default=4, help="capas iniciales congeladas (CPU: backward más barato)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--export", action="store_true", help="exporta best.pt a models/cls_esp32.onnx")
    args = ap.parse_args()

    model = YOLO(args.model)
    model.train(
        data=args.data, epochs=args.epochs, imgsz=args.imgsz, batch=args.batch,
        patience=args.patience, workers=args.workers, device="cpu",
        freeze=args.freeze, cache="ram",
        project=str(ROOT / "runs" / "classify"), name=args.name, exist_ok=True,
        fliplr=0.5, flipud=0.2, hsv_h=0.02, hsv_s=0.5, hsv_v=0.4,
        scale=0.5, erasing=0.3, dropout=0.1,
        cos_lr=True, plots=True, seed=0,
    )
    best = ROOT / "runs" / "classify" / args.name / "weights" / "best.pt"
    if args.export:
        onnx = Path(YOLO(str(best)).export(format="onnx", imgsz=args.imgsz, simplify=True, opset=12))
        dst = ROOT / "models" / "cls_esp32.onnx"
        if dst.exists():
            arch = ROOT / "models" / "archive"
            arch.mkdir(exist_ok=True)
            shutil.copy2(dst, arch / f"cls_esp32_prev_{int(dst.stat().st_mtime)}.onnx")
        shutil.copy2(onnx, dst)
        print(f"ONNX -> {dst}")


if __name__ == "__main__":
    main()
