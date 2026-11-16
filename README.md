# REBA

Plataforma flotante autónoma de recolección de residuos acuáticos.

La **ESP32-CAM** vigila el área de la cesta con un sensor ultrasónico (0–23 cm). Cuando un objeto entra al rango, enciende el flash, toma una foto JPEG y la envía por **HTTP POST** a la PC. Si no hay Wi-Fi o el servidor no responde, la foto queda en una **cola en RAM** y se reintenta después. Al acumular **3 fotos**, la bomba de 12 V corre durante **10 s** (relé cableado en **NC**). La PC recibe cada foto, la clasifica con **YOLOv8/ONNX local**, guarda imagen y detecciones en SQLite y actualiza el dashboard por WebSocket. Todo funciona en la red local, sin Internet; el túnel Cloudflare **`rebaserver.tech`** es el acceso remoto opcional (HTTPS dashboard, HTTP para la ESP32 con failover automático de `server_url`).

**Estado:** modelo `v5` (época 30) desplegado en producción — ver [PROGRESO.md](PROGRESO.md) y [checklist_de_entrega.md](checklist_de_entrega.md).

## Características principales

- **Detección ultrasónica con debounce** — HC-SR04 a 0–23 cm, 2 lecturas dentro / 3 fuera; ignora ruido y salpicaduras.
- **Cola anti-pérdida en el edge** — hasta 6 fotos en RAM con reintento cada 15 s; descarte anti poison-pill (HTTP 4xx o ≥25 intentos). La bomba y el trigger siguen operando aunque la PC esté apagada.
- **IA 100% local** — YOLOv8n→ONNX en CPU (~50–130 ms/foto), 3 clases: `plastico`, `unicel`, `metal`. Sin APIs ni costos en runtime.
- **Dashboard en vivo** — SPA sin build (HTML+CSS+JS vanilla), WebSocket `/ws`, tema claro/oscuro, galería paginada con lightbox, gráficas Canvas 2D, telemetría del circuito y métricas de desempeño.
- **Bomba manual remota** — `pump_cmd` con TTL de 10 min, dedup en RTC y ack (`/api/device/ack`); ejecución no bloqueante en la ESP32.
- **Config sin reflashear** — WiFi, `server_url`, bomba y distancias por portal `REBA-Setup`, serie `SET` o dashboard; validación estricta + reversión automática.
- **Failover de servidor** — `server_url`/`server_url_alt` en NVS: tras 3 polls fallidos la ESP32 conmuta sola (ej. LAN → túnel) y re-sondea la principal cada 5 min.
- **OTA por HTTP plano** — `ota_url` remota o `OTA` por serie: descarga `GET /firmware.bin`, flashea la partición inactiva y reinicia (dedup `ota_last`).
- **Telemetría del circuito** — `POST /api/device/status` tras cada poll (~60 s): uptime, heap, RSSI, cámara, cola, relé.
- **Acceso remoto opcional** — túnel nombrado Cloudflare `rebaserver.tech` (HTTP :80 para la placa, HTTPS :443 para el dashboard); zrok como respaldo.
- **Simulador del nodo** — `static/simulador_esp32.html` emula la ESP32-CAM con la cámara del celular (mismo protocolo HTTP).
- **Dataset re-entrenable** — cada captura guarda JPEG anotado + crudo; **Exportar YOLO** empaqueta imágenes + labels + `data.yaml` en ZIP; `DELETE /api/captures` resetea datos.

---

## Arquitectura

```
        Objeto entra a 0-23 cm
                │
                ▼
┌─────────────────────────────┐      POST /upload (JPEG + X-Evento)
│  ESP32-CAM (fotos_nc.ino)   │ ────────────────────────────────►
│  ultrasónico + flash +      │      reintento c/15 s si falla
│  cola RAM (máx. 6)          │         ┌────────────────────────┐
│  failover server_url(_alt)  │         │  PC — FastAPI (:5000)  │
│  OTA · telemetría · NVS     │◄────────│  ├─ ONNX YOLOv8 local  │
│  cada 3 fotos →             │  config │  ├─ SQLite (fotos+clases)
│  bomba ON 10 s (relé NC)    │  c/60 s │  ├─ /firmware.bin (OTA)│
└───────────┬─────────────────┘  +status│  └─ WS /ws ──► static/ │
            │                         └──────────▲─────────────┘
   WiFi 2.4 GHz                                 │ cloudflared
   LAN directa :5000 ───────────────────────────┘ (túnel nombrado)
   o túnel Cloudflare rebaserver.tech :80           ↑ HTTPS
                                    Dashboard local o remoto
```

