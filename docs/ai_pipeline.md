# REBA — Pipeline de IA (dataset + entrenamiento)

Herramientas en `ai_pipeline/` para construir el dataset, entrenar YOLOv8n y exportar ONNX para el servidor local.

**Regla operativa del proyecto:** el producto final debe funcionar **100% offline**. Eso significa:
- no depender de repositorios remotos en tiempo de ejecución,
- no llamar APIs externas durante inferencia,
- y no requerir Internet para usar el modelo ya entrenado.

El flujo recomendado tiene dos fases:

1. **Bootstrap temporal** (si hace falta): descargar imágenes con licencia reutilizable y auto-etiquetarlas.
2. **Modo final offline**: entrenar, exportar y desplegar exclusivamente con artefactos locales.

En ambos casos se debe conservar el histórico local en `models/archive/` antes de sobrescribir pesos.


---

## 0. Adquisición de imágenes reales — `download_openverse_dataset.py`

> **Bootstrap temporal únicamente.** Este paso puede usar Internet para reunir material inicial, pero el sistema final no debe depender de esta descarga ni consultar repositorios remotos en producción.

Este script usa **Openverse** como agregador de contenido reutilizable. Openverse expone metadatos de licencia y atribución por imagen, así que podemos filtrar de antemano por licencias compatibles con entrenamiento y distribución.


### Licencias aceptadas

- CC0 / Public Domain Mark
- CC BY
- CC BY-SA

Se excluyen licencias **NC** y **ND** para evitar restricciones no deseadas en el dataset.

### Uso

```bash
python ai_pipeline/download_openverse_dataset.py --output datasets/REBA_raw
```

### Salida

```text
datasets/REBA_raw/
├── images/train|val|test/...
├── manifests/train|val|test/...
├── manifest.csv
├── manifest.jsonl
└── summary.json
```

### Fuentes y justificación

Investigamos Openverse antes de descargar a gran escala. Las búsquedas devuelven inventario suficiente y con licencia clara para una primera versión del dataset:

| Consulta Openverse | Señal útil | Licencia típica | Comentario |
|---|---:|---|---|
| `floating trash water` | ~84 resultados | CC BY 2.0 | Buenas escenas de agua con basura flotante |
| `river debris pollution` | ~240 resultados | CC BY 2.0 / CC0 | Mucho contexto de ríos y contaminación |
| `beach litter water` | ~232 resultados | CC BY 2.0 | Útil como hard negatives y escenas costeras |
| `plastic bottles river water` | ~180 resultados | CC BY 2.0 / CC BY-SA | Muy útil para la clase `plastico` |
| `ocean trash water` | ~240 resultados | CC BY 2.0 / CC BY-SA | Escenas marítimas, playas y canalizaciones |
| `styrofoam water floating` | ~43 resultados | CC BY / CC BY-SA | Útil para `unicel` |
| `food waste water` | ~21 resultados | CC BY / PDM | Útil como fuente de `organico` |

Justificación: Openverse centraliza fuentes con atribución y licencia explícita, lo que reduce el riesgo legal frente a scraping libre de Internet.

---


## Clases del modelo

| class_id | Nombre |
|----------|--------|
| 0 | plastico |
| 1 | tela |
| 2 | organico |
| 3 | unicel |
| 4 | metal |

El orden debe coincidir con `CLASS_NAMES` en `.env` y con las anotaciones YOLO.

> Nota: el final del proyecto debe operar sin consultar repositorios remotos; solo con archivos ya presentes en disco.


---

## 1. Auto-etiquetado — `dataset_builder.py`

> **Bootstrap temporal únicamente.** Si se usa, debe ser solo para construir la primera versión del dataset. El modelo final y el servidor de inferencia deben funcionar sin llamadas a OpenAI ni a ningún otro servicio remoto.

Usa **OpenAI Vision (`gpt-4o-mini`)** para analizar cada imagen y generar `.txt` en formato YOLO.


Ahora el script acepta entradas ya particionadas (`train/val/test`) o una carpeta plana; además deduplica por hash y genera `metadata/` por split.


### Requisitos

```bash
pip install -r requirements-bootstrap.txt
```

Configura en `.env`:

```env
OPENAI_API_KEY=sk-...
OPENAI_VISION_MODEL=gpt-4o-mini
```

### Uso

```bash
# Desde la raíz REBA/
python ai_pipeline/dataset_builder.py --input raw_images --output datasets/REBA
```

### Argumentos CLI

