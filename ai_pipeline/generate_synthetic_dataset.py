#!/usr/bin/env python3
"""
Genera un dataset YOLO sintético para bootstrap del modelo REBA.

Crea fondos tipo agua + objetos por clase (plastico, tela, organico, unicel, metal)
con anotaciones YOLO normalizadas. Suficiente para entrenar YOLOv8n localmente
cuando no hay fotos reales ni API OpenAI.

Uso:
    python ai_pipeline/generate_synthetic_dataset.py --n-train 240 --n-val 60 --n-test 60
"""


from __future__ import annotations

import argparse
import random
from pathlib import Path

import cv2
import numpy as np

CLASSES = ("plastico", "tela", "organico", "unicel", "metal")
IMG_SIZE = 640


def water_background(rng: random.Random) -> np.ndarray:
    """Fondo azul-verdoso con ondas suaves y ruido."""
    img = np.zeros((IMG_SIZE, IMG_SIZE, 3), dtype=np.uint8)
    base = np.array(
        [rng.randint(40, 70), rng.randint(90, 130), rng.randint(100, 150)],
        dtype=np.float32,
    )
    yy, xx = np.mgrid[0:IMG_SIZE, 0:IMG_SIZE]
    wave = (
        12 * np.sin(xx / rng.uniform(28, 55) + rng.uniform(0, 6))
        + 10 * np.cos(yy / rng.uniform(22, 48) + rng.uniform(0, 6))
    )
    for c in range(3):
        channel = base[c] + wave + rng.uniform(-8, 8)
        img[:, :, c] = np.clip(channel, 0, 255).astype(np.uint8)
    noise = np.random.default_rng(rng.randint(0, 10_000_000)).integers(
        -6, 7, size=img.shape, dtype=np.int16
    )
    img = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    return img


