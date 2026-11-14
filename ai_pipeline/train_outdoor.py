#!/usr/bin/env python3
"""
REBA — Entrenamiento robusto para condiciones a la intemperie.

Entrenamiento YOLOv8n con aumentación de intemperie → ONNX.
No cambia la arquitectura (sigue siendo YOLOv8n → ONNX) pero endurece los datos:

1. Construye `datasets/REBA_outdoor/data.yaml` combinando el dataset sintético
   con las fuentes reales/opcionales presentes en `datasets/` (TACO, EcoSort,
   boosts sintéticos de clases raras — ver OPTIONAL_TRAIN_SOURCES).
2. Inyecta el pipeline outdoor de `outdoor_transforms.py` vía el parámetro
   nativo `augmentations` de Ultralytics (Albumentations aplicado on-the-fly
   en cada época, con manejo automático de cajas).
3. Añade oclusión extra con los hiperparámetros built-in `erasing`
   (random erasing) y `cutmix`.
4. Early stopping con `patience` — Ultralytics selecciona `best.pt` por
   fitness = mAP50-95 (peso 1.0 en Ultralytics ≥8.4), es decir, prioriza mAP.
5. Exporta `models/best.onnx` archivando los pesos
   previos en `models/archive/` (sirven de baseline para evaluate_outdoor.py).

Uso (desde la raíz REBA/, con el venv activo):
    python ai_pipeline/train_outdoor.py --epochs 50
"""

from __future__ import annotations

import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence

from ultralytics import YOLO

from outdoor_transforms import outdoor_train_transforms

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASETS_ROOT = PROJECT_ROOT / "datasets"
CLASSES = ("plastico", "tela", "organico", "unicel", "metal")


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


