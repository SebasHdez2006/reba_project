# Estado del trabajo — pipeline outdoor REBA

Snapshot al cortar sesión: entrenamiento **v2 en curso** (época 11/60).

## Lo que ya está hecho y verificado

- **Modelo desplegado (`models/best.pt` + `models/best.onnx`)** = outdoor v1,
  entrenado con sintético+TACO + aumentación albumentations. ONNX verificado
  end-to-end con `server/inference.py` (onnxruntime CPU, ~120-250 ms/frame).
- **`outdoor_training_report.md`** generado con métricas reales v1 vs baseline:
  - outdoor difícil: ΔP +0.049, ΔR +0.103, ΔmAP50 +0.113, ΔmAP50-95 +0.014
  - taco_real: ~8× mejor mAP50 (0.078 vs 0.009)
  - clean: regresión esperada (-0.12 mAP50)
- **Baseline**: el checkpoint original Oct-31 estaba corrupto (se eliminó).
  El baseline del reporte es `models/archive/best_20261112T172755Z.pt`
  (entrenado en sintético limpio — régimen equivalente al original).
- **Repo**: commit inicial + commit de tooling ya pusheados a
  `github.com/SebasHdez2006/reba_project` (main).

## Datasets (todos en `datasets/`, ignorados por git)

| Dataset | Contenido |
|---|---|
| `REBA_synthetic` | 240 train / 60 val / 60 test — limpio |
| `REBA_synthetic_boost` | 180 train con `organico`×4 y `tela`×3 (nuevo) |
| `REBA_taco` | 750 imágenes reales TACO convertidas a YOLO |
| `REBA_outdoor_eval` | 120 imágenes val+test degradadas (semilla fija) |
| `REBA_outdoor` | `data.yaml` combinado generado por train_outdoor.py |

## Entrenamiento v2 en curso

- Run: `runs/detect/REBA_outdoor_v2/` — 1022 train (sintético + boost + TACO)
- Config: yolov8n.pt, epochs=60, patience=15, batch=8, imgsz=640, CPU,
  workers=0, albumentations outdoor + cutmix/mixup/erasing
- Progreso (results.csv): época 1 = mejor hasta ahora (mAP50-95 = 0.618);
  early stop dispararía ~época 16 si nadie lo supera.
- `weights/best.pt` y `weights/last.pt` se guardan cada época.

## Al reconectar — según el escenario

### A) El entrenamiento v2 terminó solo (revisa `runs/detect/REBA_outdoor_v2/results.csv` completo y `models/best.pt` actualizado)

1. `python ai_pipeline/evaluate_outdoor.py` → regenera el reporte
   (ojo: el baseline será el v1 archivado — correcto).
2. `python ai_pipeline/tune_confidence.py` → umbral óptimo;
   actualizar `INFERENCE_CONFIDENCE` en `.env` (no existe aún — crearlo o
   editar `.env.example`).
3. Sanity check ONNX con `server/inference.py` (ya probado en v1).
4. Commit + push (identidad git ya configurada: SebasHdez2006).

### B) El entrenamiento v2 se interrumpió (results.csv truncado)

Reanudar desde el último checkpoint:

```python
from ultralytics import YOLO
YOLO("runs/detect/REBA_outdoor_v2/weights/last.pt").train(resume=True)
```

o volver a lanzar `python ai_pipeline/train_outdoor.py --epochs 60
--patience 15 --name REBA_outdoor_v2` (empieza de cero pero es determinista).
Luego seguir los pasos del escenario A.

### C) Decidir no esperar: quedarse con v1

Nada que hacer — `models/best.*` ya contiene v1 verificado. Solo faltaría
`tune_confidence.py` sobre el modelo actual.

## Pendientes no iniciados

- Barrido de `INFERENCE_CONFIDENCE` (script listo, falta correr sobre el
  modelo final).
- Capturas reales ESP32-CAM para evaluación definitiva (doc §5 de
  ai_pipeline.md).
- `organico` sigue siendo la clase más débil — el boost sintético ayuda,
  pero datos reales serían lo ideal.
