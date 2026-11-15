# Reporte de entrenamiento outdoor — REBA

Generado: 2026-11-15 22:59 UTC

## Contexto

- Modelo base: **YOLOv8n** (sin cambios de arquitectura), imgsz=640.
- Aumentación outdoor (albumentations, por época): RandomBrightnessContrast + RandomGamma + RandomShadow + RandomSunFlare (iluminación severa); RandomFog / RandomRain / Defocus + MotionBlur (clima y cámara en movimiento); HueSaturationValue + ToGray (basura desgastada por el sol); GaussNoise + ImageCompression (sensor ESP32-CAM); CoarseDropout (oclusión por hojas/tierra).
- Aumentación built-in extra de Ultralytics: `cutmix`, `mixup`, `erasing` (oclusión) + geometría leve.
- Early stopping por `patience`; `best.pt` elegido por fitness = mAP50-95 (peso 1.0 en Ultralytics ≥8.4).
- Entrenamiento combinado: dataset sintético + boost de clases raras (`REBA_synthetic_boost`) + subconjunto real TACO.

## Configuración de entrenamiento

- Pesos iniciales: `D:\Backup_Sebas\UES\Ciclo 6\ARC115\Proyecto\REBA\runs\detect\REBA_outdoor_v5\weights\last.pt`
- data.yaml: `D:\Backup_Sebas\UES\Ciclo 6\ARC115\Proyecto\REBA\datasets\REBA_outdoor\data.yaml`
- Épocas solicitadas: `50`
- Épocas ejecutadas: `30`
- Mejor época (fitness): `30`
- Batch: `8`
- imgsz: `640`
- Patience (early stop): `20`
- Optimizador: `AdamW`

## Modelos comparados

- **baseline (previo)**: `D:/Backup_Sebas/UES/Ciclo 6/ARC115/Proyecto/REBA/models/archive/best_20261112T172755Z.pt`
- **outdoor (nuevo)**: `D:/Backup_Sebas/UES/Ciclo 6/ARC115/Proyecto/REBA/models/best.pt`

## Conjuntos de evaluación

- `clean`
- `outdoor`
- `taco_real`
- `real_water`

## Métricas globales

| Modelo | Conjunto | Precisión | Recall | mAP50 | mAP50-95 |
|---|---|---|---|---|---|
| baseline (previo) | clean | 0.337 | 0.353 | 0.400 | 0.374 |
| outdoor (nuevo) | clean | 0.984 | 0.958 | 0.990 | 0.846 |
| baseline (previo) | outdoor | 0.310 | 0.256 | 0.306 | 0.260 |
| outdoor (nuevo) | outdoor | 0.970 | 0.914 | 0.971 | 0.762 |
| baseline (previo) | taco_real | 0.118 | 0.174 | 0.047 | 0.023 |
| outdoor (nuevo) | taco_real | 0.296 | 0.280 | 0.254 | 0.169 |
| baseline (previo) | real_water | 0.177 | 0.043 | 0.022 | 0.013 |
| outdoor (nuevo) | real_water | 0.495 | 0.556 | 0.469 | 0.329 |

## mAP50-95 por clase (conjunto outdoor difícil)

| Clase | baseline (previo) | outdoor (nuevo) | Δ |
|---|---|---|---|
| plastico | 0.727 | 0.698 | -0.030 |
| tela | 0.049 | 0.783 | +0.734 |
| organico | 0.002 | 0.805 | +0.803 |
| unicel | 0.260 | 0.000 |  |
| metal | 0.260 | 0.000 |  |

## Interpretación

- `clean`: ΔP +0.647, ΔR +0.605, ΔmAP50 +0.590, ΔmAP50-95 +0.472.
- `outdoor`: ΔP +0.660, ΔR +0.658, ΔmAP50 +0.665, ΔmAP50-95 +0.503.
- `taco_real`: ΔP +0.179, ΔR +0.106, ΔmAP50 +0.207, ΔmAP50-95 +0.146.
- `real_water`: ΔP +0.317, ΔR +0.512, ΔmAP50 +0.446, ΔmAP50-95 +0.316.

## Limitaciones

- `outdoor` es un proxy determinista de intemperie sobre imágenes sintéticas; no sustituye capturas reales de la ESP32-CAM en campo.
- `taco_real` tiene gap de dominio (fotos terrestres TACO vs. agua sintética) — los valores absolutos son bajos y deben leerse de forma comparativa, no absoluta.
- `real_water` (FloatWaste val limpio de fugas) es el set que decide el despliegue: agua real con objetos reales.
- La regresión en `clean` es esperada: el modelo nuevo reparte capacidad entre un dominio mucho más amplio; la decisión de despliegue debe priorizar `real_water`/`outdoor`/`taco_real`.

## Notas

- `clean`: test sintético original — condiciones ideales.
- `outdoor`: mismas imágenes val+test sintéticas degradadas de forma determinista (semilla fija) — proxy de intemperie con etiquetas exactas.
- `taco_real`: fotos reales TACO — distribución real; las clases sin equivalente REBA quedan como fondo (pueden penalizar recall).
- `real_water`: split val de FloatWaste sin near-duplicados en train — validación real acuática; nunca usada en entrenamiento.
- El modelo de producción es ONNX exportado desde el mismo `best.pt`; estas métricas usan el `.pt` directamente (val de Ultralytics).