def write_combined_data_yaml(
    out_path: Path,
    train_dirs: List[Path],
    val_dir: Path,
    test_dir: Path,
) -> Path:
    """Escribe un data.yaml con raíz absoluta y lista de carpetas de train."""
    names = "\n".join(f"  {i}: {n}" for i, n in enumerate(CLASSES))
    train_lines = "\n".join(f"  - {d.as_posix()}" for d in train_dirs)
    content = (
        "# REBA — dataset combinado outdoor (generado por train_outdoor.py)\n"
        f"path: {DATASETS_ROOT.as_posix()}\n"
        f"train:\n{train_lines}\n"
        f"val: {val_dir.as_posix()}\n"
        f"test: {test_dir.as_posix()}\n"
        f"names:\n{names}\n"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    return out_path


# Fuentes opcionales de train: (carpeta en datasets/, etiqueta para el log).
# Se incluyen solo si `datasets/<nombre>/images/train` existe y no está vacío.
OPTIONAL_TRAIN_SOURCES = (
    ("REBA_synthetic_boost", "boost(organico/tela)"),
    ("REBA_synthetic_boost2", "boost2(organico/tela/unicel)"),
    ("REBA_taco", "TACO"),
    ("REBA_ecosort", "EcoSort"),
)


def build_dataset_yaml() -> Path:
    """Combina sintético + fuentes opcionales presentes en datasets/."""
    train_dirs = [Path("REBA_synthetic/images/train")]
    parts = ["sintético"]
    for folder, label in OPTIONAL_TRAIN_SOURCES:
        train_dir = DATASETS_ROOT / folder / "images" / "train"
        if train_dir.is_dir() and any(train_dir.iterdir()):
            train_dirs.append(Path(f"{folder}/images/train"))
            parts.append(label)
    print("Dataset combinado: " + " + ".join(parts))

    yaml_path = DATASETS_ROOT / "REBA_outdoor" / "data.yaml"
    return write_combined_data_yaml(
        yaml_path,
        train_dirs=train_dirs,
        val_dir=Path("REBA_synthetic/images/val"),
        test_dir=Path("REBA_synthetic/images/test"),
    )


def main(argv: Optional[Sequence[str]] = None) -> None:
    p = argparse.ArgumentParser(
        description="Entrena YOLOv8n con aumentación outdoor → ONNX (REBA)"
    )
    p.add_argument(
        "--data", type=Path, default=None,
        help="data.yaml a usar (default: combinado synthetic+TACO generado aquí)",
    )
    p.add_argument("--epochs", type=int, default=50)
    p.add_argument("--patience", type=int, default=20,
                   help="Early stopping: épocas sin mejora de fitness (mAP)")
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--batch", type=int, default=8)
    p.add_argument("--device", type=str, default="cpu")
    p.add_argument("--model", type=str, default="yolov8n.pt")
    p.add_argument("--project", type=Path,
                   default=PROJECT_ROOT / "runs" / "detect")
    p.add_argument("--name", type=str, default="REBA_outdoor")
    # Aumentación built-in adicional (los efectos fotométricos viven en
    # outdoor_transforms.py y se pasan vía `augmentations`).
    p.add_argument("--cutmix", type=float, default=0.3)
    p.add_argument("--mixup", type=float, default=0.1)
    p.add_argument("--erasing", type=float, default=0.4)
    p.add_argument("--degrees", type=float, default=15.0)
    args = p.parse_args(argv)

    data_yaml = args.data or build_dataset_yaml()
    if not data_yaml.is_file():
        raise SystemExit(f"No existe data.yaml: {data_yaml}")

    transforms = outdoor_train_transforms()
    print(f"Data: {data_yaml}")
    print(f"Device: {args.device} | epochs={args.epochs} imgsz={args.imgsz} "
          f"batch={args.batch} patience={args.patience}")
    print(f"Albumentations outdoor: {len(transforms)} grupos de transformadas")
    print("Selección de best.pt por fitness = mAP50-95 "
          "(Ultralytics ≥8.4) — prioriza mAP; early stopping con patience.")

    model = YOLO(args.model)
    model.train(
        data=str(data_yaml),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        project=str(args.project),
        name=args.name,
        pretrained=True,
        patience=args.patience,
        workers=0,  # más estable en Windows
        exist_ok=True,
        verbose=True,
        augmentations=transforms,
        # Oclusión extra built-in
        cutmix=args.cutmix,
        mixup=args.mixup,
        erasing=args.erasing,
        # Geometría leve (la basura flota y rota)
        degrees=args.degrees,
        translate=0.15,
        scale=0.6,
        fliplr=0.5,
        # HSV built-in moderado (HueSaturationValue fuerte va en albumentations)
        hsv_h=0.02,
        hsv_s=0.7,
        hsv_v=0.45,
        mosaic=1.0,
        close_mosaic=10,
        cos_lr=True,
    )

    save_dir = Path(model.trainer.save_dir)
    best_pt = save_dir / "weights" / "best.pt"
    if not best_pt.is_file():
        raise SystemExit(f"No se encontró {best_pt}")

    print(f"Exportando ONNX desde {best_pt}…")
    best = YOLO(str(best_pt))
    export_path = Path(best.export(
        format="onnx", imgsz=args.imgsz, opset=12, simplify=True
    ))

    out_onnx = PROJECT_ROOT / "models" / "best.onnx"
    out_onnx.parent.mkdir(parents=True, exist_ok=True)
    archive_previous_model(out_onnx, PROJECT_ROOT / "models" / "archive")
    shutil.copy2(export_path, out_onnx)
    print(f"Modelo listo: {out_onnx} ({out_onnx.stat().st_size / 1e6:.2f} MB)")

    out_pt = PROJECT_ROOT / "models" / "best.pt"
    archive_previous_model(out_pt, PROJECT_ROOT / "models" / "archive")
    shutil.copy2(best_pt, out_pt)
    print(f"Weights PT: {out_pt}")
    print(f"Métricas del run en: {save_dir / 'results.csv'}")


if __name__ == "__main__":
    main()
