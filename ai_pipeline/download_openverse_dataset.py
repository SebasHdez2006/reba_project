#!/usr/bin/env python3
"""
Download publicly reusable aquatic-debris images from Openverse and build a
license-tracked raw image corpus for REBA.

Why Openverse:
    - Aggregates Flickr, Wikimedia Commons, NASA and other sources.
    - Exposes license metadata and attribution text per asset.
    - Lets us filter for reuse-friendly licenses before downloading.

This script only downloads RAW images and a manifest; it does NOT create YOLO
labels. Use `ai_pipeline/dataset_builder.py` afterwards to auto-label the raw
images (OpenAI Vision or another annotator) and generate train/val/test splits.

Typical workflow:
    python ai_pipeline/download_openverse_dataset.py --output datasets/REBA_raw
    python ai_pipeline/dataset_builder.py --input datasets/REBA_raw --output datasets/REBA

The manifest records:
    - source URL
    - license / license URL
    - creator + attribution
    - query used
    - sha256 for dedupe
    - deterministic split assignment (train/val/test)
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import mimetypes
import os
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

logger = logging.getLogger("REBA.openverse")

ALLOWED_LICENSES = {"cc0", "pdm", "by", "by-sa"}
DEFAULT_PAGE_SIZE = 100
DEFAULT_TIMEOUT = 30.0
DEFAULT_USER_AGENT = "REBADatasetBot/1.0 (+local training; respect source licenses)"

# Hand-crafted search bank aimed at real aquatic litter scenes, water surfaces
# and camera viewpoints close to an ESP32-CAM mounted on a floating platform.
DEFAULT_QUERY_BANK: Dict[str, List[str]] = {
    "plastico": [
        "plastic bottle floating water",
        "plastic bottle river water",
        "plastic trash river water",
        "floating plastic debris water",
        "water pollution plastic bottle",
    ],
    "tela": [
        "cloth trash water",
        "rag floating water",
        "fabric waste river water",
        "textile trash water",
        "cloth debris canal water",
    ],
    "organico": [
        "food waste water",
        "organic waste river water",
        "fruit debris water",
        "vegetation floating water",
        "organic debris canal water",
    ],
    "unicel": [
        "styrofoam floating water",
        "foam container water",
        "polystyrene water trash",
        "foam debris river water",
        "styrofoam trash beach water",
    ],
    "metal": [
        "metal can floating water",
        "aluminum can water trash",
        "metal debris river water",
        "rusty can water",
        "metal trash canal water",
    ],
    "context": [
        "river debris pollution water",
        "beach litter water",
        "ocean trash water",
        "canal litter water",
        "lake debris water",
        "floating trash water",
    ],
}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


@dataclass(frozen=True)
class OpenverseRecord:
    query: str
    class_hint: str
    split: str
    id: str
    title: str
    url: str
    foreign_landing_url: str
    creator: str
    creator_url: str
    license: str
    license_version: str
    license_url: str
    source: str
    provider: str
    attribution: str
    width: int
    height: int
    thumbnail: str
    detail_url: str
    related_url: str
    sha256: str
    file_name: str
    local_path: str


@dataclass
class Candidate:
    query: str
    class_hint: str
    split: str
    payload: Dict[str, object]


class OpenverseClient:
    def __init__(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": DEFAULT_USER_AGENT},
        )

    def close(self) -> None:
        self.client.close()

    def search_images(
        self,
        query: str,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
        mature: bool = False,
    ) -> Dict[str, object]:
        params = {
            "q": query,
            "license_type": "commercial",
            "mature": str(mature).lower(),
            "page": page,
            "page_size": page_size,
        }
        url = "https://api.openverse.org/v1/images/"
        response = self.client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    def download(self, url: str) -> bytes:
        response = self.client.get(url)
        response.raise_for_status()
        return response.content


def normalize_license(raw: str) -> str:
    value = raw.strip().lower()
    value = value.replace("creativecommons", "")
    if value.startswith("cc0"):
        return "cc0"
    if value.startswith("pdm") or "public domain mark" in value:
        return "pdm"
    if "by-sa" in value:
        return "by-sa"
    if re.search(r"\bby\b", value):
        return "by"
    return value


def is_allowed_license(raw_license: str) -> bool:
    return normalize_license(raw_license) in ALLOWED_LICENSES


def deterministic_split(sha256_hex: str, train_ratio: float, val_ratio: float) -> str:
    value = int(sha256_hex[:8], 16) % 1000 / 1000.0
    if value < train_ratio:
        return "train"
    if value < train_ratio + val_ratio:
        return "val"
    return "test"


def make_output_dirs(root: Path) -> None:
    for split in ("train", "val", "test"):
        (root / "images" / split).mkdir(parents=True, exist_ok=True)
        (root / "manifests" / split).mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)


def safe_slug(text: str, fallback: str = "image") -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or fallback


def guess_extension(url: str, mime_type: Optional[str] = None) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return ".jpg" if suffix == ".jpeg" else suffix
    if mime_type:
        guessed = mimetypes.guess_extension(mime_type.split(";")[0].strip()) or ".jpg"
        return ".jpg" if guessed == ".jpe" else guessed
    return ".jpg"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_safe(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def iterate_queries(args: argparse.Namespace) -> Iterator[Tuple[str, str]]:
    if args.query:
        for q in args.query:
            yield (q, args.class_hint)
        return

    query_bank = DEFAULT_QUERY_BANK.copy()
    selected_classes: Sequence[str]
    if args.classes:
        selected_classes = [c.strip() for c in args.classes.split(",") if c.strip()]
    else:
        selected_classes = ["plastico", "tela", "organico", "unicel", "metal", "context"]

    for cls in selected_classes:
        for q in query_bank.get(cls, []):
            yield (q, cls)


def extract_candidate(
    payload: Dict[str, object],
    query: str,
    class_hint: str,
    split: str,
) -> Optional[Candidate]:
    license_raw = str(payload.get("license", "")).strip()
    if not is_allowed_license(license_raw):
        return None

    width = int(payload.get("width") or 0)
    height = int(payload.get("height") or 0)
    if width < 320 or height < 240:
        return None

    url = str(payload.get("url", "")).strip()
    if not url:
        return None

    return Candidate(query=query, class_hint=class_hint, split=split, payload=payload)


def candidate_to_record(
    candidate: Candidate,
    output_root: Path,
    sha256_hex: str,
    file_name: str,
    local_path: Path,
) -> OpenverseRecord:
    payload = candidate.payload
    return OpenverseRecord(
        query=candidate.query,
        class_hint=candidate.class_hint,
        split=candidate.split,
        id=str(payload.get("id", "")),
        title=str(payload.get("title", "")),
        url=str(payload.get("url", "")),
        foreign_landing_url=str(payload.get("foreign_landing_url", "")),
        creator=str(payload.get("creator", "")),
        creator_url=str(payload.get("creator_url", "")),
        license=normalize_license(str(payload.get("license", ""))),
        license_version=str(payload.get("license_version", "")),
        license_url=str(payload.get("license_url", "")),
        source=str(payload.get("source", "")),
        provider=str(payload.get("provider", "")),
        attribution=str(payload.get("attribution", "")),
        width=int(payload.get("width") or 0),
        height=int(payload.get("height") or 0),
        thumbnail=str(payload.get("thumbnail", "")),
        detail_url=str(payload.get("detail_url", "")),
        related_url=str(payload.get("related_url", "")),
        sha256=sha256_hex,
        file_name=file_name,
        local_path=str(local_path.relative_to(output_root).as_posix()),
    )


def write_manifest(records: Sequence[OpenverseRecord], output_root: Path) -> None:
    jsonl_path = output_root / "manifest.jsonl"
    csv_path = output_root / "manifest.csv"

    with jsonl_path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json_safe(asdict(record)) + "\n")

    fieldnames = list(asdict(records[0]).keys()) if records else []
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow(asdict(record))


def write_split_summary(records: Sequence[OpenverseRecord], output_root: Path) -> None:
    summary = {
        "total": len(records),
        "by_split": {},
        "by_license": {},
        "by_class_hint": {},
    }
    for split in ("train", "val", "test"):
        summary["by_split"][split] = sum(1 for r in records if r.split == split)
    for r in records:
        summary["by_license"][r.license] = summary["by_license"].get(r.license, 0) + 1
        summary["by_class_hint"][r.class_hint] = summary["by_class_hint"].get(r.class_hint, 0) + 1

    (output_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def download_dataset(args: argparse.Namespace) -> None:
    output_root = args.output.resolve()
    make_output_dirs(output_root)

    client = OpenverseClient(timeout=args.timeout)
    seen_hashes: set[str] = set()
    seen_ids: set[str] = set()
    records: List[OpenverseRecord] = []
    per_query_counts: Dict[str, int] = {}

    try:
        for query, class_hint in iterate_queries(args):
            if query in per_query_counts and per_query_counts[query] >= args.max_per_query:
                continue

            query_count = 0
            logger.info("Querying Openverse: %s [%s]", query, class_hint)
            for page in range(1, args.max_pages + 1):
                try:
                    payload = client.search_images(query=query, page=page, page_size=args.page_size)
                except Exception as exc:
                    logger.warning("Openverse request failed for %s page %s: %s", query, page, exc)
                    break

                results = payload.get("results", [])
                if not isinstance(results, list) or not results:
                    break

                for item in results:
                    if not isinstance(item, dict):
                        continue
                    item_id = str(item.get("id", ""))
                    if item_id and item_id in seen_ids:
                        continue
                    split_guess = deterministic_split(item_id or item.get("url", ""), args.train_ratio, args.val_ratio)
                    candidate = extract_candidate(item, query, class_hint, split_guess)
                    if candidate is None:
                        continue

                    image_url = str(candidate.payload.get("url", ""))
                    try:
                        raw = client.download(image_url)
                    except Exception as exc:
                        logger.debug("Download failed for %s: %s", image_url, exc)
                        continue

                    sha = sha256_bytes(raw)
                    if sha in seen_hashes:
                        continue
                    seen_hashes.add(sha)
                    if item_id:
                        seen_ids.add(item_id)

                    split = deterministic_split(sha, args.train_ratio, args.val_ratio)
                    extension = guess_extension(image_url, mime_type=None)
                    file_stem = f"{safe_slug(class_hint)}_{len(records):06d}_{sha[:10]}"
                    file_name = f"{file_stem}{extension}"
                    split_dir = output_root / "images" / split
                    split_dir.mkdir(parents=True, exist_ok=True)
                    local_path = split_dir / file_name
                    local_path.write_bytes(raw)

                    meta_path = output_root / "manifests" / split / f"{file_stem}.json"
                    meta = candidate_to_record(candidate, output_root, sha, file_name, local_path)
                    meta_path.write_text(json_safe(asdict(meta)), encoding="utf-8")
                    records.append(meta)
                    query_count += 1
                    per_query_counts[query] = query_count

                    if args.max_per_query > 0 and query_count >= args.max_per_query:
                        break
                    if args.total_limit > 0 and len(records) >= args.total_limit:
                        logger.info("Reached total limit %d", args.total_limit)
                        write_manifest(records, output_root)
                        write_split_summary(records, output_root)
                        return

                if args.sleep > 0:
                    time.sleep(args.sleep)

                if args.max_pages > 0 and page >= args.max_pages:
                    break

    finally:
        client.close()

    if not records:
        raise SystemExit("No valid images were downloaded. Check queries / licenses / network.")

    write_manifest(records, output_root)
    write_split_summary(records, output_root)
    logger.info("Downloaded %d reusable images into %s", len(records), output_root)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Download reusable aquatic debris images from Openverse")
    p.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "datasets" / "REBA_raw",
        help="Raw image output directory",
    )
    p.add_argument(
        "--classes",
        type=str,
        default="plastico,tela,organico,unicel,metal,context",
        help="Comma-separated class hints to query",
    )
    p.add_argument(
        "--query",
        action="append",
        default=None,
        help="Custom Openverse query (repeatable). If set, class_hint applies to all.",
    )
    p.add_argument(
        "--class-hint",
        type=str,
        default="context",
        help="Class hint used when --query is passed",
    )
    p.add_argument(
        "--page-size",
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help="Openverse page size (max 200 is safe)",
    )
    p.add_argument(
        "--max-pages",
        type=int,
        default=5,
        help="Max pages per query",
    )
    p.add_argument(
        "--max-per-query",
        type=int,
        default=120,
        help="Stop after this many unique images per query",
    )
    p.add_argument(
        "--total-limit",
        type=int,
        default=0,
        help="Hard cap over all queries (0 = unlimited)",
    )
    p.add_argument(
        "--train-ratio",
        type=float,
        default=0.8,
        help="Deterministic train split ratio",
    )
    p.add_argument(
        "--val-ratio",
        type=float,
        default=0.1,
        help="Deterministic validation split ratio",
    )
    p.add_argument(
        "--sleep",
        type=float,
        default=0.15,
        help="Sleep between pages to be polite to the API",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="HTTP timeout in seconds",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    return p.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    download_dataset(args)
