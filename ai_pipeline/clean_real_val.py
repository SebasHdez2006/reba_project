"""Limpia REBA_real_val de imagenes con near-duplicados en el train.

FloatWaste reparte frames de video entre train/val; el split oficial no
garantiza desunion visual. Este script elimina de `REBA_real_val/val`
toda imagen cuyo dHash (hamming<=threshold) coincida con alguna imagen
de `REBA_floatwaste/train` (u otra fuente de train si se indica).

Solo borra imagenes+labels de real_val (nunca toca train).

Uso:
  python ai_pipeline/clean_real_val.py [--threshold 6]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
REAL_VAL = ROOT / "datasets" / "REBA_real_val" / "images" / "val"
REAL_VAL_LBL = ROOT / "datasets" / "REBA_real_val" / "labels" / "val"
TRAIN_DIRS = [
    ROOT / "datasets" / "REBA_floatwaste" / "images" / "train",
]
POP = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)


def dhash(path: Path) -> np.uint64 | None:
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    img = cv2.resize(img, (9, 8), interpolation=cv2.INTER_AREA)
    bits = img[:, 1:] > img[:, :-1]
    v = 0
    for b in bits.flatten():
        v = (v << 1) | int(b)
    return np.uint64(v)


def hashes_for(directory: Path, tag: str) -> tuple[list[Path], np.ndarray]:
    paths = sorted(p for p in directory.glob("*")
                   if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    hs = []
    for i, p in enumerate(paths):
        h = dhash(p)
        if h is not None:
            hs.append((p, h))
        if (i + 1) % 400 == 0:
            print(f"  {tag}: {i + 1}/{len(paths)}", flush=True)
    arr = np.array([h for _, h in hs], dtype=np.uint64)
    return [p for p, _ in hs], arr


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=int, default=6)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print("Hasheando train (referencia)...", flush=True)
    train_paths, train_h = [], []
    for d in TRAIN_DIRS:
        if not d.is_dir():
            continue
        ps, hs = hashes_for(d, d.parent.parent.name)
        train_paths += ps
        train_h.append(hs)
    train_h = np.concatenate(train_h) if train_h else np.empty(0, np.uint64)
    print(f"  {len(train_h)} hashes de train")

    print("Hasheando real_val...", flush=True)
    val_paths, val_h = hashes_for(REAL_VAL, "real_val")
    print(f"  {len(val_h)} hashes de val")

    tb = train_h.view(np.uint8).reshape(len(train_h), 8)
    vb = val_h.view(np.uint8).reshape(len(val_h), 8)
    contaminated = set()
    B = 200
    for s in range(0, len(val_h), B):
        e = min(s + B, len(val_h))
        xr = vb[s:e, None, :] ^ tb[None, :, :]
        dist = POP[xr].sum(axis=2)
        for bi in range(e - s):
            if (dist[bi] <= args.threshold).any():
                contaminated.add(s + bi)
        print(f"  val {e}/{len(val_h)}", flush=True)

    print(f"\nVal contaminadas: {len(contaminated)}/{len(val_paths)}")
    removed = 0
    for i in sorted(contaminated):
        p = val_paths[i]
        lp = REAL_VAL_LBL / (p.stem + ".txt")
        if not args.dry_run:
            p.unlink(missing_ok=True)
            lp.unlink(missing_ok=True)
        removed += 1
    if args.dry_run:
        print("DRY-RUN: no se borro nada")
    else:
        print(f"Eliminadas {removed} imagenes+labels de real_val")
        remain = len(list(REAL_VAL.glob('*.jpg'))) + \
            len(list(REAL_VAL.glob('*.png')))
        print(f"real_val restante: {remain} imagenes")


if __name__ == "__main__":
    sys.exit(main())
