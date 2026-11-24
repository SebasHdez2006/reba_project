#!/usr/bin/env python3
"""
REBA — Precisión POR FOTO estilo ESP32 (métrica del objetivo >=85 %).

Usa el MISMO motor que el servidor (server.inference.InferenceEngine) sobre
los splits de datasets/REBA_cls_esp32 (cada imagen 320x240 = un objeto, como
un disparo del HC-SR04). Predicción de la foto = clase de la detección de
mayor confianza; sin detección = `fondo`.

  accuracy  = aciertos / fotos con objeto (plastico|unicel|metal)
  fondo_ok  = fotos sin objeto que quedaron sin detección

Uso:
    python ai_pipeline/eval_esp32_cls.py --mode hybrid
    python ai_pipeline/eval_esp32_cls.py --mode cls                 # solo clasificador (segundos)
    python ai_pipeline/eval_esp32_cls.py --mode detector            # solo YOLO
    python ai_pipeline/eval_esp32_cls.py --mode detector --stretch  # server viejo (4:3 -> 640x640)
    python ai_pipeline/eval_esp32_cls.py --mode hybrid --cls-model models/otro.onnx --splits test_agua
    python ai_pipeline/eval_esp32_cls.py --data datasets --splits REBA_cls_esp32_real  # fotos reales ESP32
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.config import Settings  # noqa: E402
from server.inference import CLS_BACKGROUND, InferenceEngine  # noqa: E402

DATA = ROOT / "datasets" / "REBA_cls_esp32"
SPLITS = ["test_agua", "test_tierra", "test_agua_limpio", "test_tierra_limpio"]


def evaluate(engine: InferenceEngine, split_dir: Path, stretch: bool) -> dict:
    classes = list(engine.class_names)
    conf = {t: Counter() for t in classes + [CLS_BACKGROUND]}
    for cdir in sorted(p for p in split_dir.iterdir() if p.is_dir()):
        for img_path in sorted(cdir.glob("*.jpg")):
            img = cv2.imread(str(img_path))
            if stretch:
                img = cv2.resize(img, (640, 640), interpolation=cv2.INTER_LINEAR)
            dets = engine.predict(img).detections
            pred = max(dets, key=lambda d: d.confidence).class_name if dets else CLS_BACKGROUND
            conf[cdir.name][pred] += 1
    n_obj = sum(sum(conf[c].values()) for c in classes)
    hits = sum(conf[c][c] for c in classes)
    per_class = {c: round(conf[c][c] / max(1, sum(conf[c].values())), 4) for c in classes}
    n_bg = sum(conf[CLS_BACKGROUND].values())
    return {
        "n_obj": n_obj,
        "accuracy": round(hits / max(1, n_obj), 4),
        "macro_recall": round(sum(per_class.values()) / len(classes), 4),
        "per_class_recall": per_class,
        "n_fondo": n_bg,
        "fondo_ok": round(conf[CLS_BACKGROUND][CLS_BACKGROUND] / max(1, n_bg), 4),
        "confusion": {t: dict(v) for t, v in conf.items()},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["hybrid", "detector", "cls"], default="hybrid",
                    help="cls = solo clasificador (mismas clases que hybrid, sin cajas; rápido)")
    ap.add_argument("--cls-model", default="models/cls_esp32.onnx")
    ap.add_argument("--cls-conf", type=float, default=None)
    ap.add_argument("--stretch", action="store_true", help="emula el server previo (4:3 -> 640x640)")
    ap.add_argument("--splits", nargs="+", default=SPLITS)
    ap.add_argument("--data", default=str(DATA), help="carpeta con <split>/<clase>/*.jpg")
    ap.add_argument("--out", default=None, help="JSON de salida")
    args = ap.parse_args()

    kw = {"cls_model_path": args.cls_model if args.mode != "detector" else ""}
    if args.mode == "cls":
        kw["model_path"] = "models/__sin_detector__.onnx"
    if args.cls_conf is not None:
        kw["cls_confidence"] = args.cls_conf
    engine = InferenceEngine(Settings(**kw))
    if args.mode != "detector" and engine.cls_session is None:
        sys.exit(f"Clasificador no cargado: {args.cls_model}")

    report = {"mode": args.mode, "stretch": args.stretch, "cls_model": kw["cls_model_path"],
              "cls_conf": engine.settings.cls_confidence, "splits": {}}
    print(f"mode={args.mode} stretch={args.stretch} cls={kw['cls_model_path'] or '-'}")
    print(f"{'split':20s} {'n':>5s} {'acc':>6s} {'macro':>6s}  plastico unicel metal | fondo_ok")
    for split in args.splits:
        r = evaluate(engine, Path(args.data) / split, args.stretch)
        report["splits"][split] = r
        pc = r["per_class_recall"]
        print(f"{split:20s} {r['n_obj']:5d} {r['accuracy']:6.3f} {r['macro_recall']:6.3f}  "
              f"{pc['plastico']:8.3f} {pc['unicel']:6.3f} {pc['metal']:5.3f} | {r['fondo_ok']:.3f} (n={r['n_fondo']})")
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
