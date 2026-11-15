# Plan post-v5 — de `REBA_outdoor_v5` a producción

> Estado: **runbook** — ejecutar cuando v5 termine (early stop ~ép 35 o
> época 50, domingo 15-nov). La evaluación se hace ANTES de tocar el
> servidor desplegado.

## 0. Lo que la prueba real_water ya reveló (spot check 14-nov)

Muestra de 8 imágenes FloatWaste (`REBA_real_val`, agua real) con
`best.pt` época 15 a conf=0.20:

- **Sí ve**: 7/8 con detecciones; aciertos multi-clase exactos
  (`metal+plastico×2` correcto en una foto).
- **Pero**: confianzas 0.20–0.56 (vs ~0.9 sintético), confusión de
  material ocasional (`plastico`→`metal`/`unicel`), algún FP, y objetos
  <100 px² invisibles.
- **Crítico**: con `INFERENCE_CONFIDENCE=0.35` actual se pierden ~la
  mitad de las detecciones reales vistas a 0.20.
- **Conclusión**: el val sintético (mAP50-95 0.76) NO predice el campo.
  Toda decisión de despliegue se toma sobre `real_water` (415 imgs
  FloatWaste val, nunca entrenadas). La brecha #1 documentada es la
  falta de capturas acuáticas del ESP32 en campo.

## 1. Secuencia inmediata al terminar v5

```bash
# 1. Confirmar fin del run (monitor --once o ventana REBA-Train)
python ai_pipeline/train_monitor.py --run REBA_outdoor_v5 --once

# 2. Evaluación offline completa — NO reiniciar uvicorn todavía
python ai_pipeline/evaluate_outdoor.py
#    -> outdoor_training_report.md ; leer la tabla real_water y la
#       comparación vs pesos archivados (baseline)

# 3. Barrido de umbral SOBRE AGUA REAL (no sobre sintético)
python ai_pipeline/tune_confidence.py \
    --data datasets/REBA_real_val/data.yaml --split val \
    --confs 0.15 0.20 0.25 0.30 0.35 0.45 0.55

# 4. Evaluación por el path de producción real
python ai_pipeline/eval_upload_path.py --dataset datasets/REBA_real_val --split val
```

Notas:
- `evaluate_outdoor.py` ya incluye `real_water` como set decisivo y
  compara contra `models/archive/` — el nuevo modelo debe **empatempatar
  o superar al desplegado** en ese set.
- `tune_confidence.py` con `--data` apunta a `REBA_real_val/data.yaml`;
  el umbral recomendado será el que maximiza F1 en agua real — probablemente
  menor que 0.35 (los TP reales estaban en 0.20–0.35).
- `eval_upload_path.py` valida el pipeline real (ONNX + NMS + umbral del
  server), no solo el .pt teórico.

## 2. Criterios go/no-go (definidos ANTES de ver los números)

**Desplegar v5 si se cumple TODO:**

| Criterio | Piso |
|---|---|
| `real_water` mAP50 | ≥ 0.55 (sintético está en ~0.97; real será menor) |
| `real_water` mAP50-95 | ≥ 0.35 |
| F1 óptimo en real_water | ≥ 0.50 |
| vs baseline archivado | ≥ en mAP50-95 real_water (no empeorar lo desplegado) |
| `eval_upload_path` P/R | coherente con el offline (sin colapso por ONNX/NMS) |
| Latencia inferencia | ≤ ~200 ms/img en CPU (server + dashboard lo notan) |

**Si falla el piso pero mejora al baseline**: desplegar igualmente y
priorizar datos de campo (rama B) — el modelo nuevo es el mejor
disponible aunque insuficiente.

**Si ni siquiera empata al baseline en real_water**: NO desplegar;
evaluar v4/v2 archivados y ejecutar rama B completa.

## 3. Rama A — v5 convence → despliegue

1. `train_outdoor.py` ya exportó `models/best.onnx` al terminar
   (archivó el previo en `models/archive/`).
2. Ajustar `.env`: `INFERENCE_CONFIDENCE` = umbral de `tune_confidence`
   sobre real_water.
3. **Reiniciar uvicorn** (el server solo lee el ONNX al arrancar):
   cerrar ventana REBA-Server y relanzar `campo.bat` o el comando
   equivalente; verificar `GET /api/health` → `backend: onnx`.
4. Verificación E2E: `eval_upload_path.py` de nuevo contra el server
   reiniciado + una foto de prueba por `/upload`.
5. **Commit** de todo el trabajo pendiente (monitor v3, val_snapshot,
   docs, .bat, requirements) — quedó acordado commitear al terminar v5.
6. Registrar en `PROGRESO.md` y `reports/experimentos.md` el veredicto.

## 4. Rama B — v5 no convence → el plan de DATOS (prioridad #1)

La nube/GPU **no arregla datos malos**. El orden correcto:

1. **Capturas reales del ESP32 en campo** (la fuente que falta):
   - Desplegar el sistema en agua real aunque el modelo sea imperfecto —
     cada captura es un dato de entrenamiento.
   - Checklist de captura: distintas horas (mañana/tarde/noche),
     materiales presentes (plástico, unicel, metal), tamaños variados
     (incl. objetos pequeños lejanos), agua limpia y turbia.
   - `Dashboard → Analítica → Exportar YOLO` → ZIP con `images/` +
     `labels/` + `data.yaml` → `datasets/REBA_field/`.
   - **Meta**: ≥200–500 capturas con objetos REBA antes de v6.
   - Revisar labels exportados (`audit_labels.py`) antes de entrenar.
2. **Más fuentes públicas de agua** (investigar al ejecutar):
   FloodNet,Trash-ICRA, datasets de basura flotante adicionales —
   mismo pipeline `download_*` → `datasets/REBA_*` → registrar en
   `OPTIONAL_TRAIN_SOURCES`.
3. **v6 solo cuando haya datos nuevos**: la receta cloud
   (`plan_nube_colab_kaggle.md`) corre la MISMA receta; su valor real
   aparece con dataset más grande (más épocas útiles, sweeps, v8s).

## 5. Cuándo la nube GPU se justifica (resumen)

| Escenario v6 | ¿Nube? | Por qué |
|---|---|---|
| Mismos datos + más épocas | No | CPU local alcanza; retorno marginal |
| +capturas REBA_field (200-500) | **Sí** | dataset mayor → GPU paga (~7× speedup) |
| Sweep de hiperes / 2-3 configs | **Sí** | 3 runs × ~28 h local → ~4 h en T4 |
| YOLOv8s/v8m para comparar | **Sí** | v8n puede estar saturado en capacidad |
| Solo re-exportar ONNX | No | segundos en local |

Detalle técnico completo en `docs/plan_nube_colab_kaggle.md`.

## 6. Errores a evitar

- **No** ajustar `INFERENCE_CONFIDENCE` mirando el val sintético —
  el óptimo en agua real puede ser ~0.20-0.30.
- **No** desplegar `best.pt` sin pasar `eval_upload_path.py` — el ONNX
  + NMS + umbral del server pueden degradar vs el .pt teórico.
- **No** reentrenar v6 con los mismos datos esperando mejora mágica —
  el cuello es dominio, no épocas.
- **No** commitear `logs/` ni `data/` (ya en `.gitignore`).
