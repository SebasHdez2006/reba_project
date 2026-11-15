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
| `metal can floating water` | ~decenas | CC BY / CC0 | Útil para `metal` |

Justificación: Openverse centraliza fuentes con atribución y licencia explícita, lo que reduce el riesgo legal frente a scraping libre de Internet.

---


## Clases del modelo

| class_id | Nombre |
|----------|--------|
| 0 | plastico |
| 1 | unicel |
| 2 | metal |

> **Modelo de 3 clases (desde 2026-11-14).** Se eliminaron `tela` y
> `organico`: no existía dataset público acuático multi-clase que las
> cubriera y sus mAP eran inaceptables (`organico` ≈0.10–0.15). Los
> objetos de esas categorías quedan como **fondo** — el modelo aprende a
> ignorarlos (o a absorberlos en `plastico` si visualmente se parecen).
> Las etiquetas originales de 5 clases están respaldadas en
> `datasets/_backup_labels_5class/` y `ai_pipeline/filter_classes.py`
> documenta la migración (reversible).

El orden debe coincidir con `CLASS_NAMES` en `.env` y con las anotaciones YOLO.

> Nota: el final del proyecto debe operar sin consultar repositorios remotos; solo con archivos ya presentes en disco.

### Definiciones de clase (guía de etiquetado y remapeo)

| Clase | ES | EN | Incluye | NO incluye (va a fondo) |
|---|---|---|---|---|
| `plastico` | Plástico rígido o flexible sumergido/flotante | Rigid/flexible plastic | Botellas PET/PE, bolsas, film, envases, sorbete, cubiertos, tapitas, vasos duros, mascarillas, paquetes | Cartón/papel, vidrio, metal, unicel, textiles, materia orgánica |
| `unicel` | Poliestireno expandido / foam | Polystyrene foam | Vasos, contenedores y platos de foam, aislante de foam, foam fragmentado | Plásticos rígidos (vasos duros → `plastico`), gomaespuma no-PS si es dudoso (fondo) |
| `metal` | Metal: envases y piezas | Metal / ferrous & non-ferrous | Latas (bebida/comida), aerosoles, tapones metálicos, piezas de alambre, clavos, utensilios metálicos | Objetos mayormente plásticos con parte metálica pequeña |

**Fondo explícito:** textiles (`tela`), materia orgánica (`organico`),
vidrio, papel/cartón, hule/caucho y objetos irreconocibles NO se etiquetan
— enseñan al modelo a no disparar sobre ellos.

**Criterio de borde para remapeo externo:** si un dataset etiqueta por
*material* (p. ej. "paper", "glass", "rubber"), mapear solo cuando el
material sea inequívocamente uno de los 3; si etiqueta por *objeto*
("bottle", "can", "foam cup"), mapear por la clase dominante del objeto
en ambiente acuático.


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
| `--classes` | plastico,unicel,metal,context | Orden = class_id |
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

