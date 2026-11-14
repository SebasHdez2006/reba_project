"""Oversampling de clases minoritarias via hardlinks (FASE 2.5 / EXP-4).

Crea `datasets/REBA_oversample/{images,labels}/train` con copias
duricas (hardlinks NTFS — no duplican disco) de las imagenes que
contienen clases minoritarias, repetidas `factor` veces.

Uso tipico (tras evaluar v5 y justificarlo con metricas):
  python ai_pipeline/balance_oversample.py --classes unicel,metal --factor 2
  # luego registrar ("REBA_oversample", "oversample") en OPTIONAL_TRAIN_SOURCES
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATASETS = ROOT / "datasets"
OUT = DATASETS / "REBA_oversample"
CLASS_NAMES = ["plastico", "unicel", "metal"]

# Fuentes candidatas (train solamente)
SOURCES = ["REBA_floatwaste", "REBA_taco", "REBA_ecosort",
           "REBA_copypaste", "REBA_synthetic_boost", "REBA_synthetic_boost2"]


def labels_with_class(lbl_dir: Path, wanted: set[int]) -> list[Path]:
    hits = []
    for lp in sorted(lbl_dir.glob("*.txt")):
        try:
            cls_ids = {int(ln.split()[0]) for ln in
                       lp.read_text().strip().splitlines() if ln.split()}
        except (ValueError, IndexError):
            continue
        if cls_ids & wanted:
            hits.append(lp)
    return hits


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--classes", default="unicel,metal",
                    help="clases a oversamplear (CSV)")
    ap.add_argument("--factor", type=int, default=2,
                    help="copias extra por imagen (total = factor+1)")
    ap.add_argument("--max-per-class", type=int, default=1500,
                    help="tope de imagenes por clase")
    args = ap.parse_args()

    wanted_names = [c.strip() for c in args.classes.split(",") if c.strip()]
    wanted = {CLASS_NAMES.index(c) for c in wanted_names
              if c in CLASS_NAMES}
    if not wanted:
        print("ERROR: ninguna clase valida", file=sys.stderr)
        return 1

    img_dir = OUT / "images" / "train"
    lbl_dir = OUT / "labels" / "train"
    img_dir.mkdir(parents=True, exist_ok=True)
    lbl_dir.mkdir(parents=True, exist_ok=True)

    counts = defaultdict(int)
    made = 0
    for ds in SOURCES:
        d = DATASETS / ds
        if not d.is_dir():
            continue
        lbl_root = d / "labels" / "train"
        if not lbl_root.is_dir():
            continue
        img_root = d / "images" / "train"
        for lp in labels_with_class(lbl_root, wanted):
            stem = lp.stem
            # clase(s) objetivo presentes en este label
            cls_ids = set()
            for ln in lp.read_text().strip().splitlines():
                p = ln.split()
                if p:
                    cls_ids.add(int(p[0]))
            hit = cls_ids & wanted
            if not hit:
                continue
            for c in hit:
                if counts[CLASS_NAMES[c]] >= args.max_per_class:
                    continue
                img_src = None
                for ext in (".jpg", ".jpeg", ".png"):
                    cand = img_root / f"{stem}{ext}"
                    if cand.exists():
                        img_src = cand
                        break
                if img_src is None:
                    continue
                import os
                import shutil
                for k in range(args.factor + 1):
                    tag = f"os_{ds}_{stem}_c{c}_k{k}"
                    for src, dst in (
                            (img_src, img_dir / f"{tag}{img_src.suffix}"),
                            (lp, lbl_dir / f"{tag}.txt")):
                        if not dst.exists():
                            try:
                                os.link(src, dst)
                            except OSError:
                                shutil.copy2(src, dst)
                counts[CLASS_NAMES[c]] += 1
                made += 1
                if made % 200 == 0:
                    print(f"  {made} links... {dict(counts)}", flush=True)

    names = "\n".join(f"  {i}: {n}" for i, n in enumerate(CLASS_NAMES))
    (OUT / "data.yaml").write_text(
        f"path: {OUT.as_posix()}\ntrain: images/train\nval: images/train\n"
        f"names:\n{names}\n", encoding="utf-8")
    print(f"Oversample creado: {made} imagenes-extra, {dict(counts)}")
    print(f"Salida: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
