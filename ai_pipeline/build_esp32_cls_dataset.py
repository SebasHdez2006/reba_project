#!/usr/bin/env python3
"""
REBA — Dataset de CLASIFICACIÓN estilo ESP32-CAM (plastico/unicel/metal/fondo).

La ESP32 fotografía UN objeto a 0-23 cm (disparo del HC-SR04) a 320x240 con
JPEG agresivo. Los datasets de detección (FloatWaste, TACO, EcoSort) tienen
objetos pequeños en escenas grandes; aquí cada caja se convierte en una vista
"de cerca" como la de la cámara:

  recorte 4:3 alrededor de la caja (objeto = 35-95 % del cuadro)
  -> 320x240 -> degradación OV2640 (tinte, desaturación, blur, ruido, JPEG)

Splits (salida en datasets/REBA_cls_esp32/):
  train/, val/      FloatWaste train + TACO train + EcoSort train (val = 10 %
                    por hash de imagen; nunca se usan para medir el 85 %)
  test_agua/        REBA_real_val (FloatWaste val, sin near-dups en train)
  test_tierra/      TACO val+test + EcoSort val
  test_*_limpio/    mismos recortes SIN degradación (control de la simulación)

Los vídeos FloatWaste presentes en REBA_real_val se excluyen del train.

Uso:
    python ai_pipeline/build_esp32_cls_dataset.py
"""

from __future__ import annotations

import hashlib
import random
import re
import shutil
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DS = ROOT / "datasets"
OUT = DS / "REBA_cls_esp32"
CLASSES = ["plastico", "unicel", "metal"]
FONDO = "fondo"
OUT_W, OUT_H = 320, 240
MIN_REL = 0.06    # lado mayor de la caja >= 6 % de la imagen
MIN_PX = 28       # y >= 28 px reales

# (dataset, split, entorno)
TRAIN_SRC = [("REBA_floatwaste", "train", "agua"), ("REBA_taco", "train", "tierra"),
             ("REBA_ecosort", "train", "tierra")]
TEST_SRC = {"test_agua": [("REBA_real_val", "val", "agua")],
            "test_tierra": [("REBA_taco", "val", "tierra"), ("REBA_taco", "test", "tierra"),
                            ("REBA_ecosort", "val", "tierra")]}
# variantes por caja en train (balance de clases por entorno)
VARIANTS = {("agua", 0): 1, ("agua", 1): 4, ("agua", 2): 4,
            ("tierra", 0): 1, ("tierra", 1): 10, ("tierra", 2): 2}
FONDO_PER_IMG = {"agua": 0.8, "tierra": 0.6}


def vid(stem: str) -> str:
    return re.sub(r"_frame_\d+$", "", stem)


def load_boxes(lbl: Path, w: int, h: int) -> list[tuple[int, float, float, float, float]]:
    out = []
    if lbl.exists():
        for line in lbl.read_text().splitlines():
            p = line.split()
            if len(p) >= 5:
                c, x, y, bw, bh = int(p[0]), *map(float, p[1:5])
                out.append((c, (x - bw / 2) * w, (y - bh / 2) * h, (x + bw / 2) * w, (y + bh / 2) * h))
    return out