| Capa | Tecnología |
|------|------------|
| Firmware | C++ Arduino, AI-Thinker ESP32-CAM (`esp32_firmware/fotos_nc/`) |
| Servidor | Python 3.10+, FastAPI, OpenCV, ONNX Runtime |
| IA | YOLOv8n → `models/best.onnx` v5 época 30 (offline, CPU) |
| UI | HTML/CSS/JS + WebSocket (tema claro/oscuro) |
| Datos | SQLite local, incluidas las fotos clasificadas |
| Acceso remoto | cloudflared → `rebaserver.tech` (+ zrok de respaldo) |

En campo la ESP32 y la PC comparten una **red Wi-Fi de 2.4 GHz** sin Internet; si la placa está en una red con Internet, el failover la lleva por el túnel. La ESP32 es cliente: solo sube fotos; no expone endpoints. El servidor, el modelo, la base de datos y el dashboard corren en la PC.

---

## Stack tecnológico — herramientas utilizadas

### Diseño e interfaz (dashboard `static/`)

| Herramienta | Versión / tipo | Uso en REBA |
|-------------|----------------|-------------|
| HTML5 + CSS3 | Estándar, sin framework | Estructura semántica y design system propio (superficies planas, tokens `#090D12`/`#111720`/`#18212E` + tema claro) |
| JavaScript (vanilla) | ES2020+, sin build | Todo `app.js`: WebSocket, fetch, DOM, estado — cero dependencias externas |
| Canvas 2D API | Nativo del navegador | Donut de materiales y barras apiladas de actividad (con DPR adaptable) |
| WebSocket API | Nativo | Canal `/ws` en vivo (capturas, detecciones, eventos de bomba) |
| SVG inline | — | Iconos de navegación, botón exportar, lightbox |
| Space Grotesk + JetBrains Mono | woff2 variables auto-hospedadas (`static/fonts/`) | Interfaz y datos técnicos; `tabular-nums` en métricas. Sin CDN → funciona offline |
| Diseño responsivo | Media queries CSS | Móvil primero: bottom nav <1024px, grid 12 col. ≥1024px; toggle claro/oscuro (`localStorage` + `prefers-color-scheme`) |

Referencias de diseño aplicadas: patrones de telemetría de Teenage Engineering, dashboards oscuros tipo Linear/Vercel y Grafana (elevación por luminancia, hairlines 1px, acento único).

### Backend y servidor (`server/`)

| Herramienta | Versión | Uso en REBA |
|-------------|---------|-------------|
| Python | 3.10+ (probado 3.13) | Runtime del servidor |
| FastAPI | ≥0.115 | API REST + WebSocket + estáticos del dashboard |
| Uvicorn | ≥0.32 (`[standard]`) | Servidor ASGI en `0.0.0.0:5000` |
| SQLite | embebido, WAL + FK | `data/REBA.db`: captures (doble JPEG), detections, counters, pump_events, device_config |
| Pydantic Settings | ≥2.5 | `.env` tipado (`server/config.py`) |
| httpx | ≥0.27 | Cliente HTTP en scripts de evaluación |

### Visión artificial e IA (`ai_pipeline/` + `server/inference.py`)

| Herramienta | Versión | Uso en REBA |
|-------------|---------|-------------|
| Ultralytics YOLOv8n | ≥8.3 | Entrenamiento (`train_outdoor.py`) y exportación ONNX; pesos base `yolov8n.pt` |
| ONNX Runtime | ≥1.19 | Inferencia CPU local en producción (~50–130 ms/foto) |
| ONNX (`opset=12`, `simplify`) | ≥1.16 | Export del modelo; `onnxslim` para simplificar |
| Albumentations | ≥1.4.15 | Pipeline de augmentations "outdoor" (brillo, sombra, niebla, lluvia, blur, HSV, dropout) |
| NumPy | ≥1.26 | Pre/post-procesamiento de tensores |
| OpenCV (headless) | ≥4.10 | Decodificación JPEG, validación magic bytes, dibujo de cajas anotadas |

Scripts del pipeline: `train_outdoor.py` (entrena+exporta), `train_monitor.py` (GUI del train), `evaluate_outdoor.py` (compara pesos), `eval_upload_path.py` (evalúa el path real `/upload`), `tune_confidence.py` (umbral óptimo), `build_outdoor_eval.py` (set degradado), `balance_oversample.py` (oversampling de clases raras), `download_taco.py` / `download_ecosort.py` / `download_floatwaste.py` / `generate_synthetic_dataset.py` / `generate_copypaste_dataset.py` (datasets), `audit_labels.py` / `audit_pixels.py` / `dedup_audit.py` / `clean_real_val.py` / `filter_classes.py` / `dataset_stats.py` / `val_snapshot.py` (curación y auditoría).

### Datos y datasets (`datasets/`)

