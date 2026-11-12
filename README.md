# REBA

Plataforma flotante autónoma de recolección de residuos acuáticos.

La **ESP32-CAM** vigila el área de la cesta con un sensor ultrasónico (0–23 cm). Cuando un objeto entra al rango, enciende el flash, toma una foto JPEG y la envía por **HTTP POST** a la PC. Si no hay Wi-Fi o el servidor no responde, la foto queda en una **cola en PSRAM** y se reintenta después. Al acumular **3 fotos**, la bomba de 12 V corre durante **10 s** (relé cableado en **NC**). La PC recibe cada foto, la clasifica con **YOLOv8/ONNX local**, guarda imagen y detecciones en SQLite y actualiza el dashboard por WebSocket. Todo funciona en la red local, sin Internet.


---

## Arquitectura

```
        Objeto entra a 0-23 cm
                │
                ▼
┌─────────────────────────────┐      POST /upload (JPEG + X-Evento)
│  ESP32-CAM (fotos_nc.ino)   │ ────────────────────────────────►
│  ultrasónico + flash +      │      reintento c/15 s si falla
│  cola PSRAM (máx. 6)        │         ┌────────────────────────┐
│                             │         │  PC — FastAPI (:5000)  │
│  cada 3 fotos →             │         │  ├─ ONNX YOLOv8 local  │
│  bomba ON 10 s (relé NC)    │         │  ├─ SQLite (fotos+clases)
└─────────────────────────────┘         │  └─ WS /ws ──► static/ │
                                        └────────────────────────┘
```

| Capa | Tecnología |
|------|------------|
| Firmware | C++ Arduino, AI-Thinker ESP32-CAM (`esp32_firmware/fotos_nc/`) |
| Servidor | Python 3.10+, FastAPI, OpenCV, ONNX Runtime |
| IA | YOLOv8n → `models/best.onnx` (offline, CPU) |
| UI | HTML/CSS/JS + WebSocket |
| Datos | SQLite local, incluidas las fotos clasificadas |

La ESP32 y la PC deben estar conectadas a la **misma red Wi-Fi de 2.4 GHz** (no se requiere salida a Internet). La ESP32 es cliente: solo sube fotos; no expone endpoints. El servidor, el modelo, la base de datos y el dashboard corren en la PC.

---

## Datos que debes configurar

| Dato | Formato | Default / ejemplo | Dónde se usa |
|------|---------|-------------------|--------------|
| **IP del PC (server)** | IPv4, ej. `192.168.1.50`. Obtenerla: `ipconfig` (Windows) → "Dirección IPv4" de la Wi-Fi conectada a la misma red | `192.168.1.50` | `SERVIDOR_URL` en el firmware |
| **`SERVIDOR_URL`** (firmware) | `http://<IP_PC>:<PUERTO>/upload` | `http://192.168.1.50:5000/upload` | `fotos_nc.ino` — cambiarla requiere **reflashear** |
| **`WIFI_SSID` / `WIFI_PASS`** (firmware) | Cadena literal C. **Solo 2.4 GHz** (el ESP32 no soporta 5 GHz); evita comillas `"` o `\` en el nombre/clave | `"TU_RED_WIFI"` / `"TU_CONTRASENA"` | `fotos_nc.ino` |
| **`HOST` / `PORT`** (server) | `0.0.0.0` (escucha en toda la LAN) / entero | `0.0.0.0` / `5000` | `.env` — el puerto **debe coincidir** con `SERVIDOR_URL` |
| **Dashboard** | `http://<IP_PC o localhost>:<PUERTO>` | `http://localhost:5000` | Navegador en la misma LAN |
| **`MODEL_PATH`** | Ruta relativa o absoluta al `.onnx` | `models/best.onnx` | `.env` |
| **`INFERENCE_CONFIDENCE` / `INFERENCE_IOU`** | 0.0–1.0 | `0.45` / `0.45` | `.env` |
| **`CLASS_NAMES`** | CSV, orden = class_id | `plastico,tela,organico,unicel,metal` | `.env` |
| **`PUMP_TRIGGER_PHOTOS` / `PUMP_RUN_SECONDS`** | entero / segundos | `3` / `10` — **informativo**: reflejar `FOTOS_PARA_BOMBA` / `TIEMPO_BOMBA_MS` | `.env` + firmware |
| **Rango ultrasónico** | cm | `0–23` (`DIST_MIN_CM` / `DIST_MAX_CM`) | firmware + `.env` (`DETECT_*_CM`, display) |
| **Pines** | GPIO | RELAY=13, TRIG=14, ECHO=15 (divisor), FLASH=4 | firmware |
| **Contrato `/upload`** | `POST`, body = JPEG binario, `Content-Type: image/jpeg`, header opcional `X-Evento: ingreso`. Respuesta **2xx** → la foto sale de la cola del ESP32 | — | firmware + server |

