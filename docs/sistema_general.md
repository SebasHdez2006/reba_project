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
residuo (`plastico`, `unicel`, `metal`) y, tras N fotos válidas, una
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
                        │   ├── /api/captures/*  álbum de fotos + export YOLO                │
                        │   ├── /api/perf /stats métricas y estadísticas                     │
                        │   ├── /firmware.bin    OTA                                         │
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
(render `.svg`/`.png` junto al fuente).

---

## 3. Subsistemas

### 3.1 Hardware / circuito

| Componente | Función | Detalle |
|---|---|---|
| ESP32-CAM (AI Thinker) | Captura fotos, sensa, acciona relé, sube datos | Cámara OV2640, WiFi 2.4 GHz, PSRAM para cola de fotos |
| HC-SR04 | Sensor ultrasónico de presencia | TRIG GPIO14, ECHO GPIO15 con divisor 1 kΩ+2 kΩ |
| LED flash GPIO4 | Iluminación de la escena (~0.3 s por captura) | Se enciende solo durante la foto |
| Relé GPIO13 | Conmuta la bomba | Módulo activo-bajo; **NC: relé energizado = bomba OFF, reposo = ON** |
| Bomba 12 V + XL6009 | Succión del residuo | Alimentada por elevador step-up desde 5 V USB |

Detalle de pines y cableado: `docs/componentes.md`, `docs/hardware.md`.

### 3.2 Firmware ESP32 — `esp32_firmware/fotos_nc/fotos_nc.ino`

Firmware Arduino único (~1500 líneas). Ciclo principal en `loop()`:

- **Sensor**: cada 60 ms lee el HC-SR04; necesita 2 lecturas seguidas
  dentro de `dist_min_cm`–`dist_max_cm` (0–23 cm) para armar el conteo
  de fotos; 3 fuera para rearmar.
- **Captura**: enciende flash 150 ms → `esp_camera_fb_get()` → JPEG →
  apaga flash. Si la cámara falla 5 veces seguidas re-inicializa.
- **Cola en PSRAM**: hasta `MAX_COLA=6` fotos pendientes; si se llena se
  descarta la más vieja (FIFO). Reintento de envío cada 15 s; una foto
  que falla 25 veces (~6 min) se descarta (anti poison).
- **Upload**: `POST {srvBase}/upload` con HTTP/1.1 mínimo propio
  (WiFiClient, sin librerías TLS — ver §5.3).
- **Bomba automática**: cada foto subida con detección válida incrementa
  el contador; al llegar a `pump_photos` (3) energiza el relé durante
  `pump_seconds` (10 s). En NC: `digitalWrite(RELE, LOW)` = bomba ON
  durante esos segundos, luego OFF.
- **Bomba manual remota**: el server encola `pump_cmd=<unix_ts>:<seg>`
  en la config remota; el ESP32 la ejecuta una sola vez y la confirma
  con `POST /api/device/ack`. Expira a los ~10 min (anti "comando
  fantasma").
- **Config remota**: `GET /api/device/config` cada 60 s — aplica
  `wifi_*`, `server_url*`, `pump_*`, `dist_*`, `ota_url`, `queue_clear`,
  `pump_cmd` sin reflashear.
- **Telemetría**: `POST /api/device/status` tras cada poll — uptime,
  heap, RSSI, cámara OK, distancia, cola, relé, bomba, config efectiva.
- **OTA**: `ota_url` (serie `OTA` o clave remota) → descarga `.bin`,
  flashea partición inactiva, reinicia, reporta `ota_last`.
- **Portal cautivo `REBA-Setup`**: si no hay credenciales WiFi o ~10 min
  sin conexión, abre AP `REBA-Setup` en `192.168.4.1` para configurar
  desde el celular (WiFi + URL del server).
- **Respaldo de WiFi**: si las credenciales nuevas no conectan en 90 s,
  revierte a las anteriores.

Valores por defecto y constantes: cabecera del `.ino`; configuración
efectiva en vivo: `GET /api/admin/device-config`.

### 3.3 Servidor FastAPI — `server/`

`uvicorn server.main:app --host 0.0.0.0 --port 5000` (un solo proceso).

| Módulo | Rol |
|---|---|
| `main.py` | Endpoints REST + WebSocket (lista completa abajo) |
| `inference.py` | Sesión ONNX (onnxruntime), pre/post-proceso, NMS |
| `database.py` | Capa SQLite (`data/REBA.db`) |
| `config.py` | Settings desde `.env` (`INFERENCE_CONFIDENCE`, `CLASS_NAMES`, retención…) |

Endpoints principales:

- **Device/ESP32**: `POST /upload` (foto→detección→DB), `GET /api/device/config` (pull de config), `POST /api/device/ack` (confirmación de comandos), `POST/GET /api/device/status` (telemetría), `GET /firmware.bin` (OTA).
- **Dashboard/datos**: `GET /api/captures*` (álbum + imágenes), `GET /api/detections/*`, `GET /api/pump/events`, `GET /api/stats`, `GET /api/stats/timeseries`, `GET /api/perf`, `GET /api/export/yolo`, `POST /api/pump/run` (manual), `DELETE /api/captures` (reset álbum/datos), `WS /ws`.
- **Admin**: `GET/POST /api/admin/device-config` (empuja config al ESP32), `GET /api/config`, `GET /api/pump-status`.
- **Health**: `GET /api/health` (`backend: onnx|none`, clases, modelo).

Persistencia: SQLite con capturas JPEG (original + anotado),
detecciones, eventos de bomba y telemetría. `CAPTURE_RETENTION=2000`
poda las fotos viejas automáticamente.

### 3.4 Dashboard — `static/`

SPA vanilla (sin framework): `index.html` + `app.js` + `styles.css`,
tema claro/oscuro. Secciones: **En vivo** (stream de capturas,
detecciones, eventos WS), **Rendimiento** (RTT, latencia ONNX, heartbeat,
uploads vía `/api/perf`), **Estado del circuito** (telemetría real del
ESP32), **Dispositivo** (config remota, "Activar ahora", vaciar cola),
**Álbum/Analítica** (fotos, export YOLO, reset de datos). Simulador de
ESP32 para pruebas sin hardware: `/simulador_esp32.html`.

### 3.5 Pipeline de IA — `ai_pipeline/`

Scripts Python (venv `.venv-reba`) para dataset, entrenamiento,
evaluación y calibración. Detalle por script: `docs/ai_pipeline.md`.

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
  `~/.cloudflared/`. Log: `logs/cloudflared.log`.
- **zrok (respaldo, solo HTTPS)**: share reservado `reba-cam` →
  `https://reba-cam.shares.zrok.io`. Solo dashboard/simulador — la ESP32
  no puede usarlo (sin TLS). Lo lanzan los `.bat` vía
  `tools/zrok_tunnel.ps1`; setup único con `setup_zrok.bat`.

Arranque completo: `campo.bat` (server + cloudflared + zrok) ·
retorno: `retomar.bat` (lo mismo + `--resume` de entrenamiento).

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
| `REBA_synthetic*` | Generados (`generate_*`) | Escenas sintéticas de agua + residuos, Copypaste/Augmentations |
| `REBA_ecosort` | HF EcoSort | 718 tr / 82 val reales (plastico, metal) |
| `REBA_taco` | TACO | 1293 imgs reales de basura en el wild, clases mapeadas agotadas |
| `REBA_floatwaste` | HF FloatWaste | Superficie de río real (AU/ID), COCO bboxes, 25 clases→3 REBA |
| `REBA_outdoor` | Mezcla final de entrenamiento | Unión curada de las anteriores + balanceo (`balance_oversample.py`) |
| `REBA_outdoor_eval` | Hard eval | Set difícil para medir F1/umbral |
| `REBA_real_val` | FloatWaste val completo | 796 imgs de río real — validación honesta de dominio |

### 4.3 Entrenamiento — `train_outdoor.py`

```bash
python ai_pipeline/train_outdoor.py --name REBA_outdoor_v5
# reanudar tras interrupción:
python ai_pipeline/train_outdoor.py --resume --name REBA_outdoor_v5
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
el ONNX v2 en producción) · `v3`, `v4` · `v5` (30/50 épocas al 15-nov —
**run en pausa, reanudable con `--resume`**).

### 4.4 Inferencia en producción — `server/inference.py`

- Backend `onnxruntime` (CPU), modelo `models/best.onnx` cargado **una
  vez al arrancar** el server (cambiar el ONNX requiere restart).
- Pre-proceso: resize 640×640, normalización, tensor NCHW.
- Post-proceso: umbral de confianza **`INFERENCE_CONFIDENCE=0.35`**
  (punto de máximo F1 medido con `tune_confidence.py` sobre
  `REBA_outdoor_eval`: P=0.79 / R=0.77 / F1=0.78), **NMS IoU=0.45**
  (`INFERENCE_IOU`), topes de detecciones.
- Cada `POST /upload` devuelve detecciones con `confidence`, `bbox`,
  `class`; el resultado queda en `detections` (SQLite) y se emite por
  WebSocket al dashboard. `inference_ms` se registra por captura
  (panel Rendimiento).
- Fallback: si el ONNX no carga, `backend: none` y las capturas se
  guardan sin detección (el flujo de fotos/bomba sigue).

### 4.5 Evaluación y calibración

| Script | Qué hace |
|---|---|
| `evaluate_outdoor.py` | Métricas por dataset (mAP, P/R/F1 por clase) — **requiere copiar `best.pt` del run a `models/best.pt` antes** |
| `tune_confidence.py` | Sweep de umbrales → curva P/R/F1 → recomienda `INFERENCE_CONFIDENCE` |
| `eval_upload_path.py` | Prueba el path real de producción (JPEG→server→ONNX→NMS→umbral) contra `REBA_real_val` |
| `val_snapshot.py` | Caché de done-count para `--resume` sin re-evaluar |
| `train_monitor.py` | Monitor GUI/CLI del run (épocas, mAP, best/last) |

Veredicto go/no-go y brecha de dominio sintético→agua real:
`docs/plan_post_v5.md`.

---

## 5. Flujos clave extremo a extremo

### 5.1 Captura automática con bomba

```
objeto <23 cm ×2 lecturas → flash+foto → cola PSRAM → POST /upload
→ ONNX detecta → detección válida cuenta → 3 fotos → relé ON 10 s → OFF
→ pump_event en DB → dashboard (WS + /api/pump/events)
```

### 5.2 "Activar ahora" (bomba manual remota)

```
dashboard POST /api/pump/run → server encola pump_cmd=<ts>:<seg> en device-config
→ ESP32 lo recoge en su poll (≤60 s) → acciona relé → POST /api/device/ack
→ dashboard muestra ejecutado+confirmado (expira ~10 min)
```

### 5.3 Config remota y failover de URL

`GET /api/device/config` cada 60 s aplica cualquier clave permitida
(`allowed_keys` en la respuesta). URLs: `server_url` (primaria LAN) +
`server_url_alt` (respaldo Internet). Si la primaria falla 3 polls
seguidos → conmuta a la alternativa; re-sondea la primaria cada 5 min
y vuelve si responde. **HTTP plano obligatorio**: TLS/mbedTLS provoca
FB-OVF en el driver de cámara (bug verificado en banco) — por eso la
ESP32 jamás recibe `https://`.

### 5.4 OTA

`ota_url` → ESP32 descarga `firmware.bin` por el server (local o
`rebaserver.tech`), valida, flashea partición inactesa y reporta
`ota_last` por telemetría.

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
| Respaldo dashboard (zrok) | `https://reba-cam.shares.zrok.io` | Internet — solo HTTPS |
| Simulador ESP32 | `…/simulador_esp32.html` (en cualquiera de las anteriores) | Sin hardware real |
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
campo.bat     REM arranque campo: server + cloudflared + zrok
retomar.bat   REM retorno: lo mismo + resume entrenamiento + monitor
```

Verificaciones:

```bash
curl http://localhost:5000/api/health        # backend: onnx, ok: true
curl http://rebaserver.tech/api/health       # túnel Cloudflare (HTTP)
curl https://rebaserver.tech/api/health      # túnel Cloudflare (HTTPS)
```

Logs: `logs/cloudflared.log` (túnel) · `logs/tunnel.log` +
`logs/zrok_url.txt` (zrok) · consola `REBA-Server` (uvicorn).