El entrenamiento oficial es **100% local** con `ai_pipeline/train_outdoor.py` (CPU). Combina el dataset sintético (+boosts de clases raras) con las fuentes reales presentes en `datasets/` — TACO, EcoSort, FloatWaste y copy-paste — inyecta augmentations de intemperie vía Albumentations, usa early stopping por mAP50-95 y exporta `models/best.onnx` archivando los pesos previos en `models/archive/`.

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
| `ai_pipeline/download_taco.py` | Descarga TACO (basura real "in the wild", formato COCO) y lo convierte a YOLO con las 3 clases REBA en `datasets/REBA_taco/`. Soporta `--limit 0` (todo), `--per-class` y `--prioritize-classes` para sesgar la descarga hacia clases escasas; reanuda por `manifest.jsonl`. |
| `ai_pipeline/download_ecosort.py` | Descarga el dataset **EcoSort** de Hugging Face (`dataset.tar` ~1.5 GB, YOLO de 10 clases ya fusionado de Open Images + TACO + Roboflow) y lo remapea a las 3 clases REBA en `datasets/REBA_ecosort/`. Vidrio/cartón/papel quedan como fondo. `--limit`/`--val-limit`/`--seed` para subconjunto determinista; `--tar-file` reutiliza un tar local. |
| `ai_pipeline/download_floatwaste.py` | Descarga **FloatWaste** (HF `OliviaWuYJ/sc-floatwaste`, CC BY 4.0 — 7,906 imgs de superficie de río, COCO). Remapea 25 categorías → REBA en `datasets/REBA_floatwaste/` (train priorizado a clases raras + `--negatives` de agua limpia) y **todo el split val → `datasets/REBA_real_val/`** = set de validación real acuático que NUNCA entra a entrenamiento. Re-encodea a JPEG ≤1600px. |
| `ai_pipeline/generate_copypaste_dataset.py` | Genera `datasets/REBA_copypaste/` (~600 imgs): recorta instancias reales de todos los `REBA_*` y las pega sobre fondos de agua con escala/rotación/jitter/submersión parcial. |
| `ai_pipeline/dedup_audit.py` | Auditoría de near-duplicados perceptuales (dHash 64-bit, Hamming≤6) entre todos los `REBA_*` → `reports/dedup_report.json`. Detecta fugas train↔val/test. |
| `ai_pipeline/clean_real_val.py` | Elimina de `REBA_real_val` las imágenes con near-duplicado en train (FloatWaste reparte frames de video; el split oficial no es visualmente disjunto). |
| `ai_pipeline/outdoor_transforms.py` | Define el pipeline albumentations compartido (entrenamiento y evaluación). |
| `ai_pipeline/build_outdoor_eval.py` | Genera `datasets/REBA_outdoor_eval/` — copias deterministas y severamente degradadas de val+test, para evaluar robustez. |
| `ai_pipeline/train_outdoor.py` | Re-entrena YOLOv8n combinando sintético + boosts + TACO + EcoSort + FloatWaste + copy-paste (auto-descubre `OPTIONAL_TRAIN_SOURCES`), con la aumentación outdoor inyectada vía `model.train(augmentations=...)`, early stopping (`patience`) y exportación ONNX con archivo de pesos previos. |
| `ai_pipeline/evaluate_outdoor.py` | Compara pesos previos (`models/archive/best_*.pt`) vs. nuevos en clean/outdoor/TACO/**real_water** (`REBA_real_val` — FloatWaste val limpio de fugas) y escribe `outdoor_training_report.md`. |
| `ai_pipeline/eval_upload_path.py` | Evalúa el **path de producción real**: sube imágenes a `POST /upload` del server y compara detecciones contra ground-truth YOLO → `eval_upload_report.json`. |
| `ai_pipeline/tune_confidence.py` | Barre `conf` sobre el conjunto difícil y recomienda `INFERENCE_CONFIDENCE` (máx. F1). |
| `ai_pipeline/train_monitor.py` | Monitor GUI (tkinter) de solo lectura: épocas, losses, LR, mAP, métricas por clase, telemetría de hardware Windows y ETAs — ver §2c. |
| `ai_pipeline/val_snapshot.py` | `model.val()` sobre `weights/last.pt` → JSON por clase (P/R/mAP/img/inst) + `speed` (ms/img). Lo invoca el monitor una vez por época vía subprocess. |

### Aumentación outdoor

- **Iluminación severa**: `RandomBrightnessContrast`, `RandomGamma`, `RandomShadow`, `RandomSunFlare`.
- **Clima / ambiente**: `RandomFog`, `RandomRain`, `Defocus`, `MotionBlur`.
- **Desgaste por el sol**: `HueSaturationValue`, `ToGray`.
- **Sensor ESP32-CAM**: `GaussNoise`, `ImageCompression` (JPEG agresivo).
- **Oclusión parcial**: `CoarseDropout` (albumentations) + `erasing` y `cutmix` (built-in de Ultralytics).

Todas son transformaciones de nivel píxel (no espaciales): las cajas YOLO no
cambian y el pipeline es seguro para detección.

### Mapeo de clases TACO → REBA

Las 60 categorías COCO de TACO se mapean a las 3 clases en la tabla
`TACO_TO_REBA` de `download_taco.py` (botellas/etiquetas plásticas, bolsas,
film, utensilios → `plastico`; espuma/styrofoam → `unicel`; latas/foil/
tapas metálicas → `metal`). Cuerdas/calzado (`tela`) y residuos de comida
(`organico`) quedaron **descartadas** al eliminar esas clases — ahora son
fondo. Lo mismo para vidrio, papel, cartón, cigarros y basura sin etiquetar:
quedan como fondo, lo que enseña al modelo a no detectarlas.

### Mapeo de clases EcoSort → REBA

EcoSort trae 10 clases propias; `download_ecosort.py` aplica la tabla
`ECOSORT_TO_REBA`: `plastic_bottle`, `plastic_bag`, `plastic_food_container` y
`snack_bag` → `plastico`; `aluminum_can`, `food_can` y `battery` → `metal`.
`glass_bottle`, `cardboard_box` y `paper_sheet` se descartan (fondo). EcoSort
no contiene `unicel` — el refuerzo de esa clase viene de FloatWaste
(`D_polystyrene`) y del sintético acuático.

Fuentes del dataset EcoSort (según su tarjeta en HF): Open Images V7 (CC BY),
TACO (MIT/CC), `dmedhi/garbage-image-classification-detection` (CC BY 4.0),
Drinking Waste Classification de Kaggle (CC0) y tres datasets de Roboflow
Universe (CC BY 4.0). Revisar licencias antes de redistribuir.

### Mapeo de clases FloatWaste → REBA

FloatWaste (`OliviaWuYJ/sc-floatwaste`, CC BY 4.0) aporta el dominio más
cercano al despliegue: residuos **flotando en la superficie de ríos**
(Hobart, Sydney, Citarum). Sus 25 categorías COCO se remapean en la tabla
`FLOATWASTE_TO_REBA` de `download_floatwaste.py`:

- → `plastico`: Packaging, Other_packaging, S_*, PS_string, P_foodcontainer,
  PH_cup, H_*, DH_lid, F_facemask (la mayoría de las 25 categorías).
- → `unicel`: `D_polystyrene` (2,308 cajas en el train subset).
- → `metal`: `M_beveragecan`, `M_foodcan/tin`, `M_aerosol`.
- → fondo: `P_cardboard`, `R_ball/balloon`, `G_beveragebottle`, `Other`,
  `T_wood/timber` (madera — antes `organico`, ahora fondo).

El split `val` de FloatWaste (796 imgs) se reserva completo como
`REBA_real_val` — el set de validación real acuático. `clean_real_val.py`
ya eliminó 345 imágenes con near-duplicados en train (frames de video
del mismo tramo); quedan **415 imágenes limpias** (~873 plastico,
~171 unicel, ~57 metal tras el filtrado 3-clases — antes había 20
`organico` que ahora son fondo).

### Copy-paste (`REBA_copypaste`)

`generate_copypaste_dataset.py` pega recortes reales (de todos los
`REBA_*`) sobre fondos de agua limpia de FloatWaste y sintéticos, con
escala/rotación/jitter/submersión parcial. 600 imágenes de datos
acuáticos sintéticos realistas. (Su propósito original era reforzar
`tela`/`organico`; tras la migración a 3 clases conserva ~600
instancias de `plastico`/`unicel`/`metal`.)

### Flujo completo outdoor

```bash
python ai_pipeline/download_taco.py --limit 0 \
    --prioritize-classes unicel,metal             # TACO completo, prioriza escasas
python ai_pipeline/download_ecosort.py --limit 800  # dataset.tar de HF → YOLO-REBA
python ai_pipeline/download_floatwaste.py --limit 1500 --negatives 120
    # FloatWaste → REBA_floatwaste (train) + REBA_real_val (val limpio)
python ai_pipeline/clean_real_val.py            # quita fugas train↔val
python ai_pipeline/dedup_audit.py               # auditoría global de duplicados
python ai_pipeline/generate_synthetic_dataset.py \   # opcional: boost clases raras
    --output datasets/REBA_synthetic_boost2 \
    --n-train 240 --n-val 0 --n-test 0 \
    --class-weights 1,2,1 --seed 1234            # orden: plastico,unicel,metal
python ai_pipeline/generate_copypaste_dataset.py --images 600   # datos acuáticos
python ai_pipeline/build_outdoor_eval.py --preview   # conjunto difícil
python ai_pipeline/train_outdoor.py --epochs 50      # re-entrenar
python ai_pipeline/evaluate_outdoor.py               # reporte comparativo
python ai_pipeline/tune_confidence.py                # umbral óptimo → .env
```

Si existen `datasets/REBA_synthetic_boost*/` (carpetas `images/train` no vacías),
`train_outdoor.py` las suma automáticamente al train combinado. Igual con
`datasets/REBA_taco/`, `REBA_ecosort/`, `REBA_floatwaste/` y
`REBA_copypaste/`: basta con que la carpeta exista con formato YOLO y las
3 clases REBA — ver `OPTIONAL_TRAIN_SOURCES` en `train_outdoor.py` para
registrar una fuente nueva.

`ai_pipeline/filter_classes.py` migró todo el dataset de 5 a 3 clases
(eliminando `tela`/`organico`; backup en `datasets/_backup_labels_5class/`).

`train_outdoor.py` archiva los pesos previos en `models/archive/` antes de
sobrescribir, así `evaluate_outdoor.py` siempre tiene baseline.

---

## 2c. Monitoreo del entrenamiento — `train_monitor.py`

Monitor GUI (tkinter, **solo lectura** — nunca toca el proceso de
entrenamiento) que funciona para cualquier run de Ultralytics. Fuentes:
`results.csv`, `args.yaml` y `val_snapshot.json` del run, más telemetría
del SO vía `psutil` y PowerShell CIM.

```bash
python ai_pipeline/train_monitor.py --run REBA_outdoor_v5   # GUI
python ai_pipeline/train_monitor.py --run REBA_outdoor_v5 --once   # snapshot consola
monitor_entrenamiento.bat                                  # launcher GUI
```

| Qué muestra | Fuente |
|-------------|--------|
| Época actual, progreso %, ETA por época / a early-stop / total | `results.csv` (`time`, mAP50-95) + `args.yaml` (`epochs`, `patience`) + `create_time` del proceso |
| iter/s-it y ETA en tiempo real | tqdm del stdout (si el log es accesible; `--log` para ruta explícita) |
| Ficha de hiperes: `imgsz · batch · optimizer · workers · patience · Mosaic ON/OFF · device` | `args.yaml` (Mosaic se apaga en `epochs - close_mosaic`) |
| Historial por época: `tBOX tCLS tDFL P R mAP50 mAP50-95 vBOX vCLS vDFL LR` | `results.csv` |
| Alerta de posible overfitting (≥3 épocas `val/box↑` + `train/box↓`) | derivado de `results.csv` |
| Mejor modelo destacado (`Época X · mAP50-95 · F1 · P · R`) + `Sin mejora: X / patience` | pico de `metrics/mAP50-95(B)` = el `best.pt` real |
| Tabla por clase: `Img Inst P R F1 mAP50 mAP50-95` + `Speed` (ms/img → FPS) | `val_snapshot.py` sobre `last.pt` (una vez por época, ~3 min CPU en segundo plano) |
| Trainer: PID, CPU%, RSS, IO MB/s, hilos, nice, uptime | `psutil.Process` |
| Hardware (hilo daemon 2 s): freq GHz + %clock, AC/batería, RAM%, swap%, disco R/W MB/s, temp CPU | `psutil` + cadena CIM (`LibreHardwareMonitor` → `OpenHardwareMonitor` → `MSAcpi_ThermalZoneTemperature`); temp `N/D` si no hay sensor |

La ventana tiene scroll con rueda del mouse. Colores semánticos:
temp >80 °C / RAM-swap >85 % / clock <80 % bajo carga → ámbar-rojo.

**Dependencias:** `psutil` y `pyyaml` (`import yaml`) requeridas;
`wmi`/`pywin32` opcionales (sin ellas la temperatura usa PowerShell CIM).
El monitor tolera escrituras concurrentes de Ultralytics
(`utf-8`/`errors="replace"`, líneas parciales ignoradas) y no compite
por CPU de forma relevante (~1 val de 60 imágenes cada ~54 min).

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