> **Recomendación:** configura una **IP estática o reserva DHCP** para el PC en el router — la IP queda grabada en el firmware y cambiarla implica reflashear.

---

## Estructura del repositorio

```
REBA/
├── README.md                 ← este archivo
├── .env.example              ← plantilla de configuración del servidor
├── requirements.txt
├── docs/
│   ├── hardware.md           ← esquema eléctrico, BOM, relé NC, divisor ECHO
│   ├── firmware.md           ← flasheo, constantes, contrato /upload
│   ├── server.md             ← instalación, API, WebSocket
│   ├── ai_pipeline.md        ← dataset y entrenamiento Colab/local
│   └── dashboard.md          ← uso de la UI
├── esp32_firmware/
│   └── fotos_nc/
│       └── fotos_nc.ino      ← sketch desplegado (EDITAR constantes aquí)
├── ai_pipeline/
│   ├── generate_synthetic_dataset.py
│   ├── download_openverse_dataset.py
│   ├── dataset_builder.py
│   ├── train_local.py
│   └── train_yolo.ipynb
├── server/
│   ├── config.py             ← settings (.env)
│   ├── main.py               ← POST /upload + API + WS + estáticos
│   ├── inference.py          ← motor ONNX local
│   └── database.py           ← SQLite (captures, detections, pump_events)
├── static/
│   ├── index.html
│   ├── app.js
│   └── styles.css
└── models/
    └── best.onnx             ← modelo entrenado (generado por train_local.py)
```

---

## Valores por defecto (configurables)

| Parámetro | Default | Dónde cambiarlo |
|-----------|---------|-----------------|
| Rango de detección | **0–23 cm** | `fotos_nc.ino` → `DIST_MIN_CM`/`DIST_MAX_CM` y `.env` → `DETECT_*_CM` |
| Fotos para activar bomba | **3** | `FOTOS_PARA_BOMBA` (firmware) y `PUMP_TRIGGER_PHOTOS` (`.env`) |
| Duración del ciclo de bomba | **10 s** | `TIEMPO_BOMBA_MS` (firmware) y `PUMP_RUN_SECONDS` (`.env`) |
| Cola de fotos sin Wi-Fi | **6** (descarta la más vieja) | `MAX_COLA` |
| Relé / bomba | GPIO **13**, relé activo **LOW**, bomba en **NC** | `PIN_RELE`, `RELE_ACTIVO_BAJO`, `BOMBA_EN_NC` |
| TRIG / ECHO / FLASH | GPIO **14** / **15** / **4** | `PIN_TRIG` / `PIN_ECHO` / `PIN_FLASH` |
| Clases | plastico, tela, organico, unicel, metal | `.env` → `CLASS_NAMES` |

> Mantén los valores del firmware y del `.env` **alineados** para que el progreso de bomba del dashboard coincida con el comportamiento real.

---

## Quick start

### 1. Firmware ESP32-CAM

1. Abre `esp32_firmware/fotos_nc/fotos_nc.ino` en Arduino IDE.
2. Edita el bloque de constantes: `WIFI_SSID`, `WIFI_PASS`, `SERVIDOR_URL` (IP de tu PC + `:5000/upload`).
3. Placa: **AI Thinker ESP32-CAM**. Sube el sketch.
4. Abre Serial Monitor (115200): verás "Conectado. IP del ESP32-CAM" y dos destellos del flash.

