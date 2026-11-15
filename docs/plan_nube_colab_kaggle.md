# Plan post-v5 (fase 2): entrenar en Colab / Kaggle (GPU)

> Estado: **diseño aprobado, implementar DESPUÉS de ejecutar
> `docs/plan_post_v5.md` completo** (evaluación real_water + decisión
> go/no-go + datos de campo). La nube es la fase 2: solo procede si la
> evaluación muestra que el cuello es capacidad de cómputo/dataset, no
> la receta — ver "Cuándo la nube se justifica" abajo.
>
> Hallazgo clave (spot check real_water 14-nov): el modelo ve materiales
> en agua real pero con confianza baja y confusión ocasional — la brecha
> es de DATOS/dominio, no de épocas. Más GPU sobre los mismos datos no
> la cierra.

## Principio rector: la nube es SUSTRATO, no experimento

**v6 = la misma receta de `train_outdoor.py` corriendo en GPU.** La nube
no cambia alcance, arquitectura, clases, datos ni umbrales. Cualquier
cambio de receta (nuevo dataset, oversampling EXP-4, otra arquitectura)
se decide aparte en `reports/experimentos.md` y aplica igual en local o
en nube. Si una idea no puede correrse con `train_outdoor.py` local,
tampoco va en `train_cloud.py`.

## Alcance — NO negociable (guardarraíles)

- **Modelo**: YOLOv8n, 3 clases (`plastico`, `unicel`, `metal`), `imgsz=640`.
- **Export**: ONNX `opset=12, simplify=True` → `models/best.onnx`
  (server/inference.py lo consume sin cambios).
- **Receta**: mismos hiperparámetros que el run local — `epochs`,
  `patience`, `cos_lr`, `mosaic`, `cutmix`, `mixup`, `erasing`, `degrees`,
  `translate`, `scale`, `fliplr`, `hsv_*`, y sobre todo
  **`augmentations=outdoor_train_transforms()`** (Albumentations: lluvia,
  glare, haze, CoarseDropout). Este parámetro es la mitad del valor del
  entrenamiento outdoor — sin él el modelo nube NO es comparable a v5.
- **Datos**: exactamente las fuentes de `OPTIONAL_TRAIN_SOURCES` +
  `REBA_synthetic` que `build_dataset_yaml()` combine en ese momento.
  `pack_dataset.py` debe DERIVAR su lista de archivos de esa misma
  función — nunca una lista hardcodeada que pueda derivar.
- **Naming/flows**: run `runs/detect/REBA_outdoor_v6` (o el que siga),
  `archive_previous_model()` igual, `best.pt`/`last.pt`/`results.csv`
  idénticos → `evaluate_outdoor.py`, `tune_confidence.py`,
  `eval_upload_path.py` funcionan sin tocarlos.
- **NO tocar**: `server/`, `esp32_firmware/`, `static/`, `CLASS_NAMES`,
  `INFERENCE_CONFIDENCE` (eso lo decide tune_confidence post-eval),
  `requirements.txt` (no nuevas deps — ultralytics/psutil/pyyaml ya están).

## Estructura — todo aditivo, nada se mueve

```
REBA/
├── ai_pipeline/
│   ├── train_outdoor.py      # existente — se refactoriza mínimo (ver abajo)
│   ├── train_cloud.py        # NUEVO — orquestador de entorno
│   ├── pack_dataset.py       # NUEVO — zip + validación + metadata
│   ├── outdoor_transforms.py # existente — se reutiliza tal cual
│   └── ...                   # el resto intacto
├── notebooks/                # NUEVO dir — train_colab.ipynb, train_kaggle.ipynb
├── datasets/                 # intacto (los scripts nube lo reciben empaquetado)
└── docs/plan_nube_colab_kaggle.md
```

## Diseño técnico

### `train_cloud.py` (orquestador, ~150 líneas)

1. **Detecta entorno**: `import google.colab` → Colab; `Path("/kaggle/working").exists()` → Kaggle; si no → local (dev/dry-run).
2. **Localiza datasets**: `/kaggle/input/<slug>/datasets/...` (Kaggle) o `/content/drive/MyDrive/reba/datasets/...` (Colab) o `datasets/` local.
3. **Genera `data_cloud.yaml`** reutilizando `write_combined_data_yaml()`
   de `train_outdoor.py` (refactor: extraerla para aceptar `root`
   parametrizable — un cambio de firma interno, comportamiento idéntico).
4. **Lanza training con LA MISMA receta**: importar
   `outdoor_train_transforms()` y el bloque de kwargs de
   `train_outdoor.py`. Para no duplicar ~30 kwargs, refactor mínimo:
   extraer `run_train(args) -> Path` en `train_outdoor.py` y que ambos
   scripts lo llamen. `train_outdoor.py` CLI no cambia.
5. **GPU**: `torch.cuda.device_count()` → `device=0` o `device=[0,1]`
   (Kaggle T4×2 DDP; P100 = 1 sola).
6. **Resume**: `--resume` acepta ruta a `last.pt` (local subido a Drive/
   Kaggle dataset). Ojo: el checkpoint guarda `device: cpu` en args →
   pasar `device` explícito y verificar que gane sobre el restaurado.