| Herramienta / fuente | Uso en REBA |
|----------------------|-------------|
| `REBA_synthetic` (propio) | 240 tr / 60 val / 60 test — escenas acuáticas sintéticas generadas por script |
| `REBA_synthetic_boost` + `REBA_synthetic_boost2` (propio) | 180 + 240 train extra — refuerzo de clases raras |
| TACO (Trash Annotations in Context, real) | ~1300 imágenes vía `download_taco.py`, mapeadas a las clases REBA |
| EcoSort (Hugging Face, real) | 718 tr / 82 val vía `download_ecosort.py` — basura real fusionada (Open Images + TACO + Roboflow), remapeada a `plastico`/`metal` |
| FloatWaste (Hugging Face, real) | ~1,550 train vía `download_floatwaste.py` — residuos **flotando en ríos** (CC BY 4.0); su split val → `REBA_real_val` (415 imgs limpias de fugas) = set de validación real |
| `REBA_copypaste` (propio) | 600 imgs copy-paste de recortes reales sobre fondos de agua |
| `REBA_outdoor_eval` (propio) | 120 copias degradadas deterministas para medir robustez |
| Formato YOLO | Labels `class cx cy w h` normalizados; export del dashboard en ZIP re-entrenable |
| Openverse + OpenAI Vision | **Solo bootstrap opcional** (`download_openverse_dataset.py`, `dataset_builder.py`) — nunca en producción |

### Firmware y embebido (`esp32_firmware/fotos_nc/`)

| Herramienta | Versión | Uso en REBA |
|-------------|---------|-------------|
| Arduino IDE 2.x / arduino-cli | — | Compilación y flasheo del sketch |
| Core `esp32` (Espressif) | 3.3.x, placa `esp32cam` | Framework Arduino para ESP32-CAM |
| C++ (sketch `.ino`) | — | Todo el firmware: sensor, cámara, cola, HTTP, bomba, NVS |
| WiFiManager | 2.x | Portal de aprovisionamiento `REBA-Setup` |
| Preferences (NVS) | — | Persistencia de WiFi, `server_url`(+`_alt`), bomba, distancias, `ota_last` |
| `WiFiClient` TCP plano | — | HTTP sin `HTTPClient.h`/mbedTLS (ahorra ~122 KB flash, evita FB-OVF) |
| `Update` (ESP32 core) | — | OTA: flashea el `.bin` en la partición inactiva y reinicia |

### Hardware

| Componente | Rol |
|------------|-----|
| AI-Thinker ESP32-CAM (OV2640) | Captura JPEG + Wi-Fi + lógica |
| HC-SR04 | Trigger ultrasónico 0–23 cm (ECHO vía divisor 1k+2k) |
| Relé 1 canal + bomba 12 V | Conmuta ciclos de bombeo (cableado NC) |
| XL6009 + TP4056 + 18650 + panel solar | Potencia: boost a 12 V y carga solar |
| Fuente 5 V dedicada | Alimentación del ESP32 (nunca desde el boost) |

### Desarrollo y verificación

| Herramienta | Uso en REBA |
|-------------|-------------|
| Git | Control de versiones del repo |
| VS Code | Editor del proyecto |
| curl | Pruebas de `/upload`, `/api/*` y comandos serie |
| Playwright (MCP) | Verificación automatizada del dashboard (viewports 375×812 y 1280×800, consola, tooltips, contraste) |
| Multímetro | Ajuste del boost a 12 V y verificación de conmutación del relé |

---

## Datos que debes configurar

