#!/usr/bin/env python3
"""Evalúa weights/last.pt de un run sobre el split val -> JSON por clase.

Lo usa train_monitor.py una vez por época (en subprocess, para no cargar
ultralytics dentro de la GUI). El val es el mismo split sintético limpio
que usa results.csv -> las métricas son comparables con las agregadas.

Salida JSON:
  {"classes": {nombre: {P,R,mAP50,mAP50-95,images,instances}, ..., "all": ...},
   "speed":   {"preprocess": ms, "inference": ms, "loss": ms, "postprocess": ms}}

Uso:
    python ai_pipeline/val_snapshot.py runs/detect/REBA_outdoor_v5
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_YAML = ROOT / "datasets" / "REBA_outdoor" / "data.yaml"
# Mismo split que build_dataset_yaml(): val = REBA_synthetic/images/val
VAL_LABELS = ROOT / "datasets" / "REBA_synthetic" / "labels" / "val"
# Fuera de runs/detect para no interferir con el auto-detect de
# evaluate_outdoor.py (que busca el run REBA_outdoor_* mas reciente).
VAL_PROJECT = ROOT / "runs" / "_monitor_val"


def count_label_stats(labels_dir: Path) -> tuple[dict[int, int], dict[int, int], int]:
    """(instances por clase, imagenes-que-contienen-clase, n imagenes)."""
    inst: dict[int, int] = {}
    imgs: dict[int, int] = {}
    n_files = 0
    if not labels_dir.is_dir():
        return inst, imgs, n_files
    for lf in labels_dir.glob("*.txt"):
        n_files += 1
        seen: set[int] = set()
        try:
            text = lf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                cid = int(line.split()[0])
            except (ValueError, IndexError):
                continue
            inst[cid] = inst.get(cid, 0) + 1
            seen.add(cid)
        for cid in seen:
            imgs[cid] = imgs.get(cid, 0) + 1
    return inst, imgs, n_files


def main() -> int:
    if len(sys.argv) < 2:
        print("uso: val_snapshot.py <run_dir>", file=sys.stderr)
        return 2
    run_dir = Path(sys.argv[1])
    weights = run_dir / "weights" / "last.pt"
    if not weights.is_file():
        print(json.dumps({"error": f"no existe {weights}"}))
        return 1
    if not DATA_YAML.is_file():
        print(json.dumps({"error": f"no existe {DATA_YAML}"}))
        return 1

    from ultralytics import YOLO

    model = YOLO(str(weights))
    metrics = model.val(
        data=str(DATA_YAML),
        split="val",
        device="cpu",
        imgsz=640,
        batch=8,
        workers=0,
        plots=False,
        project=str(VAL_PROJECT),
        name="val",
        exist_ok=True,
    )
    box = metrics.box

    def _mean(scalar_attr: str, arr_attr: str) -> float:
        v = getattr(box, scalar_attr, None)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
        arr = getattr(box, arr_attr, None)
        return float(arr.mean()) if arr is not None else 0.0

    names = getattr(metrics, "names", None) or model.names or {}
    inst_counts, img_counts, n_val_imgs = count_label_stats(VAL_LABELS)

    classes: dict[str, dict[str, float]] = {}
    p = getattr(box, "p", None)
    n_cls = len(p) if p is not None else 0
    for i in range(n_cls):
        nm = names.get(i) if isinstance(names, dict) else str(i)
        classes[nm] = {
            "P": float(box.p[i]),
            "R": float(box.r[i]),
            "mAP50": float(box.ap50[i]),
            "mAP50-95": float(box.ap[i]),
            "images": img_counts.get(i, 0),
            "instances": inst_counts.get(i, 0),
        }
    classes["all"] = {
        "P": _mean("mp", "p"),
        "R": _mean("mr", "r"),
        "mAP50": _mean("map50", "ap50"),
        "mAP50-95": _mean("map", "ap"),
        "images": n_val_imgs,
        "instances": sum(inst_counts.values()),
    }

    speed = getattr(metrics, "speed", None) or {}
    speed_out = {k: float(v) for k, v in speed.items()} if speed else {}
    print(json.dumps({"classes": classes, "speed": speed_out}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
