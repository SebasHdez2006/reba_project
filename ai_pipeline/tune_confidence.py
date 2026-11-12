#!/usr/bin/env python3
"""
REBA — Barrido del umbral de confianza de inferencia.

Ejecuta `YOLO.val()` sobre un split a distintos umbrales `conf` y reporta
Precisión / Recall / F1 por punto de operación. Sirve para elegir
`INFERENCE_CONFIDENCE` en `.env` sobre datos difíciles (por defecto el
conjunto `REBA_outdoor_eval`, no el sintético limpio).

Nota: mAP50 es (casi) independiente del umbral — integra sobre toda la curva.
Lo que cambia con `conf` es el punto de operación P/R/F1 que verá el servidor.

Uso (desde la raíz REBA/, con el venv activo):
    python ai_pipeline/tune_confidence.py
    python ai_pipeline/tune_confidence.py --model models/best.pt --confs 0.15 0.25 0.35 0.45 0.55
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Optional, Sequence

from ultralytics import YOLO

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASETS_ROOT = PROJECT_ROOT / "datasets"


def eval_at_conf(model: YOLO, data_yaml: Path, split: str, conf: float,
                 imgsz: int, batch: int, device: str) -> dict:
    result = model.val(
        data=str(data_yaml), split=split, conf=conf,
        imgsz=imgsz, batch=batch, device=device,
        verbose=False, plots=False,
    )
    box = getattr(result, "box", result)
    p, r = float(box.mp), float(box.mr)
    f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
    return {"conf": conf, "precision": p, "recall": r,
            "f1": f1, "mAP50": float(box.map50)}


def main(argv: Optional[Sequence[str]] = None) -> None:
    p = argparse.ArgumentParser(
        description="Barrido de confianza → recomienda INFERENCE_CONFIDENCE"
    )
    p.add_argument("--model", type=Path,
                   default=PROJECT_ROOT / "models" / "best.pt")
    p.add_argument("--data", type=Path,
                   default=DATASETS_ROOT / "REBA_outdoor_eval" / "data.yaml",
                   help="data.yaml del conjunto difícil")
    p.add_argument("--split", type=str, default="val")
    p.add_argument("--confs", type=float, nargs="+",
                   default=[0.10, 0.15, 0.20, 0.25, 0.30, 0.35,
                            0.40, 0.45, 0.50, 0.55, 0.60])
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--batch", type=int, default=8)
    p.add_argument("--device", type=str, default="cpu")
    args = p.parse_args(argv)

    if not args.data.is_file():
        raise SystemExit(f"No existe data.yaml: {args.data}")
    model = YOLO(str(args.model))

    rows: List[dict] = []
    for conf in args.confs:
        m = eval_at_conf(model, args.data, args.split, conf,
                         args.imgsz, args.batch, args.device)
        rows.append(m)
        print(f"conf={m['conf']:.2f}  P={m['precision']:.3f}  "
              f"R={m['recall']:.3f}  F1={m['f1']:.3f}  "
              f"mAP50={m['mAP50']:.3f}")

    best = max(rows, key=lambda r: r["f1"])
    print(f"\nRecomendado INFERENCE_CONFIDENCE={best['conf']:.2f} "
          f"(máximo F1={best['f1']:.3f} en {args.data.parent.name}/{args.split})")
    print("Actualiza .env:  INFERENCE_CONFIDENCE=...")


if __name__ == "__main__":
    main()
