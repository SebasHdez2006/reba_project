"""Auditoria de duplicados percibidos (dHash) entre datasets REBA_*.

Detecta:
  - duplicados dentro del mismo split (redundancia, no critico)
  - FUGAS train<->val/test (near-duplicados entre splits = leakage)

Especialmente importante para FloatWaste: sus imagenes vienen de videos de
rio; train y val pueden contener frames casi identicos del mismo tramo.

Salida: reports/dedup_report.json + resumen en consola.
No mueve ni borra nada (report-only).

Uso:
  python ai_pipeline/dedup_audit.py [--threshold 6]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATASETS = ROOT / "datasets"
REPORT = ROOT / "reports" / "dedup_report.json"

# LUT de poblacion de bits para uint8
POP = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)


def dhash(path: Path) -> np.uint64 | None:
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    img = cv2.resize(img, (9, 8), interpolation=cv2.INTER_AREA)
    bits = img[:, 1:] > img[:, :-1]  # 8x8 diferencias horizontales
    v = 0
    for b in bits.flatten():
        v = (v << 1) | int(b)
    return np.uint64(v)


def collect() -> list[dict]:
    rows = []
    for ds in sorted(DATASETS.glob("REBA_*")):
        if ds.name in ("REBA_outdoor", "REBA_outdoor_eval"):
            continue  # datasets derivados/augmentados, no origen
        for split in ("train", "val", "test"):
            for ext in ("*.jpg", "*.jpeg", "*.png"):
                for p in sorted((ds / "images" / split).glob(ext)) if \
                        (ds / "images" / split).exists() else []:
                    rows.append({"dataset": ds.name, "split": split,
                                 "path": p.relative_to(ROOT).as_posix()})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=int, default=6,
                    help="distancia Hamming maxima = near-duplicado")
    args = ap.parse_args()

    rows = collect()
    print(f"{len(rows)} imagenes en datasets REBA_*", flush=True)
    hashes = np.empty(len(rows), dtype=np.uint64)
    ok = []
    for i, r in enumerate(rows):
        h = dhash(ROOT / r["path"])
        if h is not None:
            hashes[len(ok)] = h
            ok.append(r)
        if (i + 1) % 500 == 0:
            print(f"  hash {i + 1}/{len(rows)}", flush=True)
    rows = ok
    hashes = hashes[: len(rows)]
    n = len(rows)
    print(f"{n} hashes validos", flush=True)

    # Comparacion por bloques: distancias Hamming via popcount de XOR
    pairs = []  # (i, j, dist)
    B = 400
    hbytes = hashes.view(np.uint8).reshape(n, 8)  # 8 bytes por hash
    for s in range(0, n, B):
        e = min(s + B, n)
        blk = hbytes[s:e]                       # (b,8)
        # XOR contra todos: (b,n,8) -> popcount -> (b,n)
        xr = blk[:, None, :] ^ hbytes[None, :, :]
        dist = POP[xr].sum(axis=2)              # (b,n)
        for bi in range(e - s):
            gi = s + bi
            js = np.nonzero(dist[bi] <= args.threshold)[0]
            for j in js:
                if j > gi:  # solo pares i<j
                    pairs.append((gi, int(j), int(dist[bi, j])))
        if (s // B) % 5 == 0:
            print(f"  comparados {e}/{n}", flush=True)

    # Clasificar: fuga cross-split vs duplicado interno
    leaks, internal = [], []
    for i, j, d in pairs:
        a, b = rows[i], rows[j]
        same_split = (a["split"] == b["split"])
        rec = {"dist": d, "a": a, "b": b}
        (internal if same_split else leaks).append(rec)

    # Resumen por par de datasets
    def key(ds1, sp1, ds2, sp2):
        return f"{ds1}:{sp1} <-> {ds2}:{sp2}"
    leak_summary: dict[str, int] = {}
    for r in leaks:
        k = key(r["a"]["dataset"], r["a"]["split"],
                r["b"]["dataset"], r["b"]["split"])
        leak_summary[k] = leak_summary.get(k, 0) + 1

    report = {
        "threshold_hamming": args.threshold,
        "n_images": n,
        "n_pairs_total": len(pairs),
        "n_cross_split_leaks": len(leaks),
        "n_internal_dupes": len(internal),
        "leak_summary": dict(sorted(leak_summary.items(),
                                    key=lambda kv: -kv[1])),
        "leaks": [{"dist": r["dist"],
                   "a": r["a"]["path"], "b": r["b"]["path"]}
                  for r in leaks[:500]],
        "internal_sample": [{"dist": r["dist"],
                             "a": r["a"]["path"], "b": r["b"]["path"]}
                            for r in internal[:200]],
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"\nPARES near-dup (hamming<={args.threshold}): {len(pairs)}")
    print(f"  FUGAS cross-split: {len(leaks)}")
    print(f"  duplicados internos: {len(internal)}")
    for k, v in list(leak_summary.items())[:15]:
        print(f"    {k}: {v}")
    print(f"\nReporte: {REPORT}")


if __name__ == "__main__":
    sys.exit(main())
