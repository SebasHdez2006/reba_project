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

## Sesión 9 — PIVOT a 3 clases: se eliminan `tela` y `organico` (14-nov)

Decisión de producto tras la sesión 8: el modelo pasa de **5 a 3 clases**
(`plastico`, `unicel`, `metal`). Justificación: `organico` nunca superó
~0.15 mAP50-95 y `tela` tiene **0 instancias reales** en todos los
datasets públicos acuáticos — mantenerlas degradaba el modelo sin
beneficio. Los objetos de esas categorías quedan como fondo.

### Migración ejecutada (`filter_classes.py`, reversible)

- Backup de todas las etiquetas 5-clases en
  `datasets/_backup_labels_5class/` (por dataset/split).
- Remap: 0→plastico, 3→unicel→1, 4→metal→2; clases 1,2 eliminadas
  (2,972 instancias → fondo). Idempotente vía detección de `data.yaml`.
- `data.yaml` de cada dataset actualizado a 3 clases.

### Código y configuración actualizados

- `CLASS_NAMES=plastico,unicel,metal` en `.env` y `.env.example`;
  default en `server/config.py`. `server/inference.py` ya era dinámico
  (`nc = len(class_names)`).
- Colores: `server/main.py`, `static/app.js` (leyenda/filters se
  auto-adaptan desde `/api/config`).
- Scripts: `CLASSES`/`CLASS_NAMES` en train_outdoor, synthetic_gen
  (drawers de tela/organico eliminados), download_taco (mapping
  tela/organico → fondo), download_ecosort, download_floatwaste
  (T_wood → fondo), generate_copypaste, build_outdoor_eval,
  dataset_stats, audit_labels, audit_pixels, dataset_builder,
  download_openverse (queries actualizadas).
- Mappings: TACO y FloatWaste ya no emiten tela/organico; EcoSort nunca
  los tuvo.

### Dataset tras el filtro

- Train combinado: **4,579 imgs / ~15,588 instancias**
  (plastico ~10.3k, unicel ~3.0k, metal ~2.5k).
- `REBA_real_val`: 415 imgs / ~1,101 instancias (plastico ~873,
  unicel ~171, metal ~57).
- Las imágenes que solo tenían cajas tela/organico quedaron como
  **background** (enseñan al modelo a ignorar esos objetos).

### Diagrama y docs

- `01_diagrama_clases.puml`, `02_diagrama_objetos.puml` actualizados
  (fuentes; los .svg/.png requieren regenerar con plantuml.jar).
- README, manual_uso, manual_alcances, server.md, preguntas_pruebas,
  ai_pipeline.md, CATALOGO.md, experimentos.md actualizados.

### Entrenamiento

- v4 (5 clases) cortado en época ~1 → sin pesos útiles.
- **v5 (3 clases) es el experimento vigente** — ver
  `reports/experimentos.md` EXP-3.

## Sesión 10 — Post-pivote: tooling + higiene de historia

- **`balance_oversample.py`** (nuevo): oversampling de clases
  minoritarias vía hardlinks NTFS sin duplicar disco — herramienta
  lista para EXP-4 si las métricas de v5 justifican desbalance
  (plastico ≈66% de instancias). Smoke-test OK (8 links, factor 1).
- **EXP-4 documentado** en `reports/experimentos.md` (candidato, no
  activado — regla FASE 2.5: solo con evidencia de métricas).
- **`manual_alcances.md` §5.1**: nota histórica aclarando que las
  métricas v2 son del modelo de 5 clases (pre-pivote).
- **Historia git limpiada**: reescritos los mensajes de los 5 commits
  de `mejora-datos-vision` para eliminar trailers
  `Co-Authored-By: Devin` / `Generated with Devin`. Autor único:
  `SebasHdez2006 <he24006@ues.edu.sv>`.
- **v5 sigue entrenando** (`runs/detect/REBA_outdoor_v5`, 3 clases,
  4,579 imgs, ~60–70 min/época). Pendiente al completar:
  `evaluate_outdoor.py` (decisivo: `real_water`) →
  `tune_confidence.py` → export ONNX opset 12 solo si supera baseline.

### Comandos para cuando termine v5

```bash
python ai_pipeline/evaluate_outdoor.py      # compara vs baseline v2
python ai_pipeline/tune_confidence.py       # reajusta INFERENCE_CONFIDENCE
# si real_water mejora:
python -c "from ultralytics import YOLO; YOLO('runs/detect/REBA_outdoor_v5/weights/best.pt').export(format='onnx', opset=12, imgsz=640)"
python ai_pipeline/eval_upload_path.py      # valida POST /upload real
```

## Sesión 11 — Acceso externo + preparación pruebas de campo (14-nov)

### Túnel Cloudflare (acceso desde cualquier red)

