# Estado del trabajo — pipeline outdoor REBA

Snapshot al 2026-11-13: modelo **v2 (época 13)** ya desplegado y evaluado.

## Estado actual

- **`models/best.pt` + `models/best.onnx` = outdoor v2 (época 13)** de
  `runs/detect/REBA_outdoor_v2`. ONNX verificado end-to-end
  (~50-110 ms/frame en `server/inference.py`).
- **`outdoor_training_report.md`** actualizado con v2 vs baseline real.
- **`INFERENCE_CONFIDENCE=0.35`** en `.env` (creado) y `.env.example`
  — máximo F1=0.781 medido con `tune_confidence.py` sobre
  `REBA_outdoor_eval`.
- Repo en GitHub (`main`): commits inicial + tooling + progreso.

## Métricas v2 desplegado (vs baseline sintético limpio)

| Conjunto | ΔP | ΔR | ΔmAP50 | ΔmAP50-95 |
|---|---|---|---|---|
| clean | -0.106 | -0.046 | -0.036 | -0.151 |
| outdoor | +0.079 | +0.180 | +0.183 | +0.044 |
| taco_real | +0.099 | +0.271 | +0.120 | +0.062 |

Por clase (outdoor, mAP50-95): metal +0.187, unicel +0.070, tela +0.053,
organico +0.004, plastico -0.097.

## Datos

- `REBA_synthetic` 240 tr / 60 val / 60 test
- `REBA_synthetic_boost` 180 train (organico ×4, tela ×3)
- `REBA_taco` 750 imágenes reales → YOLO
- `REBA_outdoor_eval` 120 degradadas deterministas
- Train combinado v2: **1022 imágenes**

## Entrenamiento v2 — estado

- Run `runs/detect/REBA_outdoor_v2`: interrumpido 3 veces (épocas 11, 14);
  reanudado con `YOLO(".../weights/last.pt").train(resume=True)`.
- **14 épocas ejecutadas, mejor = época 13** (mAP50-95=0.6533 en val).
- Si se reanuda y nadie supera la 13, early stop a las ~28.

## Si el entrenamiento v2 se reanuda y termina con mejor época

1. Exportar ONNX desde `runs/detect/REBA_outdoor_v2/weights/best.pt`
   (`imgsz=640, opset=12, simplify=True`).
2. Archivar `models/best.{pt,onnx}` en `models/archive/` y copiar los nuevos.
3. `python ai_pipeline/evaluate_outdoor.py` (auto-detecta el run más nuevo).
4. `python ai_pipeline/tune_confidence.py` → ajustar `INFERENCE_CONFIDENCE`.
5. Sanity check `server/inference.py`, commit + push.

## Pendientes no iniciados

- Capturas reales ESP32-CAM para evaluación definitiva (doc §5 de
  ai_pipeline.md; la DB solo tiene 3 fotos).
- `plastico` es la única clase que retrocede en outdoor (-0.097) —
  revisar si hace falta más datos de ese tipo.
- La sesión de entrenamiento muere si se corta la conexión; reanudar con
  el comando `resume=True` de arriba.

---

## Sesión 3 — Config remota del dispositivo + firmware rediseñado (13-nov)

### Implementado y verificado

**Server (FastAPI + SQLite)** — NUEVO:
- Tabla `device_config(key, value)` en `data/REBA.db`.
- `GET /api/device/config` → texto plano `clave=valor` (lo consume el ESP32).
- `GET /api/admin/device-config` → overrides actuales + claves permitidas.
- `POST /api/admin/device-config` → body JSON `{clave: valor}`; valor vacío/null
  borra el override. Claves válidas: `wifi_ssid`, `wifi_pass`, `server_url`,
  `pump_photos`, `pump_seconds`, `dist_min_cm`, `dist_max_cm`. Valida claves
  (400 si inválida) antes de aplicar.
- Probado end-to-end con curl: aplica, lista y borra correctamente.

**Dashboard** — NUEVO panel "Dispositivo ESP32-CAM" en `static/index.html`:
- Formulario para WiFi SSID/pass, URL servidor, fotos/segundos de bomba,
  distancias mín/máx. Guarda vía POST admin; recarga estado al abrir.
- Valida en JS que WiFi SSID y pass vayan en pareja.
- Estilos en `styles.css` (`.device-form*`).

