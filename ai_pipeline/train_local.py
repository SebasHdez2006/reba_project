#!/usr/bin/env python3
"""
Entrena YOLOv8n en el dataset local y exporta models/best.onnx.

Uso (desde raíz REBA/, con venv activo):
    python ai_pipeline/train_local.py
"""

from __future__ import annotations

import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path


from ultralytics import YOLO

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def archive_previous_model(path: Path, archive_dir: Path) -> None:
    """Preserva una copia del modelo previo antes de sobrescribirlo."""
    if not path.is_file():
        return
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archived = archive_dir / f"{path.stem}_{stamp}{path.suffix}"
    shutil.copy2(path, archived)
    meta = archive_dir / f"{path.stem}_{stamp}.json"
    meta.write_text(
        f'{{"source":"{path.as_posix()}","archived":"{archived.as_posix()}"}}',
        encoding="utf-8",
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Train REBA YOLOv8n → ONNX")
    p.add_argument(
        "--data",
        type=Path,
        default=PROJECT_ROOT / "datasets" / "REBA_synthetic" / "data.yaml",
    )
    p.add_argument("--epochs", type=int, default=40)
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--batch", type=int, default=8)
    p.add_argument("--device", type=str, default="cpu")
    p.add_argument("--model", type=str, default="yolov8n.pt")
    p.add_argument(
        "--project",
        type=Path,
        default=PROJECT_ROOT / "runs" / "detect",
    )
    p.add_argument("--name", type=str, default="REBA_waste")
    args = p.parse_args()

    if not args.data.is_file():
        raise SystemExit(f"No existe data.yaml: {args.data}")

    print(f"Data: {args.data}")
    print(f"Device: {args.device} | epochs={args.epochs} imgsz={args.imgsz} batch={args.batch}")

    model = YOLO(args.model)
    model.train(
        data=str(args.data),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        project=str(args.project),
        name=args.name,
        pretrained=True,
        patience=15,
        workers=0,  # más estable en Windows
        exist_ok=True,
        verbose=True,
    )

    # Ultralytics normaliza project/name internamente.  Consultar el directorio
    # efectivo evita depender de rutas relativas (especialmente en Windows).
    save_dir = Path(model.trainer.save_dir)
    best_pt = save_dir / "weights" / "best.pt"
    if not best_pt.is_file():
        raise SystemExit(f"No se encontró {best_pt}")

    print(f"Exportando ONNX desde {best_pt}…")
    best = YOLO(str(best_pt))
    export_path = best.export(format="onnx", imgsz=args.imgsz, opset=12, simplify=True)
    export_path = Path(export_path)

    out_onnx = PROJECT_ROOT / "models" / "best.onnx"
    out_onnx.parent.mkdir(parents=True, exist_ok=True)
    archive_previous_model(out_onnx, PROJECT_ROOT / "models" / "archive")
    shutil.copy2(export_path, out_onnx)
    print(f"Modelo listo: {out_onnx} ({out_onnx.stat().st_size / 1e6:.2f} MB)")

    # Copia también el .pt por si se necesita re-exportar
    out_pt = PROJECT_ROOT / "models" / "best.pt"
    archive_previous_model(out_pt, PROJECT_ROOT / "models" / "archive")
    shutil.copy2(best_pt, out_pt)
    print(f"Weights PT: {out_pt}")




if __name__ == "__main__":
    main()