| Dato | Formato | Default / ejemplo | Dónde se configura |
|------|---------|-------------------|--------------------|
| **IP del PC (server)** | IPv4, ej. `192.168.1.50`. Obtenerla: `ipconfig` (Windows) → "Dirección IPv4" de la Wi-Fi conectada a la misma red | `192.168.1.3` | `server_url` del ESP32 (NVS) — **sin reflashear**: dashboard → Dispositivo, portal `REBA-Setup` o serie `SET server_url=…` |
| **`server_url`** (ESP32) | `http://<IP_PC>:<PUERTO>` (sin `/upload` — el firmware lo agrega) | `http://192.168.1.3:5000` | NVS `srv` + overrides remotos |
| **`server_url_alt`** (ESP32) | `http://…` ≤96 chars — respaldo del failover | `http://rebaserver.tech` (túnel) | NVS `srv_alt` + overrides remotos; tras 3 polls fallidos el ESP32 conmuta solo y re-sondea la principal c/5 min |
| **`ota_url`** (ESP32) | `http://…/*.bin` ≤128 chars | `http://rebaserver.tech/firmware.bin` | Override remoto — OTA con dedup `ota_last`; vaciar la cola: `queue_clear=<unix_ts>` |
| **`wifi_ssid` / `wifi_pass`** (ESP32) | Cadena; **solo 2.4 GHz** (el ESP32 no soporta 5 GHz) | — | Portal `REBA-Setup`, serie o dashboard → Dispositivo |
| **`HOST` / `PORT`** (server) | `0.0.0.0` (escucha en toda la LAN) / entero | `0.0.0.0` / `5000` | `.env` — el puerto **debe coincidir** con `server_url` del ESP32 |
| **Dashboard** | `http://<IP_PC o localhost>:<PUERTO>` | `http://localhost:5000` | Navegador en la misma LAN |
| **`MODEL_PATH`** | Ruta relativa o absoluta al `.onnx` | `models/best.onnx` | `.env` |
| **`INFERENCE_CONFIDENCE` / `INFERENCE_IOU`** | 0.0–1.0 | `0.35` / `0.45` | `.env` |
| **`CLASS_NAMES`** | CSV, orden = class_id | `plastico,unicel,metal` | `.env` |
| **`PUMP_TRIGGER_PHOTOS` / `PUMP_RUN_SECONDS`** | entero / segundos | `3` / `10` — **informativo**: espejar `pump_photos`/`pump_seconds` del ESP32 | `.env` + config remota del dispositivo |
| **`CAPTURE_RETENTION`** | entero (0 = sin límite) | `2000` | `.env` |
| **`DATABASE_PATH`** | Ruta SQLite | `data/REBA.db` | `.env` |
| **Rango ultrasónico** | cm | `0–23` (`dist_min_cm`/`dist_max_cm` en el ESP32; `DETECT_*_CM` en `.env` para display) | config remota + `.env` |
| **Pines** | GPIO | RELAY=13, TRIG=14, ECHO=15 (divisor), FLASH=4 | `#define` en `fotos_nc.ino` (compilados) |
| **Contrato `/upload`** | `POST`, body = JPEG binario, `Content-Type: image/jpeg`, header opcional `X-Evento: ingreso`. Respuesta **2xx** → la foto sale de la cola del ESP32 | — | firmware + server |

> **Sin reflasheo:** desde el firmware v2, WiFi, `server_url`, bomba y distancias se cambian en caliente por portal `REBA-Setup`, comandos serie o el panel **Dispositivo** del dashboard — con validación estricta y reversión automática si la config nueva falla. Solo los pines y la lógica NC/ACTIVE-LOW requieren recompilar.
>
> **Recomendación:** configura una **IP estática o reserva DHCP** para el PC en el router — la URL queda guardada en el ESP32.

---

## Estructura del repositorio

