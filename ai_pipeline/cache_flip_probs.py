#!/usr/bin/env python3
"""REBA — Probs con imagen volteada horizontalmente (TTA flip) por modelo ONNX
y split -> _feat/<split>_<stem>probs_flip.npy. El meta-LR las combina con las
probs normales."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from server.inference import cls_preprocess  # noqa: E402

DATA = ROOT / "datasets" / "REBA_cls_esp32"
FEAT = DATA / "_feat"


def run(model: str, splits: list[str]) -> None:
    import onnxruntime as ort
    sess = ort.InferenceSession(model, providers=["CPUExecutionProvider"])
    inp = sess.get_inputs()[0]
    th, tw = (int(v) for v in inp.shape[2:4])
    names = ast.literal_eval(sess.get_modelmeta().custom_metadata_map["names"])
    order = [names[i] for i in sorted(names)]
    classes = sorted(d.name for d in (DATA / "train").iterdir() if d.is_dir())
    perm = np.array([order.index(c) for c in classes])
    tag = Path(model).stem
    for s in splits:
        out = FEAT / f"{s}_{tag}probs_flip.npy"
        if out.exists():
            continue
        paths = [p for c in classes for p in sorted((DATA / s / c).glob("*.jpg"))]
        P = np.empty((len(paths), len(order)), np.float32)
        for i, p in enumerate(paths):
            img = cv2.imread(str(p))
            blob = cls_preprocess(cv2.flip(img, 1), th, tw)
            P[i] = sess.run(None, {inp.name: blob})[0][0]
        np.save(out, P[:, perm])
        print(f"{s}: {P.shape} flip -> {out.name}", flush=True)


if __name__ == "__main__":
    for m in ["models/cls_esp32.onnx", "models/cls_dino.onnx",
              "models/cls_11s.onnx"]:
        run(str(ROOT / m), ["test_agua", "test_tierra",
                            "test_agua_limpio", "test_tierra_limpio"])
