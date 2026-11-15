#!/usr/bin/env python3
"""
REBA — Descarga el dataset EcoSort (Hugging Face) y lo convierte a YOLO con
las 5 clases del proyecto.

EcoSort (`qihang3426/ecosort-dataset`) es un dataset de detección de basura
real ya fusionado y deduplicado (Open Images V7 + TACO + dmedhi + Roboflow +
Kaggle drinking waste), publicado en formato YOLO dentro de `dataset.tar`
(~1.5 GB, solo bootstrap — el modelo final sigue siendo 100% offline).

El script:

1. Descarga `dataset.tar` desde el Hub de Hugging Face (URL resoluble por HTTP,
   sin API key) a una carpeta temporal.
2. Extrae `images/<split>` y `labels/<split>` (labels YOLO de 10 clases).
3. Remapea las 10 clases EcoSort a las 5 clases REBA con la tabla
   `ECOSORT_TO_REBA` (botellas/bolsas/envases plásticos y snacks → `plastico`;
   latas/alimentos en lata/baterías → `metal`). Vidrio, cartón y papel se
   descartan: quedan como fondo — el modelo aprende a no detectarlos.
4. Conserva solo imágenes con ≥1 caja mapeable (misma convención que
   `download_taco.py`) y aplica `--limit`/`--seed` para un subconjunto
   determinista (el dataset completo tiene ~7k imágenes; demasiado para el
   entrenamiento en CPU).
5. Escribe `datasets/REBA_ecosort/` con `images/`, `labels/`, `data.yaml`,
   `manifest.jsonl` y `summary.json`.

Uso (desde la raíz REBA/, con el venv activo):
    python ai_pipeline/download_ecosort.py --limit 800
    python ai_pipeline/download_ecosort.py --limit 0   # todo el tar
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import httpx
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent

CLASSES: Tuple[str, ...] = ("plastico", "unicel", "metal")

DEFAULT_DATASET_URL = (
    "https://huggingface.co/datasets/qihang3426/ecosort-dataset"
    "/resolve/main/dataset.tar"
)
DEFAULT_TIMEOUT = 60.0
DEFAULT_USER_AGENT = "REBADatasetBot/1.0 (+local training; EcoSort dataset)"
CHUNK = 1 << 20  # 1 MiB

logger = logging.getLogger("REBA.ecosort")

# ---------------------------------------------------------------------------
# Mapeo EcoSort (class_id YOLO original → nombre de clase REBA).
# Clases ausentes de la tabla se descartan (quedan como fondo).
# ---------------------------------------------------------------------------
ECOSORT_TO_REBA: Dict[int, str] = {
    0: "plastico",  # plastic_bottle
    1: "plastico",  # plastic_bag
    2: "plastico",  # plastic_food_container
    3: "plastico",  # snack_bag
    4: "metal",     # aluminum_can
    5: "metal",     # food_can
    9: "metal",     # battery
    # 6 glass_bottle, 7 cardboard_box, 8 paper_sheet → sin clase REBA (fondo)
}


def write_data_yaml(out_root: Path) -> None:
    names = "\n".join(f"  {i}: {n}" for i, n in enumerate(CLASSES))
    content = (
        "# REBA — subconjunto EcoSort convertido a YOLO (download_ecosort.py)\n"
        "train: images/train\n"
        "val: images/val\n"
        "names:\n"
        f"{names}\n"
    )
    (out_root / "data.yaml").write_text(content, encoding="utf-8")


def remap_label_text(text: str) -> Tuple[List[str], Dict[str, int]]:
    """Reescribe un label YOLO de EcoSort a ids REBA; devuelve (líneas, stats)."""
    out_lines: List[str] = []
    dropped: Dict[str, int] = {}
    for raw in text.splitlines():
        parts = raw.split()
        if len(parts) != 5:
            continue
        try:
            src_id = int(parts[0])
            coords = [float(v) for v in parts[1:]]
        except ValueError:
            continue
        reba = ECOSORT_TO_REBA.get(src_id)
        if reba is None:
            dropped[str(src_id)] = dropped.get(str(src_id), 0) + 1
            continue
        dst_id = CLASSES.index(reba)
        out_lines.append(
            f"{dst_id} {coords[0]:.6f} {coords[1]:.6f} "
            f"{coords[2]:.6f} {coords[3]:.6f}"
        )
    return out_lines, dropped


def find_split_dirs(root: Path) -> Dict[str, Tuple[Path, Optional[Path]]]:
    """Localiza `images/<split>` y su `labels/<split>` hermano dentro del tar
    extraído (admite cualquier nivel de anidación)."""
    found: Dict[str, Tuple[Path, Optional[Path]]] = {}
    for img_root in root.rglob("images"):
        if not img_root.is_dir():
            continue
        lbl_root = img_root.parent / "labels"
        for split in ("train", "val", "test"):
            img_dir = img_root / split
            if img_dir.is_dir() and split not in found:
                lbl_dir = lbl_root / split
                found[split] = (
                    img_dir,
                    lbl_dir if lbl_dir.is_dir() else None,
                )
    return found


def download_tar(url: str, dest: Path, timeout: float) -> None:
    """Descarga el tar con barra de progreso simple y reanudación por tamaño."""
    headers = {"User-Agent": DEFAULT_USER_AGENT}
    existing = dest.stat().st_size if dest.is_file() else 0
    if existing:
        headers["Range"] = f"bytes={existing}-"
        logger.info("Reanudando descarga en %d bytes…", existing)
    with httpx.Client(timeout=timeout, follow_redirects=True,
                      headers=headers) as client:
        with client.stream("GET", url) as resp:
            if existing and resp.status_code == 200:
                # El servidor ignoró Range: empezar de cero.
                existing = 0
                dest.unlink(missing_ok=True)
            resp.raise_for_status()
            mode = "ab" if existing else "wb"
            total = int(resp.headers.get("content-length", 0)) + existing
            done = existing
            with dest.open(mode) as fh:
                for chunk in resp.iter_bytes(CHUNK):
                    fh.write(chunk)
                    done += len(chunk)
                    if total:
                        pct = done * 100 // total
                        print(
                            f"\rDescargando dataset.tar: {pct:3d}% "
                            f"({done / 1e6:.0f}/{total / 1e6:.0f} MB)",
                            end="", flush=True,
                        )
            print()


def convert_split(
    img_dir: Path,
    lbl_dir: Optional[Path],
    out_root: Path,
    split: str,
    limit: int,
    seed: int,
    stats: Dict[str, object],
) -> None:
    """Convierte un split (train/val) de EcoSort a YOLO-REBA."""
    image_files = sorted(
        p for p in img_dir.iterdir()
        if p.suffix.lower() in (".jpg", ".jpeg", ".png")
    )
    rng = random.Random(seed)
    rng.shuffle(image_files)

    written = 0
    for img_path in image_files:
        if limit > 0 and written >= limit:
            break
        stem = img_path.stem
        raw_label = ""
        if lbl_dir is not None:
            lbl_file = lbl_dir / f"{stem}.txt"
            if lbl_file.is_file():
                raw_label = lbl_file.read_text(encoding="utf-8", errors="replace")
        lines, dropped = remap_label_text(raw_label)
        dropped_boxes = stats["dropped_boxes_by_ecosort_class"]
        assert isinstance(dropped_boxes, dict)
        for k, v in dropped.items():
            dropped_boxes[k] = dropped_boxes.get(k, 0) + v
        if not lines:
            stats["skipped_no_mapped"] += 1
            continue

        img = cv2.imread(str(img_path), cv2.IMREAD_COLOR)
        if img is None:
            stats["failures"] += 1
            continue

        out_stem = f"eco_{split}_{written:05d}"
        out_img = out_root / "images" / split / f"{out_stem}.jpg"
        ok = cv2.imwrite(
            str(out_img), img, [int(cv2.IMWRITE_JPEG_QUALITY), 90]
        )
        if not ok:
            stats["failures"] += 1
            continue
        (out_root / "labels" / split / f"{out_stem}.txt").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

        classes_here = {CLASSES[int(l.split()[0])] for l in lines}
        per_class = stats["per_class_images"]
        assert isinstance(per_class, dict)
        for c in classes_here:
            per_class[c] += 1
        stats["manifest"].append(
            {
                "ecosort_file": img_path.name,
                "source_split": split,
                "local_path": str(out_img.relative_to(out_root).as_posix()),
                "classes": sorted(classes_here),
                "n_boxes": len(lines),
            }
        )
        written += 1


def convert(args: argparse.Namespace) -> None:
    out_root = args.output.resolve()
    for split in ("train", "val"):
        (out_root / "images" / split).mkdir(parents=True, exist_ok=True)
        (out_root / "labels" / split).mkdir(parents=True, exist_ok=True)

    work_dir = Path(tempfile.mkdtemp(prefix="ecosort_"))
    try:
        tar_path = args.tar_file
        if tar_path is None:
            tar_path = work_dir / "dataset.tar"
            logger.info("Descargando %s → %s", args.dataset_url, tar_path)
            download_tar(args.dataset_url, tar_path, args.timeout)
        else:
            tar_path = Path(tar_path)
            logger.info("Usando tar local: %s", tar_path)

        extract_dir = work_dir / "extracted"
        extract_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Extrayendo tar → %s", extract_dir)
        with tarfile.open(tar_path, "r:*") as tar:
            tar.extractall(extract_dir, filter="data")

        splits = find_split_dirs(extract_dir)
        if not splits:
            raise SystemExit(
                "No se encontraron carpetas images/<split> en el tar de "
                "EcoSort. Revisa la estructura del archivo."
            )
        logger.info("Splits encontrados en el tar: %s", sorted(splits))

        stats: Dict[str, object] = {
            "manifest": [],
            "per_class_images": {c: 0 for c in CLASSES},
            "dropped_boxes_by_ecosort_class": {},
            "skipped_no_mapped": 0,
            "failures": 0,
        }
        limits = {"train": args.limit, "val": args.val_limit,
                  "test": args.limit}
        for split in ("train", "val"):
            if split not in splits:
                continue
            img_dir, lbl_dir = splits[split]
            logger.info("Procesando split %s (%d imágenes)…",
                        split, sum(1 for _ in img_dir.iterdir()))
            convert_split(
                img_dir, lbl_dir, out_root, split,
                limits.get(split, 0), args.seed, stats,
            )
        manifest: List[dict] = stats["manifest"]  # type: ignore[assignment]
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    if not manifest:
        raise SystemExit(
            "No se extrajo ninguna imagen mapeable de EcoSort. Revisa el tar."
        )

    write_data_yaml(out_root)
    (out_root / "manifest.jsonl").write_text(
        "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in manifest),
        encoding="utf-8",
    )
    summary = {
        "source": "qihang3426/ecosort-dataset (Hugging Face)",
        "url": args.dataset_url,
        "images": len(manifest),
        "skipped_no_mapped_boxes": stats["skipped_no_mapped"],
        "failures": stats["failures"],
        "per_class_images": stats["per_class_images"],
        "dropped_boxes_by_ecosort_class":
            stats["dropped_boxes_by_ecosort_class"],
        "class_map": {str(k): v for k, v in ECOSORT_TO_REBA.items()},
        "classes": list(CLASSES),
    }
    (out_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(
        "EcoSort listo: %d imágenes en %s | por clase: %s | sin mapeo: %s",
        len(manifest), out_root, stats["per_class_images"],
        stats["skipped_no_mapped"],
    )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Descarga EcoSort (HF) → YOLO con clases REBA"
    )
    p.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "datasets" / "REBA_ecosort",
    )
    p.add_argument(
        "--dataset-url", type=str, default=DEFAULT_DATASET_URL,
        help="URL resolve del dataset.tar en Hugging Face",
    )
    p.add_argument(
        "--tar-file", type=Path, default=None,
        help="Usa un dataset.tar ya descargado (omite la descarga de 1.5 GB)",
    )
    p.add_argument("--limit", type=int, default=800,
                   help="Máximo de imágenes de train a convertir (0 = todas)")
    p.add_argument("--val-limit", type=int, default=200,
                   help="Máximo de imágenes de val a convertir (0 = ninguna)")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    return p.parse_args(argv)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    convert(parse_args())
