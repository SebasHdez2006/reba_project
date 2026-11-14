#!/usr/bin/env python3
"""
REBA — Auto-etiquetado de dataset con OpenAI Vision (gpt-4o-mini).

Toma imágenes crudas de residuos (descargadas o capturadas), envía cada una a
la API de OpenAI Vision y genera anotaciones YOLO (archivo .txt por imagen).

Formato YOLO por línea:
    <class_id> <x_center> <y_center> <width> <height>
Todos los valores de caja están normalizados a [0, 1] respecto al tamaño de la imagen.

Clases por defecto (class_id):
    0 plastico | 1 unicel | 2 metal

Uso (desde la raíz REBA/):
    python ai_pipeline/dataset_builder.py --input raw_images --output datasets/REBA

Requisitos:
    - Variable de entorno OPENAI_API_KEY (o en .env en la raíz del proyecto)
    - pip install openai python-dotenv Pillow

Documentación: docs/ai_pipeline.md
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


from dotenv import load_dotenv
from PIL import Image

# ---------------------------------------------------------------------------
# Constantes editables
# ---------------------------------------------------------------------------

DEFAULT_CLASSES: Tuple[str, ...] = (
    "plastico",
    "unicel",
    "metal",
)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
SPLITS = ("train", "val", "test")


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("REBA.dataset_builder")

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_api_key() -> str:
    """Carga OPENAI_API_KEY desde entorno o `.env` del proyecto."""
    load_dotenv(PROJECT_ROOT / ".env")
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise SystemExit(
            "OPENAI_API_KEY no configurada. "
            "Cópiala en .env (ver .env.example) o expórtala en el entorno."
        )
    return key


def discover_images(folder: Path) -> List[Path]:
    files = [
        p
        for p in sorted(folder.rglob("*"))
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    ]
    return files


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def safe_stem(path: Path) -> str:
    """Convierte una ruta relativa en un stem estable y compatible con Windows."""
    rel = str(path.as_posix())
    rel = rel.replace("/", "_").replace("\\", "_")
    rel = rel.replace(" ", "_")
    return rel


def infer_split(img_path: Path, input_dir: Path, val_ratio: float, test_ratio: float) -> str:
    """Usa la estructura del directorio si ya contiene train/val/test; si no, hash split."""
    rel = img_path.relative_to(input_dir)
    if rel.parts and rel.parts[0] in SPLITS:
        return rel.parts[0]

    digest = sha256_file(img_path)
    bucket = int(digest[:8], 16) % 1000 / 1000.0
    train_ratio = max(0.0, 1.0 - val_ratio - test_ratio)
    if bucket < train_ratio:
        return "train"
    if bucket < train_ratio + val_ratio:
        return "val"
    return "test"


def sidecar_json_path(path: Path) -> Optional[Path]:
    candidates = [path.with_suffix(".json"), path.with_suffix(".meta.json")]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None



def encode_image_b64(path: Path, max_side: int = 1280) -> Tuple[str, int, int]:
    """
    Codifica la imagen a JPEG base64, redimensionando si el lado mayor > max_side
    (ahorra tokens / latencia). Retorna (b64, width, height) del tamaño enviado.
    """
    with Image.open(path) as im:
        im = im.convert("RGB")
        w, h = im.size
        scale = min(1.0, max_side / max(w, h))
        if scale < 1.0:
            new_size = (int(w * scale), int(h * scale))
            im = im.resize(new_size, Image.Resampling.LANCZOS)
            w, h = new_size
        from io import BytesIO

        buf = BytesIO()
        im.save(buf, format="JPEG", quality=85)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return b64, w, h


def build_prompt(class_names: Sequence[str], width: int, height: int) -> str:
    classes_str = ", ".join(class_names)
    return (
        "Eres un anotador experto de residuos acuáticos flotantes para entrenamiento YOLO.\n"
        f"Clases permitidas (nombres exactos): {classes_str}.\n"
        f"La imagen mide {width}x{height} píxeles (origen arriba-izquierda).\n"
        "Detecta TODOS los residuos visibles. Para cada uno, estima un bounding box "
        "axis-aligned en píxeles: x1, y1, x2, y2.\n"
        "Devuelve SOLO JSON válido con esta forma:\n"
        "{\n"
        '  "detections": [\n'
        '    {"class_name": "plastico", "confidence": 0.92, '
        '"x1": 10, "y1": 20, "x2": 200, "y2": 180}\n'
        "  ]\n"
        "}\n"
        "Si no hay residuos, devuelve {\"detections\": []}."
    )


def call_openai_vision(
    api_key: str,
    model: str,
    b64_jpeg: str,
    prompt: str,
) -> Dict[str, Any]:
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{b64_jpeg}",
                            "detail": "high",
                        },
                    },
                ],
            }
        ],
        max_tokens=1200,
        temperature=0.0,
    )
    text = response.choices[0].message.content or "{}"
    text = text.strip()
    if "```" in text:
        start = text.find("{")
        end = text.rfind("}") + 1
        text = text[start:end]
    return json.loads(text)


def xyxy_to_yolo(
    x1: float, y1: float, x2: float, y2: float, img_w: int, img_h: int
) -> Tuple[float, float, float, float]:
    """Convierte caja píxeles a formato YOLO normalizado."""
    x1 = max(0.0, min(float(x1), img_w))
    x2 = max(0.0, min(float(x2), img_w))
    y1 = max(0.0, min(float(y1), img_h))
    y2 = max(0.0, min(float(y2), img_h))
    bw = max(0.0, x2 - x1)
    bh = max(0.0, y2 - y1)
    xc = x1 + bw / 2.0
    yc = y1 + bh / 2.0
    return xc / img_w, yc / img_h, bw / img_w, bh / img_h


def detections_to_yolo_lines(
    detections: List[Dict[str, Any]],
    class_names: Sequence[str],
    img_w: int,
    img_h: int,
    min_conf: float,
) -> List[str]:
    name_to_id = {n: i for i, n in enumerate(class_names)}
    lines: List[str] = []
    for det in detections:
        cname = str(det.get("class_name", "")).strip().lower()
        if cname not in name_to_id:
            logger.warning("Clase desconocida ignorada: %s", cname)
            continue
        conf = float(det.get("confidence", 1.0))
        if conf < min_conf:
            continue
        xc, yc, bw, bh = xyxy_to_yolo(
            det.get("x1", 0),
            det.get("y1", 0),
            det.get("x2", 0),
            det.get("y2", 0),
            img_w,
            img_h,
        )
        if bw <= 0 or bh <= 0:
            continue
        cid = name_to_id[cname]
        lines.append(f"{cid} {xc:.6f} {yc:.6f} {bw:.6f} {bh:.6f}")
    return lines


def write_data_yaml(out_root: Path, class_names: Sequence[str]) -> None:
    """Escribe data.yaml compatible con Ultralytics YOLOv8."""
    yaml_path = out_root / "data.yaml"
    names_block = "\n".join(f"  {i}: {n}" for i, n in enumerate(class_names))
    content = (
        f"# REBA dataset — generado por dataset_builder.py\n"
        f"train: images/train\n"
        f"val: images/val\n"
        f"test: images/test\n"
        f"names:\n{names_block}\n"
    )
    yaml_path.write_text(content, encoding="utf-8")
    logger.info("Escrito %s", yaml_path)



def ensure_split_dirs(out_root: Path) -> None:
    for split in SPLITS:
        (out_root / "images" / split).mkdir(parents=True, exist_ok=True)
        (out_root / "labels" / split).mkdir(parents=True, exist_ok=True)
        (out_root / "metadata" / split).mkdir(parents=True, exist_ok=True)



def copy_file(src: Path, dst: Path) -> None:
    shutil.copy2(src, dst)



def label_dataset(
    input_dir: Path,
    output_dir: Path,
    class_names: Sequence[str],
    model: str,
    val_ratio: float,
    test_ratio: float,
    min_conf: float,
    limit: Optional[int],
    sleep_s: float,
) -> None:
    load_api_key()
    images = discover_images(input_dir)
    if not images:
        raise SystemExit(f"No se encontraron imágenes en {input_dir}")

    if limit is not None:
        images = images[:limit]

    ensure_split_dirs(output_dir)
    write_data_yaml(output_dir, class_names)

    seen_hashes: set[str] = set()
    split_buckets: Dict[str, List[Path]] = {"train": [], "val": [], "test": []}
    for img_path in images:
        digest = sha256_file(img_path)
        if digest in seen_hashes:
            logger.debug("Duplicado descartado: %s", img_path)
            continue
        seen_hashes.add(digest)
        split = infer_split(img_path, input_dir, val_ratio=val_ratio, test_ratio=test_ratio)
        split_buckets[split].append(img_path)

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit(
            "OPENAI_API_KEY no configurada. Agrégala a .env para el bootstrap o expórtala en el entorno."
        )

    def process_split(files: List[Path], split: str) -> None:
        for idx, img_path in enumerate(files, start=1):
            rel = img_path.relative_to(input_dir)
            digest = sha256_file(img_path)
            stem = f"{safe_stem(rel.with_suffix(''))}_{digest[:10]}"
            ext = img_path.suffix.lower()
            if ext == ".jpeg":
                ext = ".jpg"
            dst_img = output_dir / "images" / split / f"{stem}{ext}"
            dst_lbl = output_dir / "labels" / split / f"{stem}.txt"
            dst_meta = output_dir / "metadata" / split / f"{stem}.json"

            logger.info("[%s %d/%d] %s", split, idx, len(files), rel.as_posix())
            b64, w, h = encode_image_b64(img_path)
            prompt = build_prompt(class_names, w, h)
            try:
                data = call_openai_vision(api_key, model, b64, prompt)
            except Exception as exc:
                logger.error("Fallo API en %s: %s", img_path.name, exc)
                continue

            lines = detections_to_yolo_lines(
                data.get("detections", []), class_names, w, h, min_conf
            )
            copy_file(img_path, dst_img)
            dst_lbl.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

            sidecar = sidecar_json_path(img_path)
            if sidecar is not None:
                copy_file(sidecar, dst_meta)
            else:
                dst_meta.write_text(
                    json.dumps(
                        {
                            "source_image": rel.as_posix(),
                            "sha256": digest,
                            "width": w,
                            "height": h,
                            "labels": len(lines),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            logger.info("  → %d cajas", len(lines))
            if sleep_s > 0:
                time.sleep(sleep_s)

    for split in SPLITS:
        process_split(split_buckets[split], split)
    logger.info("Dataset listo en %s", output_dir.resolve())



def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Auto-etiquetado YOLO con OpenAI gpt-4o-mini Vision (REBA)"
    )
    p.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Carpeta con imágenes crudas",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "datasets" / "REBA",
        help="Carpeta de salida del dataset YOLO",
    )
    p.add_argument(
        "--classes",
        type=str,
        default=",".join(DEFAULT_CLASSES),
        help="Clases separadas por coma (orden = class_id)",
    )
    p.add_argument(
        "--model",
        type=str,
        default=os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini"),
        help="Modelo Vision de OpenAI",
    )
    p.add_argument(
        "--val-ratio",
        type=float,
        default=0.2,
        help="Fracción de imágenes para validación (default 0.2)",
    )
    p.add_argument(
        "--test-ratio",
        type=float,
        default=0.1,
        help="Fracción de imágenes para test (default 0.1)",
    )
    p.add_argument(
        "--min-conf",
        type=float,
        default=0.35,
        help="Confianza mínima para aceptar una caja",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Procesar solo las primeras N imágenes (pruebas)",
    )
    p.add_argument(
        "--sleep",
        type=float,
        default=0.4,
        help="Pausa entre llamadas API (segundos) para rate-limit",
    )
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    classes = tuple(c.strip() for c in args.classes.split(",") if c.strip())
    if not classes:
        raise SystemExit("Lista de clases vacía")
    if not args.input.is_dir():
        raise SystemExit(f"Input no es carpeta: {args.input}")

    label_dataset(
        input_dir=args.input,
        output_dir=args.output,
        class_names=classes,
        model=args.model,
        val_ratio=args.val_ratio,
        test_ratio=args.test_ratio,
        min_conf=args.min_conf,
        limit=args.limit,
        sleep_s=args.sleep,
    )


if __name__ == "__main__":
    main(sys.argv[1:])

