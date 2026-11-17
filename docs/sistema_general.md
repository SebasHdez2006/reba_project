# REBA — Sistema general: subsistemas, modelo de IA y acceso

Documento de referencia del sistema completo: qué hace cada subsistema,
cómo se conectan y dónde está cada portal/servicio. Para detalles
operativos ver `docs/manual_uso.md`; para implementación paso a paso,
`docs/manual_implementacion.md`; bitácora en `PROGRESO.md`.

---

## 1. Qué es REBA

Sistema de monitoreo de residuos en superficie de agua. Una **ESP32-CAM**
montada en el borde (muelle/bote) vigila con un **HC-SR04** cuándo un
objeto entra al rango de detección, toma una foto, la envía a un
**servidor FastAPI en la PC**, un modelo **YOLOv8→ONNX** clasifica el
residuo (`plastico`, `unicel`, `metal`) y, tras N fotos tomadas, una
**bomba de 12 V** lo retira del agua. Todo se supervisa desde un
**dashboard web** con datos en vivo (WebSocket), telemetría del
circuito, métricas de rendimiento y control manual de la bomba.

Regla de arquitectura: el producto final funciona **100% offline en
campo** — la inferencia corre en la PC (ONNX local), sin APIs externas.
Internet solo se usa para acceso remoto del dashboard y del ESP32.

---

## 2. Vista de arquitectura

```
                        ┌─────────────────────────── PC (Windows) ───────────────────────────┐
                        │                                                                    │
  Celular/dashboard ────┤  static/index.html + app.js (SPA)                                   │
  (HTTPS/HTTP)          │        │                                                          │
                        │        ▼ WebSocket / REST                                          │
                        │  FastAPI :5000 ── server/main.py                                   │
                        │   ├── /upload          POST foto ESP32 → detección ONNX → SQLite    │
                        │   ├── /api/device/*    config remota, telemetría, ack              │
                        │   ├── /api/pump/*      eventos de bomba, comando manual            │
                        │   ├── /api/capture/run foto remota (botón "Tomar foto ahora")     │
                        │   ├── /api/captures/*  álbum de fotos + export YOLO                │
                        │   ├── /api/perf /stats métricas y estadísticas                     │
                        │   ├── /firmware.bin    OTA del ESP32                               │
                        │   ├── /firmware/*      binarios p/flasheo USB (bootloader, etc.)   │
                        │   └── /ws               eventos en vivo                            │
                        │        │                                                          │
                        │   inference.py → onnxruntime → models/best.onnx                   │
                        │   database.py  → SQLite data/REBA.db                              │
                        │                                                                    │
  ESP32-CAM ────────────┤  cloudflared (túnel nombrado)      zrok (respaldo, solo HTTPS)      │
  http://rebaserver.tech│        │                                │                           │
  http://192.168.137.1  └────────┴────────────────────────────────┴───────────────────────────┘
        :5000 (LAN REBA-AP)
```

Diagrama formal del software: `docs/diagramas/arquitectura_software.puml`
(render `.svg`/`.png` junto al fuente). En `docs/diagramas/uml/` hay 13
diagramas UML adicionales (clases, objetos, componentes, despliegue,
casos de uso, secuencia, etc.) con fuente `.puml` y render `.svg`/`.png`.

---

## 3. Subsistemas

### 3.1 Hardware / circuito

| Componente | Función | Detalle |
|---|---|---|
| ESP32-CAM (AI Thinker) | Captura fotos, sensa, acciona relé, sube datos | Cámara OV2640, WiFi 2.4 GHz; cola de fotos en RAM (la PSRAM de esta placa es defectuosa → fallback a heap interno) |
| HC-SR04 | Sensor ultrasónico de presencia | TRIG GPIO14, ECHO GPIO15 con divisor 1 kΩ+2 kΩ |
| LED flash GPIO4 | Iluminación de la escena (~0.3 s por captura) | Se enciende solo durante la foto |
| Relé GPIO13 | Conmuta la bomba | Módulo activo-bajo; **NC: relé energizado = bomba OFF, reposo = ON** |
| Bomba 12 V sumergible (AR-29209) | Succión del residuo | Relé NC la conmuta; 12 V desde XL6009 |
| Fuente de potencia | Autonomía | 18650 + TP4056 (recarga solar opcional) → XL6009 a 12.0 V; **el ESP32 usa 5 V dedicado, nunca el rail de 12 V** |