**Firmware `esp32_firmware/fotos_nc/fotos_nc.ino`** — reescrito:
- `Preferences` (NVS, namespace `reba`) persiste: ssid, pass, srv, pump_photos,
  pump_seconds, dist_min_cm, dist_max_cm. Sin credenciales hardcodeadas.
- WiFiManager 2.0.17 como portal de aprovisionamiento: AP `REBA-Setup`
  (http://192.168.4.1), campo extra "URL servidor", timeout 5 min, no bloquea.
- Se abre solo si: no hay creds guardadas, o ~20 fallos WiFi seguidos (~10 min,
  cubre cambio de red).
- Poll cada 60 s a `GET {srv}/api/device/config` → aplica cambios en caliente:
  - wifi_ssid/wifi_pass: guarda backup `ssid_bak/pass_bak` y revierte solo si
    no conecta en 90 s.
  - server_url: normaliza `/` final, backup `srv_bak` y revierte si >6 min sin
    GET exitoso.
  - pump_photos/pump_seconds/dist_*: aplican al instante (runtime vars).
- Cámara: fallback chain VGA+PSRAM → QVGA+DRAM → QQVGA+DRAM → 96x96+DRAM.
- Cola de fotos con fallback ps_malloc→malloc.

### RESUELTO — pipeline END-TO-END verificado (2026-11-13)

**Captura → WiFi → upload → inferencia → SQLite: FUNCIONA.**

- **Causa raíz de la cámara (definitiva)**: `camera_config_t` se construía
  dentro de `configCamara()` SIN inicializar — campos no asignados
  (`sccb_freq`, `dma_half`, `dma_thresh`) tomaban basura de stack → SCCB/DMA
  mal configurados → `init OK pero sin frames` / `FB-OVF` intermitente.
  Fix: `camera_config_t config = {}`. Verificado: los tests que declaraban
  el struct inline en `setup()` (test_cam4/5) siempre funcionaron; los que
  lo construían en función (test_cam6/7, fotos_nc) fallaban — mismo
  hardware, misma config de pines/resolución.
- **HTTPClient.h eliminado**: se reemplazó por `httpSimple()` con
  `WiFiClient` TCP plano (GET config + POST upload). Ahorra ~122KB flash y
  evita el problema de mbedTLS/FB-OVF. Forward-declared antes de uso.
- **`iniciarCamara` robusto**: tras `esp_camera_init` OK, reintenta
  `esp_camera_fb_get` 4× con delays ANTES de `deinit` — un `fb_get` fallido
  seguido de `deinit` deja el sensor inutilizable ("init OK sin frames").
- **Prueba real ejecutada**: `CAPTURE` por USB → JPEG 3755 B → cola →
  POST `/upload` → **HTTP 200** → ONNX inferencia (92 ms) → persistido en
  `captures` (id=8, JPEG válido 320×240, 167 niveles de gris = foto real).
  IP ESP32: `192.168.137.161` en hotspot `REBA-AP`.
- Boot completo verificado: `heap_setup=264900`, `Camara OK
  frame_size=6 loc=DRAM`, WiFi conecta, `REBA listo`, config NVS correcta
  (`wifi_ssid=REBA-AP`, `server_url=http://192.168.137.1:5000`,
  `pump_photos=3`, `dist_max_cm=23`).

### Estado del repo

- Firmware `fotos_nc`: cámara QVGA/DRAM + WiFiManager + Preferences +
  httpSimple + comandos serie (GET/SET/CAPTURE/REBOOT) + config remota.
- Server corriendo en `0.0.0.0:5000` (uvicorn), ONNX `best.onnx` época-16.
- `.env` tiene `INFERENCE_CONFIDENCE=0.35` (NO commitear).
- Hotspot PC `REBA-AP` 2.4GHz / `192.168.137.1` — OJO: Windows lo apaga por
  inactividad; en campo usar router local o hotspot persistente.

### Pendiente para el agua real

- **HC-SR04**: acercar objeto <23 cm → debe disparar flash+foto solo
  (el `CAPTURE` manual ya probó el path de imagen; falta el trigger
  ultrasónico). TRIG=GPIO14, ECHO=GPIO15 (divisor 1k+2k).
- **Bomba**: 3 fotos → `pump_events` → relé GPIO13 (bomba en NC, 12V boost).
- **Sumergibilidad**: la ESP32-CAM NO es estanca — necesita carcasa/
  funda sellada con orificio para la cámara; el HC-SR04 debe quedar
  fuera del agua apuntando a la superficie (ultrasonido no atraviesa agua).
- **Dominio del modelo**: entrenado con sintético+TACO terrestre; las
  primeras fotos reales de REBA son el dataset que falta — etiquetar y
  reentrenar cuando haya ~50-100 capturas acuáticas.
- PSRAM de la placa es defectuoso (`psramFound=1` pero `ps_malloc` falla)
  — el firmware usa DRAM para framebuffer así que funciona, pero es un
  defecto de hardware documentado.

---

## Sesión 4 — Hardening de producción firmware + servidor (13-nov, tarde)

Objetivo: "la mayor cantidad de validaciones y acción de mitigación para
cada caso inoportuno" antes del despliegue en agua real. Todo verificado
en banco con la ESP32 en `COM9` contra el server real.

### Server (`server/main.py`, `database.py`, `config.py`, `static/`)

- **Heartbeat del dispositivo**: `_touch_device()` en `GET /api/device/config`
  y `POST /upload` actualiza `device_state` (memoria); `GET /api/health` y
  `GET /api/stats` exponen `device: {online, last_seen, ip}`.
  `online` = último contacto < 150 s. Dashboard muestra pill de estado.
- **Validación JPEG en `/upload`**: magic bytes `FF D8 FF` + tamaño mínimo;
  400 si no es JPEG decodificable (ya existía decodificación OpenCV; se
  añadió rechazo temprano por magic bytes).
- **Retención de capturas**: `_retention_loop` cada hora llama
  `db.prune_captures(CAPTURE_RETENTION)` (default 2000, env
  `CAPTURE_RETENTION`); borra las más viejas y sus detecciones
  (`ON DELETE CASCADE`). Verificado: prune de 6/9 filas OK.

### Firmware `fotos_nc.ino` — cambios de robustez

- **`millis()` rollover-safe**: todos los deadlines (`wifiRevertEn`,
  `srvRevertEn`, portal, cámara, reintentos) usan
  `(long)(millis() - deadline) < 0` — seguro tras ~49 días de uptime.
- **`cargarConfig` (NVS)**: clamps en todos los numéricos
  (`clampInt`/`clampFloat`); si `distMin >= distMax` restaura defaults;
  `server_url` sin `http://` → default.
- **`aplicarClaveRemota` → `bool` + validación estricta**:
  - `esEntero()`/`esFloat()` rechazan formatos inválidos — antes
    `String::toInt()` aceptaba `"3abc"→3` y `"abc"→0` silenciosamente.
  - Rangos: pump_photos 1-50, pump_seconds 1-300, dist 0-500 cm,
    coherencia min<max; server_url solo `http://` ≤96 chars sin espacios.
  - wifi_ssid 1-32 chars, wifi_pass ≤63 (pass vacío = red abierta).
  - El comando serie `SET` ahora reporta `aplicado`/`rechazada` real
    (antes decía "aplicado" siempre — mensaje engañoso corregido).
- **WiFi**: conexión exige `WL_CONNECTED` **y** IP válida; `WiFi.mode(STA)`
  antes de `begin()` si el portal no está activo.
- **`tomarFoto`**: reintenta `fb_get` (hasta 4×, 60 ms), valida magic JPEG
  (`FF D8 FF`), contador `camFallos` → reinit de cámara tras 3 fallos
  seguidos; health check periódico en `loop()`.
- **HC-SR04**: `pulseIn` timeout → -1; distancias >450 cm o <0 → -1
  (ruido); `lastDistCm` expuesto en `STATUS`; piso de 1 cm en `enRango`.
- **Cola anti poison-pill**: cada `Foto` lleva `intentos`; se descarta en
  HTTP 4xx o ≥25 reintentos; heap guard en `colaAgregar`.
- **`httpSimple`**: loops de espera verifican `client.connected()`;
  body de respuesta cap 4 KB (anti heap-exhaustion).
- **Diagnóstico**: comando serie `STATUS` (uptime, boots, heap, cámara,
  WiFi, cola, bomba, distancia); boot counter `RTC_DATA_ATTR` sobrevive
  resets por brownout/watchdog.

### Verificación ejecutada (banco, COM9 + server :5000)

| Prueba | Resultado |
|---|---|
| Boot | `Camara OK frame_size=6 loc=DRAM`, WiFi `192.168.137.161`, `REBA listo`, `boots=1` |
| `STATUS`/`GET` serie | uptime, heap, camara=OK, wifi=conectada, cola, rango — todo correcto |
| `SET` inválidos | `999`, `-5`, `abc`, `3abc`, `ftp://x`, clave inexistente → todos `rechazada` |
| `SET` válido | `dist_max_cm=30.5` aplicado y persistido |
| CAPTURE → upload | JPEG ~3.7 KB → HTTP 200 → ONNX → `captures` id=9,10 |
| **Retry de cola** | `server_url`→puerto muerto → foto encolada `cola=1/6` → restaurar :5000 → retry exitoso → `cola=0`, capture id=10 en DB |
| **Config remota** | overrides inválidos (`pump_photos=999`, `dist_max_cm=-5`, `server_url=ftp://bad`) rechazados por el ESP32; `pump_seconds=15` válido aplicado |
| Heartbeat | `device.online=true`, `ip=192.168.137.161` en `/api/health` y `/api/stats` |

### Hallazgos / notas

- **La cola de fotos es solo RAM**: un reboot/brownout pierde pendientes.
  Aceptable (evidencia transitoria) — si se requiere persistencia, evaluar
  LittleFS en una sesión futura.
- **HC-SR04 sin lecturas en banco**: `dist_ultima=-1.0` durante >60 s de
  polling. El sensor está siendo sondeado (TRIG/ECHO conmutan) pero no
  recibe eco — probablemente no está cableado a esta placa en el banco o
  falta objeto reflector. **Pendiente prueba física**: objeto a <23 cm.
- Al abrir el puerto serie la placa se resetea (DTR/RTS) — para pruebas
  largas mantener una sola sesión abierta.
- `srv_bak` residual: si `server_url` cambia y el nuevo responde, el
  respaldo se auto-limpia en el siguiente poll exitoso (verificado en
  código, `consultarConfigRemota` líneas ~400-407).

### Pendiente para el agua real (sin cambios de fondo)

- HC-SR04: verificar cableado + objeto <23 cm → trigger automático.
- Bomba: 3 fotos → relé GPIO13 (NC, 12V boost) — verificar con multímetro.
- Sumergibilidad: carcasa estanca + HC-SR04 fuera del agua.
- Modelo: primeras fotos acuáticas reales = dataset para reentrenar.

---

## Sesión 5 — Evaluación por path de producción + módulos del dashboard (13-nov, noche)

### Evaluación de reconocimiento SIN hardware (path /upload real)

Nuevo script `ai_pipeline/eval_upload_path.py`: sube imágenes del dataset
por HTTP a `POST /upload` — el mismo contrato del ESP32 — y compara las
detecciones contra etiquetas YOLO ground-truth (IoU≥0.5, AP@0.5 VOC-101pts).

**Resultado TACO test (74 fotos reales de basura), umbral operativo 0.35:**

| Clase | P | R | F1 |
|---|---|---|---|
| plastico | 0.400 | 0.238 | 0.298 |
| tela | 0.000 | 0.000 | 0.000 |
| unicel | 0.000 | 0.000 | 0.000 |
| metal | 0.333 | 0.143 | 0.200 |
| GLOBAL | 0.394 | 0.198 | 0.264 |

- mAP@0.5 operativo = 0.050 (el server solo devuelve dets ≥0.35).
- Inferencia media 97.8 ms (p95 121 ms) en CPU.
- **Lectura**: conservador en escenas terrestres reales — pierde ~76% de
  objetos. El dominio real (basura flotando, vista superior, QVGA) es más
  simple; estas fotos son un piso pesimista. Las capturas acuáticas
  decidirán. Reporte: `ai_pipeline/eval_upload_report.json`.
- Las 77 imágenes de eval se purgaron de la DB tras el test (quedaron solo
  las 4 capturas reales del ESP32).

### Nuevos módulos del dashboard (sin autenticación — LAN local)

**Server:**
- `POST /api/pump/run {seconds}` → encola `pump_cmd=<unix_ts>:<s>` en
  `device_config` (válidas 1-300 s) + `pump_event` "manual".
- `GET /api/device/config` → sirve `pump_cmd` solo si <10 min (TTL);
  expirado se borra (evita bomba fantasma tras offline largo).
- `POST /api/device/ack {cmd_id}` → el ESP32 confirma ejecución; borra el
  override.
- `GET /api/pump/events` → historial + `pending_cmd`.
- `GET /api/stats/timeseries?days=N` → detecciones/día por clase +
  capturas + pump_events.
- `GET /api/export/yolo?min_conf=&limit=` → ZIP con `images/*.jpg`
  (JPEG ORIGINAL del ESP32, sin cajas) + `labels/*.txt` YOLO + `data.yaml`.
- `GET /api/captures?offset=` paginación; `GET /api/captures/{id}/detections`
  detalle; `GET /api/captures/{id}/image?orig=1` JPEG crudo.
- `captures.image_orig_jpeg` (nueva columna, migración automática) guarda
  el JPEG sin anotaciones para reentrenar limpio.

**Firmware `fotos_nc.ino`:**
- `aplicarClaveRemota` acepta `pump_cmd=<ts>:<s>` (validación estricta,
  dedup por `lastPumpCmdId` RTC_DATA_ATTR — sobrevive resets).
- Ejecución NO bloqueante en `loop()`: `bombaManualActiva`/`bombaManualFin`
  con comparación wrap-safe; el sensor/cola/WiFi siguen vivos durante el
  ciclo. `correrBomba()` limpia el flag para consistencia.
- ACK por `POST /api/device/ack` tras ejecutar; si falla, el TTL del
  servidor lo expira y el dedup RTC evita re-ejecución.

**Dashboard (`static/`):**
- Panel **Actividad**: barras apiladas Canvas 2D de detecciones/día por
  clase (selector 7/14/30 días, auto-refresh 30 s).
- Panel **Bomba**: control manual (segundos + "Activar ahora"), estado de
  orden pendiente y lista de eventos recientes.
- **Galería completa**: grid paginado (24/pág, "Cargar más"), filtro por
  clase en vivo, click → lightbox con imagen anotada + chips de detección
  con confianza. Nuevas capturas WS aparecen arriba solas.
- Botón **Exportar YOLO** (descarga el ZIP directo).

### Verificado end-to-end

- `POST /api/pump/run {seconds:5}` → ESP32 lo recibió en su poll →
  `Bomba MANUAL 5s` → `Ack -> HTTP 200` → `Bomba manual APAGADA` →
  `pending_cmd: None` en servidor. Relay conmutó (GPIO13, semántica NC).
- Export YOLO: ZIP válido (4 imágenes + labels + data.yaml).
- Paginación, detecciones por captura (404 correcto), orig/annotated OK.
- Compilación firmware OK (1 134 541 B, 36%).

### Pendiente

- HC-SR04 sigue sin lecturas en banco (no cableado a la placa del banco).
- Prueba del trigger ultrasónico y del ciclo automático 3-fotos→bomba.
- Evaluación con capturas acuáticas reales cuando existan.

## Sesión 6 — Pulido estético/móvil + documentación completa (13-nov, noche)

### Dashboard — estética y responsive

- Capa de pulido en `static/styles.css` (~930 líneas): gradientes sutiles,
  glassmorphism en paneles, micro-interacciones (hover, transiciones),
  scrollbar personalizada, estados vacíos ilustrados.
- **Responsive móvil** (acceso desde celular): grid de paneles colapsa a
  1 columna <900 px, tarjetas compactas, tipografía fluida, lightbox
  adaptado a pantalla táctil, controles ≥44 px táctiles, gráficas con DPR.
- `static/app.js`: ajustes de clases/IDs para los nuevos módulos, lightbox
  táctil, y poll de estado del dispositivo cada 30 s.
- Sintaxis JS verificada (`JS OK`), dashboard responde HTTP 200.

### Acceso desde el celular (verificado)

| Escenario | URL |
|-----------|-----|
| Celular en hotspot `REBA-AP` de la PC | `http://192.168.137.1:5000` |
| Celular en la misma Wi-Fi que la PC | `http://<IP-PC>:5000` (ej. `172.16.110.29` si sigue vigente — `ipconfig`) |
| Requisito | Firewall Windows permite TCP 5000; servidor en `0.0.0.0` |

### Documentación actualizada al estado real

- `docs/componentes.md` **(nuevo)**: función + decisión justificada + uso
  breve de cada componente físico y software; incluye tabla de acceso
  móvil y la advertencia de que el cableado NC deja la bomba ON si el
  ESP32 pierde alimentación por separado.
- `docs/server.md`: endpoints nuevos (`pump/run`, `pump/events`,
  `device/ack`, `device/config`, `admin/device-config`, `timeseries`,
  `export/yolo`, `detections/{id}`), validación JPEG/magic bytes, doble
  JPEG (anotado vs original), heartbeat `device`, retención horaria,
  umbral `INFERENCE_CONFIDENCE=0.35` (máx F1 medido).
- `docs/dashboard.md`: reescrito con los 9 módulos actuales, flujo WS,
  bomba manual vs inferida, acceso móvil.
- `docs/firmware.md`: reescrito antes (Preferences/WiFiManager/
  httpSimple/pump_cmd/validación estricta).
- `docs/hardware.md`: ya era fiel al cableado real (sin cambios).
- `docs/ai_pipeline.md`: añadido `eval_upload_path.py`, umbral 0.35,
  sección 5 con export YOLO y advertencia de purgar capturas de eval.

### Estado del hardware en este momento

- ESP32-CAM **desconectada de USB** por el usuario — operará inalámbrica
  en el artefacto (NVS ya tiene WiFi `REBA-AP` + `server_url`; si el
  hotspot no existe, el portal `REBA-Setup` se reabre solo).
- HC-SR04: pendiente cableado físico (TRIG→14, ECHO→15 divisor 1k/2k).
- Última captura real: id=10 en SQLite (firmware endurecido, source=onnx).

## Sesión 7 — Ampliación del dataset (14-nov)

### Motivación

El eval outdoor mostró desbalance severo: `organico` mAP50-95 = 0.095 y
`plastico` -0.097 vs baseline. TACO aportaba 863 cajas de `plastico` pero
solo 5 de `organico` y 25 de `tela` en train. Se decidió ampliar datos
dirigido a clases débiles + un repo externo, sin tocar servidor/firmware.

### Datos nuevos

- **TACO completo**: `download_taco.py --limit 0 --prioritize-classes
  organico,tela,unicel` → ~1293 imágenes mapeables (antes 750). Reanuda por
  manifest; prioriza las clases escasas.
- **`REBA_synthetic_boost2` (nuevo, propio)**: 240 train acuáticas con
  `--class-weights 1,3,6,2,1` (organico ×6, tela ×3, unicel ×2) —
  distribución resultante: organico 298, tela 135, unicel 88 cajas.
- **EcoSort (nuevo, externo)**: `download_ecosort.py` descarga
  `qihang3426/ecosort-dataset` de Hugging Face (`dataset.tar` ~1.5 GB,
  formato YOLO de 10 clases fusionado de Open Images + TACO + Roboflow +
  CC0) y remapea a REBA: plastic_*→plastico, cans/battery→metal;
  glass/cardboard/paper→fondo. Resultado: **718 train + 82 val**
  (`datasets/REBA_ecosort/`, manifest + summary + data.yaml).
  Descartados como fuente: IWHR floater (1 sola clase "floater", sin
  clases REBA), Ku-YOLO (es TACO re-etiquetado), Waste-Identifier (clf).

### Código

- `ai_pipeline/download_ecosort.py` (nuevo): descarga tar → extrae a temp →
  remapea labels → subconjunto determinista (`--limit/--val-limit/--seed`,
  `--tar-file` para reusar tar local).
- `train_outdoor.py`: `build_dataset_yaml()` refactorizado con
  `OPTIONAL_TRAIN_SOURCES` — auto-incluye cualquier
  `datasets/REBA_*/images/train` registrado (sintético + boost + boost2 +
  TACO + EcoSort). Val/test siguen siendo los sintéticos limpios.
- Verificado: `data.yaml` combinado genera las 5 fuentes correctamente.

### Pendiente

- Reentrenar con `train_outdoor.py` cuando TACO termine de descargar
  (~2700+ imágenes train esperadas) → `evaluate_outdoor.py` →
  `tune_confidence.py` → ajustar `.env` si cambia el óptimo.
- Las clases `tela`/`organico`/`unicel` siguen dependiendo del sintético:
  no existe dataset público acuático multi-clase con ellas — las capturas
  reales del ESP32-CAM (export YOLO del dashboard) son la fuente clave
  a futuro.

## Sesión 8 — Fases 0-2: auditoría + datasets acuáticos + dedup (14-nov)

Misión formal: mejorar el modelo priorizando `tela`/`organico`, con
validación en set real separado. Rama `mejora-datos-vision`.

### Auditoría (FASE 0)

- Baseline guardado en `reports/baseline/baseline_eval.md` (v2 outdoor:
  clean 0.681 / outdoor 0.591 / taco_real 0.083 mAP50-95).
- `audit_labels.py`, `audit_pixels.py`, `dataset_stats.py`:
  sin etiquetas malformadas ni fuera de rango; sin stems duplicados
  entre splits; stats completas en `reports/dataset_stats.json`.
- **Definiciones de clase escritas** en `docs/ai_pipeline.md` (qué es
  `organico` y `tela`, qué queda como fondo).

### Catálogo de datasets (FASE 1)

`datasets/CATALOGO.md` — ~30 candidatos verificados con licencia y
veredicto. Usados: **FloatWaste** (HF, CC BY 4.0), más TACO/EcoSort ya
integrados. Pendientes humanos: Garbage Classification 3 (Roboflow
bloquea sin API key — única fuente pública de `cloth`+`biodegradable`
reales), FloW (requiere solicitud), capturas ESP32-CAM.

### Integración (FASE 2)

- **`download_floatwaste.py` (nuevo)**: FloatWaste COCO→YOLO-REBA.
  25 categorías remapeadas (Packaging/S_*/H_*→plastico, D_polystyrene→
  unicel, M_*→metal, T_wood→organico). Salida: `REBA_floatwaste`
  **1,549 train** (incl. 49 fondos de agua limpia) + **`REBA_real_val`
  760→415 val** = set de validación real acuático.
- **`generate_copypaste_dataset.py` (nuevo)**: 600 imgs copy-paste de
  recortes reales sobre fondos de agua → **594 tela + 585 organico**
  (casi duplica esas clases en train).
- **`dedup_audit.py` + `clean_real_val.py` (nuevos)**: dHash perceptual.
  Hallazgo crítico: FloatWaste train↔val compartía **4,572 near-dup**
  (frames de video) → 345 imágenes contaminadas eliminadas de
  `real_val`. Quedan 415 limpias (873 plastico, 171 unicel, 57 metal,
  20 organico, **0 tela** — no existe fuente pública acuática con tela).
- `evaluate_outdoor.py`: nuevo conjunto `real_water` (= `REBA_real_val`,
  nunca entrenado) — **es el set que decide el despliegue**.
- `train_outdoor.py`: `OPTIONAL_TRAIN_SOURCES` +FloatWaste +copypaste.

### Dataset v4 (train combinado)

**4,579 imágenes / 18,560 instancias** (v3 tenía 2,430):
plastico 10,352 · tela 1,025 · organico 1,678 · unicel 2,974 · metal 2,531.
`tela` +138%, `organico` +189%, `unicel` +697% vs v3.

### Entrenamiento

- v3 (TACO+boost2+EcoSort) **cortado en época ~2** (~36 min/época CPU) al
  completarse v4 — superconjunto estricto; pesos parciales preservados.
- **v4 EN CURSO**: `runs/detect/REBA_outdoor_v4`, 50 epochs patience 20,
  ~573 iter/época (~60-70 min/época estimado en CPU i5-1135G7).
- Pendiente: `evaluate_outdoor.py` (con `real_water`) → comparar vs
  baseline → `tune_confidence.py` → ONNX solo si supera en set real →
  archivar `models/best.*` previos.

### Qué debe hacer el humano

1. **Capturas ESP32-CAM reales** (la fuente definitiva para `tela`/
   `organico`/`unicel` en dominio exacto): protocolo — 20-30 fotos por
   clase, objetos a 5-23 cm, agua con/sin reflejos, luz mañana/tarde/
   nublado, parcial y totalmente sumergidos; exportar YOLO desde el
   dashboard cuando el servidor esté corriendo.
2. **Garbage Classification 3** (Roboflow): descargar zip YOLOv8 desde
   universe.roboflow.com/material-identification/garbage-classification-3
   (necesita cuenta gratuita/API key — bloquea scraping). Aporta
   `cloth`→tela y `biodegradable`→organico reales.
3. **FloW dataset**: solicitud en github.com/ORCAUBOAT/FloW-Dataset.
4. Si v4 tarda demasiado: dejar la PC encendida; el run reanuda con
   `YOLO("runs/detect/REBA_outdoor_v4/weights/last.pt").train(resume=True)`.
