# Registro de experimentos — mejora del modelo de visión REBA

Rama: `mejora-datos-vision`. Cada experimento = un cambio controlado con
sus métricas. Las métricas "decisivas" se miden en `real_water`
(FloatWaste val — datos reales de río, nunca entrenados) y `taco_real`.

## Línea base (modelo outdoor v2, `models/best.pt` actual)

Evaluado el 2026-11-14 sobre los conjuntos de `evaluate_outdoor.py`.
Detalle completo: `reports/baseline/baseline_eval.md`.

| Conjunto | mAP50-95 global | Nota |
|---|---|---|
| clean | 0.681 | regresión vs sintético puro (0.802) — costo del dominio amplio |
| outdoor | 0.591 | mejor conjunto proxy de intemperie |
| taco_real | 0.083 | dominio terrestre real |

mAP50-95 por clase en `outdoor` (referencia de la misión):
`plastico 0.628 · tela 0.620 · organico 0.148 · unicel 0.765 · metal 0.793`

> Nota: los valores absolutos difieren ligeramente de los citados en el
> brief original (0.095 organico) porque la evaluación usa el pipeline
> actual; la tendencia es la misma — `organico` es la clase crítica.

## EXP-1 — v3: TACO completo + boost2 + EcoSort (sin FloatWaste)

- **Cambio:** dataset train 1022 → 2430 imágenes (TACO 750→1293,
  `REBA_synthetic_boost2` +240, `REBA_ecosort` +718).
- **Run:** `runs/detect/REBA_outdoor_v3` — 50 epochs, patience 20, CPU.
- **Estado:** **CORTADO en época ~2** (~36 min/época en CPU) al completarse
  el dataset v4. `weights/best.pt` y `last.pt` de época 1-2 preservados en
  el run dir por si se quiere evaluar su trayectoria.
- **Métrica época 1 (val sintético):** mAP50-95 = 0.517, P=0.740, R=0.785.
- **Razón del corte:** v4 es superconjunto estricto que ataca los cuellos
  reales (`tela`/`organico` + dominio agua); mantener v3 habría duplicado
  ~15 h de CPU sin información decisiva nueva. Queda como punto de datos
  en `results.csv`.

## EXP-2 — v4: + FloatWaste (agua real) + copy-paste + negativos

> **PIVOT 2026-11-14 (tarde):** antes de que v4 completara la época 1 se
> decidió migrar el modelo de **5 → 3 clases** (`plastico`, `unicel`,
> `metal`) — `tela` y `organico` se eliminaron por falta de datos reales
> que las cubrieran (0 instancias `tela` en todo lo público acuático;
> `organico` mAP50-95 ≈0.10–0.15). v4 (5 clases) se cortó sin métricas;
> el experimento vigente es **EXP-3 = v5 3-clases** con el mismo dataset
> filtrado por `ai_pipeline/filter_classes.py` (backup de etiquetas
> originales en `datasets/_backup_labels_5class/`).

- **Dataset train final: 4,579 imágenes** (mismas fuentes; las cajas
  `tela`/`organico` quedaron como fondo — 2,972 instancias eliminadas,
  quedan ~15,588). Antes del filtrado eran 18,560 instancias (v3: 2,430
  imgs / ~7,700):

  | Fuente | imgs train | aporte clave |
  |---|---|---|
  | REBA_synthetic | 240 | dominio agua sintético base |
  | REBA_synthetic_boost | 180 | organico/tela |
  | REBA_synthetic_boost2 | 240 | organico/tela/unicel |
  | REBA_taco | 1,052 | basura real terrestre |
  | REBA_ecosort | 718 | plástico/metal real |
  | **REBA_floatwaste** | **1,549** | **superficie de río real**: 7,916 plastico, 2,308 unicel, 1,081 metal, 513 organico (madera) + 49 fondos de agua limpia |
  | **REBA_copypaste** | **600** | **594 tela + 585 organico** (recortes reales sobre agua) |

- **Instancias train por clase (pre-filtrado 5 clases):** plastico 10,352 ·
  tela 1,025 · organico 1,678 · unicel 2,974 · metal 2,531.
  `plastico` ~66% del total tras el filtrado — desbalance a vigilar.

- **Set de validación real `real_water`:** `REBA_real_val` = 415 imágenes
  limpias de FloatWaste val (se eliminaron 345 con near-duplicados en
  train — `dedup_audit.py` + `clean_real_val.py`). Composición tras
  filtrado 3-clases: ~873 plastico, ~171 unicel, ~57 metal
  (las 20 `organico` originales ahora son fondo).

- **Auditoría de fugas:** `dedup_audit.py` (dHash, Hamming≤6) encontró
  18,206 pares near-dup globales: 4,572 eran FloatWaste train↔val
  (frames de video) — limpiados del val; 212 pares internos en
  `REBA_synthetic` train↔val (limitación del generador procedural,
  documentada); el resto <120 pares coincidentes débiles.

- **Run:** `runs/detect/REBA_outdoor_v4` — **CORTADO** al migrar a 3
  clases (no llegó a época 1 completa; sin pesos útiles).

## EXP-3 — v5: modelo de 3 clases sobre dataset filtrado

- **Cambio:** ontología 5→3 clases en TODO el proyecto (server `.env`,
  `CLASS_NAMES`, mappings de datasets, `data.yaml`, dashboard, docs,
  diagramas). Etiquetas migradas por `filter_classes.py`
  (0→plastico, 3→unicel, 4→metal; 1,2 eliminadas).
- **Dataset train:** mismas 4,579 imágenes, ~15,588 instancias restantes
  (plastico ~10.3k, unicel ~3.0k, metal ~2.5k — proporciones similares,
  `plastico` sigue dominante ~66%).