def draw_plastico(img: np.ndarray, rng: random.Random) -> tuple[int, int, int, int]:
    """Botella / fragmento plástico (cian-azul, forma alargada)."""
    w = rng.randint(40, 110)
    h = rng.randint(70, 180)
    x1 = rng.randint(10, IMG_SIZE - w - 10)
    y1 = rng.randint(10, IMG_SIZE - h - 10)
    color = (rng.randint(180, 255), rng.randint(120, 200), rng.randint(40, 100))
    overlay = img.copy()
    cv2.ellipse(overlay, (x1 + w // 2, y1 + h // 2), (w // 2, h // 2), 0, 0, 360, color, -1)
    # "cuello" de botella
    neck_w = max(8, w // 4)
    cv2.rectangle(
        overlay,
        (x1 + w // 2 - neck_w // 2, max(0, y1 - h // 8)),
        (x1 + w // 2 + neck_w // 2, y1 + h // 10),
        color,
        -1,
    )
    cv2.addWeighted(overlay, 0.85, img, 0.15, 0, img)
    y0 = max(0, y1 - h // 8)
    return x1, y0, x1 + w, y1 + h


def draw_tela(img: np.ndarray, rng: random.Random) -> tuple[int, int, int, int]:
    """Trapo / tela (morado-rosa, polígono irregular)."""
    cx = rng.randint(80, IMG_SIZE - 80)
    cy = rng.randint(80, IMG_SIZE - 80)
    pts = []
    for a in range(0, 360, 40):
        r = rng.randint(35, 90)
        rad = np.deg2rad(a + rng.randint(-15, 15))
        pts.append([int(cx + r * np.cos(rad)), int(cy + r * np.sin(rad))])
    pts_arr = np.array(pts, dtype=np.int32)
    color = (rng.randint(160, 230), rng.randint(60, 140), rng.randint(160, 240))
    overlay = img.copy()
    cv2.fillPoly(overlay, [pts_arr], color)
    # pliegues
    for _ in range(3):
        p1 = (rng.randint(cx - 40, cx + 40), rng.randint(cy - 40, cy + 40))
        p2 = (rng.randint(cx - 40, cx + 40), rng.randint(cy - 40, cy + 40))
        cv2.line(overlay, p1, p2, tuple(max(0, c - 40) for c in color), 2)
    cv2.addWeighted(overlay, 0.8, img, 0.2, 0, img)
    x1, y1 = pts_arr[:, 0].min(), pts_arr[:, 1].min()
    x2, y2 = pts_arr[:, 0].max(), pts_arr[:, 1].max()
    return int(x1), int(y1), int(x2), int(y2)


def draw_organico(img: np.ndarray, rng: random.Random) -> tuple[int, int, int, int]:
    """Restos orgánicos (verde-marrón, manchas)."""
    x1 = rng.randint(20, IMG_SIZE - 140)
    y1 = rng.randint(20, IMG_SIZE - 140)
    w = rng.randint(50, 120)
    h = rng.randint(40, 110)
    overlay = img.copy()
    for _ in range(rng.randint(3, 7)):
        ox = x1 + rng.randint(0, w)
        oy = y1 + rng.randint(0, h)
        rw = rng.randint(12, 35)
        rh = rng.randint(10, 30)
        color = (rng.randint(20, 80), rng.randint(80, 160), rng.randint(40, 100))
        cv2.ellipse(overlay, (ox, oy), (rw, rh), rng.randint(0, 180), 0, 360, color, -1)
    cv2.addWeighted(overlay, 0.9, img, 0.1, 0, img)
    return x1, y1, x1 + w, y1 + h


def draw_unicel(img: np.ndarray, rng: random.Random) -> tuple[int, int, int, int]:
    """Unicel / foam (blanco-crema, rectángulo irregular)."""
    w = rng.randint(50, 140)
    h = rng.randint(40, 100)
    x1 = rng.randint(10, IMG_SIZE - w - 10)
    y1 = rng.randint(10, IMG_SIZE - h - 10)
    color = (rng.randint(210, 255), rng.randint(210, 255), rng.randint(200, 240))
    overlay = img.copy()
    cv2.rectangle(overlay, (x1, y1), (x1 + w, y1 + h), color, -1)
    # poros
    for _ in range(25):
        px = rng.randint(x1, x1 + w - 1)
        py = rng.randint(y1, y1 + h - 1)
        cv2.circle(overlay, (px, py), rng.randint(1, 3), (180, 180, 180), -1)
    cv2.addWeighted(overlay, 0.88, img, 0.12, 0, img)
    return x1, y1, x1 + w, y1 + h


def draw_metal(img: np.ndarray, rng: random.Random) -> tuple[int, int, int, int]:
    """Lata / metal (gris plateado con brillo)."""
    w = rng.randint(45, 100)
    h = rng.randint(60, 140)
    x1 = rng.randint(10, IMG_SIZE - w - 10)
    y1 = rng.randint(10, IMG_SIZE - h - 10)
    base = rng.randint(140, 190)
    color = (base, base, base + rng.randint(0, 20))
    overlay = img.copy()
    cv2.rectangle(overlay, (x1, y1), (x1 + w, y1 + h), color, -1)
    # highlight
    cv2.rectangle(
        overlay,
        (x1 + w // 5, y1 + 4),
        (x1 + w // 5 + max(4, w // 8), y1 + h - 4),
        (min(255, base + 50),) * 3,
        -1,
    )
    # etiqueta de color
    label_c = (
        rng.randint(0, 255),
        rng.randint(0, 255),
        rng.randint(0, 255),
    )
    cv2.rectangle(
        overlay,
        (x1 + 4, y1 + h // 3),
        (x1 + w - 4, y1 + 2 * h // 3),
        label_c,
        -1,
    )
    cv2.addWeighted(overlay, 0.9, img, 0.1, 0, img)
    return x1, y1, x1 + w, y1 + h


DRAWERS = (draw_plastico, draw_tela, draw_organico, draw_unicel, draw_metal)


def clip_box(x1, y1, x2, y2) -> tuple[int, int, int, int]:
    x1 = int(np.clip(x1, 0, IMG_SIZE - 1))
    y1 = int(np.clip(y1, 0, IMG_SIZE - 1))
    x2 = int(np.clip(x2, 0, IMG_SIZE - 1))
    y2 = int(np.clip(y2, 0, IMG_SIZE - 1))
    if x2 <= x1:
        x2 = min(IMG_SIZE - 1, x1 + 1)
    if y2 <= y1:
        y2 = min(IMG_SIZE - 1, y1 + 1)
    return x1, y1, x2, y2


def to_yolo(x1, y1, x2, y2) -> tuple[float, float, float, float]:
    bw = x2 - x1
    bh = y2 - y1
    xc = x1 + bw / 2
    yc = y1 + bh / 2
    return xc / IMG_SIZE, yc / IMG_SIZE, bw / IMG_SIZE, bh / IMG_SIZE


def make_image(rng: random.Random) -> tuple[np.ndarray, list[str]]:
    img = water_background(rng)
    labels: list[str] = []
    n_objects = rng.randint(1, 4)
    used_classes = rng.sample(range(len(CLASSES)), k=min(n_objects, len(CLASSES)))
    # permitir repetir si n_objects > clases muestreadas
    while len(used_classes) < n_objects:
        used_classes.append(rng.randrange(len(CLASSES)))

    for cid in used_classes:
        x1, y1, x2, y2 = clip_box(*DRAWERS[cid](img, rng))
        xc, yc, bw, bh = to_yolo(x1, y1, x2, y2)
        if bw < 0.02 or bh < 0.02:
            continue
        labels.append(f"{cid} {xc:.6f} {yc:.6f} {bw:.6f} {bh:.6f}")

    # blur leve / JPEG-like noise
    if rng.random() < 0.4:
        k = rng.choice([3, 5])
        img = cv2.GaussianBlur(img, (k, k), 0)
    return img, labels


def write_split(out_root: Path, split: str, n: int, seed: int) -> None:
    img_dir = out_root / "images" / split
    lbl_dir = out_root / "labels" / split
    img_dir.mkdir(parents=True, exist_ok=True)
    lbl_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    for i in range(n):
        img, labels = make_image(rng)
        stem = f"{split}_{i:04d}"
        cv2.imwrite(str(img_dir / f"{stem}.jpg"), img, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        (lbl_dir / f"{stem}.txt").write_text("\n".join(labels) + ("\n" if labels else ""), encoding="utf-8")
    # Usar ASCII para que el script también funcione desde consolas Windows CP1252.
    print(f"[{split}] {n} imagenes -> {img_dir}")


def write_data_yaml(out_root: Path) -> None:
    names = "\n".join(f"  {i}: {n}" for i, n in enumerate(CLASSES))
    content = (
        f"# REBA synthetic bootstrap dataset\n"
        f"train: images/train\n"
        f"val: images/val\n"
        f"test: images/test\n"
        f"names:\n{names}\n"
    )
    (out_root / "data.yaml").write_text(content, encoding="utf-8")
    print(f"data.yaml -> {out_root / 'data.yaml'}")



def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, default=Path("datasets/REBA_synthetic"))
    p.add_argument("--n-train", type=int, default=240)
    p.add_argument("--n-val", type=int, default=60)
    p.add_argument("--n-test", type=int, default=60)
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    write_split(args.output, "train", args.n_train, args.seed)
    write_split(args.output, "val", args.n_val, args.seed + 1)
    write_split(args.output, "test", args.n_test, args.seed + 2)

    write_data_yaml(args.output)
    print("Dataset sintético listo.")


if __name__ == "__main__":
    main()

