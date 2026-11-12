# Reporte de entrenamiento outdoor — REBA

Generado: 2026-11-12 19:02 UTC

## Contexto

- Modelo base: **YOLOv8n** (sin cambios de arquitectura), imgsz=640.
- Aumentación outdoor (albumentations, por época): RandomBrightnessContrast + RandomGamma + RandomShadow + RandomSunFlare (iluminación severa); RandomFog / RandomRain / Defocus + MotionBlur (clima y cámara en movimiento); HueSaturationValue + ToGray (basura desgastada por el sol); GaussNoise + ImageCompression (sensor ESP32-CAM); CoarseDropout (oclusión por hojas/tierra).
- Aumentación built-in extra de Ultralytics: `cutmix`, `mixup`, `erasing` (oclusión) + geometría leve.
- Early stopping por `patience`; `best.pt` elegido por fitness = mAP50-95 (peso 1.0 en Ultralytics ≥8.4).
- Entrenamiento combinado: dataset sintético + subconjunto real TACO (basura in the wild) cuando está disponible.

## Configuración de entrenamiento

- Pesos iniciales: `yolov8n.pt`
- data.yaml: `D:\Backup_Sebas\UES\Ciclo 6\ARC115\Proyecto\REBA\datasets\REBA_outdoor\data.yaml`
- Épocas solicitadas: `50`
- Épocas ejecutadas: `21`
- Mejor época (fitness): `6`
- Batch: `8`
- imgsz: `640`
- Patience (early stop): `15`
- Optimizador: `auto`

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
| outdoor (nuevo) | clean | 0.728 | 0.738 | 0.796 | 0.645 |
| baseline (previo) | outdoor | 0.654 | 0.623 | 0.636 | 0.533 |
| outdoor (nuevo) | outdoor | 0.703 | 0.726 | 0.749 | 0.547 |
| baseline (previo) | taco_real | 0.043 | 0.027 | 0.009 | 0.006 |
| outdoor (nuevo) | taco_real | 0.129 | 0.110 | 0.078 | 0.042 |

## mAP50-95 por clase (conjunto outdoor difícil)

| Clase | baseline (previo) | outdoor (nuevo) | Δ |
|---|---|---|---|
| plastico | 0.727 | 0.621 | -0.107 |
| tela | 0.635 | 0.692 | +0.057 |
| organico | 0.092 | 0.040 | -0.052 |
| unicel | 0.672 | 0.649 | -0.023 |
| metal | 0.538 | 0.734 | +0.196 |

## Interpretación

- `clean`: ΔP -0.145, ΔR -0.196, ΔmAP50 -0.120, ΔmAP50-95 -0.156.
- `outdoor`: ΔP +0.049, ΔR +0.103, ΔmAP50 +0.113, ΔmAP50-95 +0.014.
- `taco_real`: ΔP +0.086, ΔR +0.083, ΔmAP50 +0.069, ΔmAP50-95 +0.036.

## Limitaciones

- `outdoor` es un proxy determinista de intemperie sobre imágenes sintéticas; no sustituye capturas reales de la ESP32-CAM en campo.
- `taco_real` tiene gap de dominio (fotos terrestres TACO vs. agua sintética) y desbalance severo de clases (TACO casi no contiene `organico`/`tela` mapeables) — los valores absolutos son bajos para ambos modelos y deben leerse de forma comparativa, no absoluta.
- La regresión en `clean` es esperada: el modelo nuevo reparte capacidad entre un dominio mucho más amplio; la decisión de despliegue debe priorizar `outdoor`/`taco_real`.

## Notas

- `clean`: test sintético original — condiciones ideales.
- `outdoor`: mismas imágenes val+test sintéticas degradadas de forma determinista (semilla fija) — proxy de intemperie con etiquetas exactas.
- `taco_real`: fotos reales TACO — distribución real; las clases sin equivalente REBA quedan como fondo (pueden penalizar recall).
- Baseline: los archivos archivados `best_20261112T141304Z.pt` están corruptos y se omitieron; el baseline evaluado es el checkpoint previo cargable más antiguo (entrenado sobre el dataset sintético limpio, sin aumentación outdoor ni TACO — régimen equivalente al del modelo original).
- El modelo de producción es ONNX exportado desde el mismo `best.pt`; estas métricas usan el `.pt` directamente (val de Ultralytics).