Cadena de potencia y notas críticas: `docs/componentes.md`, `docs/hardware.md`.

### 3.2 Firmware ESP32 — `esp32_firmware/fotos_nc/fotos_nc.ino`

Firmware Arduino único (~1600 líneas, flash ~58 % del
app OTA). Ciclo principal en `loop()`:

- **Sensor**: cada 60 ms lee el HC-SR04; necesita 2 lecturas seguidas
  dentro de `dist_min_cm`–`dist_max_cm` (0–23 cm) para armar el conteo
  de fotos; 3 fuera para rearmar.
- **Captura**: enciende flash ~150 ms → `esp_camera_fb_get()` → JPEG →
  apaga flash. Framebuffer en **DRAM** (la PSRAM de esta placa es
  defectuosa → fallback `ps_malloc`→`malloc`); si la cámara falla 5
  veces seguidas re-inicializa; health check silencioso cada 15 min.
- **Cola en RAM**: hasta `MAX_COLA=6` fotos pendientes; si se llena se
  descarta la más vieja (FIFO). Reintento de envío cada 15 s; una foto
  que falla 25 veces (~6 min) se descarta (anti poison).
- **Upload**: `POST {srvBase}/upload` con HTTP/1.1 mínimo propio
  (`httpSimple()` con `WiFiClient`, sin TLS — ver §5.3).
- **Bomba automática**: cada foto tomada incrementa el contador (el
  firmware no conoce el resultado ONNX — cuenta capturas); al llegar a
  `pump_photos` (3) energiza el relé durante `pump_seconds` (10 s). En
  NC: `digitalWrite(RELE, LOW)` = bomba ON durante esos segundos,
  luego OFF. `bombaApagar()` corre primero en `setup()` (estado
  seguro).
- **Bomba manual remota**: el server encola `pump_cmd=<unix_ts>:<seg>`
  en la config remota; el ESP32 la ejecuta una sola vez (no bloqueante,
  dedup RTC `lastPumpCmdId`) y la confirma con `POST /api/device/ack`.
  Expira a los ~10 min (anti "comando fantasma").
- **Foto remota**: `capture_cmd=<unix_ts>` (botón "Tomar foto ahora"
  del dashboard → `POST /api/capture/run`) dispara el mismo camino del
  trigger del sensor: flash + foto + cola + upload + contador de bomba;
  dedup RTC `lastCaptureCmdId` + ack. Mismo TTL de ~10 min.
- **Vaciado de cola**: `queue_clear=<unix_ts>` descarta las fotos
  pendientes una sola vez por timestamp (dedup RTC `lastQueueClear`).
- **Config remota**: `GET /api/device/config` cada 60 s — aplica
  `wifi_*`, `server_url`, `server_url_alt`, `pump_*`, `dist_*`,
  `ota_url`, `queue_clear`, `pump_cmd`, `capture_cmd` sin reflashear.
  Tras cada poll exitoso reporta telemetría por `POST
  /api/device/status` (uptime, boots, heap, RSSI, cámara, distancia,
  cola, progreso de bomba, pin del relé).
- **Failover de URL**: 3 polls fallidos seguidos → conmuta
  `server_url`↔`server_url_alt` (persiste en NVS `srv_act`); con la
  alternativa activa re-sondea la principal cada 5 min y vuelve si
  responde (detalle en §5.3).