| Flag | Default | Descripción |
|------|---------|-------------|
| `--input` | (requerido) | Carpeta de imágenes crudas |
| `--output` | `datasets/REBA` | Dataset YOLO de salida |
| `--classes` | plastico,tela,... | Orden = class_id |
| `--model` | gpt-4o-mini | Modelo Vision |
| `--val-ratio` | 0.2 | Fracción validación |
| `--test-ratio` | 0.1 | Fracción test |
| `--min-conf` | 0.35 | Filtra cajas poco confiables |
| `--limit` | None | Solo N primeras (pruebas) |
| `--sleep` | 0.4 | Pausa entre llamadas API |

### Salida generada

```
datasets/REBA/
├── data.yaml
├── images/train/ ...
├── images/val/ ...
├── images/test/ ...
├── labels/train/*.txt
├── labels/val/*.txt
├── labels/test/*.txt
└── metadata/train|val|test/*.json

```

`data.yaml` conserva las rutas de cada partición relativas al propio archivo. Así se puede mover el dataset sin editar una ruta absoluta del equipo.

Cada línea de label:

```text
<class_id> <x_center> <y_center> <width> <height>
```

Valores normalizados en \[0, 1\].

### Buenas prácticas

- Revisa visualmente un subconjunto de labels (herramientas como Label Studio / CVAT / YOLO Label) — el auto-etiquetado es un **acelerador**, no verdad absoluta.
- Usa fotos con residuos flotantes similares a las del entorno real (agua, iluminación, distancia de cámara).
- Empieza con `--limit 10` para validar costos de API.

---

## 2. Entrenamiento — `train_outdoor.py`

El entrenamiento oficial es **100% local** con `ai_pipeline/train_outdoor.py` (CPU). Combina el dataset sintético (+boost de clases raras) con el subconjunto real TACO, inyecta augmentations de intemperie vía Albumentations, usa early stopping por mAP50-95 y exporta `models/best.onnx` archivando los pesos previos en `models/archive/`.

```bash
python ai_pipeline/train_outdoor.py --epochs 50   # desde la raíz REBA/
```

| Parámetro | Recomendado |
|-----------|-------------|
| `EPOCHS` | 50 (early stop `patience=20` suele cortar antes) |
| `IMGSZ` | 640 |
| `BATCH` | 8–16 (8 en CPU) |
| `MODEL` | `yolov8n.pt` (Ultralytics lo descarga solo si falta) |

El detalle completo del pipeline outdoor está en §2b.

---

## 2b. Pipeline outdoor (robustez a la intemperie) — detalle de `train_outdoor.py`

El dataset sintético es muy limpio: fondos planos, objetos de color sólido, sin
sombras ni oclusiones. Para que el modelo funcione a la intemperie (basura
sucia, arrugada, distintas horas del día, clima, oclusión parcial) se añaden
tres herramientas:

| Script | Propósito |
|--------|-----------|
| `ai_pipeline/download_taco.py` | Descarga un subconjunto de **TACO** (basura real "in the wild", formato COCO) y lo convierte a YOLO con las 5 clases REBA en `datasets/REBA_taco/`. |
| `ai_pipeline/outdoor_transforms.py` | Define el pipeline albumentations compartido (entrenamiento y evaluación). |
| `ai_pipeline/build_outdoor_eval.py` | Genera `datasets/REBA_outdoor_eval/` — copias deterministas y severamente degradadas de val+test, para evaluar robustez. |
| `ai_pipeline/train_outdoor.py` | Re-entrena YOLOv8n combinando sintético + boost de clases raras + TACO, con la aumentación outdoor inyectada vía `model.train(augmentations=...)`, early stopping (`patience`) y exportación ONNX con archivo de pesos previos. |
| `ai_pipeline/evaluate_outdoor.py` | Compara pesos previos (`models/archive/best_*.pt`) vs. nuevos en clean/outdoor/TACO y escribe `outdoor_training_report.md`. |
| `ai_pipeline/eval_upload_path.py` | Evalúa el **path de producción real**: sube imágenes a `POST /upload` del server y compara detecciones contra ground-truth YOLO → `eval_upload_report.json`. |
| `ai_pipeline/tune_confidence.py` | Barre `conf` sobre el conjunto difícil y recomienda `INFERENCE_CONFIDENCE` (máx. F1). |

### Aumentación outdoor

- **Iluminación severa**: `RandomBrightnessContrast`, `RandomGamma`, `RandomShadow`, `RandomSunFlare`.
- **Clima / ambiente**: `RandomFog`, `RandomRain`, `Defocus`, `MotionBlur`.
- **Desgaste por el sol**: `HueSaturationValue`, `ToGray`.
- **Sensor ESP32-CAM**: `GaussNoise`, `ImageCompression` (JPEG agresivo).
- **Oclusión parcial**: `CoarseDropout` (albumentations) + `erasing` y `cutmix` (built-in de Ultralytics).

