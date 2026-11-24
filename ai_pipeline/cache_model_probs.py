#!/usr/bin/env python3
"""REBA — Cachea probs de un clasificador ONNX (p.ej. v1 cls_esp32.onnx) sobre
los splits de datasets/REBA_cls_esp32 en _feat/probs_<modelo>_<split>.npz.
Permite re-ajustar el meta-head del ensemble sin re-correr el modelo."""
from __future__ import annotations

import argparse
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=str(ROOT / "models" / "cls_esp32.onnx"))
    ap.add_argument("--splits", nargs="*", default=["train_extra"])
    args = ap.parse_args()

    import onnxruntime as ort
    sess = ort.InferenceSession(args.model, providers=["CPUExecutionProvider"])
    inp = sess.get_inputs()[0]
    th, tw = (int(v) for v in inp.shape[2:4])
    names = ast.literal_eval(sess.get_modelmeta().custom_metadata_map["names"])
    order = [names[i] for i in sorted(names)]
    tag = Path(args.model).stem

    classes = sorted(d.name for d in (DATA / "train").iterdir() if d.is_dir())
    perm = np.array([order.index(c) for c in classes])

    for s in args.splits:
        out = FEAT / f"{s}_{tag}probs.npy"
        if out.exists():
            continue
        paths, ys = [], []
        for ci, c in enumerate(classes):
            for p in sorted((DATA / s / c).glob("*.jpg")):
                paths.append(p)
                ys.append(ci)
        if not paths:
            continue
        P = np.empty((len(paths), len(order)), np.float32)
        for i, p in enumerate(paths):
            blob = cls_preprocess(cv2.imread(str(p)), th, tw)
            P[i] = sess.run(None, {inp.name: blob})[0][0]
        np.save(out, P[:, perm])
        print(f"{s}: {P.shape} -> {out.name}", flush=True)


if __name__ == "__main__":
    main()