- **OTA**: `ota_url` (serie `OTA` o clave remota) → descarga `.bin`,
  flashea partición inactiva (`Update`), reinicia, reporta `ota_last`
  (dedup: la misma URL no re-flashea). Requiere partition scheme con
  OTA; los `.bin` se sirven en `/firmware.bin` (OTA) y `/firmware/*`
  (flasheo USB por esptool/web-flasher).
- **Portal `REBA-Setup`**: AP abierto **siempre activo** desde el
  arranque (en paralelo al STA) en `192.168.4.1` — permite configurar
  WiFi + URL del server desde el celular en cualquier momento, sin
  reiniciar.
- **Respaldo de WiFi**: si las credenciales nuevas no conectan en 90 s,
  revierte a las anteriores.
- **Serie (115200)**: `HELP`, `GET`, `SET clave=valor` (mismas claves
  remotas, validación estricta), `CAPTURE` (foto manual), `STATUS`
  (diagnóstico), `OTA [url]`, `REBOOT`. Ojo: abrir el puerto resetea
  la placa (glitch DTR/RTS).

Valores por defecto y constantes: cabecera del `.ino`; configuración
efectiva en vivo: `GET /api/admin/device-config`. El `SRV_BASE_DEFAULT`
compilado apunta a un Portwarp ya descartado (§3.6) — en producción la
URL efectiva la fijan NVS + `device_config` remoto.

Detalle completo: `docs/firmware.md`; herramienta de banco
`tools/serial_reba.py` (monitor serie + aprovisionamiento).

### 3.3 Servidor FastAPI — `server/`

`uvicorn server.main:app --host 0.0.0.0 --port 5000` (un solo proceso).

| Módulo | Rol |
|---|---|
| `main.py` | Endpoints REST + WebSocket (lista completa abajo) |
| `inference.py` | Sesión ONNX (onnxruntime), pre/post-proceso, NMS |
| `database.py` | Capa SQLite (`data/REBA.db`) |
| `config.py` | Settings desde `.env` (`INFERENCE_CONFIDENCE`, `CLASS_NAMES`, retención…) |

Endpoints principales:

- **Device/ESP32**: `POST /upload` (foto→detección→DB), `GET /api/device/config` (pull de config `clave=valor`; sirve `pump_cmd`/`capture_cmd` solo si tienen <10 min — TTL anti "comando fantasma"), `POST /api/device/ack` (el ESP32 confirma `pump_cmd` o `capture_cmd` ejecutado → borra el override), `POST/GET /api/device/status` (telemetría del circuito), `GET /firmware.bin` (binario OTA), `/firmware/*` (binarios de flasheo USB: bootloader, partitions, `fotos_nc.bin`).
- **Dashboard/datos**: `GET /api/captures*` (álbum + imágenes anotadas u originales `?orig=1`), `GET /api/detections/*`, `GET /api/pump/events` (+ alias `/api/pump-events`), `GET /api/stats`, `GET /api/stats/timeseries`, `GET /api/perf`, `GET /api/export/yolo` (ZIP dataset), `POST /api/pump/run` (bomba manual), `POST /api/capture/run` (foto remota), `DELETE /api/captures` (reset álbum/datos), `WS /ws`.
- **Admin**: `GET/POST /api/admin/device-config` (empuja config al ESP32; whitelist `DEVICE_CONFIG_KEYS`), `GET /api/config`, `GET /api/pump-status`.
- **Health**: `GET /api/health` (`backend: onnx|none`, clases, modelo, `device.online` — heartbeat <150 s).

Persistencia: SQLite con capturas JPEG (original + anotado),
detecciones, eventos de bomba, telemetría y `device_config`
(overrides). `CAPTURE_RETENTION=2000` poda las fotos viejas
automáticamente (cada hora).

Ojo: el **simulador** `/simulador_esp32.html` usa los mismos endpoints
del dispositivo (`/api/device/config`, `/api/device/status`), así que
su polling también alimenta el heartbeat — un `device.online=true`
puede ser el simulador y no la placa física.

