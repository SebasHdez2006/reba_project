#!/usr/bin/env python3
"""
REBA — Descarga un subconjunto de TACO (Trash Annotations in Context) y lo
convierte a formato YOLO con las 5 clases del proyecto.

TACO contiene ~1500 fotos reales de basura "in the wild" (sucia, arrugada,
semienterrada, con oclusiones) anotadas en formato COCO. Este script:

1. Descarga `annotations.json` del repositorio oficial de TACO (o lo lee de
   una copia local con --annotations-file).
2. Mapea las 60 categorías COCO de TACO a las 5 clases REBA
   (tabla TACO_TO_REBA editable más abajo). Las categorías sin clase REBA
   equivalente (vidrio, papel, cartón, cigarros, etc.) se ignoran y quedan
   como fondo — el modelo aprende a no detectarlas.
3. Descarga las imágenes desde los URLs registrados en las anotaciones
   (img.tacodataset.org, espejo CDN del proyecto) y genera:
       datasets/REBA_taco/
       ├── data.yaml
       ├── images/train|val|test/*.jpg
       ├── labels/train|val|test/*.txt
       └── manifest.jsonl          (trazabilidad: url, tamaño, categorías)

Las etiquetas YOLO son normalizadas, así que son válidas aunque la imagen se
redimensione al guardarse.

Uso (desde la raíz REBA/, con el venv activo):
    python ai_pipeline/download_taco.py --limit 400
    python ai_pipeline/download_taco.py --limit 400 --per-class 120

Requiere Internet SOLO para el bootstrap del dataset (igual que
download_openverse_dataset.py); el modelo final sigue siendo 100% offline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import random
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import httpx
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent

CLASSES: Tuple[str, ...] = ("plastico", "unicel", "metal")

DEFAULT_ANNOTATIONS_URL = (
    "https://raw.githubusercontent.com/pedropro/TACO/master/data/annotations.json"
)
DEFAULT_TIMEOUT = 30.0
DEFAULT_USER_AGENT = "REBADatasetBot/1.0 (+local training; TACO dataset)"

logger = logging.getLogger("REBA.taco")

# ---------------------------------------------------------------------------
# Mapeo TACO (category_id COCO → nombre de clase REBA).
# Las categorías ausentes de esta tabla se ignoran por completo.
# ---------------------------------------------------------------------------
TACO_TO_REBA: Dict[int, str] = {
    # plastico
    2: "plastico",   # Aluminium blister pack
    3: "plastico",   # Carded blister pack
    4: "plastico",   # Other plastic bottle
    5: "plastico",   # Clear plastic bottle
    7: "plastico",   # Plastic bottle cap
    21: "plastico",  # Disposable plastic cup
    24: "plastico",  # Other plastic cup
    27: "plastico",  # Plastic lid
    29: "plastico",  # Other plastic
    35: "plastico",  # Plastified paper bag
    36: "plastico",  # Plastic film
    37: "plastico",  # Six pack rings
    38: "plastico",  # Garbage bag
    39: "plastico",  # Other plastic wrapper
    40: "plastico",  # Single-use carrier bag
    41: "plastico",  # Polypropylene bag
    42: "plastico",  # Crisp packet
    43: "plastico",  # Spread tub
    44: "plastico",  # Tupperware
    45: "plastico",  # Disposable food container
    47: "plastico",  # Other plastic container
    48: "plastico",  # Plastic glooves
    49: "plastico",  # Plastic utensils
    54: "plastico",  # Squeezable tube
    55: "plastico",  # Plastic straw
    # (tela/organico eliminados del modelo -> quedan como fondo)
    # unicel (espuma / poliestireno expandido)
    22: "unicel",    # Foam cup
    46: "unicel",    # Foam food container
    57: "unicel",    # Styrofoam piece
    # metal
    0: "metal",      # Aluminium foil
    1: "metal",      # Battery
    8: "metal",      # Metal bottle cap
    10: "metal",     # Food Can
    11: "metal",     # Aerosol
    12: "metal",     # Drink can
    28: "metal",     # Metal lid
    50: "metal",     # Pop tab
    52: "metal",     # Scrap metal
}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def deterministic_split(key: str, train_ratio: float, val_ratio: float) -> str:
    bucket = int(sha256_text(key)[:8], 16) % 1000 / 1000.0
    if bucket < train_ratio:
        return "train"
    if bucket < train_ratio + val_ratio:
        return "val"
    return "test"


def coco_bbox_to_yolo(
    bbox: Sequence[float], img_w: int, img_h: int
) -> Optional[Tuple[float, float, float, float]]:
    """COCO [x, y, w, h] píxeles → YOLO normalizado [xc, yc, w, h]."""
    x, y, w, h = (float(v) for v in bbox[:4])
    if w <= 1 or h <= 1 or img_w <= 0 or img_h <= 0:
        return None
    x1 = max(0.0, min(x, img_w))
    y1 = max(0.0, min(y, img_h))
    x2 = max(0.0, min(x + w, img_w))
    y2 = max(0.0, min(y + h, img_h))
    bw, bh = x2 - x1, y2 - y1
    if bw <= 1 or bh <= 1:
        return None
    return (x1 + bw / 2) / img_w, (y1 + bh / 2) / img_h, bw / img_w, bh / img_h


def load_annotations(args: argparse.Namespace) -> dict:
    if args.annotations_file:
        return json.loads(Path(args.annotations_file).read_text(encoding="utf-8"))
    with httpx.Client(timeout=args.timeout, follow_redirects=True,
                      headers={"User-Agent": DEFAULT_USER_AGENT}) as client:
        resp = client.get(args.annotations_url)
        resp.raise_for_status()
        return resp.json()


def decode_and_resize(raw: bytes, max_side: int) -> Optional[np.ndarray]:
    arr = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return None
    h, w = img.shape[:2]
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        img = cv2.resize(img, (int(w * scale), int(h * scale)),
                         interpolation=cv2.INTER_AREA)
    return img


def build_index(annotations: dict, min_area_frac: float):
    """Devuelve {image_id: [(reba_class_id, yolo_box), ...]} e info de imagen."""
    cat_name = {c["id"]: c["name"] for c in annotations["categories"]}
    img_info = {im["id"]: im for im in annotations["images"]}

    mapped: Dict[int, List[Tuple[int, Tuple[float, float, float, float]]]] = {}
    skipped_cats: Dict[str, int] = {}
    for ann in annotations["annotations"]:
        cid = ann.get("category_id")
        reba = TACO_TO_REBA.get(cid)
        if reba is None:
            skipped_cats[cat_name.get(cid, str(cid))] = (
                skipped_cats.get(cat_name.get(cid, str(cid)), 0) + 1
            )
            continue
        info = img_info.get(ann["image_id"])
        if info is None:
            continue
        box = coco_bbox_to_yolo(ann["bbox"], info["width"], info["height"])
        if box is None:
            continue
        if box[2] * box[3] < min_area_frac:
            continue
        mapped.setdefault(ann["image_id"], []).append((CLASSES.index(reba), box))

    if skipped_cats:
        logger.info("Categorías TACO ignoradas (sin clase REBA): %s",
                    sorted(skipped_cats.items(), key=lambda kv: -kv[1])[:15])
    return mapped, img_info


def write_data_yaml(out_root: Path) -> None:
    names = "\n".join(f"  {i}: {n}" for i, n in enumerate(CLASSES))
    content = (
        "# REBA — subconjunto TACO convertido a YOLO (download_taco.py)\n"
        "train: images/train\n"
        "val: images/val\n"
        "test: images/test\n"
        f"names:\n{names}\n"
    )
    (out_root / "data.yaml").write_text(content, encoding="utf-8")


def download(args: argparse.Namespace) -> None:
    out_root = args.output.resolve()
    for split in ("train", "val", "test"):
        (out_root / "images" / split).mkdir(parents=True, exist_ok=True)
        (out_root / "labels" / split).mkdir(parents=True, exist_ok=True)

    logger.info("Cargando anotaciones TACO…")
    coco = load_annotations(args)
    mapped, img_info = build_index(coco, args.min_area_frac)
    logger.info("Imágenes TACO con ≥1 anotación mapeable: %d", len(mapped))

    image_ids = sorted(mapped.keys())
    rng = random.Random(args.seed)
    rng.shuffle(image_ids)

    # Prioriza imágenes que contienen clases escasas.
    priority = {c.strip() for c in args.prioritize_classes.split(",") if c.strip()}
    if priority:
        def has_priority(img_id: int) -> bool:
            return any(CLASSES[c] in priority for c, _ in mapped[img_id])
        image_ids = (
            [i for i in image_ids if has_priority(i)]
            + [i for i in image_ids if not has_priority(i)]
        )

    # Reanudación: ignora imágenes ya descargadas (por manifest o archivo).
    done_ids = set()
    manifest_path = out_root / "manifest.jsonl"
    if manifest_path.is_file():
        for line in manifest_path.read_text(encoding="utf-8").splitlines():
            try:
                done_ids.add(int(json.loads(line)["taco_image_id"]))
            except Exception:
                continue
    for split in ("train", "val", "test"):
        for f in (out_root / "images" / split).glob("taco_*.jpg"):
            done_ids.add(int(f.stem.split("_")[1]))
    if done_ids:
        image_ids = [i for i in image_ids if i not in done_ids]
        logger.info("Ya existen %d imágenes; reanudando.", len(done_ids))

    per_class_count: Dict[str, int] = {c: 0 for c in CLASSES}
    manifest: List[dict] = []
    if manifest_path.is_file():
        manifest.extend(
            json.loads(line)
            for line in manifest_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    failures = 0
    downloaded = 0

    with httpx.Client(timeout=args.timeout, follow_redirects=True,
                      headers={"User-Agent": DEFAULT_USER_AGENT}) as client:
        for img_id in image_ids:
            if args.limit > 0 and downloaded >= args.limit:
                break
            if failures >= args.max_failures:
                logger.warning("Demasiados fallos (%d); deteniendo.", failures)
                break

            anns = mapped[img_id]
            classes_here = {CLASSES[c] for c, _ in anns}
            if args.per_class > 0 and all(
                per_class_count[c] >= args.per_class for c in classes_here
            ):
                continue

            info = img_info[img_id]
            urls = [info.get("flickr_url"), info.get("flickr_640_url")]
            urls = [u for u in urls if u]

            img = None
            used_url = None
            for url in urls:
                try:
                    raw = client.get(url).content
                    img = decode_and_resize(raw, args.max_side)
                    if img is not None:
                        used_url = url
                        break
                except Exception as exc:
                    logger.debug("Descarga fallida %s: %s", url, exc)
            if img is None:
                failures += 1
                continue

            split = deterministic_split(
                f"taco-{img_id}", args.train_ratio, args.val_ratio
            )
            stem = f"taco_{img_id:05d}"
            img_path = out_root / "images" / split / f"{stem}.jpg"
            lbl_path = out_root / "labels" / split / f"{stem}.txt"

            ok = cv2.imwrite(
                str(img_path), img, [int(cv2.IMWRITE_JPEG_QUALITY), 90]
            )
            if not ok:
                failures += 1
                continue

            lines = [
                f"{c} {b[0]:.6f} {b[1]:.6f} {b[2]:.6f} {b[3]:.6f}"
                for c, b in anns
            ]
            lbl_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            for c in classes_here:
                per_class_count[c] += 1
            manifest.append(
                {
                    "taco_image_id": img_id,
                    "file_name": info.get("file_name"),
                    "source_url": used_url,
                    "split": split,
                    "local_path": str(img_path.relative_to(out_root).as_posix()),
                    "classes": sorted(classes_here),
                    "n_boxes": len(anns),
                }
            )
            downloaded += 1

            if args.sleep > 0:
                time.sleep(args.sleep)

    if not manifest:
        raise SystemExit(
            "No se descargó ninguna imagen TACO. Revisa la red o usa "
            "--annotations-file con una copia local."
        )

    write_data_yaml(out_root)
    (out_root / "manifest.jsonl").write_text(
        "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in manifest),
        encoding="utf-8",
    )
    summary = {
        "images": len(manifest),
        "failures": failures,
        "per_class_images": per_class_count,
        "classes": list(CLASSES),
    }
    (out_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("TACO listo: %d imágenes en %s | por clase: %s",
                len(manifest), out_root, per_class_count)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Descarga subconjunto TACO → YOLO (clases REBA)"
    )
    p.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "datasets" / "REBA_taco",
    )
    p.add_argument(
        "--annotations-url", type=str, default=DEFAULT_ANNOTATIONS_URL,
        help="URL del annotations.json de TACO (formato COCO)",
    )
    p.add_argument(
        "--annotations-file", type=Path, default=None,
        help="Copia local de annotations.json (omite la descarga del índice)",
    )
    p.add_argument("--limit", type=int, default=400,
                   help="Máximo de imágenes a descargar (0 = todas)")
    p.add_argument("--per-class", type=int, default=0,
                   help="Tope de imágenes que contienen cada clase (0 = sin tope)")
    p.add_argument("--prioritize-classes", type=str, default="",
                   help="Clases REBA (CSV) cuyas imágenes se descargan primero")
    p.add_argument("--max-failures", type=int, default=60,
                   help="Abortar tras N descargas fallidas consecutivas")
    p.add_argument("--min-area-frac", type=float, default=0.0005,
                   help="Descarta anotaciones con área < fracción de la imagen")
    p.add_argument("--max-side", type=int, default=1024,
                   help="Redimensiona el lado mayor al guardar (JPEG q90)")
    p.add_argument("--train-ratio", type=float, default=0.8)
    p.add_argument("--val-ratio", type=float, default=0.1)
    p.add_argument("--sleep", type=float, default=0.1,
                   help="Pausa entre descargas (segundos)")
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    p.add_argument("--seed", type=int, default=42)
    return p.parse_args(argv)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    args = parse_args()
    download(args)