Detalle: [docs/firmware.md](docs/firmware.md) · Cableado: [docs/hardware.md](docs/hardware.md)

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

Arranque (el puerto debe coincidir con `SERVIDOR_URL`):

```bash
uvicorn server.main:app --host 0.0.0.0 --port 5000
```

Dashboard: [http://localhost:5000](http://localhost:5000)

Prueba sin hardware (envía una foto cualquiera como si fuera el ESP32):

```bash
curl -X POST http://localhost:5000/upload ^
  -H "Content-Type: image/jpeg" -H "X-Evento: ingreso" ^
  --data-binary @foto_prueba.jpg
```

Detalle: [docs/server.md](docs/server.md)

### 3. Modelo de visión

`models/best.onnx` ya está incluido tras entrenar con `ai_pipeline/train_local.py`. Si falta, el servidor arranca igual pero reporta `backend: none` en `/api/health` y no clasifica.

1. Genera un dataset sintético con `ai_pipeline/generate_synthetic_dataset.py` o usa fotos reales ya curadas (las capturas guardadas en SQLite sirven como dataset real).
2. Entrena con `ai_pipeline/train_local.py` → exporta `models/best.onnx`.

Detalle: [docs/ai_pipeline.md](docs/ai_pipeline.md)

---

## Endpoints ESP32 → servidor (contrato)

| Método | Ruta | Descripción |
|--------|------|-------------|
| POST | `/upload` | Foto JPEG por ingreso. Body binario, `Content-Type: image/jpeg`, header `X-Evento: ingreso`. 2xx = foto entregada (sale de la cola). |

## Endpoints servidor (resumen)

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/api/health` | Estado del motor ONNX y contadores de uploads |
| GET | `/api/config` | Config pública (clases, umbrales, constantes de bomba) |
| GET | `/api/stats` | Contadores y % por clase |
| GET | `/api/pump-status` | Progreso inferido hacia el próximo ciclo de bomba |
| GET | `/api/pump-events` | Historial de ciclos de bomba inferidos |
| GET | `/api/captures` | Fotos clasificadas (metadatos) |
| GET | `/api/captures/{id}/image` | JPEG anotado de una captura |
| GET | `/api/detections/history` | Historial de detecciones |
| WS | `/ws` | Tiempo real al dashboard (`hello`, `capture`, `detections`, `pump_event`) |

---

## Seguridad y alimentación

- El **ESP32-CAM** debe alimentarse con **5 V regulados dedicados** (power bank / fuente) en pines 5V y GND. No alimentarlo desde el boost 12 V de la bomba.
- La bomba 12 V se alimenta desde el **XL6009** vía **relé en NC**: COM = +12 V, bomba+ en NC. El GPIO13 solo conmuta la bobina del relé. Relé energizado = bomba OFF; relé en reposo = bomba ON (ver `BOMBA_EN_NC`).
- El pin ECHO del ultrasónico (5 V) **debe** pasar por el divisor **1 kΩ + 2 kΩ** antes de GPIO15.
- No subas `.env` ni credenciales Wi-Fi a repositorios públicos.

---

## Documentación

| Documento | Contenido |
|-----------|-----------|
| [docs/hardware.md](docs/hardware.md) | BOM, esquema eléctrico, relé NC, divisor |
| [docs/firmware.md](docs/firmware.md) | Flasheo, constantes, contrato `/upload` |
| [docs/server.md](docs/server.md) | venv, `.env`, API, payloads WS |
| [docs/ai_pipeline.md](docs/ai_pipeline.md) | Datasets, entrenamiento, ONNX |
| [docs/dashboard.md](docs/dashboard.md) | Lectura de la UI |

---

## Licencia / contexto académico

Proyecto académico ARC115 — Universidad (UES). Uso educativo y de prototipo.
