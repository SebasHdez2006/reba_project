#!/usr/bin/env python3
"""REBA — Convierte las imágenes de Wikimedia Commons
(datasets/REBA_commons_unicel/images) en recortes 4:3 degradados estilo ESP32
dentro de datasets/REBA_cls_esp32/train_extra/unicel/wm_*_x<k>.jpg.

Cada imagen se trata como un recorte del objeto: cover-crop a 4:3,
resize 320x240, degradación OV2640 (esp32_degrade). VARIANTES copias con
crops/jitter distintos por imagen.

Uso:
    python ai_pipeline/commons_to_train_extra.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ai_pipeline"))

from build_esp32_cls_dataset import esp32_degrade  # noqa: E402

SRC = ROOT / "datasets" / "REBA_commons_unicel" / "images"
DST = ROOT / "datasets" / "REBA_cls_esp32" / "train_extra" / "unicel"
SEED = 20261124
VARIANTS = 2  # variantes por imagen (jitter de crop + degradación distinta)


def cover_crop_43(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Crop 4:3 con jitter: la variante x0 es center-crop; el resto toma un
    crop 4:3 ligeramente desplazado/escalado."""
    h, w = img.shape[:2]
    tw = min(w, int(h * 4 / 3))
    th = int(tw * 3 / 4)
    if th > h:
        th, tw = h, int(h * 4 / 3)
    if rng.integers(2) == 0:
        x0, y0 = (w - tw) // 2, (h - th) // 2
    else:
        scale = float(rng.uniform(0.75, 1.0))
        tw2, th2 = int(tw * scale), int(th * scale)
        x0 = int(rng.integers(0, w - tw2 + 1))
        y0 = int(rng.integers(0, h - th2 + 1))
        tw, th = tw2, th2
    return img[y0:y0 + th, x0:x0 + tw]


def main() -> None:
    DST.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(SEED)
    n = 0
    for p in sorted(SRC.glob("*.jpg")):
        img = cv2.imread(str(p))
        if img is None or min(img.shape[:2]) < 120:
            continue
        for k in range(VARIANTS):
            crop = cover_crop_43(img, rng)
            crop = cv2.resize(crop, (320, 240), interpolation=cv2.INTER_AREA)
            crop = esp32_degrade(crop, rng)
            cv2.imwrite(str(DST / f"{p.stem}_x{k}.jpg"), crop,
                        [cv2.IMWRITE_JPEG_QUALITY, 90])
            n += 1
    print(f"{n} recortes -> {DST}")


if __name__ == "__main__":
    main()