7. **Persistencia anti-desconexión**: hilo daemon que copia
   `weights/last.pt`, `weights/best.pt`, `results.csv` al detectar
   mtime nuevo → `backups/` (Drive en Colab, `/kaggle/working/backups`
   en Kaggle). No depender del API de callbacks de ultralytics.
8. **Post-train**: mismo flujo de `train_outdoor.py` — export ONNX
   opset 12 simplify → `archive_previous_model()` → `models/best.{onnx,pt}`.
   En la nube escribir a una carpeta de salida que luego se descarga;
   el archivado en `models/` se hace local al importar el best.pt.

### `pack_dataset.py`

- Deriva las fuentes de `OPTIONAL_TRAIN_SOURCES` + `REBA_synthetic`
  (import de `train_outdoor`, no duplicar).
- Valida antes de comprimir: reutiliza `audit_labels.py` /
  `audit_pixels.py` (integración por subprocess o import).
- Zip con estructura `datasets/REBA_*/{images,labels}/{train,val,test}`
  preservada + `pack_manifest.json` (fuentes, conteos, sha de data.yaml
  generado) + `dataset-metadata.json` para `kaggle datasets create`.
- `--validate-only` para chequeo sin comprimir.

### Hiperparámetros nube (única diferencia vs local: hardware)

| Parámetro | Valor | Nota |
|---|---|---|
| `amp` | True | igual que local |
| `cache` | `'disk'` Colab / `'ram'` Kaggle | dataset ~1-1.5 GB zip; decodificado ~5-10 GB RAM |
| `batch` | 48-64 | T4 12 GB v8n@640 cabe holgado; `batch=-1` solo si se calibra |
| `workers` | 4-8 | `cpu_count()` miente en Colab |
| `imgsz` | 640 | igual que local |
| resto | del checkpoint/args | resume los restaura; fresh usa defaults de train_outdoor |

## Cuándo la nube se justifica (gate de entrada)

La GPU paga solo si el plan post-v5 (§5 de `plan_post_v5.md`) muestra
escenario "Sí":

- **Dataset creció**: `datasets/REBA_field/` con capturas reales del
  ESP32 (meta ≥200-500) u otras fuentes acuáticas nuevas → más datos =
  más épocas útiles = GPU paga (~7× speedup: ~55 → ~8 min/época).
- **Sweep de hiperes**: 2-3 configs de `train_outdoor.py` (p.ej. EXP-4
  oversampling, close_mosaic distinto) → ~28 h local c/u vs ~4 h en T4.
- **Arquitectura mayor**: comparar YOLOv8s/v8m si v8n quedó corto en
  capacidad (señal: train mAP alto pero real_water plano pese a datos).

Si v6 = mismos datos + más épocas → **no usar nube**: CPU local basta y
la ganancia es marginal.

## Realidades de plataforma

- **Colab**: datasets y checkpoints por Google Drive (`drive.mount`,
  auth manual 1 vez/sesión). Sesión muere ~12-24 h → backup por época
  OBLIGATORIO. GPU free puede no estar disponible → fallback CPU lento,
  abortar y reintentar otro horario.
- **Kaggle**: internet OFF por defecto → verificar `import ultralytics`
  antes de pip (suele venir en la imagen). GPU quota ~30 h/semana.
  `/kaggle/working` = output descargable; resume persistente = subir
  `last.pt` como dataset privado o usar Drive.
- **`kaggle datasets create`**: requiere `~/.kaggle/kaggle.json`.
- **Privacidad**: datasets son públicos/de investigación — subir a
  Drive/Kaggle privado es aceptable; no subir `data/REBA.db` ni `.env`.

## Secuencia de ejecución (cuando plan_post_v5 autorice v6)

1. Confirmar en `plan_post_v5.md` §5 que el escenario justifica GPU y
   definir la receta v6 en `reports/experimentos.md` (datos nuevos,
   oversampling, arquitectura — la nube no cambia esto).
2. `python ai_pipeline/pack_dataset.py` → zip + metadata.
3. Subir a Kaggle (`kaggle datasets create`) o Drive.
4. Abrir notebook → celdas install/auth → `train_cloud.py`.
5. Al terminar: descargar `best.pt` → archivar/exportar ONNX **local**
   con el flujo ya existente → `evaluate_outdoor.py` →
   `tune_confidence.py` → ajustar `.env` si cambia el umbral.

## Criterio de aceptación (Definition of Done)

- `models/best.onnx` resultante pasa `evaluate_outdoor.py` (real_water)
  igual que un entrenado local — mismas métricas dentro de ruido.
- `server/` y firmware NO requirieron ningún cambio.
- `git status` muestra solo archivos nuevos en `ai_pipeline/`,
  `notebooks/`, `docs/` + refactor mínimo documentado en
  `train_outdoor.py`.

## Verificación previa (dry-run, sin GPU)

- `train_cloud.py --dry-run` en local: detecta `entorno=local`,
  genera `data_cloud.yaml` con rutas del repo, imprime kwargs y
  device, NO entrena.
- `pack_dataset.py --validate-only`: chequea integridad del set.
