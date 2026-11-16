# REBA — Componentes: función, decisión y uso

Referencia componente por componente del sistema: qué hace cada pieza, por qué se eligió así y cómo se usa en la práctica. Detalles de cableado en `hardware.md`; de configuración en `firmware.md` y `server.md`.

---

## Hardware

### ESP32-CAM (AI Thinker, OV2640)

**Función:** cerebro del nodo acuático — captura fotos JPEG, lee el sensor de distancia, acciona el relé y sube las imágenes al servidor PC por Wi-Fi.

**Decisión:** cámara + Wi-Fi + GPIOs en una sola placa de ~5 USD; suficiente para disparo por evento (no video continuo). Se eligió QVGA/DRAM como framebuffer porque el PSRAM de esta unidad es defectuoso (`psramFound=1` pero `ps_malloc` falla) — DRAM siempre funciona.

**Uso:** se instala en el artefacto alimentada a 5 V; se configura una sola vez (portal `REBA-Setup`, serie o dashboard) y opera indefinidamente sin PC ni USB.

### Sensor ultrasónico HC-SR04

**Función:** detecta cuándo un objeto entra a la zona de captura (rango configurable, default 0–23 cm) y dispara la foto.

**Decisión:** barato, pasivo y fiable en exteriores; el ultrasonido **no atraviesa el agua**, por eso va montado fuera del líquido apuntando a la superficie donde flota la basura. Se usa con debounce (2 lecturas dentro / 3 fuera) para ignorar salpicaduras y ruido.

**Uso:** TRIG→GPIO14, ECHO→GPIO15 **a través del divisor 1k+2k** (el eco sale a 5 V y el GPIO tolera 3.3 V), VCC→5 V, GND→GND. Acercar un objeto a <23 cm debe disparar `>> Ingreso de objeto detectado` en el serial.

### Divisor de tensión 1kΩ + 2kΩ

**Función:** reduce el pulso ECHO de 5 V a ~3.3 V antes de entrar a GPIO15.

**Decisión:** protege el pin de entrada del ESP32; sin él el eco puede degradar o dañar el GPIO con el tiempo.

**Uso:** ECHO del sensor → nodo entre 1k (a GPIO15) y 2k (a GND); el pin lee ~2/3 del pulso.

### Módulo relé 1 canal (5 V)

**Función:** conmuta los 12 V de la bomba según la lógica del firmware (GPIO13).

**Decisión:** aísla los 12 V del ESP32 (que solo maneja 3.3/5 V de lógica) y permite conmutar los 12 V de la bomba con lógica de 3.3 V. Cableado en **NC** con lógica activa-baja (`RELE_ACTIVO_BAJO`): el firmware energiza el relé para apagar la bomba (`bombaApagar()` es lo primero en `setup()`). Ojo: si el ESP32 pierde energía por completo, el relé se desenergiza y la bomba queda ON — mitigado porque toda la cadena comparte batería (si no hay 12 V, no hay bomba); para fail-safe real cablear en NO con lógica invertida.

**Uso:** `RELE_ACTIVO_BAJO=true`, `BOMBA_EN_NC=true`: relé en reposo = bomba ON, relé energizado = bomba OFF. `bombaApagar()` se llama primero en `setup()`.

### Bomba sumergible 12 V (AR-29209 / AD20P)

**Función:** impulsa el agua para retirar la basura acumulada en la cámara de captura.

**Decisión:** 12 VDC / ~350 mA — el voltaje del boost XL6009; IP68 para sumersión permanente. Se activa por ciclos (N fotos o comando manual), no de forma continua, para ahorrar batería.

**Uso:** +12 V → COM del relé → NC → + bomba; − bomba → GND común. La activa el relé ~`pump_seconds` (default 10 s) tras cada ciclo.

### Cadena de potencia: 18650 + TP4056 + XL6009 + panel solar

**Función:** autonomía eléctrica — batería recargable alimenta el boost a 12 V (bomba) y una fuente 5 V dedicada alimenta el ESP32.

**Decisión:** separar la electrónica de potencia (12 V) de la lógica (5 V) evita que los picos de la bomba tumben el ESP32; el TP4056 permite recarga solar opcional. Regla de oro: **nunca** alimentar el ESP32 desde el XL6009.