### 3.4 Dashboard — `static/`

SPA vanilla (sin framework): `index.html` + `app.js` + `styles.css`,
tema claro/oscuro (tokens CSS, Space Grotesk + JetBrains Mono
auto-hospedadas). Navegación por **3 vistas** — sidebar en escritorio,
píldora inferior en móvil:

- **En vivo**: *Última captura* (foto anotada, metadatos, tira de
  miniaturas, botón **"Tomar foto ahora"** → `POST /api/capture/run`),
  *Bomba* (progreso `fotos/N`, control manual con chips de duración +
  "Activar ahora", registro de eventos), *Rendimiento* (RTT, latencia
  ONNX avg/p95, heartbeat, uploads/h, uptime, clientes WS — `/api/perf`).
- **Analítica**: *Materiales acumulados* (KPIs + donut por clase),
  *Actividad* (barras por día/clase, selector 7/14/30 días), *Galería*
  (filtro por clase, paginación, lightbox con detecciones, **Exportar
  YOLO**, reset de datos).
- **Dispositivo**: *Config remota* del ESP32 (WiFi, URLs prim/sec,
  bomba, distancias, vaciar cola, OTA) y *Estado del circuito*
  (telemetría real: sensor, relé, cola, cámara, heap, RSSI).

Datos en vivo por WebSocket `/ws` (`hello`, `capture`, `detections`,
`pump_event`, `stats`, `pong`). Simulador de ESP32 para pruebas sin
hardware: `/simulador_esp32.html`. Detalle: `docs/dashboard.md`.

### 3.5 Pipeline de IA — `ai_pipeline/`

Scripts Python (venv `.venv-reba`) para todo el ciclo del modelo:
descarga de fuentes (`download_taco/ecosort/floatwaste/openverse`),
generación de datos (`generate_synthetic_dataset`,
`generate_copypaste_dataset`, `dataset_builder`, `filter_classes`),
auditoría y balanceo (`dedup_audit`, `clean_real_val`, `audit_*`,
`balance_oversample`, `dataset_stats`), entrenamiento
(`train_outdoor.py` + `outdoor_transforms.py`), monitoreo
(`train_monitor.py`, `val_snapshot.py`) y evaluación
(`evaluate_outdoor.py`, `eval_upload_path.py`, `tune_confidence.py`,
`build_outdoor_eval.py`). Detalle por script: `docs/ai_pipeline.md`.

### 3.6 Infraestructura de red

| Escenario | Cómo llega el ESP32 | Cómo llega el dashboard |
|---|---|---|
| Banco/campo con PC (REBA-AP, sin Internet) | `http://192.168.137.1:5000` (LAN directa) | `http://192.168.137.1:5000` o `http://localhost:5000` |
| ESP32 en otra red con Internet | `server_url_alt=http://rebaserver.tech` (failover automático) | `https://rebaserver.tech` |
| Provisioning sin credenciales | Portal `REBA-Setup` en `192.168.4.1` | — |

**Túneles** (ambos apuntan a `localhost:5000`):

- **Cloudflare nombrado (definitivo)**: dominio propio `rebaserver.tech`,
  `cloudflared` con `--protocol http2` (QUIC/UDP hace timeout en la red
  institucional). Sirve **HTTP plano en :80** → la ESP32 lo usa; y
  **HTTPS** → dashboard. Autostart: `tools/cloudflared_tunnel_hidden.vbs`
  en la carpeta Startup; `campo.bat`/`retomar.bat` lo relanzan si falta.
  Config: `tools/cloudflared_config.yml` + credenciales en
  `~/.cloudflared/`. Log: `logs/cloudflared.log`. Ojo: la red
  institucional ha bloqueado el edge `:7844` (túnel cae con HTTP 530);
  en ese caso LAN + zrok cubren las pruebas.