```
REBA/
├── README.md                 ← este archivo
├── PROGRESO.md               ← bitácora de sesiones y estado del proyecto
├── checklist_de_entrega.md   ← plan de cierre y checklist de la entrega
├── outdoor_training_report.md← métricas del entrenamiento outdoor
├── .env.example              ← plantilla de configuración del servidor
├── .env                      ← config local (no versionada)
├── requirements.txt
├── campo.bat                 ← arranque campo: server + túnel Cloudflare + zrok
├── retomar.bat               ← retorno: server + túneles + --resume v5 + monitor
├── detener_entrenamiento.bat ← respaldo de pesos + stop seguro del train
├── monitor_entrenamiento.bat ← launcher del monitor GUI de entrenamiento
├── setup_zrok.bat            ← setup único del túnel zrok (respaldo, URL fija)
├── tools/
│   ├── run_cloudflared.bat       ← túnel nombrado Cloudflare → rebaserver.tech
│   ├── cloudflared_tunnel_hidden.vbs ← launcher oculto (Startup folder)
│   ├── cloudflared_config.yml    ← ingress del túnel (localhost:5000)
│   ├── zrok_tunnel.ps1           ← lanza el share zrok headless (respaldo)
│   └── vps_*.{sh,bat,vbs}        ← scripts Oracle VPS (reserva, sin usar)
├── docs/
│   ├── sistema_general.md    ← visión general: subsistemas, IA y enlaces
│   ├── manual_implementacion.md ← implementación E2E (hardware→campo)
│   ├── manual_uso.md         ← operación del dashboard
│   ├── manual_alcances.md    ← alcance, limitaciones y métricas
│   ├── hardware.md           ← esquema eléctrico, BOM, relé NC, divisor ECHO
│   ├── firmware.md           ← flasheo, NVS, comandos serie, pump_cmd, OTA
│   ├── server.md             ← instalación, API REST/WS, acceso remoto
│   ├── ai_pipeline.md        ← dataset y entrenamiento outdoor
│   ├── dashboard.md          ← referencia técnica del frontend
│   ├── componentes.md        ← función/decisión/uso por componente
│   ├── preguntas_pruebas.md  ← banco de preguntas de verificación
│   ├── plan_*.md             ← planes: túnel fijo, apagado/retorno, nube, post-v5
│   └── diagramas/            ← arquitectura + 13 UML (.puml/.png/.svg)
├── esp32_firmware/
│   └── fotos_nc/
│       └── fotos_nc.ino      ← sketch (pines #define; config en NVS)
├── firmware/
│   ├── fotos_nc.bin          ← app compilada — se sirve por GET /firmware.bin (OTA)
│   └── *.bin / *.elf / *.map ← artefactos del build Arduino (bootloader, partitions, merged para flash USB)
├── ai_pipeline/
│   ├── generate_synthetic_dataset.py / generate_copypaste_dataset.py
│   ├── download_taco.py / download_ecosort.py / download_floatwaste.py
│   ├── download_openverse_dataset.py / dataset_builder.py  ← bootstrap opcional
│   ├── outdoor_transforms.py ← augmentations de intemperie
│   ├── build_outdoor_eval.py ← eval set degradado
│   ├── train_outdoor.py      ← entrenamiento actual (sintético+TACO+EcoSort+FloatWaste+copy-paste)
│   ├── train_monitor.py      ← monitor GUI del entrenamiento (métricas+telemetría HW)
│   ├── val_snapshot.py       ← val por clase → JSON (lo usa el monitor)
│   ├── evaluate_outdoor.py   ← comparación de pesos
│   ├── eval_upload_path.py   ← eval por path real /upload
│   ├── tune_confidence.py    ← umbral óptimo de confianza
│   └── balance_oversample.py / audit_*.py / dedup_audit.py / clean_real_val.py / filter_classes.py / dataset_stats.py
├── server/
│   ├── config.py             ← settings (.env)
│   ├── main.py               ← POST /upload + API + WS + estáticos
│   ├── inference.py          ← motor ONNX local
│   └── database.py           ← SQLite (captures, detections, pump_events, device_config)
├── static/
│   ├── index.html            ← 3 vistas: En vivo / Analítica / Dispositivo
│   ├── simulador_esp32.html  ← simulador móvil del nodo ESP32-CAM (cámara real)
│   ├── app.js                ← WS, gráficas canvas, galería, bomba, config remota
│   ├── styles.css            ← design system claro/oscuro, móvil primero
│   ├── fonts/                ← Space Grotesk + JetBrains Mono (woff2 locales)
│   └── favicon.svg
├── models/
│   ├── best.onnx / best.pt   ← modelo actual v5 época 30 (3 clases)
│   └── archive/              ← pesos históricos (baseline, snapshots)
├── datasets/                 ← solo CATALOGO.md se versiona (imágenes locales)
├── data/                     ← REBA.db SQLite (no versionada)
├── logs/                     ← uvicorn.log · cloudflared.log · tunnel.log · zrok_url.txt
├── reports/                  ← audits, baseline y experimentos.md
└── runs/                     ← corridas de entrenamiento YOLO (no versionadas)
```

---

## Valores por defecto (configurables)

| Parámetro | Default | Dónde cambiarlo |
|-----------|---------|-----------------|
| Rango de detección | **0–23 cm** | ESP32: `dist_min_cm`/`dist_max_cm` (dashboard/portal/serie) y `.env` → `DETECT_*_CM` (display) |
| Fotos para activar bomba | **3** | ESP32: `pump_photos` (dashboard/portal/serie) y `.env` → `PUMP_TRIGGER_PHOTOS` (informativo) |
| Duración del ciclo de bomba | **10 s** | ESP32: `pump_seconds` (dashboard/portal/serie) y `.env` → `PUMP_RUN_SECONDS` (informativo) |
| Cola de fotos sin Wi-Fi | **6** (descarta la más vieja) | `MAX_COLA` (`#define` en `fotos_nc.ino`, requiere recompilar) |
| Retención de capturas | **2000** | `.env` → `CAPTURE_RETENTION` |
| Relé / bomba | GPIO **13**, relé activo **LOW**, bomba en **NC** | `PIN_RELE`, `RELE_ACTIVO_BAJO`, `BOMBA_EN_NC` (`#define`, requieren recompilar) |
| TRIG / ECHO / FLASH | GPIO **14** / **15** / **4** | `PIN_TRIG` / `PIN_ECHO` / `PIN_FLASH` (`#define`) |
| Clases | plastico, unicel, metal | `.env` → `CLASS_NAMES` |

> Mantén los valores del ESP32 (vía dashboard → Dispositivo) y del `.env` **alineados** para que el progreso de bomba del dashboard coincinda con el comportamiento real.

---

## Quick start

### 1. Firmware ESP32-CAM

