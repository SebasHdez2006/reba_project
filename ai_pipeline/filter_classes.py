"""Migracion 5 -> 3 clases REBA (elimina `tela` y `organico`).

Nuevo orden canonico:  plastico=0, unicel=1, metal=2
Remap de etiquetas viejas: 0->0 (plastico), 3->1 (unicel), 4->2 (metal);
las lineas con clase 1 (tela) y 2 (organico) se ELIMINAN (quedan fondo).

- Respalda todas las etiquetas originales en
  `datasets/_backup_labels_5class/<dataset>/labels/...` (solo la primera vez).
- Reescribe los .txt de labels en el sitio.
- Actualiza el bloque `names:` de cada `data.yaml` de dataset.

Idempotente: si las etiquetas ya son 3-clases, no las toca.

Uso:
  python ai_pipeline/filter_classes.py [--dry-run]
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATASETS = ROOT / "datasets"
BACKUP = DATASETS / "_backup_labels_5class"

NEW_CLASSES = ("plastico", "unicel", "metal")
OLD_TO_NEW = {0: 0, 1: None, 2: None, 3: 1, 4: 2}


def filter_label_file(path: Path, dry: bool) -> tuple[int, int]:
    """Devuelve (lineas_mantenidas, lineas_eliminadas)."""
    kept, dropped = [], 0
    for ln in path.read_text(encoding="utf-8").splitlines():
        p = ln.split()
        if len(p) != 5:
            continue
        try:
            cls = int(p[0])
        except ValueError:
            continue
        new_cls = OLD_TO_NEW.get(cls)
        if new_cls is None:
            dropped += 1
        else:
            kept.append(f"{new_cls} " + " ".join(p[1:]))
    if not dry:
        path.write_text("\n".join(kept) + ("\n" if kept else ""),
                        encoding="utf-8")
    return len(kept), dropped


def update_yaml_names(yaml_path: Path, dry: bool) -> bool:
    """Reemplaza el bloque names: final del data.yaml."""
    text = yaml_path.read_text(encoding="utf-8")
    m = re.search(r"(?ms)^names:\s*\n(?:\s+\d+:.*\n?)*", text)
    if not m:
        return False
    names_block = "names:\n" + "".join(
        f"  {i}: {n}\n" for i, n in enumerate(NEW_CLASSES))
    new_text = text[:m.start()] + names_block
    if not dry:
        yaml_path.write_text(new_text, encoding="utf-8")
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    dry = args.dry_run

    total_kept = total_dropped = 0
    for ds in sorted(DATASETS.glob("REBA_*")):
        if not ds.is_dir() or ds.name in ("REBA_outdoor",):
            continue  # REBA_outdoor solo tiene data.yaml (se regenera)
        lbl_root = ds / "labels"
        if not lbl_root.is_dir():
            continue
        # Idempotencia: si el data.yaml ya declara las 3 clases nuevas,
        # las etiquetas ya fueron migradas -> no tocar (cls 1,2 ahora son
        # unicel/metal y OLD_TO_NEW los borraria).
        yaml_path = ds / "data.yaml"
        if yaml_path.exists():
            head = yaml_path.read_text(encoding="utf-8")[:600]
            if all(f"{i}: {n}" in head for i, n in enumerate(NEW_CLASSES)) \
                    and "tela" not in head and "organico" not in head:
                print(f"{ds.name:24s} ya migrado (3 clases) - skip")
                continue
        # backup una sola vez
        if not dry and not BACKUP.joinpath(ds.name).exists():
            shutil.copytree(lbl_root, BACKUP / ds.name / "labels")
        ds_kept = ds_dropped = n_files = 0
        for lp in sorted(lbl_root.rglob("*.txt")):
            k, d = filter_label_file(lp, dry)
            ds_kept += k
            ds_dropped += d
            n_files += 1
        yaml_ok = update_yaml_names(ds / "data.yaml", dry) \
            if (ds / "data.yaml").exists() else False
        total_kept += ds_kept
        total_dropped += ds_dropped
        print(f"{ds.name:24s} files={n_files:5d} kept={ds_kept:6d} "
              f"dropped(tela/organico)={ds_dropped:5d} yaml={'OK' if yaml_ok else '-'}")

    print(f"\nTOTAL kept={total_kept} dropped={total_dropped}")
    if dry:
        print("DRY-RUN: nada modificado")
    else:
        print(f"Backup en: {BACKUP}")
        print("Nuevas clases:", NEW_CLASSES)


if __name__ == "__main__":
    sys.exit(main())