def ioa(a, b) -> float:
    """Intersección / área de b."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    ab = max(1e-6, (b[2] - b[0]) * (b[3] - b[1]))
    return ix * iy / ab


def crop_window(box, img_w, img_h, frac, rng: random.Random, jitter: float):
    """Ventana 4:3 donde el lado mayor relativo del objeto ocupa `frac`."""
    x1, y1, x2, y2 = box[1:]
    bw, bh = x2 - x1, y2 - y1
    ch = max(bh, bw * 0.75) / frac
    cw = ch * 4 / 3
    if cw > img_w:
        cw = img_w; ch = cw * 0.75
    if ch > img_h:
        ch = img_h; cw = ch * 4 / 3
    cx = (x1 + x2) / 2 + rng.uniform(-jitter, jitter) * max(0.0, cw - bw)
    cy = (y1 + y2) / 2 + rng.uniform(-jitter, jitter) * max(0.0, ch - bh)
    cx0 = min(max(0.0, cx - cw / 2), img_w - cw)
    cy0 = min(max(0.0, cy - ch / 2), img_h - ch)
    return (cx0, cy0, cx0 + cw, cy0 + ch)


def clean_window(win, target, boxes) -> bool:
    """Rechaza recortes con otro objeto REBA de distinta clase relevante dentro."""
    ta = (target[3] - target[1]) * (target[4] - target[2])
    for b in boxes:
        if b is target or b[0] == target[0]:
            continue
        if ioa(win, b[1:]) > 0.5 and (b[3] - b[1]) * (b[4] - b[2]) > 0.25 * ta:
            return False
    # el objeto objetivo debe quedar casi completo dentro
    return ioa(win, target[1:]) > 0.85


def esp32_degrade(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Simulación del sensor OV2640 + JPEG de la ESP32-CAM (BGR uint8)."""
    f = img.astype(np.float32)
    # balance de blancos malo: tinte verde/azul/amarillo
    gains = 1.0 + rng.normal(0, 0.08, 3)
    gains[1] += rng.uniform(0.0, 0.10)          # OV2640 tiende a verde
    f *= gains
    # desaturación + exposición
    gray = f.mean(axis=2, keepdims=True)
    f = gray + (f - gray) * rng.uniform(0.45, 1.05)
    f = f * rng.uniform(0.6, 1.35) + rng.uniform(-25, 25)
    f = np.clip(f, 0, 255)
    gamma = rng.uniform(0.7, 1.4)
    f = 255.0 * (f / 255.0) ** gamma
    out = f.astype(np.uint8)
    # desenfoque (foco fijo a corta distancia) / movimiento
    r = rng.random()
    if r < 0.45:
        k = int(rng.choice([3, 5, 7]))
        out = cv2.GaussianBlur(out, (k, k), 0)
    elif r < 0.65:
        k = int(rng.choice([5, 7, 9]))
        kern = np.zeros((k, k), np.float32)
        kern[k // 2, :] = 1.0 / k
        m = cv2.getRotationMatrix2D((k / 2 - 0.5, k / 2 - 0.5), rng.uniform(0, 180), 1)
        kern = cv2.warpAffine(kern, m, (k, k))
        kern /= max(kern.sum(), 1e-6)
        out = cv2.filter2D(out, -1, kern)
    # viñeteo
    if rng.random() < 0.5:
        yy, xx = np.mgrid[0:OUT_H, 0:OUT_W].astype(np.float32)
        d = ((xx - OUT_W / 2) / (OUT_W / 2)) ** 2 + ((yy - OUT_H / 2) / (OUT_H / 2)) ** 2
        out = np.clip(out * (1 - rng.uniform(0.1, 0.35) * d[..., None] / 2), 0, 255).astype(np.uint8)
    # ruido de sensor
    noise = rng.normal(0, rng.uniform(2, 9), out.shape)
    out = np.clip(out.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    # JPEG agresivo (q15 ESP32 ~ q20-45 libjpeg)
    ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, int(rng.integers(18, 46))])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def to_frame(img, win) -> np.ndarray:
    x1, y1, x2, y2 = (int(round(v)) for v in win)
    crop = img[max(0, y1):y2, max(0, x1):x2]
    interp = cv2.INTER_AREA if crop.shape[1] > OUT_W else cv2.INTER_LINEAR
    return cv2.resize(crop, (OUT_W, OUT_H), interpolation=interp)


def fondo_window(boxes, w, h, rng: random.Random):
    for _ in range(12):
        ch = h * rng.uniform(0.25, 0.7)
        cw = min(w, ch * 4 / 3)
        ch = cw * 0.75
        x0, y0 = rng.uniform(0, w - cw), rng.uniform(0, h - ch)
        win = (x0, y0, x0 + cw, y0 + ch)
        if all(ioa(win, b[1:]) < 0.08 for b in boxes):
            return win
    return None