- **zrok (respaldo, solo HTTPS)**: share reservado `reba-cam` →
  `https://reba-cam.shares.zrok.io`. Solo dashboard/simulador — la ESP32
  no puede usarlo (sin TLS). Lo lanzan los `.bat` vía
  `tools/zrok_tunnel.ps1`; setup único con `setup_zrok.bat`.

**Descartados** (histórico en `docs/plan_tunel_fijo.md`): Portwarp —
listener público muerto y agente UWP sin API; el `SRV_BASE_DEFAULT`
del firmware aún apunta a él pero la config efectiva lo pisa. Oracle
VPS — `tools/vps_*.{sh,bat,vbs}` quedaron como scripts huérfanos (la
cuenta fue rechazada en verificación). playit.gg, Azure, DigitalOcean —
ver plan.

**Herramientas de campo**: `tools/enable_hotspot.ps1` enciende el
hotspot móvil `REBA-AP` por WinRT (Windows lo apaga por inactividad);
`tools/serial_reba.py` monitor serie + aprovisionamiento USB;
`build_firmware/` es la salida de `arduino-cli` (gitignored).

Arranque completo: `campo.bat` (server + cloudflared + zrok) ·
retorno: `retomar.bat` (lo mismo + `--resume` de entrenamiento — ojo:
reanudaría v5 y reexportaría el ONNX al terminar, ver §4.3).

---

## 4. Subsistema de visión artificial

### 4.1 Tarea y clases

Detección de objetos (no clasificación): el modelo localiza residuos en
la foto y asigna una de **3 clases** (el proyecto migró de 5→3 el
14-nov: `tela` y `organico` se eliminaron por falta de datos reales):

| class_id | Clase | Ejemplos |
|---|---|---|
| 0 | `plastico` | botellas PET, empaques, tapitas, bolsas |
| 1 | `unicel` | poliestireno / foam |
| 2 | `metal` | latas, aerosoles |

Entrada del modelo: **640×640 RGB**, un solo tensor `[1,3,640,640]`.

### 4.2 Datasets (`datasets/`)

Dos familias: **sintéticos** (generados/copypaste para volumen) y
**reales** (descargados con licencia, mapeados a las 3 clases). El set
de validación real `REBA_real_val` **nunca entra a entrenamiento**.
Auditoría completa de fuentes y licencias: `datasets/CATALOGO.md`.

| Dataset | Origen | Contenido |
|---|---|---|
| `REBA_synthetic` | Generado (`generate_synthetic_dataset.py`) | 360 imgs: 240 tr / 60 val / 60 test — escenas sintéticas de agua + residuos |
| `REBA_synthetic_boost` / `_boost2` | Generados (mismo script) | 180 + 240 imgs extra de train, sesgadas a clases raras (`--class-weights`) |
| `REBA_copypaste` | Generado (`generate_copypaste_dataset.py`) | 600 imgs: instancias reales pegadas sobre fondos de agua (escala/rotación/submersión) |
| `REBA_taco` | TACO (GitHub) | 1293 imgs reales de basura en el wild, clases mapeadas a las 3 REBA |
| `REBA_ecosort` | HF EcoSort | 800 imgs (718 tr / 82 val) reales, 10 clases→3 REBA |
| `REBA_floatwaste` | HF FloatWaste | 1549 imgs de superficie de río real (AU/ID), COCO bboxes, 25 clases→3 REBA |
| `REBA_outdoor` | Manifiesto generado | `data.yaml` que `train_outdoor.py` escribe al vuelo: train = las 7 fuentes de arriba, val/test = `REBA_synthetic` |
| `REBA_outdoor_eval` | Generado (`build_outdoor_eval.py`) | 120 imgs degradadas deterministas — eval de robustez |
| `REBA_real_val` | FloatWaste val limpio | 415 imgs de río real (val oficial depurado de near-duplicados por `clean_real_val.py`) — validación honesta de dominio, **nunca entrena** |

