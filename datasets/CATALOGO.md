# Catálogo de datasets externos — REBA

Auditoría de fuentes de datos de residuos para el modelo de visión.
Fecha: 2026-11-14. Cada entrada fue verificada en su fuente oficial
(HF / Zenodo / Mendeley / Roboflow / GitHub / paper) — nada inventado.

**Regla del proyecto:** todo dataset externo es *bootstrap* — el sistema
final es 100% offline. Las licencias se respetan; lo dudoso se marca.

## Veredictos

| Dataset | Veredicto | Motivo |
|---|---|---|
| **FloatWaste** (HF) | **USAR** | Dominio acuático real (ríos AU/ID), COCO bboxes, CC BY 4.0, 25 clases → mapea a 4 de 5 REBA (sin tela) |
| **AquaPlasticWaste 2026** (Mendeley) | **USAR PARCIAL** | Drone aéreo (no superficie), pero clases `foam`→unicel, `shoe`→tela, `bottle/cap/polythene/wrapper`→plastico. CC BY 4.0 |
| **Garbage Classification 3** (Roboflow) | **USAR PARCIAL — requiere humano** | 10.4k imgs detección, clases `cloth`→tela y `biodegradable`→organico — las 2 clases débiles. Terrestre, dominio distinto. CC BY 4.0. Descarga BLOQUEADA: universe.roboflow.com devuelve 403 sin API key → descarga manual pendiente |
| **EcoSort** (HF) | **YA INTEGRADO** | `REBA_ecosort` — 718 tr / 82 val, plastico/metal |
| **TACO completo** | **YA INTEGRADO** | `REBA_taco` — 1293 imgs, todas las clases mapeables agotadas |
| **RiSID** (Zenodo) | DESCARTAR | 9.2 GB, segmentación (no bboxes), 7 clases todas plásticas — FloatWaste cubre el mismo nicho mejor |
| **IWHR Floater** (Figshare) | DESCARTAR | Una sola clase "floater" — no sirve para 5 clases REBA |
| **FloW / FloW-Img** (GitHub ORCAUBOAT) | PENDIENTE HUMANO | Requiere solicitud de acceso en el portal ORCA-Uboat |
| **TUD-GV** (Zenodo) | DESCARTAR | Clasificación por conteo de objetos (0/1-2/3-5/6-10), sin cajas |
| **Garbage Dataset (GD)** | DESCARTAR | Clasificación a nivel imagen (12k), sin localización |
| **Garbage Dataset V2** (Kaggle) | DESCARTAR | Clasificación (19.7k), sin cajas; además Kaggle requiere cuenta |
| **DeepPlastic** (Zenodo) | DESCARTAR | Solo plástico submarino (capa superficial océano) — sin tela/organico, dominio distinto |
| **Seaclear Marine Debris** | DESCARTAR | Submarino ROV (Croacia/Francia) — el brief excluye datasets submarinos |
| **Marine Debris FLS** | DESCARTAR | Sonar de tanques de agua, no cámaras ópticas |
| **Trash-ICRA19 / TrashCan** | DESCARTAR | Submarino (JAMSTEC/J-EDI), mismo motivo |
| **UAVVaste / AerialWaste / DroneWaste** | DESCARTAR | Vista aérea/satélite — perspectiva incompatible con cámara a 0-23 cm; AerialWaste es CC BY-NC-ND (no reutilizable) |
| **WaDaBa / TrashNet / MJU-Waste** | DESCARTAR | Objeto único sobre fondo limpio — dominio opuesto al flotante en agua |
| **Open Litter Map / pLitterStreet / RoLID-11K / SWiM3 / Litter / Domestic Trash** | DESCARTAR | Basura en calles — sin agua; TACO ya cubre "basura en el wild" terrestre |
| **BePLi / BeachLitter** | DESCARTAR | Segmentación de playas — arena, no agua libre |
| **RealWaste / ZeroWaste / SpectralWaste / CODD / WaRP** | DESCARTAR | Plantas de reciclaje/rellenos — dominio industrial |
| **Medical-Waste-4.0 / ETID-v2 / e-waste** | DESCARTAR | Residuos médicos/electrónicos — fuera del dominio acuático |
| **Cigarette butts** | DESCARTAR | Una sola clase (colillas) |
| **Saigón water hyacinth** (272 imgs) | PENDIENTE VERIFICACIÓN | Vegetación flotante = organico potencial; hay que confirmar formato/licencia |
| **Latas flotantes arXiv 2605.16774** | PENDIENTE VERIFICACIÓN | Solo metal; verificar si el paper/dataset es accesible |
| **Ku-YOLO-DataSet** (HF) | DESCARTAR | Es TACO re-etiquetado a otras 5 clases — redundante |
| **Waste-Identifier** (HF) | DESCARTAR | Clasificación (Recycle/Trash/Compost), no detección |
| **detect-waste** (HF, Yorai) | DESCARTAR | COCO de 1-2 clases, pequeño |

