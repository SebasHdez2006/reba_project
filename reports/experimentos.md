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

- **Dataset train final: 4,579 imágenes / 18,560 instancias**
  (antes v3: 2,430 / ~7,700):

  | Fuente | imgs train | aporte clave |
  |---|---|---|
  | REBA_synthetic | 240 | dominio agua sintético base |
  | REBA_synthetic_boost | 180 | organico/tela |
  | REBA_synthetic_boost2 | 240 | organico/tela/unicel |
  | REBA_taco | 1,052 | basura real terrestre |
  | REBA_ecosort | 718 | plástico/metal real |
  | **REBA_floatwaste** | **1,549** | **superficie de río real**: 7,916 plastico, 2,308 unicel, 1,081 metal, 513 organico (madera) + 49 fondos de agua limpia |
  | **REBA_copypaste** | **600** | **594 tela + 585 organico** (recortes reales sobre agua) |

- **Instancias train por clase:** plastico 10,352 · tela 1,025 ·
  organico 1,678 · unicel 2,974 · metal 2,531.
  `tela` 431→1,025 (+138%), `organico` 580→1,678 (+189%),
  `unicel` 373→2,974 (+697%), `metal` 1,301→2,531 (+95%).
  `plastico` crece a 10,352 (56% del total) — desbalance a vigilar en
  las métricas por clase del set real.

- **Set de validación real `real_water`:** `REBA_real_val` = 415 imágenes
  limpias de FloatWaste val (se eliminaron 345 con near-duplicados en
  train — `dedup_audit.py` + `clean_real_val.py`). Composición:
  873 plastico, 171 unicel, 57 metal, 20 organico, **0 tela**
  (no existe dataset público acuático con tela — se evalúa en
  `outdoor`/`clean` y pendientes capturas ESP32-CAM).

- **Auditoría de fugas:** `dedup_audit.py` (dHash, Hamming≤6) encontró
  18,206 pares near-dup globales: 4,572 eran FloatWaste train↔val
  (frames de video) — limpiados del val; 212 pares internos en
  `REBA_synthetic` train↔val (limitación del generador procedural,
  documentada); el resto <120 pares coincidentes débiles.

- **Run:** `runs/detect/REBA_outdoor_v4` — 50 epochs, patience 20,
  batch 8, imgsz 640, CPU. ~573 iter/época → ~60-70 min/época estimado.
- **Métricas:** pendiente.