Internos: `_raw/` (descargas sin procesar), `_backup_labels_5class/`
(etiquetas previas a la migración 5→3).

### 4.3 Entrenamiento — `train_outdoor.py`

```bash
python ai_pipeline/train_outdoor.py --name REBA_outdoor_v6
# reanudar tras interrupción:
python ai_pipeline/train_outdoor.py --resume --name REBA_outdoor_v6
```

Parámetros (defaults): **YOLOv8n** (`yolov8n.pt`), 50 épocas, patience
20 (early stopping), imgsz 640, batch 8, device auto (CPU aquí). El
script serializa args/augmentations en el run → `--resume` restaura
epochs, LR schedule y Albumentations desde `last.pt`.

Al terminar: exporta `runs/detect/<name>/weights/best.pt` →
`models/best.onnx` (opset 12, imgsz 640) y **archiva** los pesos
previos en `models/archive/` como baseline. Si el run ya completó las
épocas, `--resume` solo reexporta el ONNX.

Historial de runs: `runs/detect/REBA_outdoor_v2` (interrumpido, exportó
el ONNX v2) · `v3`, `v4` · **`v5` — cortado en época 30 por decisión
(15-nov noche, para la entrega del 16-nov); su `best.pt` de la época 30
(mAP50-95=0.8513 en val) es el modelo en producción** — `models/best.onnx`
exportado el 15-nov. `--resume` sobre v5 reanudaría las épocas 31–50 y
al terminar reexportaría `best.onnx`/`best.pt` (archivando los actuales
en `models/archive/`).

### 4.4 Inferencia en producción — `server/inference.py`

- Backend `onnxruntime` (CPU), modelo `models/best.onnx` cargado **una
  vez al arrancar** el server (cambiar el ONNX requiere restart). Al
  cargar valida que el nº de clases del ONNX coincida con `CLASS_NAMES`.
- Pre-proceso: **letterbox** 640×640 (resize con padding gris 114,
  estilo YOLO — conserva el aspecto), BGR→RGB, /255, tensor NCHW.
- Post-proceso: umbral de confianza **`INFERENCE_CONFIDENCE=0.25`**
  (máx. F1 medido con `tune_confidence.py` sobre `REBA_real_val` —
  set acuático real: F1=0.524, P=0.486 / R=0.568; el 0.35 anterior se
  calibró sobre `REBA_outdoor_eval` con el modelo viejo), **NMS OpenCV
  IoU=0.45** (`INFERENCE_IOU`).
- Latencia ~86-104 ms/frame; por el path real `/upload` ~150 ms media
  (p95 ~290 ms) en esta CPU.
- Cada `POST /upload` devuelve detecciones con `confidence`, `bbox`,
  `class`; el resultado queda en `detections` (SQLite) y se emite por
  WebSocket al dashboard. `inference_ms` se registra por captura
  (panel Rendimiento).
- Fallback: si el ONNX no carga (o las clases no coinciden),
  `backend: none` y las capturas se guardan sin detección (el flujo de
  fotos/bomba sigue).

### 4.5 Evaluación y calibración

