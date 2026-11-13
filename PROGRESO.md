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