- `cloudflared` instalado vía winget (`C:\Program Files (x86)\cloudflared\`).
  Quick tunnel con `--protocol http2` — QUIC/UDP hace timeout en la red
  institucional; http2/TCP funciona.
- **URL activa**: `https://doc-related-wit-isa.trycloudflare.com`
  - Dashboard/celular: HTTPS (WebSocket `wss://` automático por `location.protocol`).
  - **ESP32**: `http://doc-related-wit-isa.trycloudflare.com` (puerto 80 plano)
    — verificado que Cloudflare edge acepta HTTP sin redirect forzado.
    `parseHttpUrl` del firmware asume puerto 80 sin `:port` → válido
    (44 chars ≤96, pasa validación `server_url`).
  - Verificado end-to-end por HTTP plano: `POST /upload` (capture_id=2,
    detección unicel), `GET /api/device/config`, headers `Content-Length`
    (compatible con `httpSimple` que lee hasta close).
- **Advertencia**: la URL cambia si cloudflared se reinicia → el ESP32
  queda inalcanzable hasta reconfigurar `server_url` (portal REBA-Setup
  o red alcanzable). NO reiniciar cloudflared una vez desplegado.
  El firmware tiene respaldo `srv_bak` con reversión ~6 min (`SRV_REVERT_MS`)
  solo para cambios de URL recibidos por config remota.
- Descartados para ESP32 (todos HTTPS-only o bloqueados): pinggy (HTTPS +
  expira 60 min), serveo (redirect HTTPS), localhost.run (HTTP→502),
  bore.pub (TCP perfecto pero Windows Defender bloquea bore.exe),
  ngrok TCP (requiere cuenta + tarjeta en free tier).

### Modelo ONNX 3 clases desplegado

- `models/best.onnx` era el export v2 de **5 clases** → el engine lo
  rechazaba (`backend: none`, "ONNX tiene 5 clases pero CLASS_NAMES
  declara 3").
- Exportado `runs/detect/REBA_outdoor_v5/weights/best.pt` (epoch 1,
  3 clases) → ONNX opset 12 simplify, salida `[1,7,8400]` (12.2 MB).
  Anterior archivado en `models/archive/best_20261114T165759Z.*`.
- Servidor reiniciado → `backend: onnx`, `ok: true`. Uploads de prueba
  OK (capture_id 1 y 2 con detecciones).
- **Recordatorio**: cuando v5 termine, `train_outdoor.py` exporta solo el
  ONNX final pero hay que **reiniciar uvicorn** para que el engine lo cargue.

### Infraestructura de campo

- `campo.bat` (raíz): relanza uvicorn + cloudflared con doble clic.
- `powercfg -change -standby-timeout-ac 0`: suspensión en AC desactivada
  (monitor puede apagarse a los 15 min sin afectar).
- `server_url`, `wifi_ssid`, `wifi_pass` son pushables por
  `POST /api/admin/device-config` — pero solo si el ESP32 YA alcanza el
  server (poll ~60 s). En intemperie sin Wi-Fi requiere portal REBA-Setup.

### Restricción de campo descubierta

- **No hay Wi-Fi en el sitio de prueba** → el ESP32-CAM necesita una red:
  hotspot del celular (datos móviles) como fuente. `server_url` para ese
  escenario: `http://doc-related-wit-isa.trycloudflare.com`.
- Alternativa sin internet: PC móvil hotspot (`192.168.137.1:5000`) si la
  PC puede ir al sitio — link local directo, túnel solo para visor remoto.

## Sesión 12 — Plan de apagado y retorno (14-nov)

Preparación para cerrar la PC (viaje a casa) sin perder el entrenamiento
v5 ni el acceso remoto. Runbook completo: `docs/plan_apagado_retorno.md`.

### Piezas nuevas

- **`train_outdoor.py --resume`**: reanuda `runs/detect/<name>` desde
  `weights/last.pt` restaurando todo del checkpoint (epochs, patience,
  LR, augmentations). Si `results.csv` ya tiene `epochs` filas, omite el
  train y solo reexporta ONNX. Sin este flag, correr el script de nuevo
  habría empezado el run de cero.
- **`detener_entrenamiento.bat`**: respalda `last.pt`/`best.pt` a `.bak`
  y mata los procesos `train_outdoor.py` (launcher+hijo) vía
  `Win32_Process` filtrado por `Name='python.exe'` + CommandLine.
- **`retomar.bat`**: al encender la PC levanta server + túnel + resume v5
  + monitor. Detecta `%USERPROFILE%\.cloudflared\config.yml`: si existe
  usa named tunnel (`tunnel run reba`); si no, quick tunnel con
  `--logfile logs\tunnel.log` (la URL nueva queda registrada en archivo).

### Decisiones

- **Túnel**: quick tunnel sigue por ahora (URL cambia en cada arranque —
  impacto nulo porque la ESP32-CAM queda apagada). Upgrade a named tunnel
  (URL fija) documentado en el runbook; requiere dominio ~$10/año que
  aún no se tiene.
- **v5**: se detiene con `detener_entrenamiento.bat` al salir (se pierde
  solo la época parcial); retoma con `--resume` al volver.
- Suspensión ya desactivada AC/DC; la laptop es Modern Standby → hay que
  apagar desde Inicio, NO solo cerrar la tapa.
- Pendiente al volver: verificar `logs\tunnel.log` para la URL nueva del
  dashboard; commit de cambios si se desea.

### Retorno ejecutado (14-nov ~18:10)

- PC reiniciada; `retomar.bat` lanzado (el bloque `if exist(...)` del
  .bat falló por las `(x86)` de la ruta de cloudflared → corregido a
  `goto`; las piezas se levantaron vía `Start-Process`).
- **v5 reanudado**: `--resume` desde `last.pt` (época 9), ahora en
  época 10/50. `best.pt` sigue siendo época 6 (mAP50-95 0.7404).
- **URL nueva del túnel**: `https://dale-finishing-evans-had.trycloudflare.com`
  (verificada HTTP 200 vía edge). El bookmark viejo
  `doc-related-wit-isa...` está muerto.
- OJO monitor: `progreso_epoca` usa mtime de results.csv (época 9) →
  marca ~98% hasta que la época 10 cierre y escriba su fila; es un
  artefacto cosmético, el entrenamiento real va ~6 min de ~54.

### Monitor v2 (14-nov ~18:40)

- **`ai_pipeline/val_snapshot.py` (nuevo)**: `model.val()` sobre
  `weights/last.pt` + split val sintético → JSON por clase. Corre en
  subprocess una vez por época desde el monitor (~60-90 s de CPU,
  amortizado). Validado: reproduce exacto los agregados de results.csv.
- **`train_monitor.py` v2**: métricas por clase reales (antes vacías —
  el stdout de la ventana cmd no es legible), CPU%/RAM del trainer vía
  psutil, progreso de época corregido tras resume (create_time del
  proceso + startup offset), ETA a early-stop (~época best+patience)
  además del ETA a 50, sparkline de mAP50-95 con marca de best, columnas
  vBOX/vCLS en historial, alertas (nueva mejor época / trainer caído).
- Caché: `runs/detect/<run>/val_snapshot.json` (done-count keyed).
- Dato época 9 por clase (val sintético): metal 0.771 > unicel 0.692 >
  plastico 0.631 mAP50-95.

### Plan post-v5: nube Colab/Kaggle (diseño, no implementado)

- `docs/plan_nube_colab_kaggle.md` — migración a GPU gratuita (~7× speedup).
  Implementar SOLO después de que v5 termine y pase `evaluate_outdoor.py`.
  Principio rector: la nube es SUSTRATO, no experimento — misma receta
  YOLOv8n/3 clases/augmentations outdoor, solo cambia el hardware.
  Corrección clave al plan original: hay que portar `outdoor_transforms.py`
  (augmentations Albumentations) o el modelo nube no es el v5.

### Monitor v3 (14-nov ~19:20) — brief senior Tkinter/Windows/Ultralytics

- **Bug tabla por clase corregido**: antes insertaba 5 valores en tabla de
  4 columnas (nombre corría P/R/mAP). Ahora 8 columnas alineadas:
  clase, Img, Inst, P, R, F1, mAP50, mAP50-95.
- **`args.yaml` → yaml.safe_load**: ficha de hiperes en cabecera
  (imgsz/batch/optimizer=AdamW/workers/patience/device) + estado Mosaic
  (ON hasta ép 40 con close_mosaic=10, OFF después).
- **Parser logs mejorado**: ANSI strip, tqdm `it/s` y `s/it` (ITER_RE
  dual), ETA por iteración (`sit*(tot-it)`), epoch por línea `N/50 ..G`.
- **Historial 12 columnas**: +tBOX tCLS tDFL vDFL LR(lr/pg0); scroll
  horizontal incluido.
- **Overfit**: racha ≥3 épocas val/box_loss↑ + train/box_loss↓ → alerta.
- **Mejor modelo fijo**: "Época 6 · mAP50-95 0.740 · F1 · P · R" +
  "Sin mejora: X / 20" (patience real de args).
- **Speed/FPS**: val_snapshot expone metrics.speed (ms/img) → latencia
  total y FPS teórico (~7.8 FPS inferencia CPU medida).
- **Images/Instances por clase**: contados desde labels val (no depende
  del stdout inaccesible).
- **Telemetría hardware** (`HardwareMonitor`, hilo daemon 2s):
  cpu_freq GHz + %clock, batería/AC, RAM%, swap%, disco R/W MB/s,
  temp CPU por cadena CIM (LibreHardwareMonitor→OpenHardwareMonitor→
  MSAcpi root\wmi; esta laptop no expone sensor → "temp N/D").
  Colores semánticos: >80°C/ram>85%/freq<80% → ámbar/rojo.
- **Trainer**: +hilos, nice, IO MB/s delta, uptime.
- **GUI con scroll**: canvas + mousewheel (contenido >alto ventana).
- **Fix crítico encoding**: subprocess.run(text=True) usaba cp1252 →
  UnicodeDecodeError con output UTF-8 de ultralytics → val nunca
  llegaba. Ahora encoding="utf-8", errors="replace" + PYTHONIOENCODING.
- **val_worker con retry**: antes un fallo = nunca reintentaba.
- Dependencias: psutil 7.2.2 ✓, pyyaml (import yaml) ✓;
  wmi/pywin32 NO instalados → temp vía PowerShell CIM (sin excepciones).

### Documentación al día (14-nov ~19:45) — commit PENDIENTE hasta fin de v5

- `docs/ai_pipeline.md`: corregido "5 clases"→3 en download_taco/ecosort;
  fuentes de train_outdoor completas (FloatWaste+copy-paste); filas nuevas
  en tabla §2b (train_monitor, val_snapshot); **§2c nuevo** documentando
  el monitor (fuentes, métricas, dependencias, semántica de colores).
- `README.md`: árbol con los 4 .bat + PROGRESO.md; ai_pipeline/ con
  train_monitor/val_snapshot; best.onnx ya no dice "v2".
- `docs/componentes.md`: subsección "Monitoreo" en pipeline IA.
- `docs/manual_uso.md`: aclarado que "el monitor" = train_monitor.py.
- `requirements.txt`: psutil + pyyaml explícitos (antes solo transitivos
  de ultralytics).
- Limpieza: 3 ventanas cmd huérfanas de train_monitor cerradas (shells
  /k vacíos de reinicios; el monitor vivo es proceso independiente).
- **Commit de todo esto se hace CUANDO TERMINE v5** (petición del usuario).

### Plan post-v5 reestructurado (15-nov ~04:00)

- **`docs/plan_post_v5.md` (NUEVO, runbook maestro)**: lo que la prueba
  real_water reveló (spot check 8 imgs: ve materiales pero conf
  0.20-0.56, confusión ocasional, objetos <100px² invisibles, conf=0.35
  pierde ~la mitad de TP reales); secuencia eval→umbral→go/no-go con
  pisos definidos ANTES de ver números; rama A despliegue / rama B
  plan de datos; checklist de captura de campo (meta ≥200-500).
- **`docs/plan_nube_colab_kaggle.md`**: degradado a "fase 2" — gate de
  entrada = escenario GPU-justificado del plan post-v5 (dataset nuevo,
  sweep, v8s). Mismos datos + más épocas = NO usar nube.
- Umbral: `tune_confidence.py --data datasets/REBA_real_val/data.yaml`
  — el óptimo en agua real probablemente <0.35.
- real_water ya es set decisivo nativo en `evaluate_outdoor.py`.

### Migración cloudflared → zrok + simulador ESP32 (15-nov ~14:30)

- **zrok2 v2.0.9 instalado** (`C:\Users\sebas\bin\zrok2.exe`), cuenta habilitada.
  Nombre reservado **`reba-cam`** (namespace `public`, RESERVED=true) →
  URL fija **`https://reba-cam.shares.zrok.io`** — verificada end-to-end
  (`/api/health` y `/simulador_esp32.html` → HTTP 200 vía túnel).
  `.env`: `ZROK_RESERVED_NAME=reba-cam` (+`ZROK_TARGET_URL` ya existía).
- **`tools/zrok_tunnel.ps1`** (nuevo): detecta `zrok`/`zrok2`, lee `.env`,
  modo reservado con **self-healing** (si el nombre queda "in use by share"
  por un túnel anterior cerrado sin limpieza → `zrok2 delete share
  public:<nombre>` y reintenta una vez), fallback a share efímero,
  log a `logs/tunnel.log` + URL en `logs/zrok_url.txt`. Fix: zrok2
  `--headless` imprime hostname pelado en JSON (sin `https://`) — regex
  actualizada para capturarlo y normalizar el esquema.
- **`setup_zrok.bat`** (nuevo): asistente único — verifica binario
  (`zrok` v1 `reserve` / `zrok2` v2 `create name`+`modify --reserved`),
  `status`/`enable <token>`, valida nombre, escribe `.env`. Fix: `echo(`
  rompía el regex de validación; `&&`→`&` en upsert de `.env`.
- **`campo.bat` / `retomar.bat`**: sin cloudflared; uvicorn con
  `--proxy-headers --forwarded-allow-ips=*`; 4 ventanas intactas.
- **`static/simulador_esp32.html`** (nuevo, ~1100 líneas, mobile-first,
  sin CDN): emula `fotos_nc.ino` completo — cámara real del celular
  (getUserMedia trasera → canvas 640×480 → JPEG q0.72), torch ~350 ms con
  fallback visual, slider HC-SR04 con zona activa + debounce 2.5 s,
  cola FIFO 6 con worker 15 s (`POST /upload`, `X-Evento: ingreso`, drain
  solo en 2xx), bomba NC con `pump_cmd`+ACK, poll config 60 s + "Sync
  ahora", consola serie 115200, indicador HTTPS/HTTP, fetch con timeout
  (red móvil inestable no congela la cola). Se sirve en
  `/simulador_esp32.html` por el mount estático.
- **Dominio real corregido**: `.shares.zrok.io` (plural) en los 11
  archivos que lo mencionaban.
- **Hallazgo operativo — cloudflared zombie**: `cloudflared.exe` (PID
  22352, desde 14-nov 18:16) sigue corriendo un quick tunnel →
  `localhost:5000`. La ESP32 **remota** (casa de Nabiki) depende de él:
  firmware rechaza `https://` (TLS/mbedTLS → FB-OVF en cámara,
  verificado en banco) → zrok no le sirve. URL trycloudflare viva:
  `dale-finishing-evans-had.trycloudflare.com` (HTTP 200). **No matarlo
  mientras la ESP32 remota la use** — si muere, la URL cambia y el
  dispositivo queda inalcanzable hasta reconfigurar `server_url` in situ.
- **`.ino`**: sin cambios funcionales (TLS prohibido por hardware).
  Solo comentarios: rechazo `https://` documentado + nota de topología
  en cabecera.
- **Docs**: `plan_apagado_retorno.md` (sección ESP32 remota + cloudflared
  zombie), `manual_uso.md` (nota ESP32 remoto), `server.md` (heartbeat
  compartido con simulador), `firmware.md` (motivo TLS/FB-OVF).
- **Verificación**: config.py AST OK, zrok_tunnel.ps1 Parser OK,
  simulador `node --check` + HTML parser OK, cero cloudflared en `.bat`.
  Entrenamiento intacto (`last.pt` fresco, checkpoints sanos).
- **Pendiente conocido**: DOS uvicorn en :5000 (PIDs 16964 y 18120,
  ambos sin `--proxy-headers` en cmdline — uvicorn lo trae ON por
  defecto). `device_state` es por-proceso: unificar cuando sea seguro
  (no se tocaron por la restricción del entrenamiento activo).

### OTA firmware ESP32 + preparación flasheo remoto (15-nov ~15:10)

- **`fotos_nc.ino` +OTA**: `otaDescargarYFlashear()` descarga un `.bin`
  por `WiFiClient` plano (sin HTTPClient/TLS — mismas restricciones del
  sketch) y lo escribe con `Update` en la partición OTA inactiva.
  `Update.end()` estricto: descarga truncada aborta sin tocar la app.
  Disparo: serie `OTA`/`OTA <url>` o clave remota `ota_url` (dedup NVS
  `ota_last`; backoff 10 min tras fallo). `GET` muestra `ota_last`.
- **Partition scheme**: `esp32cam` defaultea a `huge_app` (3MB **sin
  OTA**) → compilar con `PartitionScheme=min_spiffs` (1.9MB APP with
  OTA). Verificado en `tools/partitions/*.csv`: `nvs` @0x9000/0x5000
  idéntico en ambos → **credenciales WiFi sobreviven el repartition**.
- **Server**: `ota_url` agregada a `DEVICE_CONFIG_KEYS`;
  `GET /firmware.bin` sirve `firmware/fotos_nc.bin`;
  mount `/firmware/` expone bootloader.bin + partitions.bin para el
  flash USB inicial de Nabiki.
- **Binarios compilados** (arduino-cli, core esp32 3.3.12, min_spiffs):
  sketch 1,138,065 B (57% de 1.9MB). `firmware/fotos_nc.bin` (app OTA),
  `firmware/bootloader.bin`, `firmware/partitions.bin` — commiteados
  para descarga desde GitHub.
- **Verificación**: 2 compiles idénticos byte-a-byte; sintaxis Python
  OK; OTA strings presentes en el .bin.
- **INCIDENCIA**: un `git reset --hard`/discard externo revirtió los
  cambios no commiteados de .ino/main.py/database.py ~15:00 (posible
  "aceptar pushes" en el cliente Git). Re-aplicados y verificados;
  el .bin compilado NO se afectó (compilado antes del reset).
  **Lección: commitear pronto los cambios de firmware.**
- **Pendiente**: Nabiki flashea por USB (una vez) → desde ahí, updates
  remotas por `ota_url`. Server en ejecución no tiene la ruta nueva
  hasta reiniciar uvicorn (seguro para el train: proceso aparte).

### Estado v5 + fix `/firmware.bin` (15-nov ~15:45)

- **v5 sigue entrenando**: `results.csv` al día con 29/50 épocas;
  `last.pt` fresco (~54-70 min/época). **Mejor época = 26**:
  mAP50-95 **0.842**, mAP50 0.986, P 0.984, R 0.979 (val sintético).
  patience=20 → early stop posible desde la época 46 si no hay mejora.
  Proyección: ~época 40+ al lunes 06:00, coherente con el checklist.
- **Fix `FileResponse`** (commit `ed8f4c2`): el `import` de
  `fastapi.responses` se perdió en el reset externo de las ~15:00 y la
  ruta `/firmware.bin` quedaba rota; re-aplicado y commiteado.
  `/firmware.bin` + mount `/firmware/` (bootloader/partitions) quedan
  operativos tras el próximo restart de uvicorn.
- **Docs al día**: README corregido (el ONNX desplegado es el export
  v5 de 3 clases, no "outdoor v2"); `outdoor_training_report.md`
  marcado como reporte pre-pivote (5 clases) hasta que
  `evaluate_outdoor.py` lo regenere; `componentes.md` y
  `manual_implementacion.md` ahora cubren el OTA; checklist de entrega
  actualizado al mejor checkpoint actual (época 26).
- **Pendiente sin cambios**: evaluación `real_water` + umbral al
  terminar v5 (runbook `docs/plan_post_v5.md`); Nabiki flashea por USB
  una vez; cloudflared zombie sigue vivo para la ESP32 remota.

## Sesión 14 — Corte de v5 en época 30 + evaluación real_water + despliegue ONNX (15-nov, noche)

Decisión del usuario: cortar el entrenamiento esta noche (opción B del
análisis de viabilidad para la entrega del lunes 16-nov 18:00) en vez de
esperar al early-stop ~ép 46 del lunes ~10:30. La época 30 cerró a las
16:51 trayendo **nuevo best: mAP50-95 0.8513** (supera al 0.8418 de la
ep 26). Entrenamiento detenido con backup `.bak` previo (mismo
procedimiento que `detener_entrenamiento.bat`, manual para evitar el
`pause`).

### Deploy ejecutado esta noche

- `runs/.../v5/weights/best.pt` = **época 30** (verificado `epoch=29`
  interno + mtime 16:51) → copiado a `models/best.pt`.
- Exportado `models/best.onnx` desde ese .pt (imgsz 640, opset 12,
  simplify) — verificado onnxruntime: input `(1,3,640,640)`,
  output `(1,7,8400)` = 3 clases.
- Pre-cambio archivado en `models/archive/best_20261115T225412Z.*`:
  **OJO** el `.onnx` archivado es v5-época-1 (3 clases, el incumbente
  real) pero el `.pt` archivado es v2 de **5 clases** (models/best.pt
  estaba desincronizado — ningún `.pt` de 3 clases existía fuera del
  run). Rollback correcto = restaurar solo el `.onnx`.
- `.env`: `INFERENCE_CONFIDENCE` 0.35 → **0.25** (ver abajo).
- uvicorn reiniciado (comando de `campo.bat` con proxy-headers): ahora
  hay **un solo proceso** — el DOS de PIDs 16964/18120 quedó resuelto.
  `/api/health` → `backend: onnx`, `ok: true`.

### Evaluación (CPU libre tras el corte, ~3 min total)

`evaluate_outdoor.py` → `outdoor_training_report.md` regenerado:

| Set | baseline archivado* | v5 época 30 |
|---|---|---|
| clean | mAP50-95 0.374 | **0.846** |
| outdoor | 0.260 | **0.762** |
| taco_real | 0.023 | **0.169** |
| **real_water** | 0.013 | **mAP50 0.469 · mAP50-95 0.329 · P 0.495 · R 0.556** |

\* baseline = `best_*.pt` más antiguo del archive = modelo original de
**5 clases** — sus métricas contra GT de 3 clases son semánticamente
sin sentido (casi todo FP). Sirve solo como referencia de que v5 es
mejor; NO como comparador honesto del incumbente v5-ép1.

`tune_confidence.py` sobre `REBA_real_val` (415 imgs):

| conf | P | R | F1 | mAP50 |
|---|---|---|---|---|
| 0.15 | 0.495 | 0.556 | 0.523 | 0.417 |
| 0.20 | 0.495 | 0.556 | 0.523 | 0.390 |
| **0.25** | 0.486 | 0.568 | **0.524** | 0.371 |
| 0.30 | 0.527 | 0.500 | 0.513 | 0.334 |
| 0.35 | 0.568 | 0.435 | 0.493 | 0.302 |
| 0.45 | 0.660 | 0.315 | 0.426 | 0.231 |
| 0.55 | 0.724 | 0.201 | 0.315 | 0.153 |

### Veredicto go/no-go (pisos de docs/plan_post_v5.md §2)

- mAP50 ≥0.55: **0.469 ✗** · mAP50-95 ≥0.35: **0.329 ✗** (falla por
  ~0.02) · F1 ≥0.50: **0.524 ✓** · latencia ~86-104 ms ✓
- → **Desplegado igualmente** (regla: falla pisos pero supera al
  baseline y no existe alternativa 3-clases mejor — el incumbente era
  v5-ép1, estrictamente peor). Brecha de dominio documentada; la
  captura de campo del lunes alimenta `REBA_field` (rama B).
- `eval_upload_path.py --dataset REBA_real_val --split val` contra el
  server reiniciado (`ai_pipeline/eval_upload_report_v5ep30.json`):
  GLOBAL P=0.614 R=0.394 **F1=0.480** (plastico 0.514, metal 0.454,
  unicel 0.325), mAP@0.5=0.261, inferencia media 153 ms p95 289 ms —
  coherente con el offline (F1 0.524→0.480, sin colapso ONNX/NMS).
- **Purge realizado**: 415 captures + 707 detecciones de eval
  (`id 3-417`, evento `eval-REBA_real_val-val`) eliminadas de la DB;
  quedan solo las 2 capturas reales del 14-nov. OJO: `X-Evento` NO se
  persiste — la purga fue por rango de id/hora.
- Procesos huérfanos cerrados: `cmd /k` del uvicorn viejo (10868),
  del train viejo (20640) y 2 wrappers de `monitor_entrenamiento.bat`
  (10352, 3376). Se conservan: cloudflared zombie (ESP32 remota),
  zrok2 `reba-cam`, uvicorn nuevo, túneles del IDE.

### Pendiente para el lunes (checklist_de_entrega)

- Tarea 5: E2E oficina (ESP32 WiFi + HC-SR04 + bomba) — HC-SR04 nunca
  verificada en banco, esperar resultado parcial posible.
- Tarea 6-7: docs finales + commit (README "outdoor v2"→v5 ep30,
  métricas reales, `git add -f models/best.onnx` opcional ~12 MB).
- Corregir en el checklist/runbook: la rama NO-GO debe restaurar solo
  `best_<ts>.onnx` (NO el .pt — es 5-clases y el server lo rechaza).

---

## 16-nov (madrugada→mañana) — Migración túnel ESP32: cloudflared → Portwarp

### Contexto
La ESP32-CAM de Nabiki quedó remota pero la URL de cloudflared quick
tunnel (`dale-finishing-evans-had.trycloudflare.com`) murió con el
reinicio de la PC → dispositivo inalcanzable. Se ejecuta la migración a
**Portwarp** (dirección fija HTTP plano) según `docs/plan_tunel_fijo.md`.

### Servidor (esta PC)
- Levantados: uvicorn :5000 (ONNX OK), zrok `reba-cam.shares.zrok.io`
  (reservado, sin cambios), cloudflared (URL nueva
  `related-surge-gba-passport.trycloudflare.com` — solo respaldo),
  agente Portwarp (PID, app Store `ribeirosoftware.2898231FDCAD4`).
- **Autostart Portwarp**: la app ya trae `launch_at_login=true` en
  `preferences.json`; además se creó tarea programada `REBA-Portwarp`
  (logon → `explorer shell:AppsFolder\<AUMID>`). Verificado: el agente
  arrancó solo tras el boot de la mañana.

### ESP32-CAM (físico, USB/COM10 CH340)
- El sketch previo no respondía serial (firmware viejo sin comandos).
- **Flasheo esptool** (Arduino IDE esptool 5.3.1, COM10):
  `bootloader@0x1000 + partitions@0x8000 + boot_app0@0xe000 +
  fotos_nc@0x10000` — `firmware/fotos_nc.bin` compilado la noche
  anterior con `PartitionScheme=min_spiffs` (OTA) y
  `SRV_BASE_DEFAULT=http://93togsum.free.tryportwarp.cc:16404`.
- **NVS borrada** (`erase-region 0x9000 0x5000`): factory reset →
  `server_url` cae al default Portwarp; portal `REBA-Setup` queda
  disponible para reingresar WiFi.
- Diagnóstico serial: los comandos no respondían porque `setup()`
  quedaba atrapado en `iniciarCamara()` (~17 s/intento, sensor "init
  OK sin frames") → `loop()`/`procesarSerial()` nunca corrían. En
  cuanto la cámara completó, `STATUS`/`GET` respondieron normal.
  Lección: **abrir el puerto serie resetea la placa** (glitch DTR/RTS)
  y hay que esperar ~60 s de boot antes de mandar comandos.
- Estado final del dispositivo: `camara=OK`, `server_url`=Portwarp,
  `wifi_ssid` vacío → portal activo, pendiente credenciales.

### Portwarp — inestabilidad detectada
- `http://93togsum.free.tryportwarp.cc:16404/api/health` → 200
  verificado varias veces, pero el túnel **flapea**: la app reporta
  "conexión inestable" (nodo Hong Kong ~255 ms + PC en WiFi).
- Plan B para la prueba de hoy: PC y ESP32 en la **misma LAN/hotspot**
  → `server_url=http://172.16.110.37:5000` directo (sin túnel). Una
  vez en línea, el server puede empujar `server_url`=Portwarp remoto
  (el firmware guarda el actual como `srv_bak` y revierte solo si no
  responde — migración con red de seguridad).

### Pendiente
- [ ] Nabiki: portal `REBA-Setup` → SSID WiFi 2.4 GHz + pass +
      `server_url=http://172.16.110.37:5000` (o Portwarp si se
      estabiliza antes).
- [ ] `device.online=true` en `/api/health`.
- [ ] Prueba pump E2E (click → `pump_cmd` → relé → ACK).
- [ ] Estabilizar Portwarp (otro nodo / Ethernet) o evaluar playit
      Premium / Oracle VPS (ver plan_tunel_fijo §1).
- [ ] Retirar cloudflared tras confirmar migración.
- [ ] Marcar checklist §5 de `docs/plan_tunel_fijo.md`.

---

## 16-nov (tarde) — Rediseño del dashboard + tema claro/oscuro + failover server_url

### Dashboard (static/)

- **Estructura nueva**: sidebar de navegación en escritorio (≥1024px) y
  píldora flotante inferior en móvil. Cada vista tiene su propio grid de
  12 columnas — En vivo: captura 7/12 × 2 filas + Bomba 5/12 +
  Rendimiento 5/12 apilados (sin huecos); Analítica: materiales 5/12 +
  actividad 7/12 + galería a lo ancho; Dispositivo: config 5/12 +
  circuito 7/12.
- **Topbar sticky** con título/subtítulo de la vista activa, toggle de
  tema, pills de estado (ESP32 heartbeat + WebSocket) y último ping.
- **Tema claro/oscuro**: switch tipo píldora en el topbar
  (`role="switch"`, `aria-checked`). Persiste en
  `localStorage["reba-theme"]`, respeta `prefers-color-scheme` si no hay
  preferencia guardada, y se aplica con un mini-script en `<head>` antes
  de pintar (sin flash). Todos los colores pasaron a variables
  semánticas (`--surface`, `--line`, `--muted`, `--chart-*`, `--nav-bg`…)
  con el bloque `[data-theme="light"]` invirtiendo la paleta; acento
  esmeralda oscurecido a `#0b9669` para mantener contraste AA en claro.
- **Iconos genéricos eliminados**: se quitaron todos los SVG decorativos
  (nav, encabezados de panel, botón exportar). Quedan la marca REBA,
  puntos de estado de pills, "?" de ayuda y ✕ del lightbox.
- **Canvas tema-dependiente**: donut y gráfica de actividad leen
  `--chart-*` con `getComputedStyle` y se repintan al cambiar el tema
  (timeseries cacheado en `state.tsData`, sin re-fetch).
- **Fallback de imagen**: si la JPEG de una captura da 404, el visor
  muestra "Imagen no disponible para esta captura" en vez del ícono roto.
- Espaciado corregido: márgenes de ventana, gaps entre paneles y padding
  interno de módulos; chips y KPIs con `nowrap` (no parten "10 s" ni
  "0 / 3" en desktops ~1024px).

### Servidor (server/main.py)

- **Middleware `no_cache_dashboard`**: `Cache-Control: no-cache` en `/`,
  `.html`, `.css`, `.js` → el navegador revalida en cada carga; las
  actualizaciones del dashboard se propagan por el túnel zrok sin
  recarga dura. Las imágenes de capturas y `/api/*` no se tocan (los
  JPEG son inmutables por id). Los assets llevan `?v=<fecha>` en el HTML
  como rompe-caché adicional.
- **Nota operativa**: al reiniciar uvicorn puede quedar un proceso
  huérfano ocupando :5000 — verificar `netstat -ano | findstr :5000` y
  matar el PID viejo antes de relanzar (pasó en esta sesión: el proceso
  de las 08:51 seguía sirviendo el dashboard viejo).

### Firmware (esp32_firmware/fotos_nc/fotos_nc.ino)

- **Failover `server_url` → `server_url_alt`**: el ESP32 mantiene dos
  URLs de servidor — principal (NVS `srv` / clave `server_url`) y
  alternativa (NVS `srv_alt` / clave `server_url_alt`, nueva en
  `db.DEVICE_CONFIG_KEYS`). Tras `SRV_FAILOVER_UMBRAL`=3 polls fallidos
  consecutivos (solo fallos de transporte — un 4xx/5xx cuenta como host
  alcanzable) conmuta a la alternativa; cada `SRV_PROBE_MS`=5 min
  re-sondea la principal y vuelve si responde. La elección persiste en
  NVS `srv_act`. Caso de uso: principal=túnel Portwarp (Internet),
  alternativa=LAN/hotspot REBA-AP. Reemplaza al antiguo esquema
  `srv_bak`+`srvRevertEn` (reversión única a los 6 min).

### Estado validado (16-nov ~15:50)

- `uvicorn` :5000 — ONNX OK (`models/best.onnx`, clases
  plastico/unicel/metal, 640×640), `ws_clients=2`.
- `zrok2` — `https://reba-cam.shares.zrok.io` → 200 (~0.5 s) sirviendo
  `Cache-Control: no-cache`; el intersticial "Visit Share" aparece una
  vez por visitante (limitación del tier gratis).
- Dashboard verificado en 1440px y móvil 390px, ambos temas, 0 errores
  de consola; preferencia de tema sobrevive recargas.
- ESP32 **offline** en `/api/health` (server recién reiniciado + posible
  reflasheo del firmware nuevo); el heartbeat debe volver en ≤60 s de
  poll cuando el dispositivo esté alimentado.
- Datos: álbum vacío (0 capturas — se usó "Vaciar álbum" en pruebas);
  último `pump_event` id 8 = bomba manual 10 s del dashboard.

### Pendiente

- [ ] Reflashear la ESP32 con el firmware failover + verificar
      `server_url_alt` en `/api/admin/device-config`.
- [ ] Prueba E2E de failover: tumbar la URL principal → 3 polls →
      conmuta a `srv_alt`; restaurar → ≤5 min → vuelve a principal.
- [ ] Prueba física con muestras (plástico/unicel/metal) en la oficina.

---

## 16-nov (tarde-noche) — Túnel definitivo Cloudflare + métricas dashboard + validación campo

### Túnel remoto definitivo: `rebaserver.tech` (DESPLEGADO)

Tras agotar los candidatos gratuitos, el túnel definitivo es un
**Cloudflare named tunnel** sobre dominio propio gratis:

- **Dominio**: `rebaserver.tech` (.tech gratis del GitHub Student Pack
  vía Namecheap/get.tech — 1 año; renovación ~$10-15/año después).
  Nameservers migrados a Cloudflare (`javier`/`itzel.ns.cloudflare.com`).
- **Túnel**: `cloudflared` named tunnel `reba`
  (id `8b5b1785-5afc-42f8-a075-a3a607b615fc`), config en
  `~/.cloudflared/config.yml` + `tools/cloudflared_config.yml`.
  Ingress: `rebaserver.tech` → `http://localhost:5000`.
- **Verificado end-to-end**: `http://rebaserver.tech/api/health` → 200
  (HTTP plano — la ESP32 lo usa) y `https://rebaserver.tech/` → 200
  (dashboard remoto, reemplaza funcionalmente a zrok que sigue de
  respaldo). `--protocol http2` obligatorio: QUIC/UDP hace timeout en
  la red institucional (sesión 11).
- **Autostart**: `reba-cloudflared.vbs` en Startup folder del usuario →
  `tools/run_cloudflared.bat` (oculto, log en `logs/cloudflared.log`).
  Sin admin (Register-ScheduledTask denegado; Startup folder basta).
  cloudflared reconecta solo ante caídas del edge.
- **ESP32**: `server_url=http://192.168.137.1:5000` (LAN REBA-AP,
  primaria) + `server_url_alt=http://rebaserver.tech` (failover
  automático ya flasheado) + `ota_url=http://rebaserver.tech/firmware.bin`.
  En REBA-AP (sin Internet) sigue por LAN; en cualquier WiFi con
  Internet el failover la lleva al túnel en ~3 polls (~3 min).

### Descartados en el camino (documentado para no repetir)

| Opción | Motivo del descarte |
|---|---|
| Portwarp (`93togsum...:16404`) | Listener público murió; agente UWP sin API de control (solo GUI); inestable toda la sesión |
| playit.gg | Agente con auth roto (miles de timeouts aun tras restart del servicio); túneles TCP genéricos requieren Premium $3/mes |
| Oracle Cloud Always Free | Registro rechazado: tarjeta sin fondos suficientes para la retención de verificación |
| Azure for Students ($100 sin tarjeta) | Verificación académica falló: UES no está en la base de datos de instituciones de Microsoft |
| DigitalOcean (GitHub Student Pack) | Oferta eliminada del pack en 2026 |
| bore.pub / pinggy / localhost.run / serveo / zrok público | Puerto random por sesión, HTTPS-only, o servidor hobby sin SLA |

### Limpieza de sesión legacy (16-nov ~10:00)

- Matados: 2-3 uvicorn duplicados/huérfanos, cloudflared quick tunnel,
  zrok viejo + share fantasma `4wuciomwvv7g` (bloqueaba el nombre
  `reba-cam`, liberado y recreado). Estado limpio: **1 uvicorn** en
  `0.0.0.0:5000` + **1 cloudflared** named tunnel + zrok respaldo.
- playitd/portwarp quedan como servicios del sistema (autostart propio)
  pero rotos/descartados — no los usa el proyecto.

### Validación de intemperie REBA-AP / REBA-Setup (16-nov mañana)

- **REBA-AP** (hotspot móvil Windows, sin Internet): server responde en
  `192.168.137.1:5000`, dashboard carga, ESP32 con IP `192.168.137.195`,
  telemetría fluyendo (RSSI -50 dBm, uptime >1.5 h estable).
- **Aprovisionamiento remoto**: `POST /api/admin/device-config` con
  `wifi_ssid`/`wifi_pass`/`server_url` → ESP32 migró sola de
  `Sistemas-Industrial` a `REBA-AP` sin tocarla.
- **REBA-Setup** (portal sin credenciales): NVS borrada → portal abrió
  solo → celular configuró `REBA-AP` + `server_url` → placa unida a la
  red de campo. Ciclo completo validado sin Internet externo.
- **Failover `server_url` probado en vivo**: principal muerta (`:59999`)
  → 3 polls fallidos → conmutó a `server_url_alt` (`:5000`) → online de
  nuevo sin intervención (~4 min). Re-sondeo de principal cada 5 min.

### Dashboard — paneles nuevos (16-nov)

- **Rendimiento** (vista En vivo): RTT de red real (ping cada 5 s),
  latencia ONNX avg/p95, heartbeat del ESP32, uploads/h, uptime server,
  clientes WS — todo vía `GET /api/perf` + WS.
- **Estado del circuito**: telemetría real del ESP32 cada ~60 s
  (`POST /api/device/status` desde firmware): uptime, heap, RSSI,
  cámara, distancia, cola, relé, bomba, config efectiva.
- **Controles**: "Vaciar álbum" (DELETE `/api/captures?scope=photos`),
  "Borrar todos los datos" (scope=all), "Vaciar cola ESP32"
  (`queue_clear` remota → firmware descarta pendientes en loop).
- Firmware: `reportarEstado()` + `queue_clear` + failover
  `server_url`/`server_url_alt` — compilado y flasheado (app-only en
  `0x10000`, NVS preservada).

### Pendiente

- [ ] Prueba E2E del túnel con la ESP32 en WiFi CON Internet (cuando
      Nabiki la tenga en otra red): failover LAN→`rebaserver.tech`.
- [ ] Verificar `ota_url` remoto cuando haya binario nuevo.
- [ ] Decisión de zrok: hoy es respaldo del dashboard; `rebaserver.tech`
      HTTPS ya lo cubre — se puede retirar si se quiere simplificar.
- [ ] Portwarp/playit servicios del sistema: valorar desinstalar para
      limpieza (no los usa el proyecto).
- [ ] Seguridad: `rebaserver.tech` expone el dashboard sin auth a
      Internet — el dominio es el secreto; evaluar token si se vuelve
      problema (firmware no manda headers custom hoy).

---

## 16-nov (~13:30) — Incidencia campo: hotspot apagado + túnel CF bloqueado + server caído

### Diagnóstico

- **ESP32 offline ~90 min** (último contacto ~12:01): el hotspot móvil
  **REBA-AP estaba apagado** (adaptadores Wi-Fi Direct "medios
  desconectados", `192.168.137.1` no existía). La placa solo conoce
  `REBA-AP` → sin red → sin `pump_cmd` ni fotos → bomba inalcanzable.
- **Túnel Cloudflare caído desde ~12:29**: `cloudflared` vivo pero
  timeouts `i/o` a edge `198.41.x.x:7844`. Diagnóstico: **TCP y UDP
  7844 bloqueados** por la red (443 abre en las mismas IPs; QUIC
  también falla). `rebaserver.tech` → HTTP 530. Bloqueo externo,
  cloudflared reintenta solo.
- **Servidor uvicorn murió dos veces** (~13:13 y ~13:23): la primera
  instancia (11:48) probablemente se cerró su ventana "REBA-Server";
  `logs/uvicorn.log` quedó congelado desde 11:47 porque esa instancia
  logueaba solo a consola.
- **Telemetría previa a la caída**: `dist_cm=-1.0` = **sin eco del
  HC-SR04** → aunque la placa vuelva, el trigger no dispara fotos ni
  bomba automática. Revisar cableado TRIG=14/ECHO=15 (divisor) y
  alimentación del sensor.

### Acciones aplicadas

- **Hotspot encendido por script**: `tools/enable_hotspot.ps1` (WinRT
  `NetworkOperatorTetheringManager.StartTetheringAsync`) — SSID
  `REBA-AP` 2.4 GHz, `192.168.137.1` restaurada. La ESP32 debe
  reconectar sola si está alimentada.
- **cloudflared reiniciado** (PID nuevo) — sigue reintentando; si el
  bloqueo de 7844 cede, el túnel vuelve solo. zrok
  (`reba-cam.shares.zrok.io`) siguió UP todo el tiempo (respaldo OK).
- **uvicorn relanzado oculto** (`Start-Process -WindowStyle Hidden`,
  `>> logs/uvicorn.log`) — ya no depende de ventana de consola y
  queda logueado en archivo.
- uvicorn "duplicado" (PID pequeño ~4 MB) es proceso normal de la app,
  no un huérfano — no tocar.

### Pendiente físico (no resoluble por software)

- [ ] **Alimentar/verificar la ESP32**: ~90 min sin contacto → probable
      batería 18650 agotada o placa apagada. Revisar LED de power,
      carga solar/USB. Si el hotspot lleva >5 min arriba y no aparece
      → portal `REBA-Setup` (`192.168.4.1`) o serie.
- [ ] **HC-SR04 sin eco** (`dist_cm=-1`): revisar TRIG/ECHO, divisor
      1k+2k, alimentación del sensor.
- [ ] Al volver la placa: prueba de bomba manual desde dashboard y de
      ciclo automático (3 fotos).
- [ ] Si `rebaserver.tech` sigue en 530: el bloqueo 7844 es de la red
      institucional — probar desde otra red o esperar; mientras tanto
      LAN (campo) + zrok (dashboard remoto) cubren las pruebas.

---

## 16-nov (~14:10) — Vista en vivo MJPEG (stream :8080 + proxy + panel)

### Implementado

- **Firmware** (`fotos_nc.ino`): servidor HTTP propio en **:8080** dentro
  de una tarea FreeRTOS `reba_stream` (core 0, prio 1) — `GET /stream`
  (MJPEG ~8 fps), `GET /capture` (JPEG suelto), `GET /status` (JSON).
  Comparte la cámara con los triggers vía `camMutex` (todos los
  fb_get/fb_return/deinit/re-init serializados) + flag `camReinit`.
  **Sin buffer intermedio**: la PSRAM de la placa es defectuosa
  (`ps_malloc` falla) → el frame se escribe directo al socket bajo el
  mutex (~ms; el único cliente real es el proxy del PC por LAN).
  `loop()` no se bloquea: sensor/cola/bomba/polls siguen durante el
  stream. Compila OK (1,144,896 B, 58% partición) →
  `firmware/fotos_nc.bin` ya sirve el build nuevo por `/firmware.bin`.
- **Server** (`main.py`): `GET /api/device/stream` → proxy MJPEG al
  `:8080` de la ESP32 (IP = último heartbeat, `?ip=` override; 503 sin
  IP, 502 si no responde/firmware viejo); `GET /api/device/snapshot` →
  JPEG suelto. Verificado end-to-end con mock local (30 frames OK).
- **Dashboard**: panel "En vivo — cámara" en la vista En vivo (fila
  completa en desktop, primero en móvil): `<img>` MJPEG con estados
  conectando/en vivo/reintentando, auto-retry 15 s, pausa con pestaña
  oculta. Degrada limpio con firmware sin stream.

### Pendiente

- [x] **Flashear la ESP32** con este build — hecho por USB (esptool,
      ambos slots OTA); ver sección 16-nov ~15:45.
- [x] Prueba E2E real cuando la placa esté encendida — verificado:
      `GET /api/device/snapshot` → 200 JPEG vía proxy →
      `ESP32:8080/capture` (~5.9 KB/frame).
- [x] Directo sin proxy (misma LAN): `http://<ip-esp32>:8080/stream` —
      verificado en `192.168.137.121:8080` (`/status` JSON,
      `/capture` JPEG).

---

## 16-nov (~15:15) — Incidencia resuelta: "dashboard inaccesible" desde el celular (IP confundida)

### Síntoma

El celular en `REBA-AP` no podía abrir `http://192.168.137.216` — el
navegador reportaba sitio inalcanzable. **No era una falla de red ni del
servidor.**

### Diagnóstico (evidencia)

- `arp -a` en la PC mostró dos clientes en el hotspot:
  `192.168.137.163` → MAC `f8:b3:b7` (**Espressif** = ESP32) y
  `192.168.137.216` → MAC `42:6c:1a` (*locally administered*/aleatoria
  = **el propio celular**, Android/iOS usan MAC randomizada).
- `192.168.137.216` rechazaba TCP en :80, :5000 y :8080 — ningún
  servicio escuchando ahí; era la IP del celular misma, no del server.
- La PC (host del hotspot) es siempre `192.168.137.1` y
  `http://192.168.137.1:5000/api/health` respondía 200; la ESP32
  seguía pollando `/api/device/config` y subiendo capturas — la LAN
  nunca estuvo caída.

### Causa raíz

Las IPs de los **clientes** del hotspot son DHCP y cambian entre
sesiones (la ESP32 fue `.161` antes, `.163` ahora). `192.168.137.216`
— probablemente vista como IP de la ESP32 en una sesión/log anterior —
esta vez le correspondía al celular. Abrirla en el navegador apuntaba
al propio teléfono, que no sirve nada.

### Mitigación

Abrir la URL correcta: `http://192.168.137.1:5000` (la PC; IP fija del
host ICS). `logs/uvicorn.log` registra el primer `GET /` desde
`.216` (~15:10) cargando dashboard + WS con 200 en todo.

### Lección / regla operativa

- **Nunca usar la IP de un cliente como si fuera fija**: el host del
  hotspot es siempre `192.168.137.1`; los clientes rotan
  (`.161` → `.163` → `.216`…). Para saber qué IP tiene la ESP32 ahora:
  `arp -a` (MAC Espressif) o `GET /api/device/status` → `device.ip`.
- Identificar dispositivos por MAC: `f8:b3:b7…` = ESP32 (Espressif);
  MAC con bit local (p.ej. `42:…`, `x2/x6/xA/xE` en el 1er octeto) =
  celular con MAC aleatoria.

### Estado colateral

- La ESP32 reinició (~15:12, uptime 1140→60 s; `boots=1`) pero **sigue
  con el firmware previo al stream**: no hubo `GET /firmware.bin` desde
  `.163` y su `:8080` sigue cerrado → `/api/device/stream` responde
  502. El flasheo del build `firmware/fotos_nc.bin` (stream :8080)
  sigue pendiente — vía `OTA` por serie o
  `SET ota_url=http://192.168.137.1:5000/firmware.bin`.

---

## 16-nov (~15:45) — Flasheo USB del firmware stream (:8080) — resuelto

### Resultado

Firmware nuevo flasheado y verificado end-to-end: serial muestra
`Stream MJPEG en :8080`; `GET http://192.168.137.121:8080/status` →
`{"ok":true,"heap":144016,"rssi":-49,"cam":1}`; proxy
`GET /api/device/snapshot` → 200 JPEG. ESP32 `EN LÍNEA`, IP nueva
`.121` (DHCP rotó tras re-asociar).

### Incidente durante el flasheo (documentado)

1. Se encoló `ota_url=http://192.168.137.1:5000/firmware.bin?v=…` en
   el config remoto (la anterior apuntaba a `rebaserver.tech`, caído
   por el bloqueo 7844 — nunca corría y entraba en backoff 10 min).
   El hotspot estaba apagado → sin WiFi → OTA no podía correr.
2. Primer intento USB: `esptool write_flash 0x10000` — write+hash OK,
   pero el board seguía sin stream: **boot por `ota_1` (0x1F0000),
   firmware viejo** — `otadata` (0xE000) tenía `ota_seq` apuntando al
   slot viejo.
3. Al re-encender el hotspot la placa ejecutó su **OTA propia**:
   `Update.begin()` apuntó al slot inactivo (app0) y lo borró; las
   aperturas del monitor serie (DTR glitch resetea la placa en cada
   open) interrumpieron la escritura → `app0` quedó todo `0xFF` y
   `verify-flash` falló por digest. El bootloader cayó a `app1`.
4. Fix: quitar `ota_url` del config remoto (evita OTA concurrente) +
   `write-flash 0x10000 + 0x1F0000` con el mismo bin → ambos slots
   verificados → hard reset → stream OK.

### Reglas operativas aprendidas

- **El monitor serie resetea la placa al abrir** (CH340 DTR). No abrir
  COM durante un OTA/flash en curso — aborta `Update` y deja el slot
  erizado (backoff RTC 10 min, sobrevive resets suaves).
- En esquema OTA (app0/app1), flashear **el slot activo o ambos** —
  solo app0 no basta si `otadata` boota `ota_1`. Verificar el slot
  activo leyendo `0xE000` (seq) o comparando cabeceras.
- `ota_url` remoto con una URL muerta martillea cada 10 min por el
  backoff — retirar la clave del config si el servidor destino cae.
- IP de la ESP32 rota por DHCP del hotspot: esta sesión fue
  `.163` → `.121`. `device.ip` del server sigue el heartbeat.

### Comando usado

```
python -m esptool --chip esp32 --port COM9 --baud 460800 \
  write-flash 0x10000 firmware/fotos_nc.bin 0x1F0000 firmware/fotos_nc.bin
```

---

## 16-nov (~17:05) — Firmware slim: stream :8080 eliminado + REBA-Setup siempre activo

### Motivo

La ESP32-CAM se calienta mucho sirviendo MJPEG continuo (el dashboard la
re-sondeaba cada 15 s) y el nodo perdía señal solo en el dispositivo
acuático (probable brownout de alimentación). Se pidió lo mínimo:
capturar → enviar → detectar.

### Cambios firmware (`fotos_nc.ino`)

- **Stream MJPEG eliminado**: `streamServer`/:8080, tarea `reba_stream`,
  handlers `/stream` `/capture` `/status`, `camMutex`, `camReinit` y
  `WiFiServer.h`. `tomarFoto()` ya no serializa con mutex (único
  consumidor). Compila 1,141,537 B (58%), ~3.4 KB más liviano.
- **`REBA-Setup` siempre disponible**: `iniciarPortal()` corre en
  `setup()` (no solo sin creds/tras 20 fallos), `setConfigPortalTimeout(0)`
  (sin timeout) y `revisarPortal()` reabre el AP tras cada guardado —
  AP `REBA-Setup` activo en paralelo al STA 24/7 para reconfigurar
  desde el celular (`http://192.168.4.1`) sin reiniciar.
- Flasheado por USB a **ambos slots** (0x10000 + 0x1F0000) en COM10.

### Cambios server/dashboard

- `main.py`: eliminados `/api/device/stream` y `/api/device/snapshot`
  (proxy MJPEG), `ESP32_STREAM_PORT`, `_esp32_ip`, imports `httpx` +
  `StreamingResponse`.
- `index.html`: panel "En vivo — cámara" eliminado.
- `app.js`: funciones `camStream*`/`updateCamStream`, estado `stream*`,
  intervalo de reintento 15 s y listener `visibilitychange` del stream.
- `styles.css`: reglas `.panel-cam`/`.cam-frame`/`.cam-note`.

### Verificado en banco

Serial: `AP 'REBA-Setup'` activo desde boot (`portal=1`), `Camara OK`,
sin línea de stream, `REBA listo`. Bin nuevo en `firmware/fotos_nc.bin`
(servido por `/firmware.bin` para futuras OTAs).