| Script | Qué hace |
|---|---|
| `evaluate_outdoor.py` | Métricas por dataset (clean/outdoor/taco_real/**real_water**: mAP, P/R/F1 por clase) → `outdoor_training_report.md`. **Requiere copiar `best.pt` del run a `models/best.pt` antes** |
| `tune_confidence.py` | Sweep de umbrales → curva P/R/F1 → recomienda `INFERENCE_CONFIDENCE` |
| `eval_upload_path.py` | Prueba el path real de producción (JPEG→server→ONNX→NMS→umbral) contra cualquier dataset → `eval_upload_report*.json` |
| `build_outdoor_eval.py` | Genera `REBA_outdoor_eval` (degradadas deterministas de val+test) |
| `val_snapshot.py` | `model.val()` sobre `weights/last.pt` → JSON por clase + speed (lo invoca `train_monitor` 1×/época) |
| `train_monitor.py` | Monitor GUI/CLI del run (épocas, losses, mAP, telemetría HW, ETAs) |
| Descarga/generación | `download_taco/ecosort/floatwaste/openverse`, `generate_synthetic_dataset`, `generate_copypaste_dataset`, `dataset_builder`, `filter_classes` (migración 5→3) |
| Auditoría | `dedup_audit` (near-duplicados globales), `clean_real_val` (fugas train↔val), `audit_labels`, `audit_pixels`, `dataset_stats`, `balance_oversample` |

Veredicto go/no-go y brecha de dominio sintético→agua real:
`docs/plan_post_v5.md` (v5-ép30 desplegado con mAP50-95=0.329 /
F1=0.524 en `REBA_real_val` — bajo los pisos pero mejor que el
incumbente; la captura de campo alimenta el siguiente ciclo).

---

## 5. Flujos clave extremo a extremo

### 5.1 Captura automática con bomba

```
objeto <23 cm ×2 lecturas → flash+foto → cola RAM → POST /upload
→ ONNX detecta y persiste (el conteo de bomba es por fotos tomadas)
→ 3 fotos → relé ON 10 s → OFF → pump_event en DB → dashboard (WS)
```

### 5.2 Comandos remotos de un solo uso (bomba y foto)

Mismo canal para ambos: el server escribe el override en
`device_config` (`POST /api/pump/run` → `pump_cmd=<unix_ts>:<seg>`;
`POST /api/capture/run` → `capture_cmd=<unix_ts>`), viaja en el poll
`GET /api/device/config` del ESP32 (≤60 s), expira a los ~10 min si no
se recoge (TTL en el servidor), el firmware lo ejecuta una sola vez
(dedup RTC) y confirma con `POST /api/device/ack` — que borra
`pump_cmd` **o** `capture_cmd`. En el dashboard: "Activar ahora" y
"Tomar foto ahora".

### 5.3 Config remota y failover de URL

`GET /api/device/config` cada 60 s aplica cualquier clave permitida
(`allowed_keys` en la respuesta). URLs: `server_url` (primaria LAN
`http://192.168.137.1:5000`) + `server_url_alt` (respaldo Internet
`http://rebaserver.tech`). Si la primaria falla 3 polls seguidos →
conmuta a la alternativa; re-sondea la primaria cada 5 min y vuelve si
responde (elección persistida en NVS `srv_act`). **HTTP plano
obligatorio**: TLS/mbedTLS provoca FB-OVF en el driver de cámara (bug
verificado en banco) — por eso la ESP32 jamás recibe `https://`.

### 5.4 OTA y flasheo USB

`ota_url` → ESP32 descarga `firmware.bin` por el server (local o
`rebaserver.tech`), valida, flashea la partición inactiva (`Update`),
reinicia y reporta `ota_last` por telemetría (dedup NVS: la misma URL
no re-flashea). Para flasheo presencial los binarios completos
(`bootloader.bin`, `partitions.bin`, `fotos_nc*.bin`) se sirven en
`/firmware/` — descargables por HTTP plano o visibles para
esptool/web-flasher; `tools/serial_reba.py` asiste por puerto COM.
En esquema OTA conviene flashear **ambos slots** (o verificar el activo
en `otadata` 0xE000) — lección del incidente del 16-nov.

---

## 6. Enlaces y portales

### 6.1 Servicios del proyecto

| Servicio | URL | Acceso |
|---|---|---|
| Dashboard local | `http://localhost:5000` | Esta PC |
| Dashboard LAN (celular en REBA-AP) | `http://192.168.137.1:5000` | Misma LAN/hotspot |
| **Dashboard remoto (principal)** | `https://rebaserver.tech` | Internet — fija para siempre |
| **ESP32 remoto (server_url_alt)** | `http://rebaserver.tech` | Internet — HTTP plano |
| OTA remoto | `http://rebaserver.tech/firmware.bin` | Internet |
| Binarios flasheo USB | `…/firmware/` (`bootloader.bin`, `partitions.bin`, `fotos_nc*.bin`) | esptool / web-flasher |
| Respaldo dashboard (zrok) | `https://reba-cam.shares.zrok.io` | Internet — solo HTTPS |
| Simulador ESP32 | `…/simulador_esp32.html` (en cualquiera de las anteriores) | Sin hardware real — **ojo: su polling enciende el heartbeat `device.online`** |
| Health check | `…/api/health` | Diagnóstico (`backend: onnx`) |
| Portal provisión ESP32 | `http://192.168.4.1` | Celular en WiFi `REBA-Setup` |

### 6.2 Portales de administración

| Portal | URL | Para qué |
|---|---|---|
| Cloudflare dashboard | `https://dash.cloudflare.com` | Dominio `rebaserver.tech`, zona DNS, túnel `reba` |
| zrok dashboard | `https://dashboard.zrok.io` | Cuenta/consultas del share `reba-cam` |
| GitHub repo | `https://github.com/SebasHdez2006/reba_project` | Código, docs, historial |

### 6.3 Datos y modelos (fuentes externas)

| Recurso | URL |
|---|---|
| Catálogo/auditoría de datasets | `datasets/CATALOGO.md` |
| FloatWaste (HF) | `https://huggingface.co/datasets/OliviaWuYJ/sc-floatwaste` |
| EcoSort (HF) | Hugging Face `EcoSort` (ver catálogo) |
| TACO | `https://github.com/pedropro/TACO` |
| Ultralytics YOLOv8 | `https://docs.ultralytics.com` |
| Cloudflare tunnel docs | `https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/` |
| zrok docs | `https://docs.zrok.io` |

---

## 7. Operación rápida

```bat
campo.bat                 REM arranque campo: server + cloudflared + zrok
retomar.bat               REM retorno: lo mismo + resume v5 + monitor
detener_entrenamiento.bat REM detiene el run de entrenamiento (backup .bak)
monitor_entrenamiento.bat REM monitor GUI del run (épocas, mAP, HW)
setup_zrok.bat            REM setup único del share zrok (una vez)
```

> **Ojo con `retomar.bat`**: lanza `train_outdoor.py --resume --name
> REBA_outdoor_v5`. Como v5 quedó cortado en la época 30 de 50,
> `--resume` **reanudará el entrenamiento** (épocas 31–50, horas en CPU)
> y al terminar **reexportará `models/best.onnx`/`best.pt`** — el modelo
> en producción cambiaría solo. Si se quiere conservar el v5-ép30
> desplegado, comentar la línea de entrenamiento en `retomar.bat` o
> borrar la ventana `REBA-Train` al arrancar.

Apoyo de campo: `tools/enable_hotspot.ps1` enciende `REBA-AP` si
Windows lo durmió · `tools/serial_reba.py` monitor/aprovisionamiento
serie de la ESP32 · `tools/vps_*` son scripts huérfanos del VPS Oracle
descartado (§3.6).

Verificaciones:

```bash
curl http://localhost:5000/api/health        # backend: onnx, ok: true, device.online
curl http://rebaserver.tech/api/health       # túnel Cloudflare (HTTP — el de la ESP32)
curl https://rebaserver.tech/api/health      # túnel Cloudflare (HTTPS — dashboard)
curl https://reba-cam.shares.zrok.io/api/health   # respaldo zrok
```

Logs: `logs/cloudflared.log` (túnel CF) · `logs/tunnel.log` +
`logs/zrok_url.txt` + `logs/uvicorn.log` (zrok/server oculto) ·
consola `REBA-Server` (uvicorn interactivo) · consola `REBA-Train`
(entrenamiento).
