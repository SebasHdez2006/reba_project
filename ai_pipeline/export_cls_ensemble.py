#!/usr/bin/env python3
"""
REBA — Entrena el head DINO (LR) + el meta-head del ensemble (LR sobre probs
concatenadas) y exporta los artefactos de despliegue:

  models/cls_dino.onnx   backbone DINOv2 + head + softmax
  models/cls_meta.npz    W/b del meta-LR sobre [p_m1, p_m2, ...]

El orden de los modelos en el meta es el de --models (debe coincidir con
CLS_MODEL_PATH del .env). Cada modelo aporta sus probs alineadas al orden
canónico sorted(clases).

Usa las features cacheadas en datasets/REBA_cls_esp32/_feat/*.npz (train, val
y opcionalmente train_extra u otros splits extra vía --extra-splits).

Uso:
    python ai_pipeline/export_cls_ensemble.py \
        --models models/cls_esp32.onnx,models/cls_dino.onnx
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ai_pipeline"))

from server.inference import cls_preprocess  # noqa: E402
import train_esp32_cls_dino as T  # noqa: E402

REPORT = ROOT / "reports" / "esp32_cls" / "ensemble_export.json"
TEST_SPLITS = ["test_agua", "test_tierra", "test_agua_limpio", "test_tierra_limpio"]


def sm(z: np.ndarray) -> np.ndarray:
    z = z - z.max(1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(1, keepdims=True)


def model_probs(onnx_path: Path, paths: list[Path], classes: list[str],
                splits: list[str]) -> np.ndarray:
    """Probs del modelo; usa la caché _feat/probs_<stem>_<split>.npz cuando
    cubre exactamente estos splits (en el orden canónico de split_paths)."""
    parts = []
    for s in splits:
        c = T.FEAT_DIR / f"{s}_{onnx_path.stem}probs.npy"
        if not c.exists():
            parts = None
            break
        parts.append(np.load(c))
    if parts is not None and sum(len(p) for p in parts) == len(paths):
        return np.concatenate(parts)
    import onnxruntime as ort

    sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    inp = sess.get_inputs()[0]
    th, tw = (int(v) for v in inp.shape[2:4])
    names = ast.literal_eval(sess.get_modelmeta().custom_metadata_map["names"])
    order = [names[i] for i in sorted(names)]
    perm = [order.index(c) for c in classes]
    P = np.empty((len(paths), len(order)), np.float32)
    for i, p in enumerate(paths):
        blob = cls_preprocess(cv2.imread(str(p)), th, tw)
        P[i] = sess.run(None, {inp.name: blob})[0][0]
    return P[:, perm]


def split_paths(classes: list[str], *splits: str) -> list[Path]:
    out = []
    for s in splits:
        for c in classes:
            out += sorted((T.DATA / s / c).glob("*.jpg"))
    return out


def metrics(y: np.ndarray, probs: np.ndarray, conf: float = 0.5) -> dict:
    pred = probs.argmax(1)
    conf_ok = probs.max(1) >= conf
    pred = np.where(conf_ok, pred, 0)  # baja conf -> fondo
    obj = y != 0
    rec = {}
    for c in range(1, probs.shape[1]):
        m = y == c
        rec[c] = float((pred[m] == c).mean()) if m.any() else 0.0
    bg = y == 0
    return {"acc": float((pred[obj] == y[obj]).sum() / max(1, obj.sum())),
            "macro": float(np.mean(list(rec.values()))),
            "fondo_ok": float((pred[bg] == 0).mean()) if bg.any() else 1.0,
            "per_class": rec}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="models/cls_esp32.onnx,models/cls_dino.onnx")
    ap.add_argument("--extra-splits", nargs="*", default=["train_extra"])
    ap.add_argument("--dino-wd", type=float, default=1e-2)
    ap.add_argument("--meta-wd", type=float, default=1e-2)
    ap.add_argument("--dino-variant", default="cls+mean", choices=list(T.VARIANTS))
    ap.add_argument("--dino-weights", default="balanced", choices=["natural", "balanced"])
    ap.add_argument("--meta-out", default=str(ROOT / "models" / "cls_meta.npz"))
    ap.add_argument("--export-dino", action="store_true")
    args = ap.parse_args()

    classes = sorted(d.name for d in (T.DATA / "train").iterdir() if d.is_dir())
    model_files = [ROOT / p.strip() for p in args.models.split(",")]
    splits = T.TRAIN_SPLITS + [s for s in args.extra_splits
                               if (T.FEAT_DIR / f"{s}.npz").exists()]
    data = T.load_features(splits)
    X, y, groups = data["X"], data["y"], data["groups"]
    print(f"train: X={X.shape} splits={splits}")

    # --- head DINO ---
    sl = T.VARIANTS[args.dino_variant]
    cw = (np.ones(len(classes), np.float32) if args.dino_weights == "natural"
          else T.balanced_weights(y, len(classes)))
    w, b = T.fit_lr(X[:, sl], y, cw, args.dino_wd)
    w_out = np.zeros((2 * T.EMBED, len(classes)), np.float32)
    w_out[sl] = w.numpy()
    Pd = sm(X @ w_out + b.numpy())
    if args.export_dino:
        import torch
        feat = T.make_feat_module(T.load_backbone())
        T.export_onnx(T.make_cls_module(
            feat, torch.from_numpy(w_out), b), T.OUT_ONNX, "probs", classes)

    # --- probs de cada miembro del ensemble en train ---
    # ORDEN: el de --models (igual que CLS_MODEL_PATH en el server).
    print("probs de miembros en train (v1/onnx)...")
    tr_paths = split_paths(classes, *splits)
    members = [Pd if mf.name == "cls_dino.onnx"
               else model_probs(mf, tr_paths, classes, splits)
               for mf in model_files]
    Xm = np.concatenate(members, 1)

    folds = T.group_kfold(groups, 5)
    for wd in (1e-3, 1e-2, 1e-1):
        recs = []
        for idx in folds:
            tr = np.setdiff1d(np.arange(len(y)), idx)
            wm, bm = T.fit_lr(Xm[tr], y[tr], np.ones(len(classes), np.float32), wd)
            m, _ = T.macro_recall(y[idx], T.predict(wm, bm, Xm[idx]), len(classes))
            recs.append(m)
        print(f"  meta wd={wd:g} cv_macro={np.mean(recs):.4f}", flush=True)
    wm, bm = T.fit_lr(Xm, y, np.ones(len(classes), np.float32), args.meta_wd)
    np.savez(args.meta_out, W=wm.numpy(), b=bm.numpy(), classes=classes,
             models=[m.name for m in model_files])
    print(f"meta -> {args.meta_out}")

    # --- eval por split (protocolo del server: conf -> fondo) ---
    report = {"classes": classes, "models": [m.name for m in model_files],
              "dino": {"variant": args.dino_variant, "wd": args.dino_wd,
                       "weights": args.dino_weights},
              "meta_wd": args.meta_wd, "splits_train": splits, "test": {}}
    for s in TEST_SPLITS:
        z = np.load(T.FEAT_DIR / f"{s}.npz")
        Xt, yt = z["X"], z["y"]
        cols = [sm(Xt @ w_out + b.numpy()) if mf.name == "cls_dino.onnx"
                else model_probs(mf, split_paths(classes, s), classes, [s])
                for mf in model_files]
        pe = sm(np.concatenate(cols, 1) @ wm.numpy() + bm.numpy())
        for conf in (0.3, 0.4, 0.5, 0.6):
            r = metrics(yt, pe, conf)
            report["test"].setdefault(s, {})[f"conf{conf}"] = {
                k: (v if not isinstance(v, dict)
                    else {classes[c]: r for c, r in v.items()})
                for k, v in r.items()}
        r = report["test"][s]["conf0.5"]
        print(f"{s:20s} acc={r['acc']:.3f} macro={r['macro']:.3f} "
              f"uni={r['per_class']['unicel']:.3f} fondo={r['fondo_ok']:.3f}")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False), "utf-8")
    print(f"-> {REPORT}")


if __name__ == "__main__":
    main()
