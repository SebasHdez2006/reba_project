"""Descarga y convierte FloatWaste (HF: OliviaWuYJ/sc-floatwaste) a YOLO/REBA.

FloatWaste: 7,906 imagenes de superficie de rio (Hobart, Sydney, Citarum),
COCO bbox, CC BY 4.0. Dominio mas cercano al despliegue (residuos flotando
en agua), pero sin telas ni organicos (eliminados del modelo).

Salida:
  datasets/REBA_floatwaste/   -> train subset (nunca toca val)
  datasets/REBA_real_val/     -> TODO el split val de FloatWaste = set de
                               validacion real acuatico, jamas usado en train

Las imagenes se decodifican y re-guardan como JPEG (lado max 1600 px, q92)
porque los originales son PNG 4K/8K de varios MB; el modelo entrena a 640.

Uso:
  python ai_pipeline/download_floatwaste.py --limit 1500
  python ai_pipeline/download_floatwaste.py --limit 0   # todo el train
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import cv2
import httpx
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "datasets" / "_raw" / "floatwaste"
OUT_TRAIN = ROOT / "datasets" / "REBA_floatwaste"
OUT_VAL = ROOT / "datasets" / "REBA_real_val"
HF_BASE = "https://huggingface.co/datasets/OliviaWuYJ/sc-floatwaste/resolve/main"
MAX_SIDE = 1600
JPEG_Q = 92
WORKERS = 8

CLASS_NAMES = ["plastico", "unicel", "metal"]

# FloatWaste category_id -> clase REBA (None = fondo, se descarta la caja)
FLOATWASTE_TO_REBA = {
    1: "plastico",    # Packaging
    2: "plastico",    # Other_packaging (predominantemente plastico en rios)
    3: "plastico",    # S_bubblewrap
    4: "plastico",    # S_label
    5: "plastico",    # S_squeeze
    6: "plastico",    # S_straw
    7: "plastico",    # PS_string
    9: "plastico",    # P_foodcontainer
    10: "plastico",   # PH_cup
    11: "plastico",   # H_packaging
    12: "plastico",   # H_beveragebottle
    13: "plastico",   # H_otherbottle
    14: "plastico",   # H_plate/bowl
    15: "plastico",   # H_utensil
    16: "plastico",   # DH_lid
    17: "unicel",     # D_polystyrene
    18: "metal",      # M_beveragecan
    19: "metal",      # M_foodcan/tin
    20: "metal",      # M_aerosol
    23: "plastico",   # F_facemask (polipropileno no tejido)
    # 24 T_wood/timber -> organico eliminado: ahora fondo
    # 8 P_cardboard, 21 R_ball/balloon, 22 G_beveragebottle, 24, 25 Other -> fondo
}
REBA_TO_ID = {n: i for i, n in enumerate(CLASS_NAMES)}


def fetch_json(client: httpx.Client, split: str) -> dict:
    url = f"{HF_BASE}/annotations/instances_{split}.json"
    path = RAW_DIR / f"instances_{split}.json"
    if path.exists() and path.stat().st_size > 1000:
        return json.loads(path.read_text(encoding="utf-8"))
    print(f"  descargando anotaciones {split} ...", flush=True)
    r = client.get(url, timeout=300)
    r.raise_for_status()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path.write_bytes(r.content)
    return json.loads(path.read_text(encoding="utf-8"))


def coco_to_yolo_lines(anns: list[dict], w: int, h: int) -> tuple[list[str], dict[str, int]]:
    """Convierte anns COCO -> lineas YOLO normalizadas + conteo por clase."""
    lines, counts = [], {}
    for a in anns:
        cls = FLOATWASTE_TO_REBA.get(a["category_id"])
        if cls is None:
            continue
        x, y, bw, bh = a["bbox"]
        xc, yc = x + bw / 2, y + bh / 2
        if bw <= 0 or bh <= 0 or w <= 0 or h <= 0:
            continue
        xc, yc = xc / w, yc / h
        nw, nh = bw / w, bh / h
        xc, yc, nw, nh = (min(max(v, 0.0), 1.0) for v in (xc, yc, nw, nh))
        # recortar al lienzo por si el bbox se sale
        x1, y1 = max(0.0, xc - nw / 2), max(0.0, yc - nh / 2)
        x2, y2 = min(1.0, xc + nw / 2), min(1.0, yc + nh / 2)
        nw, nh = x2 - x1, y2 - y1
        if nw <= 0 or nh <= 0:
            continue
        lines.append(f"{REBA_TO_ID[cls]} {x1 + nw / 2:.6f} {y1 + nh / 2:.6f} {nw:.6f} {nh:.6f}")
        counts[cls] = counts.get(cls, 0) + 1
    return lines, counts


def select_train_images(coco: dict) -> list[dict]:
    """Subset determinista priorizando imagenes con clases REBA raras."""
    anns_by_img: dict[int, list[dict]] = {}
    for a in coco["annotations"]:
        anns_by_img.setdefault(a["image_id"], []).append(a)
    # peso inverso por clase mapeada (metal/unicel pesan mas)
    cls_totals: dict[str, int] = {}
    for a in coco["annotations"]:
        cls = FLOATWASTE_TO_REBA.get(a["category_id"])
        if cls:
            cls_totals[cls] = cls_totals.get(cls, 0) + 1
    total_mapped = sum(cls_totals.values()) or 1
    scored = []
    for img in coco["images"]:
        anns = anns_by_img.get(img["id"], [])
        mapped = [a for a in anns if FLOATWASTE_TO_REBA.get(a["category_id"])]
        if not mapped:
            continue
        score = sum(1.0 / max(cls_totals.get(FLOATWASTE_TO_REBA[a["category_id"]], 1), 1)
                    for a in mapped)
        scored.append((score, img["id"], img))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [img for _, _, img in scored]


def process_image(client: httpx.Client, img: dict, out_root: Path, split: str,
                  anns_by_img: dict[int, list[dict]]) -> dict:
    """Descarga 1 imagen, re-encodea a JPEG, escribe label YOLO."""
    fname = Path(img["file_name"]).name
    stem = Path(img["file_name"]).stem
    img_dir = out_root / "images" / split
    lbl_dir = out_root / "labels" / split
    img_dir.mkdir(parents=True, exist_ok=True)
    lbl_dir.mkdir(parents=True, exist_ok=True)
    out_path = img_dir / f"fw_{stem}.jpg"
    if not out_path.exists():
        url = f"{HF_BASE}/{img['file_name']}"
        r = client.get(url, timeout=120, follow_redirects=True)
        r.raise_for_status()
        arr = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
        if arr is None:
            raise ValueError(f"no se pudo decodificar {img['file_name']}")
        h, w = arr.shape[:2]
        scale = MAX_SIDE / max(h, w)
        if scale < 1:
            arr = cv2.resize(arr, (int(w * scale), int(h * scale)),
                             interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", arr, [cv2.IMWRITE_JPEG_QUALITY, JPEG_Q])
        if not ok:
            raise ValueError("imencode fallo")
        out_path.write_bytes(buf.tobytes())
    # labels siempre se reescriben (barato, idempotente)
    w, h = img["width"], img["height"]
    lines, counts = coco_to_yolo_lines(anns_by_img.get(img["id"], []), w, h)
    lbl_path = lbl_dir / f"fw_{stem}.txt"
    lbl_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return {"file": f"{split}/fw_{stem}.jpg", "labels": counts,
            "n_mapped": sum(counts.values()), "n_total": len(anns_by_img.get(img["id"], []))}


def write_dataset_yaml(out_root: Path, split_dirs: list[str]) -> None:
    names_yaml = "\n".join(f"  {i}: {n}" for i, n in enumerate(CLASS_NAMES))
    paths = "\n".join(f"  {s}: images/{s}" for s in split_dirs)
    (out_root / "data.yaml").write_text(
        f"path: {out_root.as_posix()}\n"
        f"train:\n{paths}\n"
        f"val:\n  val: images/val\n"
        f"names:\n{names_yaml}\n",
        encoding="utf-8",
    )


def download_split(client: httpx.Client, coco: dict, out_root: Path,
                   split: str, limit: int, tag: str,
                   negatives: int = 0) -> dict:
    anns_by_img: dict[int, list[dict]] = {}
    for a in coco["annotations"]:
        anns_by_img.setdefault(a["image_id"], []).append(a)
    imgs = coco["images"]
    if split == "train" and limit > 0:
        # FASE 2.4: negativos de fondo (agua limpia, 0 anotaciones)
        empties = []
        if negatives > 0:
            empties = [im for im in sorted(coco["images"], key=lambda x: x["id"])
                       if not anns_by_img.get(im["id"])][:negatives]
        imgs = select_train_images(coco)[:limit] + empties
    else:
        imgs = sorted(imgs, key=lambda im: im["id"])
        # val (real_val): solo imagenes con >=1 caja mapeada o totalmente
        # vacias (agua limpia). Las que tienen SOLO clases no mapeadas
        # (carton, vidrio...) se omiten para no meter negativos ambiguos.
        if split == "val":
            keep = []
            for im in imgs:
                anns = anns_by_img.get(im["id"], [])
                has_mapped = any(FLOATWASTE_TO_REBA.get(a["category_id"])
                                 for a in anns)
                if has_mapped or not anns:
                    keep.append(im)
            imgs = keep
    print(f"[{tag}] {split}: {len(imgs)} imagenes a procesar", flush=True)
    totals = {n: 0 for n in CLASS_NAMES}
    n_done = n_fail = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(process_image, client, im, out_root, split,
                          anns_by_img): im for im in imgs}
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                res = fut.result()
            except Exception as e:  # noqa: BLE001
                n_fail += 1
                if n_fail <= 5:
                    print(f"  FAIL {futs[fut]['file_name']}: {e}", flush=True)
                continue
            n_done += 1
            for cls, c in res["labels"].items():
                totals[cls] += c
            if i % 100 == 0 or i == len(imgs):
                dt = time.time() - t0
                print(f"  [{tag}] {i}/{len(imgs)} ({dt:.0f}s, "
                      f"{i / max(dt, 1):.1f} img/s) fallos={n_fail}", flush=True)
    print(f"[{tag}] {split} OK: {n_done} imgs, {n_fail} fallos, "
          f"cajas={totals}", flush=True)
    return {"processed": n_done, "failed": n_fail, "instances": totals}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=1500,
                    help="max imagenes train de FloatWaste (0 = todas)")
    ap.add_argument("--negatives", type=int, default=120,
                    help="imagenes de fondo sin objetos a anadir al train")
    ap.add_argument("--workers", type=int, default=WORKERS)
    args = ap.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    with httpx.Client(headers={"User-Agent": "reba-dataset-bootstrap/1.0"},
                      timeout=60, follow_redirects=True) as client:
        coco_train = fetch_json(client, "train")
        coco_val = fetch_json(client, "val")

        # --- train subset -> REBA_floatwaste ---
        OUT_TRAIN.mkdir(parents=True, exist_ok=True)
        r_train = download_split(client, coco_train, OUT_TRAIN, "train",
                                 args.limit, "floatwaste", args.negatives)
        (OUT_TRAIN / "LICENSE.txt").write_text(
            "FloatWaste dataset - CC BY 4.0\n"
            "https://huggingface.co/datasets/OliviaWuYJ/sc-floatwaste\n"
            "https://doi.org/10.5281/zenodo.21934286\n", encoding="utf-8")
        write_dataset_yaml(OUT_TRAIN, ["train"])

        # --- val completo -> REBA_real_val (set de validacion real) ---
        OUT_VAL.mkdir(parents=True, exist_ok=True)
        r_val = download_split(client, coco_val, OUT_VAL, "val", 0, "real_val")
        (OUT_VAL / "LICENSE.txt").write_text(
            "FloatWaste val split - CC BY 4.0 (validacion real REBA)\n"
            "https://huggingface.co/datasets/OliviaWuYJ/sc-floatwaste\n", encoding="utf-8")
        write_dataset_yaml(OUT_VAL, ["val"])

    summary = {"train": r_train, "real_val": r_val,
               "classes": CLASS_NAMES, "mapping":
               {str(k): v for k, v in FLOATWASTE_TO_REBA.items()}}
    (RAW_DIR / "conversion_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")
    print("LISTO:", json.dumps({k: v["processed"] for k, v in
                                (("train", r_train), ("val", r_val))}))


if __name__ == "__main__":
    sys.exit(main())
