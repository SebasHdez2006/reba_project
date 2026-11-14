#!/usr/bin/env python3
"""
Evalúa el modelo DESPLEGADO a través del path de producción real.

A diferencia de evaluate_outdoor.py (que llama al engine en proceso), este
script sube cada imagen por HTTP a POST /upload — exactamente lo que hace
el ESP32-CAM — y compara las detecciones devueltas contra las etiquetas
YOLO ground-truth del dataset.

  JPEG -> POST /upload -> ONNX (letterbox+NMS+conf) -> detections[] -> JSON

Uso:
  python ai_pipeline/eval_upload_path.py --dataset datasets/REBA_taco --split test
  python ai_pipeline/eval_upload_path.py --dataset datasets/REBA_taco --split val --limit 40

Salida: precision/recall/F1 por clase + mAP@0.5 global (interp. 101 pts),
inferencia media, y JSON en ai_pipeline/eval_upload_report.json.

NOTA: cada imagen queda persistida en data/REBA.db como una captura real
(es el comportamiento del endpoint). El contador de bomba avanza igual que
con fotos del ESP32 — limpiar pump_events del periodo si se requiere.
"""

import argparse
import json
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

import cv2
import numpy as np

SERVER = "http://localhost:5000"
IOU_THR = 0.5


def load_gt(label_path: Path, img_w: int, img_h: int):
    """YOLO label (class cx cy w h normalizado) -> [(cls, x1,y1,x2,y2)]."""
    boxes = []
    if not label_path.exists():
        return boxes
    for line in label_path.read_text().strip().splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        c = int(parts[0])
        cx, cy, w, h = map(float, parts[1:5])
        x1 = (cx - w / 2) * img_w
        y1 = (cy - h / 2) * img_h
        x2 = (cx + w / 2) * img_w
        y2 = (cy + h / 2) * img_h
        boxes.append((c, x1, y1, x2, y2))
    return boxes


def iou(a, b):
    """IoU entre xyxy (a: det, b: gt)."""
    x1 = max(a[0], b[1]); y1 = max(a[1], b[2])
    x2 = min(a[2], b[3]); y2 = min(a[3], b[4])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    if inter <= 0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[3] - b[1]) * max(0.0, b[4] - b[2])
    return inter / (area_a + area_b - inter)


