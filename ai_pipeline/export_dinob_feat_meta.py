#!/usr/bin/env python3
"""REBA — Exporta el ensemble enriquecido:

  models/cls_dinob.onnx      ViT-B/14 + head + softmax  -> salidas (probs, feat)
  models/cls_meta_feat.npz   W/b del meta-LR sobre [probs miembros | pca(featB)]

El meta incluye mu/sd/P del PCA (64 comps) calculado SOLO con train.
Las probs de las heads DINO en train se obtienen out-of-fold (meta honesto).

Uso:
    python ai_pipeline/export_dinob_feat_meta.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ai_pipeline"))

import train_esp32_cls_dino as T  # noqa: E402
import train_esp32_cls_dinob as B  # noqa: E402

MODELS = ["models/cls_esp32.onnx", "models/cls_dino.onnx",
          "models/cls_11s.onnx", "models/cls_dinob.onnx"]
PCA_K = 64
WD_HEAD = 1e-2
WD_META = 1e-2
OUT_ONNX = ROOT / "models" / "cls_dinob_feat.onnx"
OUT_NPZ = ROOT / "models" / "cls_meta_feat.npz"
SPLITS = T.TRAIN_SPLITS + ["train_extra"]
DATA = T.DATA


def sm(x: np.ndarray) -> np.ndarray:
    z = x - x.max(1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(1, keepdims=True)


def paths_of(split: str, classes: list[str]) -> list[Path]:
    return [p for c in classes
            for p in sorted((DATA / split / c).glob("*.jpg"))]


def main() -> None:
    import torch
    import torch.nn as nn

    classes = sorted(d.name for d in (DATA / "train").iterdir() if d.is_dir())
    y = np.concatenate(
        [np.load(T.FEAT_DIR / f"{s}.npz")["y"] for s in SPLITS])
    Xs = np.concatenate(
        [np.load(T.FEAT_DIR / f"{s}.npz")["X"] for s in SPLITS])
    Xb = np.concatenate(
        [np.load(T.FEAT_DIR / f"{s}_b.npz")["X"] for s in SPLITS])
    groups = np.concatenate([
        np.array([T.group_of(p.stem) for p in paths_of(s, classes)])
        for s in SPLITS])
    n_c = len(classes)
    cw = T.balanced_weights(y, n_c)
    sl_s, sl_b = T.VARIANTS["cls+mean"], B.VARIANTS["cls+mean"]
    folds = T.group_kfold(groups, 5)

    # --- probs OOF de las heads dino S y B (meta honesto) ---
    print("OOF probs dino-S/B...")
    Ps = np.zeros((len(y), n_c), np.float32)
    Pb = np.zeros((len(y), n_c), np.float32)
    for i, idx in enumerate(folds):
        tr = np.setdiff1d(np.arange(len(y)), idx)
        w, b = T.fit_lr(Xs[tr][:, sl_s], y[tr], cw, WD_HEAD)
        Ps[idx] = sm(Xs[idx][:, sl_s] @ w.numpy() + b.numpy())
        w, b = T.fit_lr(Xb[tr][:, sl_b], y[tr], cw, WD_HEAD)
        Pb[idx] = sm(Xb[idx][:, sl_b] @ w.numpy() + b.numpy())
        print(f"  fold {i + 1}/5", flush=True)

    # --- heads finales (todas las filas) para test/deploy ---
    w_s, b_s = T.fit_lr(Xs[:, sl_s], y, cw, WD_HEAD)
    w_b, b_b = T.fit_lr(Xb[:, sl_b], y, cw, WD_HEAD)
    wo_b = np.zeros((2 * B.EMBED, n_c), np.float32)
    wo_b[sl_b] = w_b.numpy()

    # --- probs de los otros miembros en train (cache ONNX) ---
    def member_probs(stem: str, split: str) -> np.ndarray:
        p = np.load(T.FEAT_DIR / f"{split}_{stem}probs.npy")
        return p if p.max() <= 1.001 else sm(p)

    other = {"cls_esp32": None, "cls_11s": None}
    for m in other:
        other[m] = np.concatenate([member_probs(m, s) for s in SPLITS])

    # --- PCA de features B ---
    mu, sd = Xb.mean(0), Xb.std(0) + 1e-6
    Xbn = (Xb - mu) / sd
    _, _, vt = np.linalg.svd(Xbn, full_matrices=False)
    P = vt[:PCA_K].T.astype(np.float32)
    Z = (Xbn @ P).astype(np.float32)

    # --- meta input: [p_esp32, p_dinoS, p_11s, p_dinoB, pca64B] ---
    cols = [other["cls_esp32"], Ps, other["cls_11s"], Pb, Z]
    Xm = np.concatenate(cols, 1)
    print("meta input:", Xm.shape)
    wm, bm = T.fit_lr(Xm, y, np.ones(n_c, np.float32), WD_META)

    deploy_models = [m.replace("cls_dinob.onnx", "cls_dinob_feat.onnx")
                     for m in MODELS]
    np.savez(OUT_NPZ, W=wm.numpy(), b=bm.numpy(), classes=classes,
             models=deploy_models, feat_idx=len(MODELS) - 1,
             mu=mu, sd=sd, P=P, pca_k=PCA_K)
    print("meta ->", OUT_NPZ)

    # --- export cls_dinob_feat.onnx con salidas (probs, feat) ---
    feat = B.make_feat_module(B.load_backbone())

    class Cls2(nn.Module):
        def __init__(self):
            super().__init__()
            self.feat = feat
            self.register_buffer("w", torch.from_numpy(wo_b))
            self.register_buffer("b", b_b)

        def forward(self, img):
            f = self.feat(img)
            return torch.softmax(f @ self.w + self.b, dim=1), f

    dummy = torch.zeros(1, 3, B.IMG_H, B.IMG_W)
    torch.onnx.export(
        Cls2(), dummy, str(OUT_ONNX), opset_version=17,
        input_names=["images"], output_names=["probs", "feat"],
        dynamic_axes={"images": {0: "b"},
                      "probs": {0: "b"}, "feat": {0: "b"}},
        dynamo=False,
    )
    import onnx
    m = onnx.load(str(OUT_ONNX))
    mp_ = m.metadata_props.add()
    mp_.key, mp_.value = "names", str({i: c for i, c in enumerate(classes)})
    onnx.save(m, str(OUT_ONNX))
    print("onnx ->", OUT_ONNX)


if __name__ == "__main__":
    main()