**Uso:** panel→TP4056→18650→switch→XL6009 (ajustado a 12.0 V con multímetro)→bomba; power bank/5 V→ESP32 (pines 5V/GND). Ver diagrama en `hardware.md`.

### LED flash (GPIO4, integrado)

**Función:** ilumina la escena ~150 ms antes de cada captura para compensar poca luz en el agua.

**Decisión:** usa el LED blanco interno de la placa — cero componentes extra; se enciende solo durante la foto y se apaga al instante.

**Uso:** automático en `tomarFoto()`; no requiere configuración.

### Carcasa estanca

**Función:** protege la electrónica de salpicaduras/lluvia manteniendo la cámara y el sensor con línea de visión al agua.

**Decisión:** la ESP32-CAM **no es sumergible**; necesita funda sellada con ventana para la cámara. El HC-SR04 va **fuera del agua** (el ultrasonido no la atraviesa).

**Uso:** montar la placa en la carcasa, sellar cableados (prensaestopas o resina), dejar ventana sobre el lente y el sensor apuntando a la superficie.

---

## Software — Firmware (`fotos_nc.ino`)

### Captura por evento + cola en RAM

**Función:** convierte el trigger ultrasónico en fotos JPEG reales y las entrega al servidor aunque la red falle momentáneamente.

**Decisión:** disparo por evento (no video) porque el ancho de banda y la CPU del ESP32 no dan para streaming; la cola en RAM (`MAX_COLA=6`) absorbe cortes de Wi-Fi sin perder evidencia — se descarta solo en error permanente (HTTP 4xx o ≥25 reintentos, anti poison-pill). Límite conocido: un reboot pierde la cola (evidencia transitoria, aceptable).

**Uso:** automático; verificar con `CAPTURE` por serial o acercando objeto al sensor.

### `httpSimple()` — HTTP plano con `WiFiClient`

**Función:** hace GET (config) y POST (upload/ack) al servidor sin librerías HTTP pesadas.

**Decisión:** `HTTPClient.h` arrastra mbedTLS y ~122 KB de flash/heap estático que en esta placa marginal contribuían a fallos de framebuffer; el socket TCP plano es predecible y deja ~265 KB de heap libre. Los waits verifican `connected()` y el body de respuesta está capado a 4 KB.

**Uso:** interno; solo se nota en que el upload funciona estable en esta placa.

### Configuración NVS + portal + comandos serie

**Función:** WiFi, URL del servidor, parámetros de bomba y distancias se configuran y cambian **sin recompilar**.

**Decisión:** tres vías redundantes para distintos contextos — portal `REBA-Setup` para celular sin instalar nada, serie para banco, dashboard para operación remota. Todo pasa por la misma validación estricta (`aplicarClaveRemota` con `esEntero`/`esFloat`, rangos y coherencia min<max). Cambios de WiFi/URL llevan backup y **reversión automática** si fallan (90 s / 6 min) — el dispositivo nunca queda brickeado por una config mala.

**Uso:** dashboard → panel "Dispositivo ESP32-CAM"; o serie `SET wifi_ssid=… wifi_pass=…`; o portal si no hay credenciales.

### Canal de comandos `pump_cmd`

**Función:** permite al servidor ordenar un ciclo de bomba manual que el ESP32 ejecuta una sola vez.

**Decisión:** se reutiliza el poll de config existente (sin abrir puertos en el ESP32) con formato `<unix_ts>:<segundos>`; el server solo lo sirve fresco (<10 min, evita bomba fantasma tras offline) y el ESP32 deduplica por `lastPumpCmdId` en RTC + confirma con `POST /api/device/ack`. La ejecución es **no bloqueante** — sensor, cola y WiFi siguen vivos durante el ciclo.

**Uso:** dashboard → "Activar ahora" en el panel Bomba; o `SET pump_cmd=<ts>:<s>` por serie.

### Auto-actualización OTA

**Función:** permite actualizar el firmware del ESP32 **sin USB ni reflasheo** una vez instalado el primer binario.

