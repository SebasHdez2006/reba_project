#!/usr/bin/env python3
"""
REBA — Cosechas EXTRA para el dataset de clasificación (variantes adicionales
de FloatWaste train, sin tocar el dataset ya construido).

El builder original emite VARIANTS[(agua,0)]=1 para plastico y 4 para unicel;
aquí se generan variantes adicionales (otro jitter/frac/degradación, otro seed)
con el sufijo _x<k>, pensadas para el head DINO/LR:

  plastico: +1 variante (x1)
  unicel:   +3 variantes (x1..x3)

Salida: datasets/REBA_cls_esp32/train_extra/<clase>/floatwaste_<stem>_<bi>_x<k>.jpg
(train_extra es un split auxiliar: lo consume train_esp32_cls_dino.py /
tune_cls_head.py; ultralytics lo ignora).

Uso:
    python ai_pipeline/augment_esp32_cls_extra.py
"""

from __future__ import annotations

import random
import sys
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ai_pipeline"))

import build_esp32_cls_dataset as B  # noqa: E402

EXTRA_VARIANTS = {"plastico": 1, "unicel": 3}
SEED = 20261124


def main() -> None:
    out_dir = B.OUT / "train_extra"
    counts: Counter = Counter()
    exclude = {B.vid(p.stem) for p in (B.DS / "REBA_real_val" / "images" / "val").glob("*")}
    rng = random.Random(SEED)
    nrng = np.random.default_rng(SEED)
    for p, lbl in B.iter_images("REBA_floatwaste", "train"):
        if B.vid(p.stem) in exclude:
            continue
        img = cv2.imread(str(p))
        if img is None:
            continue
        h, w = img.shape[:2]
        boxes = B.load_boxes(lbl, w, h)
        for bi, b in enumerate(boxes):
            bw, bh = b[3] - b[1], b[4] - b[2]
            if max(bw / w, bh / h) < B.MIN_REL or max(bw, bh) < B.MIN_PX:
                continue
            cls = B.CLASSES[b[0]]
            n_extra = EXTRA_VARIANTS.get(cls, 0)
            if not n_extra:
                continue
            made = 0
            for _ in range(n_extra * 4):
                if made >= n_extra:
                    break
                win = B.crop_window(b, w, h, rng.uniform(0.35, 0.95), rng, 0.35)
                if not B.clean_window(win, b, boxes):
                    continue
                frame = B.esp32_degrade(B.to_frame(img, win), nrng)
                d = out_dir / cls
                d.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(d / f"floatwaste_{p.stem}_{bi}_x{made+1}.jpg"),
                            frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
                counts[cls] += 1
                made += 1
    print(dict(counts))


if __name__ == "__main__":
    main()
