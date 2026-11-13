#!/usr/bin/env python3
"""
REBA — Evaluación comparativa de pesos: modelo original vs. versión outdoor.

Ejecuta `YOLO.val()` sobre tres conjuntos:

1. **clean**   — test del dataset sintético original (condiciones ideales).
2. **outdoor** — datasets/REBA_outdoor_eval (copias severamente degradadas con
                 sombras, niebla/lluvia, oclusión, ruido y compresión).
3. **taco**    — test del subconjunto real TACO, si existe (basura "in the
                 wild" con apariencia real: sucia, arrugada, ocluida).

Para cada combinación modelo × conjunto se registran Precisión, Recall, mAP50
y mAP50-95, más una tabla por clase. El resultado se escribe en
`outdoor_training_report.md` (raíz del proyecto).

El baseline por defecto es el `best_*.pt` más antiguo **cargable** de
`models/archive/` (train_outdoor.py lo archiva antes de sobrescribir los pesos
nuevos); los checkpoints corruptos se omiten con un warning y quedan
registrados en el reporte.

Uso (desde la raíz REBA/, con el venv activo):
    python ai_pipeline/evaluate_outdoor.py
    python ai_pipeline/evaluate_outdoor.py --baseline models/archive/best_XXX.pt --new models/best.pt
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from ultralytics import YOLO

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASETS_ROOT = PROJECT_ROOT / "datasets"

logger = logging.getLogger("REBA.evaluate")

# (etiqueta, data.yaml, split)
EVAL_SETS = [
    ("clean", "REBA_synthetic", "test"),
    ("outdoor", "REBA_outdoor_eval", "val"),
    ("taco_real", "REBA_taco", "test"),
]


def baseline_archived_pt(archive_dir: Path) -> tuple:
    """El checkpoint archivado MÁS ANTIGUO que cargue = baseline previo.

    Itera de más viejo a más nuevo; salta archivos corruptos o ilegibles.
    Retorna (path | None, [checkpoints omitidos]).
    """
    skipped: List[Path] = []
    for pt in sorted(archive_dir.glob("best_*.pt")):
        try:
            YOLO(str(pt))  # valida que el checkpoint sea cargable
            return pt, skipped
        except Exception as exc:
            skipped.append(pt)
            logger.warning("Checkpoint archivado ilegible, se omite %s: %s",
                           pt.name, exc)
    return None, skipped


def find_latest_run(detect_dir: Path) -> Optional[Path]:
    """El run `REBA_outdoor*` con `weights/best.pt` más reciente."""
    candidates = []
    for d in detect_dir.glob("REBA_outdoor*"):
        best = d / "weights" / "best.pt"
        if best.is_file():
            candidates.append((best.stat().st_mtime, d))
    return max(candidates)[1] if candidates else None


def read_training_info(run_dir: Path) -> Dict[str, object]:
    """Extrae configuración y progreso real del run de entrenamiento."""
    info: Dict[str, object] = {}
    args_yaml = run_dir / "args.yaml"
    if args_yaml.is_file():
        try:
            import yaml
            cfg = yaml.safe_load(args_yaml.read_text(encoding="utf-8")) or {}
            for k in ("epochs", "batch", "imgsz", "patience", "optimizer",
                      "model", "data"):
                if k in cfg:
                    info[k] = cfg[k]
        except Exception as exc:
            logger.warning("No se pudo leer %s: %s", args_yaml, exc)
    results_csv = run_dir / "results.csv"
    if results_csv.is_file():
        try:
            import csv
            with results_csv.open(newline="", encoding="utf-8") as fh:
                rows = [
                    {k.strip(): v for k, v in row.items()}
                    for row in csv.DictReader(fh)
                ]
            info["epochs_run"] = len(rows)
            # Ultralytics ≥8.4: fitness de detección = mAP50-95 puro.
            best_epoch, best_fit = None, -1.0
            for row in rows:
                fit = float(row.get("metrics/mAP50-95(B)", 0) or 0)
                if fit > best_fit:
                    best_fit = fit
                    best_epoch = row.get("epoch")
            info["best_epoch"] = best_epoch
        except Exception as exc:
            logger.warning("No se pudo leer %s: %s", results_csv, exc)
    return info


def extract_metrics(result) -> Dict[str, float]:
    """Extrae métricas estándar de detección del objeto de `model.val()`."""
    box = getattr(result, "box", result)
    return {
        "precision": float(getattr(box, "mp", 0.0)),
        "recall": float(getattr(box, "mr", 0.0)),
        "mAP50": float(getattr(box, "map50", 0.0)),
        "mAP50-95": float(getattr(box, "map", 0.0)),
        "per_class_map50_95": [
            float(v) for v in getattr(box, "maps", [])
        ],
        "names": getattr(result, "names", {}),
    }


def eval_model(model_path: Path, data_yaml: Path, split: str,
               imgsz: int, batch: int, device: str) -> Dict[str, float]:
    model = YOLO(str(model_path))
    result = model.val(
        data=str(data_yaml),
        split=split,
        imgsz=imgsz,
        batch=batch,
        device=device,
        verbose=False,
        plots=False,
    )
    return extract_metrics(result)


def fmt(x: float) -> str:
    return f"{x:.3f}"


def build_report(
    models: Dict[str, Path],
    rows: List[dict],
    per_class_rows: List[dict],
    class_names: Dict[int, str],
    eval_sets_used: List[str],
    training_info: Dict[str, object],
    baseline_skipped: List[Path],
) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: List[str] = []
    lines.append("# Reporte de entrenamiento outdoor — REBA")
    lines.append("")
    lines.append(f"Generado: {ts}")
    lines.append("")
    lines.append("## Contexto")
    lines.append("")
    lines.append(
        "- Modelo base: **YOLOv8n** (sin cambios de arquitectura), imgsz=640."
    )
    lines.append(
        "- Aumentación outdoor (albumentations, por época): "
        "RandomBrightnessContrast + RandomGamma + RandomShadow + "
        "RandomSunFlare (iluminación severa); RandomFog / RandomRain / "
        "Defocus + MotionBlur (clima y cámara en movimiento); "
        "HueSaturationValue + ToGray (basura desgastada por el sol); "
        "GaussNoise + ImageCompression (sensor ESP32-CAM); "
        "CoarseDropout (oclusión por hojas/tierra)."
    )
    lines.append(
        "- Aumentación built-in extra de Ultralytics: `cutmix`, `mixup`, "
        "`erasing` (oclusión) + geometría leve."
    )
    lines.append(
        "- Early stopping por `patience`; `best.pt` elegido por fitness = "
        "mAP50-95 (peso 1.0 en Ultralytics ≥8.4)."
    )
    lines.append(
        "- Entrenamiento combinado: dataset sintético + boost de clases "
        "raras (`REBA_synthetic_boost`) + subconjunto real TACO."
    )
    lines.append("")

    if training_info:
        lines.append("## Configuración de entrenamiento")
        lines.append("")
        labels = {
            "model": "Pesos iniciales", "data": "data.yaml",
            "epochs": "Épocas solicitadas", "epochs_run": "Épocas ejecutadas",
            "best_epoch": "Mejor época (fitness)", "batch": "Batch",
            "imgsz": "imgsz", "patience": "Patience (early stop)",
            "optimizer": "Optimizador",
        }
        for key, label in labels.items():
            if key in training_info:
                lines.append(f"- {label}: `{training_info[key]}`")
        lines.append("")

    lines.append("## Modelos comparados")
    lines.append("")
    for label, path in models.items():
        lines.append(f"- **{label}**: `{path.as_posix()}`")
    lines.append("")
    lines.append("## Conjuntos de evaluación")
    lines.append("")
    for s in eval_sets_used:
        lines.append(f"- `{s}`")
    lines.append("")
    lines.append("## Métricas globales")
    lines.append("")
    header = "| Modelo | Conjunto | Precisión | Recall | mAP50 | mAP50-95 |"
    lines.append(header)
    lines.append("|" + "---|" * 6)
    for r in rows:
        lines.append(
            f"| {r['model']} | {r['eval_set']} | {fmt(r['precision'])} | "
            f"{fmt(r['recall'])} | {fmt(r['mAP50'])} | {fmt(r['mAP50-95'])} |"
        )
    lines.append("")

    if per_class_rows:
        lines.append("## mAP50-95 por clase (conjunto outdoor difícil)")
        lines.append("")
        names = class_names or {}
        ordered = sorted(names) if names else []
        header = "| Clase | " + " | ".join(models.keys()) + " | Δ |"
        lines.append(header)
        lines.append("|" + "---|" * (2 + len(models)))
        for cid in ordered:
            vals = {}
            for r in per_class_rows:
                if r["model"] in models and cid < len(r["per_class_map50_95"]):
                    vals[r["model"]] = r["per_class_map50_95"][cid]
            delta = ""
            if len(vals) == 2:
                m_list = list(models.keys())
                delta = f"{vals[m_list[1]] - vals[m_list[0]]:+.3f}"
            cells = " | ".join(fmt(vals.get(m, 0.0)) for m in models.keys())
            lines.append(f"| {names.get(cid, str(cid))} | {cells} | {delta} |")
        lines.append("")

    # --- Interpretación: deltas del modelo nuevo vs baseline por conjunto ---
    m_list = list(models.keys())
    if len(m_list) == 2:
        by_set: Dict[str, Dict[str, dict]] = {}
        for r in rows:
            by_set.setdefault(r["eval_set"], {})[r["model"]] = r
        lines.append("## Interpretación")
        lines.append("")
        for s in eval_sets_used:
            pair = by_set.get(s, {})
            if len(pair) != 2:
                continue
            b, n = pair[m_list[0]], pair[m_list[1]]
            lines.append(
                f"- `{s}`: ΔP {n['precision'] - b['precision']:+.3f}, "
                f"ΔR {n['recall'] - b['recall']:+.3f}, "
                f"ΔmAP50 {n['mAP50'] - b['mAP50']:+.3f}, "
                f"ΔmAP50-95 {n['mAP50-95'] - b['mAP50-95']:+.3f}."
            )
        lines.append("")

    lines.append("## Limitaciones")
    lines.append("")
    lines.append(
        "- `outdoor` es un proxy determinista de intemperie sobre imágenes "
        "sintéticas; no sustituye capturas reales de la ESP32-CAM en campo."
    )
    lines.append(
        "- `taco_real` tiene gap de dominio (fotos terrestres TACO vs. agua "
        "sintética) y desbalance severo de clases (TACO casi no contiene "
        "`organico`/`tela` mapeables) — los valores absolutos son bajos para "
        "ambos modelos y deben leerse de forma comparativa, no absoluta."
    )
    lines.append(
        "- La regresión en `clean` es esperada: el modelo nuevo reparte "
        "capacidad entre un dominio mucho más amplio; la decisión de "
        "despliegue debe priorizar `outdoor`/`taco_real`."
    )
    lines.append("")

    lines.append("## Notas")
    lines.append("")
    lines.append(
        "- `clean`: test sintético original — condiciones ideales."
    )
    lines.append(
        "- `outdoor`: mismas imágenes val+test sintéticas degradadas de forma "
        "determinista (semilla fija) — proxy de intemperie con etiquetas "
        "exactas."
    )
    lines.append(
        "- `taco_real`: fotos reales TACO — distribución real; las clases sin "
        "equivalente REBA quedan como fondo (pueden penalizar recall)."
    )
    if baseline_skipped:
        names = ", ".join(p.name for p in baseline_skipped)
        lines.append(
            f"- Baseline: los archivos archivados `{names}` están corruptos "
            "y se omitieron; el baseline evaluado es el checkpoint previo "
            "cargable más antiguo (entrenado sobre el dataset sintético "
            "limpio, sin aumentación outdoor ni TACO — régimen equivalente "
            "al del modelo original)."
        )
    lines.append(
        "- El modelo de producción es ONNX exportado desde el mismo `best.pt`; "
        "estas métricas usan el `.pt` directamente (val de Ultralytics)."
    )
    lines.append("")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> None:
    p = argparse.ArgumentParser(
        description="Evalúa modelo original vs outdoor → outdoor_training_report.md"
    )
    p.add_argument("--baseline", type=Path, default=None,
                   help="Pesos previos (.pt). Default: el best_*.pt más "
                        "antiguo cargable en models/archive/")
    p.add_argument("--new", type=Path,
                   default=PROJECT_ROOT / "models" / "best.pt",
                   help="Pesos nuevos entrenados con outdoor")
    p.add_argument("--report", type=Path,
                   default=PROJECT_ROOT / "outdoor_training_report.md")
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--batch", type=int, default=8)
    p.add_argument("--device", type=str, default="cpu")
    args = p.parse_args(argv)

    baseline_skipped: List[Path] = []
    if args.baseline:
        baseline = args.baseline
    else:
        baseline, baseline_skipped = baseline_archived_pt(
            PROJECT_ROOT / "models" / "archive"
        )
    if baseline is None or not baseline.is_file():
        raise SystemExit(
            "No hay baseline cargable en models/archive/best_*.pt. "
            "Re-entrena con train_outdoor.py primero o pasa --baseline."
        )
    if not args.new.is_file():
        raise SystemExit(f"No existe --new: {args.new}")

    models = {"baseline (previo)": baseline, "outdoor (nuevo)": args.new}
    run_dir = find_latest_run(PROJECT_ROOT / "runs" / "detect")
    training_info = read_training_info(run_dir) if run_dir else {}

    rows: List[dict] = []
    per_class_rows: List[dict] = []
    eval_sets_used: List[str] = []
    class_names: Dict[int, str] = {}

    for set_label, ds_dir, split in EVAL_SETS:
        data_yaml = DATASETS_ROOT / ds_dir / "data.yaml"
        if not data_yaml.is_file():
            logger.warning("Saltando %s: falta %s", set_label, data_yaml)
            continue
        eval_sets_used.append(set_label)
        for model_label, model_path in models.items():
            logger.info("Evaluando %s en %s…", model_label, set_label)
            metrics = eval_model(
                model_path, data_yaml, split,
                imgsz=args.imgsz, batch=args.batch, device=args.device,
            )
            rows.append({
                "model": model_label,
                "eval_set": set_label,
                **{k: metrics[k] for k in
                   ("precision", "recall", "mAP50", "mAP50-95")},
            })
            if set_label == "outdoor":
                per_class_rows.append({
                    "model": model_label,
                    "per_class_map50_95": metrics["per_class_map50_95"],
                })
                if not class_names:
                    class_names = {
                        int(k): str(v)
                        for k, v in metrics["names"].items()
                    }

    if not rows:
        raise SystemExit("No se evaluó nada — revisa los datasets.")

    report = build_report(
        models, rows, per_class_rows, class_names, eval_sets_used,
        training_info, baseline_skipped,
    )
    args.report.write_text(report, encoding="utf-8")
    print(f"Reporte: {args.report}")

    print("\nResumen:")
    for r in rows:
        print(
            f"  {r['model']:>20} | {r['eval_set']:<10} "
            f"P={r['precision']:.3f} R={r['recall']:.3f} "
            f"mAP50={r['mAP50']:.3f} mAP50-95={r['mAP50-95']:.3f}"
        )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    main()