**Decisión:** `ota_url` viaja por el canal `device_config` existente (o comando serie `OTA`); el ESP32 descarga el `.bin` por HTTP plano con `WiFiClient` (sin HTTPClient/TLS, mismas restricciones que `httpSimple`) y lo escribe con `Update` en la partición OTA inactiva — descarga truncada aborta sin tocar la app. Dedup por NVS `ota_last` evita re-flasheos; backoff de 10 min tras fallo. Requiere partition scheme **con OTA** (`min_spiffs`); los binarios de flasheo USB inicial (`bootloader.bin`, `partitions.bin`, `fotos_nc.bin`) se sirven desde `/firmware/`.

**Uso:** compilar con `PartitionScheme=min_spiffs` (ver `firmware.md` → OTA); actualizar con `ota_url=http://<IP-PC>:5000/firmware.bin` vía `POST /api/admin/device-config` o `SET ota_url=…`/`OTA` por serie.

### Robustez de tiempo y memoria

**Función:** operación continua de semanas/meses sin degradación.

**Decisión:** todos los deadlines usan `(long)(millis() - t) < 0` (seguro ante wrap ~49 días); validación de NVS corrupto con clamps; sensor con sanity clamp (>450 cm = ruido); cámara con reintentos, magic-JPEG y health check; boot counter RTC para detectar bootloops.

**Uso:** interno; se observa en `STATUS` (uptime, boots, heap, fallos).

---

## Software — Servidor (`server/`)

### `POST /upload` + `process_upload`

**Función:** recibe el JPEG del ESP32, corre inferencia ONNX, dibuja las cajas, persiste y notifica al dashboard.

**Decisión:** valida magic bytes/tamaño antes de decodificar (rechazo barato de basura); guarda **dos** JPEG — anotado (dashboard) y original crudo (exportar dataset sin cajas dibujadas que ensuciarían el reentrenamiento); cada upload es heartbeat del dispositivo.

**Uso:** lo llama el firmware solo; también sirve para pruebas con `curl` o `eval_upload_path.py`.

### `InferenceEngine` (`inference.py`)

**Función:** corre el modelo YOLOv8n exportado a ONNX en CPU local — letterbox 640, NCHW, postproceso con umbral de confianza y NMS.

**Decisión:** ONNX + onnxruntime CPU = inferencia 100% offline, sin GPU ni servicios remotos; ~98 ms/frame en esta PC. Umbral 0.35 afinado por `tune_confidence.py` (máx F1 medido). Si el modelo falta, `source:"none"` y la foto igual se guarda como evidencia.

**Uso:** automático por upload; estado en `/api/health` → `inference`.

### `Database` (`database.py`, SQLite)

**Función:** persistencia local de capturas (JPEG doble), detecciones, contadores, eventos de bomba y config del dispositivo.

**Decisión:** SQLite embebido — cero infraestructura, un archivo `data/REBA.db`, WAL para concurrencia con el dashboard; `capture_id` con `ON DELETE SET NULL` conserva evidencia aunque se purguen fotos; retención horaria configurable (`CAPTURE_RETENTION`, default 2000) evita crecimiento ilimitado.

**Uso:** automático; inspección con cualquier cliente SQLite o los endpoints REST.

### API REST + WebSocket `/ws`

**Función:** expone estado (health/stats/timeseries/captures/pump) y empuja eventos en vivo al dashboard.

**Decisión:** REST para consultas puntuales y snapshot; WS para tiempo real (capturas, detecciones, eventos de bomba, estado del dispositivo). Todo LAN — sin autenticación por decisión del usuario (red aislada `REBA-AP`).

**Uso:** el dashboard los consume solo; endpoints útiles a mano: `/api/stats/timeseries`, `/api/export/yolo`, `/api/pump/run`, `/api/device/config`.

### Config del dispositivo (`device_config`)

**Función:** canal servidor→ESP32: overrides clave=valor que el firmware descarga cada 60 s y aplica en caliente.

**Decisión:** tabla key/value con whitelist de claves; el firmware re-valida todo del lado del dispositivo (el servidor confía pero no obedece ciegamente). `pump_cmd` usa la misma vía con TTL; `ota_url` usa la misma vía para disparar la auto-actualización OTA.

**Uso:** dashboard → "Guardar y aplicar"; `GET/POST /api/admin/device-config`. Además `GET /firmware.bin` y `/firmware/*` sirven los binarios OTA/USB al ESP32.

