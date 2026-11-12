#!/usr/bin/env python3
"""
REBA — Genera el conjunto de validación "difícil" (datasets/REBA_outdoor_eval).

Toma las imágenes val/test del dataset origen (por defecto
datasets/REBA_synthetic), les aplica el pipeline severo de
`outdoor_transforms.outdoor_eval_transforms()` con semilla fija (resultado
determinista y reproducible) y escribe:

    datasets/REBA_outdoor_eval/
    ├── data.yaml            (split val ← todas las imágenes degradadas)
    ├── images/val/*.jpg
    ├── labels/val/*.txt     (idénticas a las originales: transforms no espaciales)
    └── preview.jpg          (opcional, con --preview: antes/después)

Como todas las transformaciones son de nivel píxel, las etiquetas se copian sin
cambios y siguen siendo válidas.

Uso (desde la raíz REBA/, con el venv activo):
    python ai_pipeline/build_outdoor_eval.py --preview
"""

from __future__ import annotations

import argparse
import logging
import shutil
from pathlib import Path
from typing import List, Optional, Sequence

import albumentations as A
import cv2
import numpy as np

from outdoor_transforms import outdoor_eval_transforms

PROJECT_ROOT = Path(__file__).resolve().parent.parent

CLASSES = ("plastico", "tela", "organico", "unicel", "metal")
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

logger = logging.getLogger("REBA.outdoor_eval")


def discover_images(folder: Path) -> List[Path]:
    return sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    ) if folder.is_dir() else []


def write_data_yaml(out_root: Path) -> None:
    names = "\n".join(f"  {i}: {n}" for i, n in enumerate(CLASSES))
    content = (
        "# REBA — conjunto de evaluación difícil (build_outdoor_eval.py)\n"
        "# Copias degradadas de val+test del dataset sintético.\n"
        "train: images/val\n"
        "val: images/val\n"
        "test: images/val\n"
        f"names:\n{names}\n"
    )
    (out_root / "data.yaml").write_text(content, encoding="utf-8")


def write_preview(out_root: Path, pairs: Sequence[tuple[np.ndarray, np.ndarray]],
                  cols: int = 4) -> None:
    """Mosaico antes/después para inspección visual rápida."""
    tiles: List[np.ndarray] = []
    for orig, aug in pairs:
        cv2.putText(orig, "orig", (8, 24), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, (0, 255, 0), 2)
        cv2.putText(aug, "outdoor", (8, 24), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, (0, 200, 255), 2)
        tiles.extend([orig, aug])
    rows = []
    for i in range(0, len(tiles), cols):
        row = tiles[i:i + cols]
        h = max(t.shape[0] for t in row)
        w = max(t.shape[1] for t in row)
        padded = [
            cv2.copyMakeBorder(t, 0, h - t.shape[0], 0, w - t.shape[1],
                               cv2.BORDER_CONSTANT, value=(20, 20, 20))
            for t in row
        ]
        rows.append(np.hstack(padded))
    grid = np.vstack(rows)
    preview_path = out_root / "preview.jpg"
    cv2.imwrite(str(preview_path), grid,
                [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    logger.info("Preview: %s", preview_path)


def build(args: argparse.Namespace) -> None:
    src_root: Path = args.source.resolve()
    out_root: Path = args.output.resolve()
    img_out = out_root / "images" / "val"
    lbl_out = out_root / "labels" / "val"
    img_out.mkdir(parents=True, exist_ok=True)
    lbl_out.mkdir(parents=True, exist_ok=True)

    transform = A.Compose(outdoor_eval_transforms())
    pairs: List[tuple[np.ndarray, np.ndarray]] = []
    n_written = 0

    for split in args.splits:
        images = discover_images(src_root / "images" / split)
        if not images:
            logger.warning("Sin imágenes en %s", src_root / "images" / split)
            continue
        for i, img_path in enumerate(images):
            img = cv2.imread(str(img_path))
            if img is None:
                logger.warning("No se pudo leer %s", img_path)
                continue
            if hasattr(transform, "set_random_seed"):
                transform.set_random_seed(args.seed + n_written)

            aug = transform(image=img)["image"]

            stem = f"{split}_{img_path.stem}"
            dst_img = img_out / f"{stem}.jpg"
            cv2.imwrite(str(dst_img), aug,
                        [int(cv2.IMWRITE_JPEG_QUALITY), 90])

            src_lbl = src_root / "labels" / split / f"{img_path.stem}.txt"
            dst_lbl = lbl_out / f"{stem}.txt"
            if src_lbl.is_file():
                shutil.copy2(src_lbl, dst_lbl)
            else:
                dst_lbl.write_text("", encoding="utf-8")

            if args.preview and len(pairs) < args.preview_images:
                pairs.append((img, aug))
            n_written += 1

    if n_written == 0:
        raise SystemExit(f"Sin imágenes procesadas desde {src_root}")

    write_data_yaml(out_root)
    if args.preview:
        write_preview(out_root, pairs)
    logger.info("Eval difícil listo: %d imágenes → %s", n_written, out_root)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Genera datasets/REBA_outdoor_eval (validación difícil)"
    )
    p.add_argument(
        "--source", type=Path,
        default=PROJECT_ROOT / "datasets" / "REBA_synthetic",
    )
    p.add_argument(
        "--output", type=Path,
        default=PROJECT_ROOT / "datasets" / "REBA_outdoor_eval",
    )
    p.add_argument(
        "--splits", nargs="+", default=["val", "test"],
        help="Splits del origen que se degradan (van todos a images/val)",
    )
    p.add_argument("--seed", type=int, default=123)
    p.add_argument("--preview", action="store_true",
                   help="Escribe preview.jpg con pares antes/después")
    p.add_argument("--preview-images", type=int, default=8)
    return p.parse_args(argv)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    build(parse_args())
