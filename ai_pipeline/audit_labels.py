#!/usr/bin/env python3
"""
REBA — Auditoría de etiquetas YOLO por clase.

Genera hojas de contacto (contact sheets) con cajas dibujadas para revisar
visualmente las etiquetas de una clase en todos los datasets, y detecta
problemas automáticos:

- cajas degeneradas (w/h <= 0 tras normalizar, fuera de rango)
- cajas que cubren >90% de la imagen (sospechosas de fondo mal anotado)
- imágenes sin etiqueta en splits que deberían tenerlas (y viceversa)
- posibles duplicados por nombre de archivo entre splits (fugas)

Uso (desde la raíz REBA/, con el venv activo):
    python ai_pipeline/audit_labels.py --class-id 2 --per-dataset 12
    python ai_pipeline/audit_labels.py --class-name organico
"""

from __future__ import annotations

import argparse
import random
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASETS_ROOT = PROJECT_ROOT / "datasets"
CLASSES = ("plastico", "tela", "organico", "unicel", "metal")

# Orígenes que participan en el train combinado (mismo orden que
# OPTIONAL_TRAIN_SOURCES de train_outdoor.py) + eval.
DATASET_DIRS = (
    "REBA_synthetic",
    "REBA_synthetic_boost",
    "REBA_synthetic_boost2",
    "REBA_taco",
    "REBA_ecosort",
    "REBA_outdoor_eval",
)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Auditoría visual de labels YOLO")
    p.add_argument("--class-id", type=int, default=None)
    p.add_argument("--class-name", type=str, default=None)
    p.add_argument("--per-dataset", type=int, default=12,
                   help="Muestras por dataset en la hoja de contacto")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--output", type=Path,
                   default=PROJECT_ROOT / "reports" / "audit")
    return p.parse_args(argv)


def resolve_class_id(args: argparse.Namespace) -> int:
    if args.class_id is not None:
        return args.class_id
    if args.class_name:
        for i, n in enumerate(CLASSES):
            if n == args.class_name:
                return i
        raise SystemExit(f"Clase desconocida: {args.class_name} ({CLASSES})")
    raise SystemExit("Indica --class-id o --class-name")


def yolo_to_xyxy(line: Sequence[float], w: int, h: int) -> Tuple[float, float, float, float]:
    _, xc, yc, bw, bh = (float(v) for v in line)
    return (
        (xc - bw / 2) * w, (yc - bh / 2) * h,
        (xc + bw / 2) * w, (yc + bh / 2) * h,
    )


def find_samples(
    ds_dir: Path, split: str, class_id: int, limit: int, rng: random.Random
) -> List[Tuple[Path, Path]]:
    """Devuelve [(img_path, label_path)] con ≥1 caja de class_id."""
    img_dir = ds_dir / "images" / split
    lbl_dir = ds_dir / "labels" / split
    if not img_dir.is_dir() or not lbl_dir.is_dir():
        return []
    out = []
    for lbl in sorted(lbl_dir.glob("*.txt")):
        try:
            hits = [
                ln for ln in lbl.read_text(encoding="utf-8").splitlines()
                if ln.strip() and int(ln.split()[0]) == class_id
            ]
        except Exception:
            continue
        if not hits:
            continue
        img = img_dir / f"{lbl.stem}.jpg"
        if not img.is_file():
            img = img_dir / f"{lbl.stem}.png"
        if img.is_file():
            out.append((img, lbl))
    rng.shuffle(out)
    return out[:limit]