---

## Software — Dashboard (`static/`)

### Panel principal (capturas, materiales, bomba, dispositivo)

**Función:** vista operativa en tiempo real: última foto clasificada, donut de materiales, progreso del ciclo de bomba y config del ESP32.

**Decisión:** un solo HTML+JS sin build ni dependencias externas — funciona 100% offline en la LAN (crítico: la PC puede no tener Internet en campo). Canvas 2D propio para gráficas.

**Uso:** abrir `http://<IP-PC>:5000` en el navegador (ver "Acceso" abajo).

### Galería completa + lightbox

**Función:** historial paginado de todas las capturas con filtro por clase y detalle ampliado con detecciones y confianza.

**Decisión:** paginación server-side (24/pág) + lazy loading de imágenes — escala a miles de capturas sin saturar; el lightbox pide `/api/captures/{id}/detections` para chips por objeto.

**Uso:** sección "Galería de capturas" → click en miniatura para ampliar.

### Panel Bomba (manual + eventos)

**Función:** muestra el progreso automático (fotos→ciclo) **y** permite disparar la bomba a mano con segundos elegibles; lista eventos recientes y estado de órdenes pendientes.

**Decisión:** la bomba real vive en el ESP32 — el servidor no puede accionarla directo; por eso el comando viaja por el canal de config con ack (ver `pump_cmd`).

**Uso:** escribir segundos → "Activar ahora" → el ESP32 la ejecuta en ≤60 s; estado en `pending_cmd`.

### Panel Actividad (gráfica temporal)

**Función:** barras apiladas de detecciones por día y clase (7/14/30 días) con auto-refresh.

**Decisión:** `/api/stats/timeseries` agrega en SQL; canvas con DPR adaptable para verse nítido en celular.

**Uso:** automático; cambiar el selector de días.

### Exportar YOLO

**Función:** descarga un ZIP con `images/` + `labels/` + `data.yaml` listos para `yolo train` — el dataset de reentrenamiento con fotos reales del agua.

**Decisión:** usa el JPEG **original** (no el anotado) y reconstruye labels desde `detections` (xyxy px → xywh normalizado); `min_conf` filtra labels débiles; imágenes sin detección quedan como background (útil para hard negatives).

**Uso:** botón "Exportar YOLO" en la galería → descomprimir → usar como dataset en `ai_pipeline/train_outdoor.py`.

### Estado del dispositivo (pill)

**Función:** indicador visual de si el ESP32-CAM está enviando heartbeat.

**Decisión:** `device.online` (<150 s desde último config-poll o upload) — una sola fuente de verdad para salud del nodo, visible sin abrir logs.

**Uso:** automático; si dice "sin señal", revisar WiFi del ESP32 y el server.

---

## Acceso desde el celular

La PC corre el servidor en `0.0.0.0:5000`. Para abrir el dashboard desde el teléfono:

| Escenario | URL en el celular |
|-----------|-------------------|
| Celular en la Wi-Fi **REBA-AP** (hotspot de la PC) | `http://192.168.137.1:5000` |
| Celular en la misma Wi-Fi que la PC (cualquier otra LAN) | `http://<IP-de-la-PC>:5000` (ver `ipconfig`) |
| Celular con hotspot propio y la PC conectado a él | `http://<IP-de-la-PC-en-esa-red>:5000` |
| **Cualquier red con Internet** (túnel zrok) | `https://<nombre>.shares.zrok.io` — ver §abajo |
| **Simulador ESP32** (celular, misma URL) | `/simulador_esp32.html` — emula el firmware con la cámara real |
| **ESP32 en campo sin Wi-Fi** | `server_url=http://<IP-PC>:5000` — va por la LAN/hotspot compartido con la PC, **no** por el túnel (zrok es solo HTTPS) |
| **ESP32 remota (otra casa)** | `server_url=http://93togsum.free.tryportwarp.cc:16404` — túnel **Portwarp** TCP/HTTP plano de dirección fija (16-nov, migración desde cloudflared; ver `docs/plan_tunel_fijo.md`) |

