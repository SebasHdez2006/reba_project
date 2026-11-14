#!/usr/bin/env python3
"""
REBA — Estadísticas del dataset combinado: imágenes e instancias por clase
y por origen (dataset), para train/val/test.

Uso:
    python ai_pipeline/dataset_stats.py
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Dict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASETS_ROOT = PROJECT_ROOT / "datasets"
CLASSES = ("plastico", "tela", "organico", "unicel", "metal")
# Auto-descubre datasets/REBA_* con labels (excluye derivados/audit)
DATASET_DIRS = tuple(
    sorted(p.name for p in DATASETS_ROOT.glob("REBA_*")
           if p.is_dir() and p.name not in
           ("REBA_outdoor", "REBA_outdoor_eval"))
)


def count_dataset(ds_dir: Path) -> Dict[str, object]:
    """Cuenta imágenes y cajas por clase/split en un dataset YOLO."""
    per_split: Dict[str, Dict[str, int]] = {}
    box_counts: Dict[str, Dict[int, int]] = {}
    img_counts: Dict[str, int] = {}
    for split in ("train", "val", "test"):
        lbl_dir = ds_dir / "labels" / split
        img_dir = ds_dir / "images" / split
        if not lbl_dir.is_dir():
            continue
        files = list(lbl_dir.glob("*.txt"))
        n_with_boxes = 0
        for lbl in files:
            counts = box_counts.setdefault(split, defaultdict(int))
            n_boxes = 0
            for ln in lbl.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines():
                parts = ln.split()
                if len(parts) != 5:
                    continue
                try:
                    cid = int(float(parts[0]))
                except ValueError:
                    continue
                counts[cid] += 1
                n_boxes += 1
            if n_boxes:
                n_with_boxes += 1
        img_counts[split] = len(files)
        per_split[split] = {"labels": len(files),
                            "with_boxes": n_with_boxes}
    return {"splits": per_split, "img_counts": img_counts,
            "box_counts": box_counts}


def main() -> None:
    # Tabla 1: imágenes por dataset/split
    print("=== Imágenes (labels) por origen y split ===")
    print(f"{'dataset':<26}{'train':>7}{'val':>6}{'test':>7}{'total':>7}")
    totals = defaultdict(int)
    for name in DATASET_DIRS:
        ds = DATASETS_ROOT / name
        if not ds.is_dir():
            continue
        stats = count_dataset(ds)
        row = []
        for split in ("train", "val", "test"):
            n = stats["img_counts"].get(split, 0)
            row.append(n)
            totals[split] += n
        print(f"{name:<26}{row[0]:>7}{row[1]:>6}{row[2]:>7}"
              f"{sum(row):>7}")
    print(f"{'TOTAL':<26}{totals['train']:>7}{totals['val']:>6}"
          f"{totals['test']:>7}{sum(totals.values()):>7}")

    # Tabla 2: instancias (cajas) por clase en TRAIN combinado
    print("\n=== Instancias (cajas) por clase — solo train ===")
    print(f"{'dataset':<26}" + "".join(f"{c:>10}" for c in CLASSES)
          + f"{'total':>8}")
    grand = defaultdict(int)
    for name in DATASET_DIRS:
        ds = DATASETS_ROOT / name
        if not ds.is_dir():
            continue
        stats = count_dataset(ds)
        counts = stats["box_counts"].get("train", {})
        vals = [counts.get(i, 0) for i in range(len(CLASSES))]
        for i, v in enumerate(vals):
            grand[i] += v
        print(f"{name:<26}" + "".join(f"{v:>10}" for v in vals)
              + f"{sum(vals):>8}")
    print(f"{'TOTAL':<26}" + "".join(f"{grand[i]:>10}" for i in range(5))
          + f"{sum(grand.values()):>8}")

    # Tabla 3: instancias por clase en val+test combinados (eval)
    print("\n=== Instancias (cajas) por clase — val+test combinados ===")
    print(f"{'dataset':<26}" + "".join(f"{c:>10}" for c in CLASSES)
          + f"{'total':>8}")
    for name in DATASET_DIRS:
        ds = DATASETS_ROOT / name
        if not ds.is_dir():
            continue
        stats = count_dataset(ds)
        counts = defaultdict(int)
        for split in ("val", "test"):
            for cid, n in stats["box_counts"].get(split, {}).items():
                counts[cid] += n
        vals = [counts.get(i, 0) for i in range(len(CLASSES))]
        print(f"{name:<26}" + "".join(f"{v:>10}" for v in vals)
              + f"{sum(vals):>8}")

    # Resumen JSON para reportes
    out = PROJECT_ROOT / "reports" / "dataset_stats.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"classes": CLASSES,
                               "datasets": {
                                   n: count_dataset(DATASETS_ROOT / n)
                                   for n in DATASET_DIRS
                                   if (DATASETS_ROOT / n).is_dir()
                               }}, ensure_ascii=False, indent=2,
                              default=str), encoding="utf-8")
    print(f"\nJSON -> {out}")


if __name__ == "__main__":
    main()
