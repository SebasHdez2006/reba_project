#!/usr/bin/env python3
"""
REBA — Descarga imágenes de unicel/poliestireno de Wikimedia Commons (API
pública, sin credenciales) como data extra de la clase débil.

Solo guarda imágenes + manifest; cada imagen se convierte luego en recortes
4:3 degradados (ver augment_commons_unicel en train_esp32_cls_dino.py o
datasets/REBA_cls_esp32/train_extra/unicel/wm_*.jpg).

Uso:
    python ai_pipeline/download_commons_unicel.py --limit 400
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "datasets" / "REBA_commons_unicel"
API = "https://commons.wikimedia.org/w/api.php"
UA = {"User-Agent": "REBADatasetBot/1.0 (local training; contact: he24006@ues.edu.sv)"}

QUERIES = [
    "polystyrene", "styrofoam", "expanded polystyrene",
    "foam cup", "styrofoam container", "polystyrene packaging",
    "foam packaging", "styrofoam waste", "polystyrene foam",
]
ALLOWED_LICENSE = ("cc0", "public domain", "cc-by", "cc by", "cc-by-sa",
                   "cc by-sa", "attribution", "pd", "gfdl")


def search(client: httpx.Client, query: str, limit: int) -> list[dict]:
    out, offset = [], 0
    while len(out) < limit:
        r = client.get(API, params={
            "action": "query", "format": "json", "generator": "search",
            "gsrsearch": f"filetype:bitmap {query}", "gsrnamespace": 6,
            "gsrlimit": 50, "gsroffset": offset,
            "prop": "imageinfo",
            "iiprop": "url|size|extmetadata",
            "iiurlwidth": 640,
        })
        r.raise_for_status()
        pages = r.json().get("query", {}).get("pages", {})
        if not pages:
            break
        out.extend(pages.values())
        offset += 50
        if "continue" not in r.json():
            break
    return out


def ok_license(meta: dict) -> bool:
    lic = str(meta.get("LicenseShortName", {}).get("value", "")).lower()
    usage = str(meta.get("UsageTerms", {}).get("value", "")).lower()
    return any(a in lic or a in usage for a in ALLOWED_LICENSE)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    (args.out / "images").mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    records: list[dict] = []
    client = httpx.Client(timeout=30, follow_redirects=True, headers=UA)
    for q in QUERIES:
        if len(records) >= args.limit:
            break
        try:
            for page in search(client, q, 60):
                if len(records) >= args.limit:
                    break
                ii = (page.get("imageinfo") or [{}])[0]
                meta = ii.get("extmetadata", {})
                if ii.get("width", 0) < 320 or ii.get("height", 0) < 240:
                    continue
                if not ok_license(meta):
                    continue
                # thumb 640px (siempre jpeg/png incluso para tif/gif) o el original
                url = ii.get("thumburl") or ii.get("url", "")
                if not url.lower().split("?")[0].endswith(
                        (".jpg", ".jpeg", ".png", ".webp")):
                    continue
                try:
                    raw = client.get(url).content
                except Exception:
                    continue
                sha = hashlib.sha256(raw).hexdigest()
                if sha in seen:
                    continue
                seen.add(sha)
                name = f"wm_{len(records):05d}_{sha[:8]}.jpg"
                (args.out / "images" / name).write_bytes(raw)
                records.append({
                    "file": name, "query": q, "title": page.get("title"),
                    "url": url, "license": meta.get("LicenseShortName", {}).get("value"),
                    "artist": meta.get("Artist", {}).get("value"),
                })
        except Exception as exc:
            print(f"{q}: {exc}", file=sys.stderr)
        time.sleep(0.2)
    (args.out / "manifest.json").write_text(
        json.dumps(records, indent=2, ensure_ascii=False), "utf-8")
    print(f"{len(records)} imágenes -> {args.out}/images")


if __name__ == "__main__":
    main()
