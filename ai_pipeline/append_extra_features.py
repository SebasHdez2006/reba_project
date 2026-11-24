#!/usr/bin/env python3
"""REBA — añade al npz de features de train_extra las filas de los recortes
wm_* (Wikimedia Commons) que se generaron después de la primera extracción.
Los wm_* ordenan después de floatwaste_* dentro de unicel, así que basta
concatenar al final."""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ai_pipeline"))

from server.inference import cls_preprocess  # noqa: E402
import train_esp32_cls_dino as T  # noqa: E402

NPZ = T.FEAT_DIR / "train_extra.npz"


def main() -> None:
    import onnxruntime as ort

    z = np.load(NPZ)
    have = set(z["files"].tolist())
    new_paths = [p for p in sorted((T.DATA / "train_extra" / "unicel").glob("wm_*.jpg"))
                 if p.name not in have]
    print(f"{len(new_paths)} nuevos")
    if not new_paths:
        return
    cls_list = sorted(d.name for d in (T.DATA / "train").iterdir() if d.is_dir())
    ci = cls_list.index("unicel")
    sess = ort.InferenceSession(str(T.BACKBONE_ONNX),
                                providers=["CPUExecutionProvider"])
    inp = sess.get_inputs()[0].name
    X = np.empty((len(new_paths), 2 * T.EMBED), np.float32)
    for i in range(0, len(new_paths), 32):
        blob = np.concatenate(
            [cls_preprocess(cv2.imread(str(p)), T.IMG_H, T.IMG_W)
             for p in new_paths[i:i + 32]])
        X[i:i + len(blob)] = sess.run(None, {inp: blob})[0]
        print(f"  {i + len(blob)}/{len(new_paths)}", flush=True)
    out = {
        "X": np.concatenate([z["X"], X]),
        "y": np.concatenate([z["y"], np.full(len(new_paths), ci, np.int64)]),
        "groups": np.concatenate(
            [z["groups"], np.array([T.group_of(p.stem) for p in new_paths])]),
        "files": np.concatenate(
            [z["files"], np.array([p.name for p in new_paths])]),
    }
    np.savez_compressed(NPZ, **out)
    print(f"-> {NPZ} {out['X'].shape}")


if __name__ == "__main__":
    main()
