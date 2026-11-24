#!/usr/bin/env python3
"""
REBA — Clasificador ESP32 v2: features DINOv2 ViT-S/14 reg congelado + head de
regresión logística (sustituto del YOLOv8n-cls de train_esp32_cls.py).

Pipeline:
  1. Backbone ONNX (solo features, pos-embed horneado para 224x294)
     -> datasets/REBA_cls_esp32/_feat/dino_feat.onnx
  2. Features de train/val -> _feat/<split>.npz (X cls+patchmean, y, grupo, file)
  3. Head LR multinomial L2 + pesos balanceados (torch); wd y variante de
     feature elegidos por CV AGRUPADA (vídeo FloatWaste / imagen TACO-EcoSort),
     nunca por los splits de test.
  4. models/cls_dino.onnx = backbone + head + softmax (mean/std ImageNet
     horneados; la entrada es el blob RGB/255 de server.inference.cls_preprocess).
  5. Reporte -> reports/esp32_cls/dino_head.json

Tras añadir imágenes al dataset basta re-extraer el split tocado (borrar su npz)
y correr --head-only: el backbone y el resto de npz se reusan (minutos).

Uso:
    python ai_pipeline/train_esp32_cls_dino.py                 # pipeline completo
    python ai_pipeline/train_esp32_cls_dino.py --head-only     # re-entrenar head con npz cacheados
    python ai_pipeline/train_esp32_cls_dino.py --splits train  # forzar re-extracción de train
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "models" / "_hub" / "facebookresearch_dinov2_main"))

from server.inference import cls_preprocess  # noqa: E402

DATA = ROOT / "datasets" / "REBA_cls_esp32"
FEAT_DIR = DATA / "_feat"
BACKBONE_ONNX = FEAT_DIR / "dino_feat.onnx"
CKPT = ROOT / "models" / "_hub" / "checkpoints" / "dinov2_vits14_reg4_pretrain.pth"
OUT_ONNX = ROOT / "models" / "cls_dino.onnx"
REPORT = ROOT / "reports" / "esp32_cls" / "dino_head.json"

IMG_H, IMG_W = 224, 294          # 4:3 sin recorte lateral (16x21 parches de 14)
PATCH = 14
MEAN = (0.485, 0.456, 0.406)
STD = (0.229, 0.224, 0.225)
EMBED = 384                      # dim del token CLS de ViT-S/14
BATCH = 8
TRAIN_SPLITS = ["train", "val"]
WD_GRID = [1e-6, 1e-5, 1e-4, 1e-3, 1e-2, 1e-1]
VARIANTS = {"cls+mean": slice(0, 2 * EMBED), "cls": slice(0, EMBED)}


# ------------------------------------------------------------------ backbone

def load_backbone():
    """DINOv2 ViT-S/14 reg4 desde el cache local de torch.hub (offline)."""
    import torch
    from dinov2.models import vision_transformer as vits

    bb = vits.vit_small(
        img_size=518, patch_size=PATCH, init_values=1.0,
        num_register_tokens=4, interpolate_antialias=True,
        interpolate_offset=0.0, block_chunks=0,
    )
    sd = torch.load(CKPT, map_location="cpu", weights_only=True)
    bb.load_state_dict(sd, strict=True)
    return bb.eval()


def make_feat_module(bb):
    """Backbone con pos-embed precomputado para IMG_HxIMG_W (la interpolación
    con antialias no exporta a ONNX) y normalización ImageNet horneada.
    Devuelve [cls_token, media de patch tokens] = 768 dims."""
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
            self.register_buffer("mean", torch.tensor(MEAN).view(1, 3, 1, 1))
            self.register_buffer("std", torch.tensor(STD).view(1, 3, 1, 1))

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


def make_cls_module(feat_mod, w, b):
    """feat + head LR + softmax."""
    import torch
    import torch.nn as nn

    class Cls(nn.Module):
        def __init__(self):
            super().__init__()
            self.feat = feat_mod
            self.register_buffer("head_w", w)
            self.register_buffer("head_b", b)

        def forward(self, img):
            return torch.softmax(self.feat(img) @ self.head_w + self.head_b, 1)

    return Cls().eval()


def export_onnx(mod, path: Path, out_name: str, classes: list[str] | None = None) -> None:
    import onnx
    import torch

    dummy = torch.zeros(1, 3, IMG_H, IMG_W)
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        mod, dummy, str(path), opset_version=17,
        input_names=["images"], output_names=[out_name],
        dynamic_axes={"images": {0: "b"}, out_name: {0: "b"}},
        dynamo=False,
    )
    if classes is not None:
        m = onnx.load(str(path))
        meta = m.metadata_props.add()
        meta.key, meta.value = "names", str({i: c for i, c in enumerate(classes)})
        onnx.save(m, str(path))
    print(f"ONNX -> {path}")


# ------------------------------------------------------------------ features

def group_of(stem: str) -> str:
    """Grupo para la CV: vídeo origen en FloatWaste (sin _frame_N), imagen
    origen en TACO/EcoSort. Nombre: {dataset}_{stem}_{box}_{variant|bg}."""
    parts = stem.split("_")
    src = parts[0]
    img_stem = "_".join(parts[1:-1] if parts[-1] == "bg" else parts[1:-2])
    if src == "floatwaste":
        img_stem = re.sub(r"_frame_\d+$", "", img_stem)
    return f"{src}:{img_stem}"


def extract_split(sess, input_name: str, split: str, classes: list[str]) -> dict:
    paths, y, groups = [], [], []
    for ci, c in enumerate(classes):
        for p in sorted((DATA / split / c).glob("*.jpg")):
            paths.append(p)
            y.append(ci)
            groups.append(group_of(p.stem))
    feats = np.empty((len(paths), 2 * EMBED), np.float32)
    t0 = time.time()
    for i in range(0, len(paths), BATCH):
        blob = np.concatenate(
            [cls_preprocess(cv2.imread(str(p)), IMG_H, IMG_W)
             for p in paths[i:i + BATCH]]
        )
        feats[i:i + len(blob)] = sess.run(None, {input_name: blob})[0]
        done = i + len(blob)
        print(f"  {split}: {done}/{len(paths)} "
              f"({(time.time() - t0) / done * 1000:.0f} ms/img)", flush=True)
    return {"X": feats, "y": np.array(y), "groups": np.array(groups),
            "files": np.array([p.name for p in paths])}


def build_features(splits: list[str], classes: list[str]) -> None:
    import onnxruntime as ort

    need = [s for s in splits if not (FEAT_DIR / f"{s}.npz").exists()]
    if not need:
        print("features cacheadas, nada que extraer")
        return
    if not BACKBONE_ONNX.exists():
        print("construyendo backbone ONNX...")
        export_onnx(make_feat_module(load_backbone()), BACKBONE_ONNX, "feat")
    sess = ort.InferenceSession(
        str(BACKBONE_ONNX), providers=["CPUExecutionProvider"]
    )
    inp = sess.get_inputs()[0].name
    for s in need:
        print(f"extrayendo {s}...")
        out = extract_split(sess, inp, s, classes)
        np.savez_compressed(FEAT_DIR / f"{s}.npz", **out)
        print(f"  -> {FEAT_DIR / (s + '.npz')}")


def load_features(splits: list[str]) -> dict:
    xs, ys, gs = [], [], []
    for s in splits:
        z = np.load(FEAT_DIR / f"{s}.npz", allow_pickle=False)
        xs.append(z["X"]); ys.append(z["y"]); gs.append(z["groups"])
    return {"X": np.concatenate(xs), "y": np.concatenate(ys),
            "groups": np.concatenate(gs)}


# ------------------------------------------------------------------ head

def balanced_weights(y: np.ndarray, n_classes: int) -> np.ndarray:
    n = np.bincount(y, minlength=n_classes).astype(np.float64)
    w = len(y) / (n_classes * np.maximum(n, 1))
    return (w / w.mean()).astype(np.float32)


def fit_lr(X: np.ndarray, y: np.ndarray, cw: np.ndarray,
           wd: float, iters: int = 500):
    import torch
    import torch.nn as nn

    xt = torch.from_numpy(X)
    yt = torch.from_numpy(y)
    n_cls = int(y.max()) + 1
    W = torch.zeros(X.shape[1], n_cls, dtype=xt.dtype, requires_grad=True)
    b = torch.zeros(n_cls, dtype=xt.dtype, requires_grad=True)
    ce = nn.CrossEntropyLoss(weight=torch.from_numpy(cw))
    opt = torch.optim.LBFGS([W, b], max_iter=iters, line_search_fn="strong_wolfe")

    def closure():
        opt.zero_grad()
        loss = ce(xt @ W + b, yt) + wd * (W * W).sum() / 2
        loss.backward()
        return loss

    opt.step(closure)
    return W.detach(), b.detach()


def predict(W, b, X: np.ndarray) -> np.ndarray:
    return (X @ W.numpy() + b.numpy()).argmax(1)


def macro_recall(y_true: np.ndarray, y_pred: np.ndarray, n_classes: int):
    rec = []
    for c in range(n_classes):
        m = y_true == c
        rec.append(float((y_pred[m] == c).mean()) if m.any() else 0.0)
    return float(np.mean(rec)), rec


def group_kfold(groups: np.ndarray, n_folds: int, seed: int = 0) -> list[np.ndarray]:
    """Folds balanceados por tamaño (greedy): índices de test de cada fold."""
    uniq, counts = np.unique(groups, return_counts=True)
    rng = np.random.default_rng(seed)
    order = np.argsort(-counts, kind="stable")
    fold_size = np.zeros(n_folds)
    fold_of = {}
    for g, c in zip(uniq[order], counts[order]):
        f = int(np.argmin(fold_size + rng.random(n_folds) * 1e-9))
        fold_size[f] += c
        fold_of[g] = f
    gfold = np.array([fold_of[g] for g in groups])
    return [np.flatnonzero(gfold == f) for f in range(n_folds)]


def train_head(data: dict, classes: list[str], folds: int, out_path: Path) -> dict:
    import torch

    X, y, groups = data["X"], data["y"], data["groups"]
    C = len(classes)
    cw = balanced_weights(y, C)
    test_idx = group_kfold(groups, folds)
    results = {}
    for vname, sl in VARIANTS.items():
        Xv = X[:, sl]
        for wd in WD_GRID:
            recs, uni = [], []
            for idx in test_idx:
                tr = np.setdiff1d(np.arange(len(y)), idx)
                w, b = fit_lr(Xv[tr], y[tr], cw, wd)
                pred = predict(w, b, Xv[idx])
                m, per = macro_recall(y[idx], pred, C)
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
    w, b = fit_lr(X[:, sl], y, cw, wd)
    m_train, per_train = macro_recall(y, predict(w, b, X[:, sl]), C)

    # head completo de 768 dims (ceros en la mitad no usada si vname == 'cls')
    print("exportando cls_dino.onnx...")
    feat = make_feat_module(load_backbone())
    w_out = torch.zeros(2 * EMBED, C, dtype=w.dtype)
    w_out[sl] = w
    export_onnx(make_cls_module(feat, w_out, b), OUT_ONNX, "probs", classes)

    report = {
        "classes": classes,
        "feature_variant": vname,
        "weight_decay": wd,
        "cv": results,
        "trainval": {"macro": m_train,
                     "per_class": dict(zip(classes, per_train)),
                     "n": int(len(y))},
        "n_groups": int(len(np.unique(groups))),
        "folds": folds,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"reporte -> {out_path}")
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--splits", nargs="+", default=TRAIN_SPLITS,
                    help="splits a extraer si falta su npz")
    ap.add_argument("--head-only", action="store_true",
                    help="saltar extracción y reusar _feat/*.npz")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--out", default=str(REPORT))
    args = ap.parse_args()

    classes = sorted(d.name for d in (DATA / "train").iterdir() if d.is_dir())
    print(f"clases={classes}")
    if not args.head_only:
        build_features(args.splits, classes)
    data = load_features(TRAIN_SPLITS)
    print(f"features train+val: X={data['X'].shape} "
          f"grupos={len(np.unique(data['groups']))}")
    train_head(data, classes, args.folds, Path(args.out))


if __name__ == "__main__":
    main()