def draw_boxes(img: np.ndarray, label_path: Path, target_cid: int) -> np.ndarray:
    h, w = img.shape[:2]
    out = img.copy()
    for ln in label_path.read_text(encoding="utf-8").splitlines():
        parts = ln.split()
        if len(parts) != 5:
            continue
        cid = int(parts[0])
        x1, y1, x2, y2 = yolo_to_xyxy([float(v) for v in parts], w, h)
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        is_target = cid == target_cid
        color = (0, 255, 0) if is_target else (0, 140, 255)  # verde=target, naranja=otras
        thick = 3 if is_target else 1
        cv2.rectangle(out, (int(x1), int(y1)), (int(x2), int(y2)), color, thick)
        if is_target:
            cv2.putText(out, CLASSES[cid], (int(x1), max(14, int(y1) - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
    return out


def contact_sheet(cells: List[np.ndarray], cols: int = 4,
                  cell_w: int = 320) -> np.ndarray:
    if not cells:
        return np.zeros((10, 10, 3), dtype=np.uint8)
    resized = []
    for c in cells:
        h, w = c.shape[:2]
        nh = int(cell_w * h / w)
        resized.append(cv2.resize(c, (cell_w, nh), interpolation=cv2.INTER_AREA))
    rh = max(r.shape[0] for r in resized)
    rows = [resized[i:i + cols] for i in range(0, len(resized), cols)]
    sheet_rows = []
    for row in rows:
        pads = [cv2.copyMakeBorder(
            r, 0, rh - r.shape[0], 0, 0, cv2.BORDER_CONSTANT, value=(20, 20, 20)
        ) for r in row]
        while len(pads) < cols:
            pads.append(np.full((rh, cell_w, 3), 20, dtype=np.uint8))
        sheet_rows.append(np.hstack(pads))
    return np.vstack(sheet_rows)


def scan_problems(ds_dir: Path, split: str) -> Dict[str, int]:
    """Detecta problemas automáticos en labels de un split."""
    lbl_dir = ds_dir / "labels" / split
    img_dir = ds_dir / "images" / split
    stats = defaultdict(int)
    if not lbl_dir.is_dir():
        return stats
    stems_img = {p.stem for p in img_dir.glob("*") if p.suffix.lower() in
                 (".jpg", ".jpeg", ".png")} if img_dir.is_dir() else set()
    stems_lbl = set()
    for lbl in lbl_dir.glob("*.txt"):
        stems_lbl.add(lbl.stem)
        stats["labels"] += 1
        for ln in lbl.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = ln.split()
            if len(parts) != 5:
                stats["malformed_lines"] += 1
                continue
            try:
                vals = [float(v) for v in parts]
            except ValueError:
                stats["malformed_lines"] += 1
                continue
            _, xc, yc, bw, bh = vals
            if not (0 <= xc <= 1 and 0 <= yc <= 1 and 0 < bw <= 1 and 0 < bh <= 1):
                stats["out_of_range"] += 1
            if bw * bh > 0.9:
                stats["huge_box_gt90pct"] += 1
            if bw < 0.005 or bh < 0.005:
                stats["tiny_box_lt0.5pct"] += 1
    stats["images_without_label"] = len(stems_img - stems_lbl)
    stats["labels_without_image"] = len(stems_lbl - stems_img)
    return stats


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    cid = resolve_class_id(args)
    args.output.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)

    print(f"=== Auditoría clase {cid} ({CLASSES[cid]}) ===\n")

    # 1) Hoja de contacto por dataset/split
    sheets = []
    for ds_name in DATASET_DIRS:
        ds_dir = DATASETS_ROOT / ds_name
        if not ds_dir.is_dir():
            continue
        for split in ("train", "val", "test"):
            samples = find_samples(ds_dir, split, cid, args.per_dataset, rng)
            if not samples:
                continue
            cells = []
            for img, lbl in samples:
                im = cv2.imread(str(img))
                if im is None:
                    continue
                cells.append(draw_boxes(im, lbl, cid))
            if not cells:
                continue
            sheet = contact_sheet(cells)
            tag = f"{ds_name}/{split}"
            cv2.putText(sheet, tag, (8, 24), cv2.FONT_HERSHEY_SIMPLEX,
                        0.8, (0, 255, 255), 2, cv2.LINE_AA)
            out = args.output / f"audit_c{cid}_{ds_name}_{split}.jpg"
            cv2.imwrite(str(out), sheet, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
            print(f"  {tag}: {len(cells)} muestras -> {out.name}")
            sheets.append(sheet)

    # 2) Problemas automáticos por dataset/split
    print("\n=== Problemas automáticos en labels ===")
    print(f"{'dataset':<26}{'split':<7}{'labels':>7}{'malform':>8}"
          f"{'outrange':>9}{'huge>90%':>9}{'tiny<0.5%':>10}{'img_sin_lbl':>11}"
          f"{'lbl_sin_img':>11}")
    for ds_name in DATASET_DIRS:
        ds_dir = DATASETS_ROOT / ds_name
        if not ds_dir.is_dir():
            continue
        for split in ("train", "val", "test"):
            st = scan_problems(ds_dir, split)
            if not st.get("labels"):
                continue
            print(f"{ds_name:<26}{split:<7}{st['labels']:>7}"
                  f"{st['malformed_lines']:>8}{st['out_of_range']:>9}"
                  f"{st['huge_box_gt90pct']:>9}{st['tiny_box_lt0.5pct']:>10}"
                  f"{st['images_without_label']:>11}{st['labels_without_image']:>11}")

    # 3) Fugas evidentes: mismo stem en train y val/test dentro de un dataset
    print("\n=== Fugas train-val/test (mismo stem) ===")
    leaks = 0
    for ds_name in DATASET_DIRS:
        ds_dir = DATASETS_ROOT / ds_name
        if not ds_dir.is_dir():
            continue
        stems: Dict[str, set] = defaultdict(set)
        for split in ("train", "val", "test"):
            lbl_dir = ds_dir / "labels" / split
            if lbl_dir.is_dir():
                for f in lbl_dir.glob("*.txt"):
                    stems[f.stem].add(split)
        for stem, splits in stems.items():
            if len(splits) > 1:
                leaks += 1
                if leaks <= 10:
                    print(f"  {ds_name}: {stem} en {sorted(splits)}")
    print(f"  Total stems duplicados entre splits: {leaks}")


if __name__ == "__main__":
    main()