def upload_image(jpg_path: Path, evento: str):
    body = jpg_path.read_bytes()
    req = urllib.request.Request(
        f"{SERVER}/upload",
        data=body,
        headers={"Content-Type": "image/jpeg", "X-Evento": evento},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def prf_at_threshold(all_dets, all_gts, n_classes, conf_thr=None):
    """TP/FP/FN por clase a un umbral de confianza dado (o todos)."""
    tp = [0] * n_classes; fp = [0] * n_classes; fn = [0] * n_classes
    for dets, gts in zip(all_dets, all_gts):
        used = [False] * len(gts)
        # dets ya vienen ordenadas por confianza desc? ordenar por si acaso
        order = sorted(range(len(dets)), key=lambda i: -dets[i]["confidence"])
        for i in order:
            d = dets[i]
            if conf_thr is not None and d["confidence"] < conf_thr:
                continue
            best_j, best_iou = -1, 0.0
            for j, g in enumerate(gts):
                if used[j] or g[0] != d["class_id"]:
                    continue
                v = iou((d["x1"], d["y1"], d["x2"], d["y2"]), g)
                if v > best_iou:
                    best_iou, best_j = v, j
            if best_j >= 0 and best_iou >= IOU_THR:
                used[best_j] = True
                tp[d["class_id"]] += 1
            else:
                fp[d["class_id"]] += 1
        for j, g in enumerate(gts):
            if not used[j]:
                fn[g[0]] += 1
    return tp, fp, fn


def ap_per_class(all_dets, all_gts, n_classes):
    """AP@0.5 por clase con interpolación 101 puntos (VOC-style)."""
    aps = []
    for c in range(n_classes):
        # GT total de la clase en todo el split (para el denominador del recall)
        npos = sum(1 for gts in all_gts for g in gts if g[0] == c)
        # todos los dets de la clase c ordenados por confianza desc
        entries = []  # (conf, is_tp)
        for dets, gts in zip(all_dets, all_gts):
            c_dets = sorted(
                [d for d in dets if d["class_id"] == c],
                key=lambda d: -d["confidence"],
            )
            used = [False] * len(gts)
            for d in c_dets:
                best_j, best_iou = -1, 0.0
                for j, g in enumerate(gts):
                    if used[j] or g[0] != c:
                        continue
                    v = iou((d["x1"], d["y1"], d["x2"], d["y2"]), g)
                    if v > best_iou:
                        best_iou, best_j = v, j
                if best_j >= 0 and best_iou >= IOU_THR:
                    used[best_j] = True
                    entries.append((d["confidence"], 1))
                else:
                    entries.append((d["confidence"], 0))
        if not entries or npos == 0:
            aps.append(None if npos == 0 else 0.0)
            continue
        entries.sort(key=lambda e: -e[0])
        tp_c = np.cumsum([e[1] for e in entries])
        fp_c = np.cumsum([1 - e[1] for e in entries])
        rec = tp_c / npos
        prec = tp_c / np.maximum(tp_c + fp_c, 1e-9)
        # interpolación 101 puntos
        ap = 0.0
        for t in np.linspace(0, 1, 101):
            m = prec[rec >= t]
            ap += (m.max() if len(m) else 0.0) / 101.0
        aps.append(float(ap))
    return aps


def main():
    global SERVER
    ap_ = argparse.ArgumentParser()
    ap_.add_argument("--dataset", default="datasets/REBA_taco")
    ap_.add_argument("--split", default="test", choices=["train", "val", "test"])
    ap_.add_argument("--limit", type=int, default=0, help="0 = todas")
    ap_.add_argument("--server", default=SERVER)
    ap_.add_argument("--out", default="ai_pipeline/eval_upload_report.json")
    args = ap_.parse_args()

    SERVER = args.server

    ds = Path(args.dataset)
    img_dir = ds / "images" / args.split
    lbl_dir = ds / "labels" / args.split
    imgs = sorted(img_dir.glob("*.jpg")) + sorted(img_dir.glob("*.png"))
    if args.limit:
        imgs = imgs[: args.limit]
    if not imgs:
        sys.exit(f"Sin imágenes en {img_dir}")

    # clases desde data.yaml
    names = {}
    yaml_path = ds / "data.yaml"
    if yaml_path.exists():
        for line in yaml_path.read_text().splitlines():
            line = line.strip()
            if line[:1].isdigit() and ":" in line:
                k, v = line.split(":", 1)
                names[int(k.strip())] = v.strip()
    n_classes = max(names) + 1 if names else 3
    class_names = [names.get(i, f"clase_{i}") for i in range(n_classes)]

    # sanity: server arriba
    try:
        urllib.request.urlopen(f"{SERVER}/api/health", timeout=5)
    except Exception as e:
        sys.exit(f"Server no responde en {SERVER}: {e}")

    print(f"Eval path /upload — {len(imgs)} imágenes de {ds.name}/{args.split}")
    print(f"Clases: {class_names}\n")

    all_dets, all_gts = [], []
    ms_list, errors = [], 0
    t0 = time.time()
    for i, img_path in enumerate(imgs, 1):
        frame = cv2.imread(str(img_path))
        if frame is None:
            continue
        h, w = frame.shape[:2]
        lbl = lbl_dir / (img_path.stem + ".txt")
        gts = load_gt(lbl, w, h)
        try:
            res = upload_image(img_path, f"eval-{ds.name}-{args.split}")
        except urllib.error.HTTPError as e:
            errors += 1
            print(f"  [{i}/{len(imgs)}] {img_path.name}: HTTP {e.code}")
            continue
        except Exception as e:
            errors += 1
            print(f"  [{i}/{len(imgs)}] {img_path.name}: {e}")
            continue
        dets = res.get("detections", [])
        all_dets.append(dets)
        all_gts.append(gts)
        ms_list.append(res.get("inference_ms", 0.0))
        if i % 10 == 0 or i == len(imgs):
            print(f"  [{i}/{len(imgs)}] capture_id={res.get('capture_id')} "
                  f"det={len(dets)} gt={len(gts)} "
                  f"({(time.time()-t0):.0f}s)", flush=True)

    # ---- métricas ----
    tp, fp, fn = prf_at_threshold(all_dets, all_gts, n_classes)
    print("\n=== Precision / Recall / F1 por clase (IoU>=0.5, conf>=server) ===")
    P = R = F1 = 0.0
    total_tp = total_fp = total_fn = 0
    for c, name in enumerate(class_names):
        p = tp[c] / (tp[c] + fp[c]) if tp[c] + fp[c] else 0.0
        r = tp[c] / (tp[c] + fn[c]) if tp[c] + fn[c] else 0.0
        f = 2 * p * r / (p + r) if p + r else 0.0
        P += tp[c]; total_tp += tp[c]; total_fp += fp[c]; total_fn += fn[c]
        print(f"  {name:10s} P={p:.3f} R={r:.3f} F1={f:.3f}  "
              f"(tp={tp[c]} fp={fp[c]} fn={fn[c]})")
    p_all = P / (P + total_fp) if P + total_fp else 0.0
    r_all = P / (P + total_fn) if P + total_fn else 0.0
    f_all = 2 * p_all * r_all / (p_all + r_all) if p_all + r_all else 0.0
    print(f"  {'GLOBAL':10s} P={p_all:.3f} R={r_all:.3f} F1={f_all:.3f}  "
          f"(tp={total_tp} fp={total_fp} fn={total_fn})")

    aps = ap_per_class(all_dets, all_gts, n_classes)
    valid_aps = [a for a in aps if a is not None]
    map50 = float(np.mean(valid_aps)) if valid_aps else 0.0
    print("\n=== mAP@0.5 por clase ===")
    for c, name in enumerate(class_names):
        if aps[c] is None:
            print(f"  {name:10s}  (sin GT en split)")
        else:
            print(f"  {name:10s} AP={aps[c]:.3f}")
    print(f"  {'mAP@0.5':10s} {map50:.3f}")
    if ms_list:
        print(f"\nInferencia: media={np.mean(ms_list):.1f} ms "
              f"p95={np.percentile(ms_list,95):.1f} ms "
              f"max={max(ms_list):.1f} ms")
    print(f"Errores de upload: {errors} | tiempo total: {time.time()-t0:.0f}s")

    report = {
        "dataset": str(ds), "split": args.split, "n_images": len(all_dets),
        "errors": errors,
        "per_class": {
            class_names[c]: {
                "precision": round(tp[c] / (tp[c] + fp[c]), 4) if tp[c] + fp[c] else 0,
                "recall": round(tp[c] / (tp[c] + fn[c]), 4) if tp[c] + fn[c] else 0,
                "tp": tp[c], "fp": fp[c], "fn": fn[c],
                "ap50": aps[c],
            } for c in range(n_classes)
        },
        "global": {"precision": round(p_all, 4), "recall": round(r_all, 4),
                   "f1": round(f_all, 4), "map50": round(map50, 4),
                   "tp": total_tp, "fp": total_fp, "fn": total_fn},
        "inference_ms_mean": round(float(np.mean(ms_list)), 1) if ms_list else None,
    }
    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nReporte: {args.out}")


if __name__ == "__main__":
    main()