Todas son transformaciones de nivel píxel (no espaciales): las cajas YOLO no
cambian y el pipeline es seguro para detección.

### Mapeo de clases TACO → REBA

Las 60 categorías COCO de TACO se mapean a las 5 clases en la tabla
`TACO_TO_REBA` de `download_taco.py` (botellas/etiquetas plásticas, bolsas,
film, utensilios → `plastico`; cuerdas/calzado → `tela`; residuos de comida →
`organico`; espuma/styrofoam → `unicel`; latas/foil/tapas metálicas → `metal`).
Las categorías sin equivalente (vidrio, papel, cartón, cigarros, basura sin
etiquetar) se ignoran — quedan como fondo, lo que enseña al modelo a no
detectarlas.

### Flujo completo outdoor

```bash
python ai_pipeline/download_taco.py --limit 400      # bootstrap con Internet
python ai_pipeline/generate_synthetic_dataset.py \   # opcional: boost clases raras
    --output datasets/REBA_synthetic_boost \
    --n-train 180 --n-val 0 --n-test 0 \
    --class-weights 1,3,4,1,1 --seed 777
python ai_pipeline/build_outdoor_eval.py --preview   # conjunto difícil
python ai_pipeline/train_outdoor.py --epochs 50      # re-entrenar
python ai_pipeline/evaluate_outdoor.py               # reporte comparativo
python ai_pipeline/tune_confidence.py                # umbral óptimo → .env
```

Si `datasets/REBA_synthetic_boost/` existe, `train_outdoor.py` lo suma
automáticamente al train combinado (recomendado para reforzar `organico` y
`tela`, que son escasas en TACO).

`train_outdoor.py` archiva los pesos previos en `models/archive/` antes de
sobrescribir, así `evaluate_outdoor.py` siempre tiene baseline.

---

## 3. Inferencia en el servidor

**Modo final offline:** el servidor usa únicamente el modelo ONNX local. No existe fallback remoto en el servidor.

Orden de prioridad recomendado para producción:

1. Si existe `MODEL_PATH` y carga OK → **ONNX local**.
2. Si no hay ONNX o falla la inferencia, devolver `source: none` y registrar el error.


Parámetros editables:

```env
MODEL_PATH=models/best.onnx
INFERENCE_CONFIDENCE=0.35
INFERENCE_IOU=0.45
```

---

## 4. Flujo completo recomendado

### Bootstrap temporal

```
Fotos crudas (locales o con licencia)
    → download_openverse_dataset.py (opcional)
    → dataset_builder.py (opcional)
    → datasets/REBA (revisar labels)
    → entrenamiento local
    → best.onnx
    → models/best.onnx
```

### Despliegue final offline

```
Datos locales ya curados
    → entrenamiento local
    → best.onnx
    → models/best.onnx
    → server/main.py
```


---

## 5. Reentrenar con fotos reales del sistema

Cada upload del ESP32 guarda **dos** JPEG en `captures`: `image_jpeg` (anotado con cajas, para el dashboard) e `image_orig_jpeg` (crudo, para entrenamiento). Las fotos con `detection_count = 0` sirven como background/negativos.

**Vía rápida:** botón "Exportar YOLO" del dashboard (o `GET /api/export/yolo?min_conf=…`) → ZIP con `images/` + `labels/` + `data.yaml` ya en formato YOLO listo para mezclar con los datasets sintéticos y reentrenar.

**Vía manual:** extraer `image_orig_jpeg` de `captures` (`SELECT id, image_orig_jpeg FROM captures`) a una carpeta, corregir/añadir labels con CVAT/Label Studio o `dataset_builder.py` (bootstrap), mezclar con `datasets/REBA_synthetic*`/`REBA_taco` y reentrenar con `train_outdoor.py`.

> Cuidado al evaluar contra el server en producción: cada upload cuenta como foto hacia el ciclo de bomba y crea `pump_events` — usar un `.env`/`DATABASE_PATH` separado o purgar las capturas de evaluación después.

---

## Costos y límites

- En modo final offline, **no debe haber costos por API**.
- `dataset_builder.py` y `download_openverse_dataset.py` solo se usan si decides hacer bootstrap inicial.
- La adquisición y el auto-etiquetado remotos son solo pasos opcionales de bootstrap; el servidor de producción no llama APIs externas.