1. Abre `esp32_firmware/fotos_nc/fotos_nc.ino` en Arduino IDE.
2. Placa: **AI Thinker ESP32-CAM**. Sube el sketch — **no hay constantes WiFi/URL que editar** (viven en NVS).
3. Configura WiFi + `server_url` por una de estas vías (todas sin reflashear):
   - **Portal** `REBA-Setup`: AP abierto **siempre activo** desde el arranque → celular a `http://192.168.4.1`.
   - **Serie** (115200): `SET wifi_ssid=…`, `SET wifi_pass=…`, `SET server_url=http://<IP_PC>:5000`.
   - **Dashboard** → panel `Dispositivo` → `Guardar y aplicar` (una vez el server esté arriba).
4. Abre Serial Monitor (115200): verás "Conectado. IP del ESP32-CAM" y `REBA listo`.

Guía completa de implementación: [docs/manual_implementacion.md](docs/manual_implementacion.md) · Cableado: [docs/hardware.md](docs/hardware.md) · Firmware: [docs/firmware.md](docs/firmware.md)

### 2. Servidor PC

```bash
cd REBA
python -m venv .venv

# Windows
.venv\Scripts\activate
# Linux/macOS
# source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env    # o: cp .env.example .env
```

Arranque (el puerto debe coincidir con `server_url` del ESP32):

```bash
uvicorn server.main:app --host 0.0.0.0 --port 5000
```

