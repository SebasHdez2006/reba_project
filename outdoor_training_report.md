# Reporte de entrenamiento outdoor — REBA

> **⚠ Reporte histórico (pre-pivote):** corresponde al modelo **v2 de 5
> clases** (`plastico, tela, organico, unicel, metal`). Desde el
> 2026-11-14 el sistema usa **3 clases** (`plastico, unicel, metal`) y el
> experimento vigente es `REBA_outdoor_v5`. Este archivo se **regenera
> automáticamente** con `ai_pipeline/evaluate_outdoor.py` cuando se
> evalúe el modelo nuevo — mientras tanto se conserva como baseline.

Generado: 2026-11-13 10:15 UTC

## Contexto

- Modelo base: **YOLOv8n** (sin cambios de arquitectura), imgsz=640.
- Aumentación outdoor (albumentations, por época): RandomBrightnessContrast + RandomGamma + RandomShadow + RandomSunFlare (iluminación severa); RandomFog / RandomRain / Defocus + MotionBlur (clima y cámara en movimiento); HueSaturationValue + ToGray (basura desgastada por el sol); GaussNoise + ImageCompression (sensor ESP32-CAM); CoarseDropout (oclusión por hojas/tierra).
- Aumentación built-in extra de Ultralytics: `cutmix`, `mixup`, `erasing` (oclusión) + geometría leve.
- Early stopping por `patience`; `best.pt` elegido por fitness = mAP50-95 (peso 1.0 en Ultralytics ≥8.4).
- Entrenamiento combinado: dataset sintético + boost de clases raras (`REBA_synthetic_boost`) + subconjunto real TACO.

## Configuración de entrenamiento

- Pesos iniciales: `runs\detect\REBA_outdoor_v2\weights\last.pt`
- data.yaml: `D:\Backup_Sebas\UES\Ciclo 6\ARC115\Proyecto\REBA\datasets\REBA_outdoor\data.yaml`
- Épocas solicitadas: `60`
- Épocas ejecutadas: `14`
- Mejor época (fitness): `13`
- Batch: `8`
- imgsz: `640`
- Patience (early stop): `15`
- Optimizador: `AdamW`

## Modelos comparados

- **baseline (previo)**: `D:/Backup_Sebas/UES/Ciclo 6/ARC115/Proyecto/REBA/models/archive/best_20261112T172755Z.pt`
- **outdoor (nuevo)**: `D:/Backup_Sebas/UES/Ciclo 6/ARC115/Proyecto/REBA/models/best.pt`

## Conjuntos de evaluación

- `clean`
- `outdoor`
- `taco_real`

## Métricas globales

| Modelo | Conjunto | Precisión | Recall | mAP50 | mAP50-95 |
|---|---|---|---|---|---|
| baseline (previo) | clean | 0.873 | 0.935 | 0.915 | 0.802 |
| outdoor (nuevo) | clean | 0.768 | 0.888 | 0.879 | 0.650 |
| baseline (previo) | outdoor | 0.654 | 0.623 | 0.636 | 0.533 |
| outdoor (nuevo) | outdoor | 0.733 | 0.803 | 0.820 | 0.576 |
| baseline (previo) | taco_real | 0.030 | 0.028 | 0.013 | 0.008 |
| outdoor (nuevo) | taco_real | 0.129 | 0.299 | 0.133 | 0.070 |

## mAP50-95 por clase (conjunto outdoor difícil)

| Clase | baseline (previo) | outdoor (nuevo) | Δ |
|---|---|---|---|
| plastico | 0.727 | 0.631 | -0.097 |
| tela | 0.635 | 0.688 | +0.053 |
| organico | 0.092 | 0.095 | +0.004 |
| unicel | 0.672 | 0.743 | +0.070 |
| metal | 0.538 | 0.725 | +0.187 |

## Interpretación

- `clean`: ΔP -0.106, ΔR -0.046, ΔmAP50 -0.036, ΔmAP50-95 -0.151.
- `outdoor`: ΔP +0.079, ΔR +0.180, ΔmAP50 +0.183, ΔmAP50-95 +0.044.
- `taco_real`: ΔP +0.099, ΔR +0.271, ΔmAP50 +0.120, ΔmAP50-95 +0.062.

## Limitaciones

- `outdoor` es un proxy determinista de intemperie sobre imágenes sintéticas; no sustituye capturas reales de la ESP32-CAM en campo.
- `taco_real` tiene gap de dominio (fotos terrestres TACO vs. agua sintética) y desbalance severo de clases (TACO casi no contiene `organico`/`tela` mapeables) — los valores absolutos son bajos para ambos modelos y deben leerse de forma comparativa, no absoluta.
- La regresión en `clean` es esperada: el modelo nuevo reparte capacidad entre un dominio mucho más amplio; la decisión de despliegue debe priorizar `outdoor`/`taco_real`.

## Notas

- `clean`: test sintético original — condiciones ideales.
- `outdoor`: mismas imágenes val+test sintéticas degradadas de forma determinista (semilla fija) — proxy de intemperie con etiquetas exactas.
- `taco_real`: fotos reales TACO — distribución real; las clases sin equivalente REBA quedan como fondo (pueden penalizar recall).
- El modelo de producción es ONNX exportado desde el mismo `best.pt`; estas métricas usan el `.pt` directamente (val de Ultralytics).
