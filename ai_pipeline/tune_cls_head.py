#!/usr/bin/env python3
"""
REBA — Ajuste del head del clasificador DINO + ensemble con YOLOv8n-cls (v1).

Sobre las features cacheadas en datasets/REBA_cls_esp32/_feat/*.npz:
  1. Prueba variantes del head LR (feature cls|cls+mean x pesos natural|balanced
     x wd) y las reporta con CV agrupada + precisión en cada split de test.
  2. Corre el clasificador v1 (models/cls_esp32.onnx) sobre las mismas imágenes
     y evalúa el ensemble p = a·p_v1 + (1-a)·p_dino para varios a.
  3. Escribe todo en reports/esp32_cls/tune_head.json.

La selección se hace por CV/val; los tests se reportan tal cual (son el proxy
de campo, no se usan para elegir hiperparámetros del head salvo desempate).

Uso:
    python ai_pipeline/tune_cls_head.py
"""

from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ai_pipeline"))

from server.inference import cls_preprocess  # noqa: E402
import train_esp32_cls_dino as T  # noqa: E402

DATA = T.DATA
FEAT_DIR = T.FEAT_DIR
V1_ONNX = ROOT / "models" / "cls_esp32.onnx"
REPORT = ROOT / "reports" / "esp32_cls" / "tune_head.json"

SPLITS = ["test_agua", "test_tierra", "test_agua_limpio", "test_tierra_limpio"]
CLASSES = ["fondo", "metal", "plastico", "unicel"]
OBJ = [CLASSES.index(c) for c in ("plastico", "unicel", "metal")]
BG = CLASSES.index("fondo")

CONFIGS = [
    (v, w, wd)
    for v in ("cls", "cls+mean")
    for w in ("natural", "balanced")
    for wd in (1e-3, 1e-2, 1e-1)
]
ALPHAS = [0.0, 0.2, 0.4, 0.5, 0.6, 0.8, 1.0]


def softmax(z: np.ndarray) -> np.ndarray:
    z = z - z.max(1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(1, keepdims=True)


def metrics(y: np.ndarray, pred: np.ndarray) -> dict:
    obj = np.isin(y, OBJ)
    hits = (pred[obj] == y[obj]).sum()
    rec = {}
    for c in OBJ:
        m = y == c
        rec[CLASSES[c]] = float((pred[m] == c).mean()) if m.any() else 0.0
    bg = y == BG
    return {
        "acc": float(hits / max(1, obj.sum())),
        "macro": float(np.mean(list(rec.values()))),
        "per_class": rec,
        "fondo_ok": float((pred[bg] == BG).mean()) if bg.any() else 1.0,
    }


def v1_probs(split: str) -> tuple[np.ndarray, np.ndarray]:
    """Probs del YOLOv8n-cls ONNX sobre cada imagen del split (en orden
    split/<clase>/*.jpg ordenado = mismo orden que los npz)."""
    import onnxruntime as ort

    sess = ort.InferenceSession(str(V1_ONNX), providers=["CPUExecutionProvider"])
    inp = sess.get_inputs()[0]
    th, tw = (int(v) for v in inp.shape[2:4])
    import ast
    names = ast.literal_eval(sess.get_modelmeta().custom_metadata_map["names"])
    order = [names[i] for i in sorted(names)]
    paths, y = [], []
    for ci, c in enumerate(CLASSES):
        for p in sorted((DATA / split / c).glob("*.jpg")):
            paths.append(p)
            y.append(ci)
    probs = np.empty((len(paths), len(order)), np.float32)
    for i, p in enumerate(paths):
        blob = cls_preprocess(cv2.imread(str(p)), th, tw)
        probs[i] = sess.run(None, {inp.name: blob})[0][0]
    # alinear columnas a CLASSES
    idx = [order.index(c) for c in CLASSES]
    return probs[:, idx], np.array(y)


def head_probs(W, b, X: np.ndarray) -> np.ndarray:
    return softmax(X @ W.numpy() + b.numpy())


def main() -> None:
    train = {s: np.load(FEAT_DIR / f"{s}.npz") for s in T.TRAIN_SPLITS}
    X = np.concatenate([train[s]["X"] for s in T.TRAIN_SPLITS])
    y = np.concatenate([train[s]["y"] for s in T.TRAIN_SPLITS])
    groups = np.concatenate([train[s]["groups"] for s in T.TRAIN_SPLITS])
    tests = {s: np.load(FEAT_DIR / f"{s}.npz") for s in SPLITS}

    folds = T.group_kfold(groups, 5)
    report = {"configs": [], "ensemble": {}}

    best = None  # (cv_macro, key, probs_por_split, desc)
    for vname, wname, wd in CONFIGS:
        sl = T.VARIANTS[vname]
        Xv = X[:, sl]
        cw = (np.ones(len(CLASSES), np.float32) if wname == "natural"
              else T.balanced_weights(y, len(CLASSES)))
        # CV agrupada
        recs = []
        for idx in folds:
            tr = np.setdiff1d(np.arange(len(y)), idx)
            w, b = T.fit_lr(Xv[tr], y[tr], cw, wd)
            m, _ = T.macro_recall(y[idx], T.predict(w, b, Xv[idx]), len(CLASSES))
            recs.append(m)
        cv_macro = float(np.mean(recs))
        # refit completo + métricas por split
        w, b = T.fit_lr(Xv, y, cw, wd)
        split_probs = {s: head_probs(w, b, tests[s]["X"][:, sl]) for s in SPLITS}
        res = {s: metrics(tests[s]["y"], p.argmax(1)) for s, p in split_probs.items()}
        key = f"{vname}|{wname}|{wd:g}"
        report["configs"].append({"key": key, "cv_macro": cv_macro, "test": res})
        print(f"{key:28s} cv={cv_macro:.4f} agua={res['test_agua']['acc']:.3f} "
              f"tierra={res['test_tierra']['acc']:.3f} "
              f"uni={res['test_agua']['per_class']['unicel']:.3f}/"
              f"{res['test_tierra']['per_class']['unicel']:.3f}", flush=True)
        if best is None or cv_macro > best[0]:
            best = (cv_macro, key, split_probs)

    print(f"\nmejor head por CV: {best[1]} (cv={best[0]:.4f})")

    # v1 probs + ensemble
    for s in SPLITS:
        p1, yv = v1_probs(s)
        pd = best[2][s]
        base = metrics(yv, p1.argmax(1))
        rows = []
        for a in ALPHAS:
            pe = a * p1 + (1 - a) * pd
            m = metrics(yv, pe.argmax(1))
            rows.append({"alpha": a, **m})
        report["ensemble"][s] = {"v1_solo": base, "rows": rows}
        line = " ".join(f"a{a}:{r['acc']:.3f}" for a, r in zip(ALPHAS, rows))
        print(f"{s:20s} v1={base['acc']:.3f} | {line}", flush=True)

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False), "utf-8")
    print(f"-> {REPORT}")


if __name__ == "__main__":
    main()