Dashboard: [http://localhost:5000](http://localhost:5000)

**Acceso remoto (túnel Cloudflare `rebaserver.tech`):** `campo.bat` levanta el servidor + el túnel nombrado de Cloudflare (autostart vía `tools/cloudflared_tunnel_hidden.vbs` en la carpeta Startup; también lo relanza si falta) + zrok como respaldo. Un solo dominio sirve ambos protocolos: `https://rebaserver.tech` para el dashboard/celular y `http://rebaserver.tech` (HTTP plano) como `server_url_alt` de la ESP32 remota — ver [docs/server.md](docs/server.md) §Acceso remoto y [docs/manual_uso.md](docs/manual_uso.md) §1. Respaldo zrok: `https://reba-cam.shares.zrok.io` (solo HTTPS).

Prueba sin hardware (envía una foto cualquiera como si fuera el ESP32):

```bash
curl -X POST http://localhost:5000/upload ^
  -H "Content-Type: image/jpeg" -H "X-Evento: ingreso" ^
  --data-binary @foto_prueba.jpg
```

Detalle: [docs/server.md](docs/server.md)

### 3. Modelo de visión

`models/best.onnx` ya está incluido — es un export ONNX de `ai_pipeline/train_outdoor.py` (YOLOv8n, **3 clases**: `plastico,unicel,metal`). Si falta, el servidor arranca igual pero reporta `backend: none` en `/api/health` y no clasifica.

1. Genera un dataset sintético con `ai_pipeline/generate_synthetic_dataset.py` o usa fotos reales ya curadas (las capturas guardadas en SQLite sirven como dataset real; **Exportar YOLO** del dashboard las empaqueta).
2. Entrena con `ai_pipeline/train_outdoor.py` → exporta `models/best.onnx`.

Detalle: [docs/ai_pipeline.md](docs/ai_pipeline.md)

---

## Endpoints del ESP32 → servidor (contrato)

| Método | Ruta | Descripción |
|--------|------|-------------|
| POST | `/upload` | Foto JPEG por ingreso. Body binario, `Content-Type: image/jpeg`, header `X-Evento: ingreso`. 2xx = foto entregada (sale de la cola). |
| GET | `/api/device/config` | Config clave=valor que el ESP32 descarga cada 60 s (heartbeat + overrides + `pump_cmd`/`ota_url`/`queue_clear` pendientes) |
| POST | `/api/device/ack` | ACK del ESP32 tras ejecutar `pump_cmd` |
| POST | `/api/device/status` | Telemetría del circuito (uptime, boots, heap, RSSI, cámara, cola, relé) tras cada poll |
| GET | `/firmware.bin` | Binario OTA (`firmware/fotos_nc.bin`) que la placa descarga y reflashea |

## Endpoints servidor (resumen)

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/api/health` | Estado del motor ONNX, contadores y `device.online` |
| GET | `/api/config` | Config pública (clases, umbrales, constantes de bomba) |
| GET | `/api/stats` | Contadores y % por clase |
| GET | `/api/stats/timeseries` | Detecciones/día por clase para la gráfica (7/14/30 días) |
| GET | `/api/perf` | Métricas de desempeño del pipeline de inferencia |
| GET | `/api/device/status` | Última telemetría reportada por la ESP32 |
| GET | `/api/pump-status` | Progreso inferido hacia el próximo ciclo de bomba |
| GET | `/api/pump-events` | Historial de ciclos de bomba inferidos |
| POST | `/api/pump/run` | Encola ciclo de bomba manual (`{"seconds": N}`) para el ESP32 |
| GET | `/api/pump/events` | Historial + `pending_cmd` (orden encolada sin confirmar) |
| GET/POST | `/api/admin/device-config` | Overrides de config del ESP32 (los edita el dashboard; whitelist de 11 claves) |
| GET | `/api/captures` | Fotos clasificadas (paginado) |
| GET | `/api/captures/latest` | Metadatos de la última captura |
| GET | `/api/captures/{id}/image` | JPEG anotado (`?orig=1` = crudo del ESP32) |
| GET | `/api/captures/{id}/detections` | Detecciones de una captura (lightbox) |
| DELETE | `/api/captures?scope=` | Reset del álbum/datos (`photos`/`all`) |
| GET | `/api/detections/history` · `/api/detections/latest` | Historial / última inferencia |
| GET | `/api/export/yolo` | ZIP con imágenes + labels YOLO + `data.yaml` |
| GET | `/` | Dashboard (`static/index.html`) · `/simulador_esp32.html` → emulador del nodo |
| WS | `/ws` | Tiempo real al dashboard (`hello`, `capture`, `detections`, `pump_event`) |

Referencia completa: [docs/server.md](docs/server.md)

---

## Operación

### Scripts de arranque (`.bat`)

| Script | Qué hace |
|--------|----------|
| `campo.bat` | Arranque de campo: uvicorn :5000 + túnel Cloudflare (`rebaserver.tech`) + zrok de respaldo. Doble clic; dejar la ventana abierta. |
| `retomar.bat` | Retorno de campo: server + túneles + `--resume` del entrenamiento + monitor GUI. |
| `detener_entrenamiento.bat` | Stop seguro del train: respalda pesos y cierra el proceso. |
| `monitor_entrenamiento.bat` | Abre `train_monitor.py` (GUI tkinter: época, ETA, métricas, telemetría HW). |
| `setup_zrok.bat` | Setup único del túnel zrok de respaldo (reserva la URL fija). |

Logs en `logs/` (`uvicorn.log`, `cloudflared.log`, `tunnel.log`, `zrok_url.txt`).

### Comandos serie de la ESP32 (115200 baud)

| Comando | Acción |
|---------|--------|
| `STATUS` | Diagnóstico: heap, cámara, WiFi, cola, sensor |
| `GET` | Lista la config NVS (`wifi_*`, `server_url`(+`_alt`), `srv_activa`, `pump_*`, `dist_*`, `ota_last`) |
| `SET clave=valor` | Cambia config en caliente (misma validación que la remota): `wifi_ssid`, `wifi_pass`, `server_url`, `server_url_alt`, `pump_*`, `dist_*`, `ota_url`, `queue_clear` |
| `CAPTURE` | Fuerza una captura de prueba |
| `OTA` / `OTA http://…/x.bin` | Auto-actualización manual desde `{server_url}/firmware.bin` o URL explícita |
| `REBOOT` | Reinicia la placa |
| `HELP` | Lista los comandos disponibles |

Detalle completo: [docs/firmware.md](docs/firmware.md)

### OTA remota

1. Compila el sketch → copia el `.bin` a `firmware/fotos_nc.bin` (requiere *Partition Scheme* **Minimal SPIFFS / 1.9 MB APP with OTA**).
2. `POST /api/admin/device-config` con `{"ota_url": "http://rebaserver.tech/firmware.bin"}` — o `SET ota_url=…` por serie.
3. La ESP32 la recoge en el siguiente poll (≤60 s), descarga, flashea la partición inactiva y reinicia. La misma URL no se re-flashea (dedup `ota_last`); si falla, reintenta en 10 min.

### Failover de `server_url`

La ESP32 mantiene `server_url` (principal, ej. LAN `192.168.x.x:5000`) y `server_url_alt` (túnel `http://rebaserver.tech`). Tras **3 polls de config fallidos** conmuta a la alternativa (`srv_act` persiste en NVS), y mientras esté en la alternativa **re-sondea la principal cada 5 min** para volver apenas responda. Cobertura típica: campo con hotspot sin Internet → LAN directa; placa en WiFi con Internet → conmuta al túnel sola.

### Simulador del nodo

`http://<PC>:5000/simulador_esp32.html` (o `https://rebaserver.tech/simulador_esp32.html` remoto) emula la ESP32-CAM en el navegador del celular: usa la **cámara real** del teléfono, habla el mismo protocolo (`/upload`, `/api/device/config`, telemetría) y sirve para probar el pipeline sin hardware.

---

## Estado del modelo

- **Desplegado:** `models/best.onnx` = YOLOv8n **v5, época 30**, 3 clases (`plastico`, `unicel`, `metal`), `imgsz=640`, opset 12.
- **Entrenamiento:** `ai_pipeline/train_outdoor.py` sobre `REBA_outdoor` (sintético + boosts + TACO + EcoSort + FloatWaste + copy-paste); augmentations de intemperie vía Albumentations.
- **Validación:** holdout + `REBA_outdoor_eval` degradado + `REBA_real_val` + eval por path real `/upload` (`eval_upload_path.py`). Métricas: [outdoor_training_report.md](outdoor_training_report.md) y `reports/experimentos.md`.
- **Pesos históricos:** `models/archive/` (baseline v2 y snapshots para comparación).
- **Próximo paso:** `REBA_outdoor_v6` en GPU nube con `REBA_field` — Fase 2 / trabajo futuro ([docs/plan_nube_colab_kaggle.md](docs/plan_nube_colab_kaggle.md)).

---

## Seguridad y alimentación

- El **ESP32-CAM** debe alimentarse con **5 V regulados dedicados** (power bank / fuente) en pines 5V y GND. No alimentarlo desde el boost 12 V de la bomba.
- La bomba 12 V se alimenta desde el **XL6009** vía **relé en NC**: COM = +12 V, bomba+ en NC. El GPIO13 solo conmuta la bobina del relé. Relé energizado = bomba OFF; relé en reposo = bomba ON (ver `BOMBA_EN_NC`).
- El pin ECHO del ultrasónico (5 V) **debe** pasar por el divisor **1 kΩ + 2 kΩ** antes de GPIO15.
- No subas `.env` ni credenciales Wi-Fi a repositorios públicos.

---

## Documentación

### Manuales

| Documento | Contenido |
|-----------|-----------|
| [docs/manual_implementacion.md](docs/manual_implementacion.md) | **Implementación**: hardware → firmware → servidor → verificación E2E → campo |
| [docs/manual_uso.md](docs/manual_uso.md) | **Uso**: operación del dashboard, vistas, procedimientos, señales de falla |
| [docs/manual_alcances.md](docs/manual_alcances.md) | **Alcances**: qué hace/no hace el sistema, métricas, limitaciones, roadmap |

### Referencia técnica

| Documento | Contenido |
|-----------|-----------|
| [docs/sistema_general.md](docs/sistema_general.md) | Visión general: subsistemas, IA, endpoints y enlaces |
| [docs/hardware.md](docs/hardware.md) | BOM, esquema eléctrico, relé NC, divisor |
| [docs/firmware.md](docs/firmware.md) | Flasheo, constantes, comandos serie, `pump_cmd`, OTA, failover |
| [docs/server.md](docs/server.md) | venv, `.env`, API REST/WS completa, acceso remoto, SQLite |
| [docs/ai_pipeline.md](docs/ai_pipeline.md) | Datasets, entrenamiento outdoor, ONNX, reentrenamiento |
| [docs/dashboard.md](docs/dashboard.md) | Referencia técnica del frontend (vistas, módulos, WS, tema) |
| [docs/componentes.md](docs/componentes.md) | Función + decisión + uso de cada componente |
| [docs/preguntas_pruebas.md](docs/preguntas_pruebas.md) | Banco de preguntas de verificación con pruebas sugeridas |
| [docs/diagramas/README.md](docs/diagramas/README.md) | Arquitectura + 13 diagramas UML (`.puml`/`.png`/`.svg`) |

### Planes y seguimiento

| Documento | Contenido |
|-----------|-----------|
| [docs/plan_tunel_fijo.md](docs/plan_tunel_fijo.md) | Migración de túneles → Cloudflare `rebaserver.tech` (Portwarp/zrok históricos) |
| [docs/plan_apagado_retorno.md](docs/plan_apagado_retorno.md) | Procedimiento de apagado/retorno del servidor y túneles |
| [docs/plan_post_v5.md](docs/plan_post_v5.md) | Runbook post-entrenamiento v5 (eval, despliegue, go/no-go) |
| [docs/plan_nube_colab_kaggle.md](docs/plan_nube_colab_kaggle.md) | Fase 2: entrenamiento v6 en GPU nube |
| [checklist_de_entrega.md](checklist_de_entrega.md) | Plan de cierre y checklist de entrega (ARC115) |
| [PROGRESO.md](PROGRESO.md) | Bitácora de sesiones de desarrollo |
| [outdoor_training_report.md](outdoor_training_report.md) | Métricas del modelo outdoor |
| [reports/experimentos.md](reports/experimentos.md) | Registro de experimentos y comparaciones |

---

## Licencia / contexto académico

Proyecto académico ARC115 — Universidad (UES). Uso educativo y de prototipo.
