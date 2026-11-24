#!/usr/bin/env python3
"""
REBA — Variante ViT-B/14 reg4 del pipeline DINO (train_esp32_cls_dino.py).

  - Features 1536d (cls 768 + patch-mean 768) -> _feat/<split>_b.npz
  - Backbone ONNX -> _feat/dino_feat_b.onnx
  - Head LR -> models/cls_dinob.onnx
  - Reporte -> reports/esp32_cls/dinob_head.json

Reusa los helpers de train_esp32_cls_dino (fit_lr, CV agrupada, etc.).

Uso:
    python ai_pipeline/train_esp32_cls_dinob.py              # extrae + head
    python ai_pipeline/train_esp32_cls_dinob.py --head-only
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "models" / "_hub" / "facebookresearch_dinov2_main"))

from server.inference import cls_preprocess  # noqa: E402
import train_esp32_cls_dino as T  # noqa: E402

DATA = T.DATA
FEAT_DIR = T.FEAT_DIR
BACKBONE_ONNX = FEAT_DIR / "dino_feat_b.onnx"
CKPT = ROOT / "models" / "_hub" / "checkpoints" / "dinov2_vitb14_reg4_pretrain.pth"
OUT_ONNX = ROOT / "models" / "cls_dinob.onnx"
REPORT = ROOT / "reports" / "esp32_cls" / "dinob_head.json"

IMG_H, IMG_W = T.IMG_H, T.IMG_W
PATCH = T.PATCH
EMBED = 768                     # ViT-B/14
BATCH = 4
TRAIN_SPLITS = T.TRAIN_SPLITS
VARIANTS = {"cls+mean": slice(0, 2 * EMBED), "cls": slice(0, EMBED)}


def npz_name(split: str) -> Path:
    return FEAT_DIR / f"{split}_b.npz"


def load_backbone():
    import torch
    from dinov2.models import vision_transformer as vits

    bb = vits.vit_base(
        img_size=518, patch_size=PATCH, init_values=1.0,
        num_register_tokens=4, interpolate_antialias=True,
        interpolate_offset=0.0, block_chunks=0,
    )
    sd = torch.load(CKPT, map_location="cpu", weights_only=True)
    bb.load_state_dict(sd, strict=True)
    return bb.eval()


def make_feat_module(bb):
    import torch
    import torch.nn as nn

    class Feat(nn.Module):
        def __init__(self):
            super().__init__()
            self.bb = bb
            with torch.no_grad():
                toks = torch.zeros(
                    1, 1 + (IMG_H // PATCH) * (IMG_W // PATCH), bb.embed_dim
                )
                pos = bb.interpolate_pos_encoding(toks, IMG_H, IMG_W)
            self.register_buffer("pos", pos)
            self.register_buffer("mean", torch.tensor(T.MEAN).view(1, 3, 1, 1))
            self.register_buffer("std", torch.tensor(T.STD).view(1, 3, 1, 1))

        def forward(self, img):
            x = (img - self.mean) / self.std
            x = self.bb.patch_embed(x)
            x = torch.cat(
                (self.bb.cls_token.expand(x.shape[0], -1, -1), x), 1
            ) + self.pos
            if self.bb.register_tokens is not None:
                x = torch.cat(
                    (x[:, :1],
                     self.bb.register_tokens.expand(x.shape[0], -1, -1),
                     x[:, 1:]),
                    1,
                )
            for blk in self.bb.blocks:
                x = blk(x)
            x = self.bb.norm(x)
            n = 1 + self.bb.num_register_tokens
            return torch.cat((x[:, 0], x[:, n:].mean(1)), 1)

    return Feat().eval()


def extract_split(sess, input_name: str, split: str, classes: list[str]) -> dict:
    paths, y, groups = [], [], []
    for ci, c in enumerate(classes):
        for p in sorted((DATA / split / c).glob("*.jpg")):
            paths.append(p)
            y.append(ci)
            groups.append(T.group_of(p.stem))
    feats = np.empty((len(paths), 2 * EMBED), np.float32)
    t0 = time.time()
    for i in range(0, len(paths), BATCH):
        blob = np.concatenate(
            [cls_preprocess(cv2.imread(str(p)), IMG_H, IMG_W)
             for p in paths[i:i + BATCH]]
        )
        feats[i:i + len(blob)] = sess.run(None, {input_name: blob})[0]
        done = i + len(blob)
        if done % 200 < BATCH or done == len(paths):
            print(f"  {split}: {done}/{len(paths)} "
                  f"({(time.time() - t0) / done * 1000:.0f} ms/img)", flush=True)
    return {"X": feats, "y": np.array(y), "groups": np.array(groups),
            "files": np.array([p.name for p in paths])}


def build_features(splits: list[str], classes: list[str]) -> None:
    import onnxruntime as ort

    need = [s for s in splits if not npz_name(s).exists()]
    if not need:
        print("features ViT-B cacheadas")
        return
    if not BACKBONE_ONNX.exists():
        print("construyendo backbone ViT-B ONNX...")
        T.export_onnx(make_feat_module(load_backbone()), BACKBONE_ONNX, "feat")
    sess = ort.InferenceSession(
        str(BACKBONE_ONNX), providers=["CPUExecutionProvider"]
    )
    inp = sess.get_inputs()[0].name
    for s in need:
        print(f"extrayendo {s} (ViT-B)...")
        out = extract_split(sess, inp, s, classes)
        np.savez_compressed(npz_name(s), **out)
        print(f"  -> {npz_name(s)}")


def load_features(splits: list[str]) -> dict:
    xs, ys, gs = [], [], []
    for s in splits:
        z = np.load(npz_name(s), allow_pickle=False)
        xs.append(z["X"]); ys.append(z["y"]); gs.append(z["groups"])
    return {"X": np.concatenate(xs), "y": np.concatenate(ys),
            "groups": np.concatenate(gs)}


def train_head(data: dict, classes: list[str], folds: int, out_path: Path) -> dict:
    import json
    import torch

    X, y, groups = data["X"], data["y"], data["groups"]
    C = len(classes)
    cw = T.balanced_weights(y, C)
    test_idx = T.group_kfold(groups, folds)
    results = {}
    for vname, sl in VARIANTS.items():
        Xv = X[:, sl]
        for wd in T.WD_GRID:
            recs, uni = [], []
            for idx in test_idx:
                tr = np.setdiff1d(np.arange(len(y)), idx)
                w, b = T.fit_lr(Xv[tr], y[tr], cw, wd)
                pred = T.predict(w, b, Xv[idx])
                m, per = T.macro_recall(y[idx], pred, C)
                recs.append(m)
                uni.append(per[classes.index("unicel")])
            results[f"{vname}|{wd:g}"] = {
                "macro": float(np.mean(recs)), "unicel": float(np.mean(uni)),
            }
            print(f"  {vname:8s} wd={wd:g}  macro={np.mean(recs):.4f} "
                  f"unicel={np.mean(uni):.4f}", flush=True)
    best_key = max(results, key=lambda k: (results[k]["macro"], results[k]["unicel"]))
    vname, wd_s = best_key.split("|")
    wd = float(wd_s)
    print(f"elegido: {best_key} cv_macro={results[best_key]['macro']:.4f}")

    sl = VARIANTS[vname]
    w, b = T.fit_lr(X[:, sl], y, cw, wd)
    m_train, per_train = T.macro_recall(y, T.predict(w, b, X[:, sl]), C)

    print("exportando cls_dinob.onnx...")
    feat = make_feat_module(load_backbone())
    w_out = torch.zeros(2 * EMBED, C, dtype=w.dtype)
    w_out[sl] = w
    T.export_onnx(T.make_cls_module(feat, w_out, b), OUT_ONNX, "probs", classes)

    report = {
        "classes": classes, "feature_variant": vname, "weight_decay": wd,
        "cv": results,
        "trainval": {"macro": m_train,
                     "per_class": dict(zip(classes, per_train)),
                     "n": int(len(y))},
        "n_groups": int(len(np.unique(groups))), "folds": folds,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"reporte -> {out_path}")
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--splits", nargs="+", default=TRAIN_SPLITS)
    ap.add_argument("--head-only", action="store_true")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--out", default=str(REPORT))
    args = ap.parse_args()

    classes = sorted(d.name for d in (DATA / "train").iterdir() if d.is_dir())
    print(f"clases={classes} (ViT-B, EMBED={EMBED})")
    if not args.head_only:
        build_features(args.splits, classes)
    data = load_features(TRAIN_SPLITS)
    print(f"features: X={data['X'].shape} grupos={len(np.unique(data['groups']))}")
    train_head(data, classes, args.folds, Path(args.out))


if __name__ == "__main__":
    main()
