#!/usr/bin/env python3
"""
REBA — Auditoría cuantitativa de contenido dentro de las cajas por clase.

Para cada clase y dataset, muestrea imágenes con cajas de esa clase, extrae
los píxeles dentro de cada caja y reporta:

- área media de la caja (fracción de imagen)
- color medio BGR de la región
- % de píxeles "agua" (azul dominante: B > R+15 y B > G)
- % de píxeles oscuros (<40 en los 3 canales)

Una clase cuyas cajas contengan mucho azul = la caja incluye fondo de agua
(box mal ajustada o clase mal definida). Una clase con casi todo azul =
las etiquetas apuntan al agua, no al objeto.

Uso:
    python ai_pipeline/audit_pixels.py --class-id 2 --samples 60
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASETS_ROOT = PROJECT_ROOT / "datasets"
CLASSES = ("plastico", "tela", "organico", "unicel", "metal")
DATASET_DIRS = (
    "REBA_synthetic", "REBA_synthetic_boost", "REBA_synthetic_boost2",
    "REBA_taco", "REBA_ecosort",
)


def yolo_to_xyxy(vals: Sequence[float], w: int, h: int):
    _, xc, yc, bw, bh = (float(v) for v in vals)
    return ((xc - bw / 2) * w, (yc - bh / 2) * h,
            (xc + bw / 2) * w, (yc + bh / 2) * h)


def region_stats(img: np.ndarray, x1, y1, x2, y2) -> Dict[str, float]:
    h, w = img.shape[:2]
    x1, y1 = int(max(0, x1)), int(max(0, y1))
    x2, y2 = int(min(w, x2)), int(min(h, y2))
    if x2 - x1 < 4 or y2 - y1 < 4:
        return {}
    reg = img[y1:y2, x1:x2].reshape(-1, 3).astype(np.float32)
    b, g, r = reg[:, 0], reg[:, 1], reg[:, 2]
    water = ((b > r + 15) & (b > g)).mean()
    dark = ((b < 40) & (g < 40) & (r < 40)).mean()
    return {
        "area_frac": ((x2 - x1) * (y2 - y1)) / (w * h),
        "mean_b": float(b.mean()), "mean_g": float(g.mean()), "mean_r": float(r.mean()),
        "water_frac": float(water), "dark_frac": float(dark),
        "n_px": int(reg.shape[0]),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--class-id", type=int, required=True)
    ap.add_argument("--class-name", type=str, default=None)
    ap.add_argument("--samples", type=int, default=60,
                    help="Imágenes máx por dataset")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    cid = args.class_id
    if args.class_name:
        cid = CLASSES.index(args.class_name)

    rng = np.random.default_rng(args.seed)
    print(f"=== Contenido de cajas clase {cid} ({CLASSES[cid]}) ===\n")
    print(f"{'dataset':<24}{'imgs':>6}{'cajas':>7}{'area%':>8}"
          f"{'meanBGR':>22}{'agua%':>8}{'oscuro%':>8}")

    for ds_name in DATASET_DIRS:
        ds = DATASETS_ROOT / ds_name
        if not ds.is_dir():
            continue
        box_stats = []
        n_imgs = 0
        for split in ("train", "val", "test"):
            lbl_dir = ds / "labels" / split
            img_dir = ds / "images" / split
            if not lbl_dir.is_dir() or not img_dir.is_dir():
                continue
            # Filtrar primero a labels que contienen la clase (clases raras
            # quedarían invisibles con muestreo sobre todos los labels).
            files = []
            for lbl in sorted(lbl_dir.glob("*.txt")):
                try:
                    if any(
                        len(ln.split()) == 5 and int(float(ln.split()[0])) == cid
                        for ln in lbl.read_text(
                            encoding="utf-8", errors="replace").splitlines()
                        if ln.strip()
                    ):
                        files.append(lbl)
                except Exception:
                    continue
            if len(files) > args.samples:
                idx = rng.choice(len(files), args.samples, replace=False)
                files = [files[i] for i in sorted(idx)]
            for lbl in files:
                lines = [
                    ln.split() for ln in
                    lbl.read_text(encoding="utf-8", errors="replace").splitlines()
                    if ln.strip()
                ]
                hits = [p for p in lines
                        if len(p) == 5 and int(float(p[0])) == cid]
                if not hits:
                    continue
                img_file = img_dir / f"{lbl.stem}.jpg"
                if not img_file.is_file():
                    img_file = img_dir / f"{lbl.stem}.png"
                if not img_file.is_file():
                    continue
                img = cv2.imread(str(img_file))
                if img is None:
                    continue
                h, w = img.shape[:2]
                n_imgs += 1
                for p in hits:
                    x1, y1, x2, y2 = yolo_to_xyxy([float(v) for v in p], w, h)
                    st = region_stats(img, x1, y1, x2, y2)
                    if st:
                        box_stats.append(st)
        if not box_stats:
            continue
        area = np.mean([s["area_frac"] for s in box_stats]) * 100
        mb = np.mean([s["mean_b"] for s in box_stats])
        mg = np.mean([s["mean_g"] for s in box_stats])
        mr = np.mean([s["mean_r"] for s in box_stats])
        water = np.mean([s["water_frac"] for s in box_stats]) * 100
        dark = np.mean([s["dark_frac"] for s in box_stats]) * 100
        print(f"{ds_name:<24}{n_imgs:>6}{len(box_stats):>7}{area:>7.2f}%"
              f"({mb:5.0f},{mg:5.0f},{mr:5.0f}){water:>7.1f}%{dark:>7.1f}%")


if __name__ == "__main__":
    main()