## Detalle de los datasets USAR

### FloatWaste — `OliviaWuYJ/sc-floatwaste` (Hugging Face)

- **URL:** https://huggingface.co/datasets/OliviaWuYJ/sc-floatwaste
- **Zenodo:** https://doi.org/10.5281/zenodo.21934286
- **Imágenes:** 7,906 (train 7,110 / val 796) — grabações de vídeo de superficie de río (Hobart, Sydney, Citarum Indonesia)
- **Anotación:** COCO bbox — `annotations/instances_train.json` + `instances_val.json` (27,583 cajas, 25 clases)
- **Licencia:** CC BY 4.0 (LICENSE en el repo)
- **Clases → REBA:** Packaging/Other_packaging/S_*/H_*/PH_*/DH_*/P_foodcontainer/F_facemask → `plastico`; M_beveragecan/M_foodcan/M_aerosol → `metal`; D_polystyrene → `unicel`; T_wood/timber → `organico`; P_cardboard/G_*/R_*/Other → fondo
- **Notas:** 54 imágenes sin anotación = agua limpia (negativos perfectos). `tela` NO existe en este dataset. Larga cola: Packaging+Other_packaging = 42% de anotaciones.
- **Uso:** train subset (~1.5k priorizando clases REBA raras) + **val completo (796) = set de validación real** `REBA_real_val` — nunca entra a entrenamiento.

### AquaPlasticWaste 2026 — Mendeley `zshjxgfmc3/1`

- **URL:** https://data.mendeley.com/datasets/zshjxgfmc3/1
- **Imágenes:** 5,000 drone UAV sobre Dal Lake (Kashmir), 4,692 con plástico
- **Anotación:** polígonos instance segmentation, exportado también en **YOLO** y COCO — 30,166 instancias, 7 clases: background, bottle, cap, foam, polythene, shoe, wrapper
- **Licencia:** CC BY 4.0
- **Clases → REBA:** bottle/cap/polythene/wrapper → `plastico`; foam → `unicel`; shoe → `tela`
- **Notas:** vista aérea (nadir drone) ≠ superficie a 23 cm, pero aporta `tela` (shoe, 1.1% instancias — poco) y `unicel` (foam) reales. Objetos pequeños (mediana 1,448 px).
- **Uso:** parcial — subconjunto balanceado, marcar origen `aqua` para análisis de dominio.

### Garbage Classification 3 — Roboflow `material-identification/garbage-classification-3`

- **URL:** https://universe.roboflow.com/material-identification/garbage-classification-3/dataset/2
- **Imágenes:** 10,464 (v2) — basura doméstica terrestre
- **Anotación:** YOLO txt + COCO (vía mirror HF `keremberke/garbage-object-detection`)
- **Clases:** biodegradable, cardboard, glass, metal, paper, plastic, cloth (7)
- **Licencia:** CC BY 4.0
- **Clases → REBA:** cloth → `tela`; biodegradable → `organico`; metal → `metal`; plastic → `plastico`; paper/cardboard/glass → fondo
- **Notas:** las ÚNICAS clases públicas con `tela` y `organico` reales. Dominio terrestre doméstico — riesgo de confundir al modelo (ropa en tendedero ≠ tela flotante). Usar con peso moderado y evaluar en set real.
- **Uso:** parcial — subconjunto priorizando cloth/biodegradable.

## Pendientes que requieren acción humana

- **Garbage Classification 3 (Roboflow):** descargar manualmente desde
  https://universe.roboflow.com/material-identification/garbage-classification-3/dataset/2
  (formato YOLOv8 zip, ~10k imgs, CC BY 4.0). universe.roboflow.com
  bloquea scraping (403); se necesita API key gratuita de Roboflow o
  descarga manual del zip. Clases útiles: `cloth`→tela,
  `biodegradable`→organico.
- **FloW (FloW-Img/FloW-RI):** solicitud en https://github.com/ORCAUBOAT/FloW-Dataset — 2,000 imgs flotantes en aguas interiores, el dataset más afín al dominio.
- **Saigón hyacinth:** localizar paper/repo oficial del dataset de jacinto de agua + plástico (272 imgs, 9,352 ann) y verificar licencia.
- **Latas flotantes (arXiv 2605.16774):** verificar si hay dataset público adjunto.
- **Capturas ESP32-CAM reales:** la fuente definitiva para `tela`/`organico`/`unicel` en dominio exacto — protocolo en sección final del reporte.