- La red `REBA-AP` es la recomendada en banco: aísla REBA del resto de tu tráfico (el celular puede mantener datos móviles activos en paralelo).
- Si la página no carga: verificar que el servidor esté corriendo (`uvicorn server.main:app --host 0.0.0.0 --port 5000`) y que el firewall de Windows permita entrada TCP 5000.
- El ESP32 solo necesita 2.4 GHz; el celular puede estar en 5 GHz o en la red que sea — son caminos independientes.

### Túnel zrok (acceso remoto del dashboard/simulador)

`tools/zrok_tunnel.ps1` (invocado por `campo.bat`/`retomar.bat`) abre un share público de zrok hacia `http://127.0.0.1:5000`:

- **Reservado** (`ZROK_RESERVED_NAME` en `.env`, creado con `setup_zrok.bat`): URL **fija** `https://<nombre>.shares.zrok.io` — gratis, sin dominio, sobrevive reinicios.
- **Efímero** (sin reserva): URL nueva por arranque del túnel → `logs/tunnel.log` + `logs/zrok_url.txt`. Fallback automático si el reservado falla.
- Soporta binarios `zrok` (v1) y `zrok2` (v2) detectados en el PATH.

**Usos:**

- **Dashboard remoto**: el navegador usa la URL `https://` — WebSocket `wss://` automático.
- **Simulador ESP32** (`/simulador_esp32.html`): la cámara del celular requiere contexto seguro → la URL HTTPS del túnel (o `localhost`) lo habilita.

**Restricciones**: sin auth → la URL es el secreto; free tier 5 GB/día; *interstitial* anti-phishing ~1 vez/semana solo para navegadores (se salta con un clic); **la ESP32 no puede usar el túnel** (sin TLS) → en campo va por la LAN/hotspot con la PC.

---

## Software — Pipeline de IA (`ai_pipeline/`)

### Entrenamiento outdoor (`train_outdoor.py`, `outdoor_transforms.py`)

**Función:** entrena YOLOv8n con augmentations de intemperie (brillo, sombra, niebla, lluvia, blur, HSV, dropout, cutmix/mixup) sobre datasets sintéticos + TACO real.

**Decisión:** el dominio objetivo es basura flotando bajo condiciones variables; augmentations outdoor cierran el gap sim→real mejor que más arquitectura. Early stopping por mAP50-95; mejor época → `best.pt` → ONNX.

**Uso:** `python ai_pipeline/train_outdoor.py` (largo, CPU); resultados en `runs/detect/`.

### Monitoreo (`train_monitor.py`, `val_snapshot.py`)

**Función:** GUI tkinter de solo lectura sobre `results.csv`/`args.yaml`/logs + telemetría Windows (psutil + CIM): progreso y ETAs, losses+LR por época, tabla por clase con F1 e Images/Instances, alerta de overfitting y de thermal-throttling.

**Decisión:** no instrumentar Ultralytics — leer sus artefactos; `val_snapshot.py` corre `model.val()` en subprocess una vez por época para métricas por clase que el stdout no expone de forma fiable.

**Uso:** `python ai_pipeline/train_monitor.py --run <run>` o `monitor_entrenamiento.bat`; `--once` para snapshot en consola.

### Evaluación (`evaluate_outdoor.py`, `eval_upload_path.py`, `tune_confidence.py`)

**Función:** mide el modelo en tres niveles — holdout offline, sets degradados, y **path de producción real** subiendo imágenes a `/upload`.

**Decisión:** `eval_upload_path.py` cierra el ciclo: valida que el ONNX desplegado + umbral + NMS del servidor realmente reconocen (TACO test 74 imgs: P=0.394 R=0.198, ~98 ms) — distinto del mAP teórico offline.

**Uso:** `python ai_pipeline/eval_upload_path.py --dataset datasets/REBA_taco --split test` → `eval_upload_report.json`.

### Generación de datos (`generate_synthetic_dataset.py`, `download_taco.py`, `build_outdoor_eval.py`)

**Función:** produce los datasets de entrenamiento (sintéticos acuáticos, subconjunto TACO real, eval degradado determinista).

**Decisión:** bootstrapping sin datos acuáticos reales — TACO aporta basura real en contexto natural; el eval degradado simula intemperie de forma reproducible.

**Uso:** scripts one-shot documentados en `docs/ai_pipeline.md`.