def save(img, split: str, cls: str, name: str, counts: Counter) -> None:
    d = OUT / split / cls
    d.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(d / f"{name}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    counts[(split, cls)] += 1


def iter_images(ds: str, split: str):
    for p in sorted((DS / ds / "images" / split).glob("*")):
        if p.suffix.lower() in (".jpg", ".jpeg", ".png"):
            yield p, DS / ds / "labels" / split / f"{p.stem}.txt"


def build_train(counts: Counter, exclude_vids: set[str]) -> None:
    rng = random.Random(20261123)
    nrng = np.random.default_rng(20261123)
    for ds, split, env in TRAIN_SRC:
        for p, lbl in iter_images(ds, split):
            if ds == "REBA_floatwaste" and vid(p.stem) in exclude_vids:
                continue
            img = cv2.imread(str(p))
            if img is None:
                continue
            h, w = img.shape[:2]
            boxes = load_boxes(lbl, w, h)
            sub = "val" if int(hashlib.md5(p.stem.encode()).hexdigest(), 16) % 10 == 0 else "train"
            for bi, b in enumerate(boxes):
                bw, bh = b[3] - b[1], b[4] - b[2]
                if max(bw / w, bh / h) < MIN_REL or max(bw, bh) < MIN_PX:
                    continue
                n = VARIANTS[(env, b[0])] if sub == "train" else 1
                made = 0
                for _ in range(n * 3):
                    if made >= n:
                        break
                    win = crop_window(b, w, h, rng.uniform(0.35, 0.95), rng, 0.35)
                    if not clean_window(win, b, boxes):
                        continue
                    frame = esp32_degrade(to_frame(img, win), nrng)
                    save(frame, sub, CLASSES[b[0]], f"{ds[5:]}_{p.stem}_{bi}_{made}", counts)
                    made += 1
            if rng.random() < FONDO_PER_IMG[env] or not boxes:
                win = fondo_window(boxes, w, h, rng)
                if win is not None:
                    frame = esp32_degrade(to_frame(img, win), nrng)
                    save(frame, sub, FONDO, f"{ds[5:]}_{p.stem}_bg", counts)


def build_tests(counts: Counter) -> None:
    for name, sources in TEST_SRC.items():
        rng = random.Random(7)
        nrng = np.random.default_rng(7)
        for ds, split, _env in sources:
            for p, lbl in iter_images(ds, split):
                img = cv2.imread(str(p))
                if img is None:
                    continue
                h, w = img.shape[:2]
                boxes = load_boxes(lbl, w, h)
                for bi, b in enumerate(boxes):
                    bw, bh = b[3] - b[1], b[4] - b[2]
                    if max(bw / w, bh / h) < MIN_REL or max(bw, bh) < MIN_PX:
                        continue
                    win = None
                    for frac in (0.65, 0.8, 0.95):
                        cand = crop_window(b, w, h, frac, rng, 0.2)
                        if clean_window(cand, b, boxes):
                            win = cand
                            break
                    if win is None:
                        continue
                    clean = to_frame(img, win)
                    stem = f"{ds[5:]}_{p.stem}_{bi}"
                    save(clean, f"{name}_limpio", CLASSES[b[0]], stem, counts)
                    save(esp32_degrade(clean, nrng), name, CLASSES[b[0]], stem, counts)
                win = fondo_window(boxes, w, h, rng)
                if win is not None and rng.random() < 0.5:
                    clean = to_frame(img, win)
                    stem = f"{ds[5:]}_{p.stem}_bg"
                    save(clean, f"{name}_limpio", FONDO, stem, counts)
                    save(esp32_degrade(clean, nrng), name, FONDO, stem, counts)


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    exclude = {vid(p.stem) for p in (DS / "REBA_real_val" / "images" / "val").glob("*")}
    counts: Counter = Counter()
    build_train(counts, exclude)
    build_tests(counts)
    for split in sorted({s for s, _ in counts}):
        row = {c: counts[(split, c)] for c in CLASSES + [FONDO]}
        print(f"{split:20s} {row}  total={sum(row.values())}")


if __name__ == "__main__":
    main()