- **Set de validación `real_water`:** 415 imgs, ~1,101 instancias
  (plastico ~873, unicel ~171, metal ~57).
- **Run:** `runs/detect/REBA_outdoor_v5` — **EN CURSO** (lanzado tras
  la migración; ~573 iter/época, estimado 60–70 min/época en CPU).
- **Métricas:** pendiente — comparación decisiva vs baseline v2 en
  `real_water` + `outdoor` + `taco_real`.

## EXP-4 — candidato: oversampling de clases minoritarias (preparado)

- **Motivación:** en v5 `plastico` ≈ 66% de las instancias
  (~10.3k vs ~3.0k unicel / ~2.5k metal). Si las métricas de v5 en
  `real_water` muestran que `unicel`/`metal` sufren por desbalance
  (baja precision/recall vs baseline), este experimento está listo.
- **Herramienta:** `ai_pipeline/balance_oversample.py` — crea
  `datasets/REBA_oversample/` con hardlinks NTFS (sin duplicar disco)
  de imágenes que contienen las clases objetivo, repetidas N veces.
  Uso: `--classes unicel,metal --factor 2`.
- **Activación:** solo si v5 lo justifica con métricas (regla FASE 2.5).
  Requiere registrar `("REBA_oversample", "oversample")` en
  `OPTIONAL_TRAIN_SOURCES` de `train_outdoor.py` y reentrenar (v6).
- **Costo:** ~0 disco; +tiempo de entrenamiento proporcional a las
  copias extra (~500–1,500 imgs → +5–15% iter/época).

## EXP-5 — clasificador híbrido por foto: ensemble v1 + DINOv2 (23-24 nov)

- **Motivación:** el detector YOLO sobre la foto completa 4:3 pierde contexto;
  un clasificador "1 objeto por foto" (estilo captura ESP32) decide la clase y
  el detector solo aporta la caja.
- **Dataset:** `datasets/REBA_cls_esp32/` (~14.8k imgs 320x240 degradadas
  OV2640, recortes 4:3 de FloatWaste/TACO/EcoSort) + `train_extra/`
  (+4,782 recortes FloatWaste + 754 de Wikimedia Commons para unicel).
- **v1** `cls_esp32.onnx` (yolov8n-cls@160): agua 0.804 / tierra 0.852.
- **DINO** `cls_dino.onnx`: backbone DINOv2 ViT-S/14 reg congelado
  (224x294, features 768d) + head LR L2 balanceada por CV agrupada:
  solo llega a 0.655/0.782, pero es complementario (unicel 0.70+, fondo 0.91+).
- **Ensemble (final, 3 miembros):** concat[p_v1, p_dino, p_11s] -> meta-LR
  (`cls_meta3.npz`). yolo11s-cls@224 (`cls_11s.onnx`, val top-1 0.864) aporta
  un votante CNN independiente. Medido end-to-end con el engine del server,
  conf 0.3:

  | set | acc | macro | plastico | unicel | metal | fondo_ok |
  |---|---|---|---|---|---|---|
  | test_agua (688) | **0.858** | 0.831 | 0.865 | 0.766 | 0.861 | 0.925 |
  | test_tierra (486) | **0.901** | 0.814 | 0.926 | 0.625 | 0.891 | 0.819 |

  El ensemble de 2 (v1+dino) quedó en 0.860/0.891
  (`ensemble_ens.json`); el de 3 gana tierra y fondo
  (`ensemble_ens3.json`). Objetivo ≥85 % cumplido en ambos entornos.
  `CLS_CONFIDENCE=0.3` rinde mejor que 0.5 (baja conf -> fondo penalizaba
  objetos reales).
- **Server:** `CLS_MODEL_PATH` acepta lista por comas + `CLS_META_PATH`
  (npz W/b); `/upload` verificado en vivo (plastico 0.96, unicel 0.95,
  metal 0.94, <1 s).
- **Pendiente:** validar con fotos reales ESP32 (`REBA_cls_esp32_real`,
  hoy solo 4 imgs — insuficiente para medir; hacen falta capturas de campo).

## EXP-6 — Ensemble 4 miembros + meta con PCA de features ViT-B (24-nov, día)

Tras el EXP-5 (3 miembros, 0.858/0.901) se añadió **DINOv2 ViT-B/14 reg4**
(features 1536d, ~870 ms/img CPU) y un meta-LR enriquecido: además de las
probs de los 4 miembros recibe un **PCA-64 de las features del ViT-B**
(el ONNX `cls_dinob_feat.onnx` expone doble salida probs+feat). Las probs de
las heads DINO en train se calcularon **out-of-fold** para no sesgar el meta.

| set | acc | macro | unicel | fondo_ok |
|---|---|---|---|---|
| test_agua | 0.872 | 0.832 | 0.723 | 0.899 |
| test_tierra | 0.907 | 0.861 | 0.750 | 0.784 |

Medido por el engine del server (`reports/esp32_cls/ensemble_feat.json`).
Latencia /upload ~1-1.4 s/foto por el ViT-B.

Notas:
- cls_v2 (v8n-cls reentrenado con 18.3k imgs, val top1 0.878) como 5º miembro
  no aporta (agua +0.6, tierra −2.3) → descartado.
- flip-TTA: ±0.2 pt → descartado (dobla latencia).
- Datos wm ronda 2 (Commons, +940): ligeramente negativos en el refit → el
  cuello ya no es cantidad de unicel, es el dominio proxy→real.
- Techo con proxies ~0.89 medio: CV 0.96 vs test 0.87-0.91 (domain gap).
  Para 95 % hacen falta fotos ESP32 reales en train, no más proxies.
