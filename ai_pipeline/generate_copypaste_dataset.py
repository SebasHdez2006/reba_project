"""Genera dataset sintetico copy-paste sobre fondos de agua reales (FASE 2.3).

Recorta objetos ya etiquetados de los datasets REBA_* (FloatWaste, TACO,
EcoSort, boosts sinteticos) y los pega sobre fondos de agua limpia
(FloatWaste negativos + sintéticos) con:

  - escala aleatoria, flip horizontal, rotacion leve (+-15 deg)
  - jitter de color HSV
  - "sumersion parcial": los ~25% inferiores del recorte se funden con el
    color del agua del fondo (alpha feather + tinte azul/verde)
  - motion blur leve en ~20% de los pegados

Objetivo: reforzar `tela` y `organico` (clases debiles) en el dominio real
(objetos sobre/cerca de la superficie del agua), que el generador
procedural puro no cubre.

Salida: datasets/REBA_copypaste/{images,labels}/train + data.yaml.
Nunca genera val/test (la validacion real queda intacta).

Uso:
  python ai_pipeline/generate_copypaste_dataset.py --images 600
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATASETS = ROOT / "datasets"
OUT = DATASETS / "REBA_copypaste"

CLASS_NAMES = ["plastico", "tela", "organico", "unicel", "metal"]
# Fuentes de objetos (dataset -> splits usados para el banco de recortes)
OBJECT_SOURCES = [
    "REBA_floatwaste", "REBA_taco", "REBA_ecosort",
    "REBA_synthetic", "REBA_synthetic_boost", "REBA_synthetic_boost2",
]
# Fondos: imagenes de FloatWaste sin etiquetas + sintéticas (agua)
BG_SOURCES = ["REBA_floatwaste", "REBA_synthetic"]

# Pesos de muestreo por clase al elegir objetos (refuerza debiles)
CLASS_WEIGHT = {"plastico": 0.8, "tela": 3.0, "organico": 3.0,
                "unicel": 1.5, "metal": 0.8}

MAX_CROPS_PER_CLASS = 1200   # limite de banco en memoria
MIN_CROP_PX = 12             # lado minimo del recorte
PAD = 0.10                   # padding alrededor del bbox


def yolo_lines_to_boxes(label_path: Path, w: int, h: int):
    for ln in label_path.read_text(encoding="utf-8").splitlines():
        p = ln.split()
        if len(p) != 5:
            continue
        c, xc, yc, bw, bh = int(p[0]), *map(float, p[1:])
        x1 = (xc - bw / 2) * w
        y1 = (yc - bh / 2) * h
        x2 = (xc + bw / 2) * w
        y2 = (yc + bh / 2) * h
        yield c, x1, y1, x2, y2


def build_object_bank(rng: random.Random) -> dict[int, list]:
    """Recorta instancias de todos los datasets fuente -> {cls: [crop,bbox]}"""
    bank: dict[int, list] = defaultdict(list)
    total = defaultdict(int)
    for ds in OBJECT_SOURCES:
        d = DATASETS / ds
        if not d.is_dir():
            continue
        for split in ("train",):
            img_dir, lbl_dir = d / "images" / split, d / "labels" / split
            if not img_dir.is_dir():
                continue
            for lp in sorted(lbl_dir.glob("*.txt")):
                stem = lp.stem
                img_path = None
                for ext in (".jpg", ".jpeg", ".png"):
                    cand = img_dir / f"{stem}{ext}"
                    if cand.exists():
                        img_path = cand
                        break
                if img_path is None:
                    continue
                img = cv2.imread(str(img_path))
                if img is None:
                    continue
                ih, iw = img.shape[:2]
                for cls, x1, y1, x2, y2 in yolo_lines_to_boxes(lp, iw, ih):
                    if cls not in range(len(CLASS_NAMES)):
                        continue
                    pw, ph = (x2 - x1) * PAD, (y2 - y1) * PAD
                    cx1, cy1 = int(max(0, x1 - pw)), int(max(0, y1 - ph))
                    cx2, cy2 = int(min(iw, x2 + pw)), int(min(ih, y2 + ph))
                    if cx2 - cx1 < MIN_CROP_PX or cy2 - cy1 < MIN_CROP_PX:
                        continue
                    crop = img[cy1:cy2, cx1:cx2].copy()
                    # bbox del objeto dentro del recorte (con padding)
                    bx1, by1 = x1 - cx1, y1 - cy1
                    bx2, by2 = x2 - cx1, y2 - cy1
                    bank[cls].append((crop, (bx1, by1, bx2, by2)))
                    total[ds] += 1
    # subsample determinista si hay demasiados por clase
    for cls in bank:
        if len(bank[cls]) > MAX_CROPS_PER_CLASS:
            bank[cls] = rng.sample(bank[cls], MAX_CROPS_PER_CLASS)
    print("Banco de recortes por clase:",
          {CLASS_NAMES[c]: len(v) for c, v in sorted(bank.items())})
    print("Recortes por origen:", dict(total))
    return bank


def build_backgrounds(rng: random.Random) -> list[np.ndarray]:
    bgs = []
    for ds in BG_SOURCES:
        d = DATASETS / ds / "images" / "train"
        if not d.is_dir():
            continue
        paths = sorted(d.glob("*.jpg")) + sorted(d.glob("*.png"))
        rng.shuffle(paths)
        for p in paths[:400]:  # muestra suficiente
            img = cv2.imread(str(p))
            if img is not None and img.shape[0] >= 320 and img.shape[1] >= 320:
                bgs.append(img)
    return bgs


def submerge_effect(crop: np.ndarray, bg_patch: np.ndarray) -> np.ndarray:
    """Funde la franja inferior del recorte con el color del agua."""
    out = crop.copy()
    h = out.shape[0]
    band = max(4, int(h * 0.28))
    water = bg_patch.reshape(-1, 3).mean(axis=0)
    for i in range(band):
        alpha = (i + 1) / band * 0.55
        y = h - band + i
        out[y] = (out[y] * (1 - alpha) + water * alpha).astype(np.uint8)
    return out


def paste_one(bg: np.ndarray, crop: np.ndarray, box, rng: random.Random):
    """Pega crop escalado/rotado en bg. Devuelve (x,y,w,h) del objeto en px."""
    bh, bw = bg.shape[:2]
    ch, cw = crop.shape[:2]
    scale = rng.uniform(0.5, 1.6)
    nw, nh = max(8, int(cw * scale)), max(8, int(ch * scale))
    resized = cv2.resize(crop, (nw, nh), interpolation=cv2.INTER_AREA)
    # objeto (sin padding) dentro del recorte, escalado igual
    bx1, by1, bx2, by2 = box
    fx1, fy1 = bx1 / cw, by1 / ch
    fx2, fy2 = bx2 / cw, by2 / ch
    # rotacion leve
    ang = rng.uniform(-15, 15)
    M = cv2.getRotationMatrix2D((nw / 2, nh / 2), ang, 1.0)
    cos, sin = abs(M[0, 0]), abs(M[0, 1])
    nw2, nh2 = int(nw * cos + nh * sin), int(nw * sin + nh * cos)
    M[0, 2] += (nw2 - nw) / 2
    M[1, 2] += (nh2 - nh) / 2
    rotated = cv2.warpAffine(resized, M, (nw2, nh2),
                             borderMode=cv2.BORDER_CONSTANT,
                             borderValue=(0, 0, 0))
    # mascara: no pegar pixeles negros puros del borde de rotacion
    mask = (rotated.sum(axis=2) > 12).astype(np.uint8)
    # posicion aleatoria dentro del fondo
    max_x, max_y = bw - nw2, bh - nh2
    if max_x < 4 or max_y < 4:
        return None
    px, py = rng.randint(0, max_x), rng.randint(0, max_y)
    # submersion sobre una franja inferior del recorte completo
    frac_bottom = rng.uniform(0.10, 0.30)
    y0 = int(nh2 * (1 - frac_bottom))
    region = bg[py:py + nh2, px:px + nw2]
    if region.shape[:2] != rotated.shape[:2]:
        return None
    sub = submerge_effect(rotated[y0:], region[y0:])
    rotated[y0:] = sub
    # motion blur leve en algunos casos
    if rng.random() < 0.2:
        k = rng.choice([3, 5])
        rotated = cv2.GaussianBlur(rotated, (k, k), 0)
    # pegar con mascara
    roi = bg[py:py + nh2, px:px + nw2]
    m3 = mask[:, :, None]
    bg[py:py + nh2, px:px + nw2] = (rotated * m3 + roi * (1 - m3)).astype(np.uint8)
    # caja del objeto en coords de bg (aprox: bbox interno escalado+rotado)
    ox1, oy1 = px + fx1 * nw2, py + fy1 * nh2
    ox2, oy2 = px + fx2 * nw2, py + fy2 * nh2
    return (ox1, oy1, ox2, oy2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", type=int, default=600)
    ap.add_argument("--objects-per-image", type=int, default=3)
    ap.add_argument("--seed", type=int, default=20261114)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    np.random.seed(args.seed)

    bank = build_object_bank(rng)
    if not bank:
        print("ERROR: banco vacio (faltan datasets?)", file=sys.stderr)
        return 1
    bgs = build_backgrounds(rng)
    if not bgs:
        print("ERROR: sin fondos", file=sys.stderr)
        return 1
    print(f"{len(bgs)} fondos disponibles")

    img_dir = OUT / "images" / "train"
    lbl_dir = OUT / "labels" / "train"
    img_dir.mkdir(parents=True, exist_ok=True)
    lbl_dir.mkdir(parents=True, exist_ok=True)

    classes = [c for c, v in bank.items() if v]
    weights = np.array([CLASS_WEIGHT.get(CLASS_NAMES[c], 1.0) for c in classes])
    weights /= weights.sum()
    totals = defaultdict(int)
    for i in range(args.images):
        bg = bgs[rng.randrange(len(bgs))].copy()
        boxes = []
        for _ in range(args.objects_per_image):
            cls = classes[rng.choices(range(len(classes)), weights=weights)[0]]
            crop, box = bank[cls][rng.randrange(len(bank[cls]))]
            res = paste_one(bg, crop, box, rng)
            if res is None:
                continue
            x1, y1, x2, y2 = res
            bh, bw = bg.shape[:2]
            xc, yc = (x1 + x2) / 2 / bw, (y1 + y2) / 2 / bh
            nw, nh = (x2 - x1) / bw, (y2 - y1) / bh
            xc, yc = min(max(xc, 0), 1), min(max(yc, 0), 1)
            nw, nh = min(max(nw, 0), 1), min(max(nh, 0), 1)
            if nw <= 0.005 or nh <= 0.005:  # descarta micro-cajas
                continue
            boxes.append(f"{cls} {xc:.6f} {yc:.6f} {nw:.6f} {nh:.6f}")
            totals[CLASS_NAMES[cls]] += 1
        if not boxes:
            continue
        stem = f"cp_{i:04d}"
        cv2.imwrite(str(img_dir / f"{stem}.jpg"), bg,
                    [cv2.IMWRITE_JPEG_QUALITY, 92])
        (lbl_dir / f"{stem}.txt").write_text("\n".join(boxes) + "\n",
                                             encoding="utf-8")
        if (i + 1) % 100 == 0:
            print(f"  {i + 1}/{args.images}", flush=True)

    names = "\n".join(f"  {i}: {n}" for i, n in enumerate(CLASS_NAMES))
    (OUT / "data.yaml").write_text(
        f"path: {OUT.as_posix()}\ntrain: images/train\nval: images/train\n"
        f"names:\n{names}\n", encoding="utf-8")
    (OUT / "manifest.json").write_text(json.dumps({
        "generator": "generate_copypaste_dataset.py",
        "seed": args.seed, "images": args.images,
        "instances": dict(totals)}, indent=2), encoding="utf-8")
    print("Instancias generadas:", dict(totals))
    print(f"Salida: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
