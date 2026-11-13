# REBA — Firmware ESP32-CAM

Código en `esp32_firmware/fotos_nc/fotos_nc.ino`. La configuración **ya no se compila**: vive en NVS (Preferences) y se edita por portal Wi-Fi, comandos serie o remotamente desde el dashboard — sin reflashear.

> La ESP32 es un **cliente**: detecta ingreso por ultrasónico, toma fotos y las sube por `POST /upload` al PC. **No expone ningún endpoint HTTP** ni stream de video.

---

## Requisitos de herramienta

| Item | Valor |
|------|-------|
| IDE | Arduino IDE 2.x |
| Core | esp32 by Espressif Systems **3.3.x** (probado con 3.3.12, placa `esp32cam`) |
| Placa | **AI Thinker ESP32-CAM** |
| Puerto | USB-C nativo de la placa (solo para flashear/debug — en campo va sin cables) |
| Baud Serial | 115200 |

Librerías: `WiFiManager` 2.x (viene con el core o Library Manager), `Preferences`, `WiFiClient` (TCP plano — **no** `HTTPClient.h`).

---

## Cómo flashear (solo banco)

```bash
arduino-cli compile --fqbn esp32:esp32:esp32cam esp32_firmware/fotos_nc
arduino-cli upload -p COMx --fqbn esp32:esp32:esp32cam esp32_firmware/fotos_nc
```

En Arduino IDE: abrir la carpeta `fotos_nc` (el `.ino` debe llevar el nombre de su carpeta) → Upload.

> **Ojo**: abrir el monitor serie por USB **resetea la placa** (DTR/RTS). Para pruebas largas, mantener una sola sesión abierta.

---

## Modos de configuración (en orden de prioridad)

1. **Dashboard** (`http://<PC>:5000` → panel "Dispositivo ESP32-CAM") → guarda overrides en SQLite; el ESP32 los descarga cada 60 s por `GET /api/device/config` y los aplica en caliente.
2. **Comandos serie** (USB, 115200): `HELP`, `GET`, `SET clave=valor`, `CAPTURE`, `STATUS`, `REBOOT`.
3. **Portal Wi-Fi** `REBA-Setup` (AP abierto, `http://192.168.4.1`): se abre solo si no hay credenciales, o si el WiFi falla ~10 min seguidos. En modo no bloqueante — `WiFiManager::process()` corre en `loop()`.

### Claves configurables (NVS + remotas)

| Clave | Rango | Default | Notas |
|-------|-------|---------|-------|
| `wifi_ssid` / `wifi_pass` | ssid 1-32, pass ≤63 | — | pass vacío = red abierta. Cambio remoto lleva backup y **revierte solo en 90 s** si no conecta |
| `server_url` | `http://…` ≤96 chars | `http://192.168.137.1:5000` | Sin https. Backup y **revierte en 6 min** si no responde |
| `pump_photos` | 1-50 | 3 | Fotos por ciclo de bomba |
| `pump_seconds` | 1-300 | 10 | Duración del ciclo |
| `dist_min_cm` / `dist_max_cm` | 0-500, min<max | 0 / 23 | Rango ultrasónico de disparo |
| `pump_cmd` | `<unix_ts>:<s>` | — | **Solo servidor→ESP32**: comando de bomba manual de un solo uso (ver abajo) |

Validación estricta en `aplicarClaveRemota()`: numéricos con `esEntero`/`esFloat` (rechaza `"3abc"`, `"abc"`, fuera de rango), `server_url` solo `http://` con host, longitudes WiFi. El comando serie `SET` reporta `aplicado`/`rechazada` real.

---

## Ciclo de operación (`loop()`)

```
leerCm() cada 60 ms ──► ¿dentro de [dist_min, dist_max]?
   │                        │
   │ 2 lecturas seguidas    │ 3 fuera de rango → rearma
   ▼                        ▼
ingresoDetectado(): flash 150 ms → tomarFoto() → cola → POST /upload
   └─► cada pump_photos fotos → correrBomba() (ON pump_seconds, bloqueante)
```

- **Cola en RAM** (`MAX_COLA=6`): si el upload falla queda pendiente y reintenta cada 15 s; se descarta en HTTP 4xx o ≥25 intentos (anti poison-pill). **Un reboot pierde la cola** (evidencia transitoria).
- **HTTP plano** (`httpSimple` con `WiFiClient`): sin `HTTPClient.h`/mbedTLS — ahorra ~122 KB flash y evita problemas de heap estático. Body de respuesta cap 4 KB, loops verifican `connected()`.
- **Cámara**: QVGA JPEG en **DRAM** (`frame_size=6`). `camera_config_t` **debe** ser `= {}` (campos no asignados como `sccb_freq`/`dma_half` quedaban con basura de stack → "init OK pero sin frames" / FB-OVF). `iniciarCamara()` reintenta `fb_get` antes de `deinit`; health check silencioso cada 15 min + re-init de cámara tras 3 fallos.
- **PSRAM defectuoso en esta placa**: `psramFound()=1` pero `ps_malloc` falla → todo el framebuffer va a DRAM. No depender de PSRAM.

---

## Comando de bomba manual (`pump_cmd`)

El dashboard puede encender la bomba a mano sin tocar el firmware:

1. `POST /api/pump/run {"seconds": 10}` → server guarda `pump_cmd=<unix_ts>:<s>` en `device_config` (válido 10 min; expirado no se sirve).
2. El ESP32 lo recibe en su poll de config → `aplicarClaveRemota` lo valida y lo deja **pendiente**.
3. `loop()` lo ejecuta **no bloqueante**: `bombaEncender()` + deadline `bombaManualFin`; el sensor, la cola y el WiFi siguen vivos. Al cumplirse el plazo → `bombaApagar()`.
4. Dedup con `lastPumpCmdId` (`RTC_DATA_ATTR`, sobrevive resets) → nunca ejecuta el mismo `cmd_id` dos veces.
5. Confirma con `POST /api/device/ack {"cmd_id": N}` → el server borra el override (`pending_cmd` queda `None`).

También funciona por serie: `SET pump_cmd=<ts>:<s>` (uso típico: pruebas de banco).

---

## Diagnóstico por serial

```text
HELP     → lista de comandos
GET      → wifi_ssid, wifi_pass, server_url, pump_*, dist_*
SET k=v  → aplica clave (mismas validaciones que la remota)
CAPTURE  → foto manual (flash + cola + upload + contador bomba)
STATUS   → uptime, boots, heap, cámara, WiFi, cola, bomba, distancia
REBOOT   → reinicio inmediato
```

Salida típica de boot sano:

```text
heap_setup=264852 boots=1
Camara OK: frame_size=6 loc=DRAM
Conectando a WiFi...
Conectado. IP del ESP32-CAM: 192.168.137.161
REBA listo: vigilando rango configurable
```

`boots` (RTC_DATA_ATTR) cuenta resets consecutivos — si sube sin power-cycle, hay brownout/watchdog. `heap_setup` ~265 KB libres es lo normal.

---

## Pines (AI-Thinker ESP32-CAM)

| Función | GPIO | Notas |
|---------|------|-------|
| Relé bomba | 13 | `RELE_ACTIVO_BAJO=true`; bomba en **NC** (`BOMBA_EN_NC=true`: relé reposo = bomba ON) |
| HC-SR04 TRIG | 14 | pulso 10 µs |
| HC-SR04 ECHO | 15 | **solo vía divisor 1k+2k** (5 V → 3.3 V) |
| Flash LED | 4 | LED blanco interno, ~150 ms antes de capturar |
| Cámara | pines SCCB/XCLK/D0-D7/PCLK/VSYNC/HREF | según `configCamara()` — **no cambiar** |

Al boot siempre `bombaApagar()` (estado seguro). Distancias >450 cm o sin eco (`pulseIn` timeout) → `-1` (ignorado por el rango).

---

## Timing y robustez

| Constante | Valor | Descripción |
|-----------|-------|-------------|
| `PERIODO_SENSOR_MS` | 60 | Lectura ultrasónica |
| `LECTURAS_PARA_CONTAR` / `REARMAR` | 2 / 3 | Debounce del trigger |
| `MAX_COLA` | 6 | Fotos pendientes en RAM |
| `REINTENTO_ENVIO_MS` | 15 s | Reintento de upload fallido |
| `POLL_CONFIG_MS` | 60 s | Descarga `GET /api/device/config` |
| `WIFI_REVERT_MS` | 90 s | Reversa de credenciales WiFi nuevas que no conectan |
| `SRV_REVERT_MS` | 6 min | Reversa de `server_url` nuevo que no responde |
| `PORTAL_RETRY_MS` | 30 s | Reapertura del AP si no hay WiFi |
| `CAMARA_RETRY_MS` / `CAMARA_TEST_MS` | 30 s / 15 min | Re-init y health check de cámara |

Todos los deadlines usan `(long)(millis() - deadline) < 0` — **seguro ante el wrap de `millis()` (~49 días)**.

---

## Troubleshooting

| Síntoma | Qué revisar |
|---------|-------------|
| `init OK pero sin frames` / FB-OVF | Verificar `camera_config_t config = {}` en `configCamara()`; alimentación 5 V |
| No conecta Wi-Fi | SSID/pass en NVS (`GET`), red **2.4 GHz**, `STATUS` → `wifi=` |
| Cola crece sin enviar | `server_url` correcto, PC alcanzable, firewall puerto 5000 |
| `SET` dice "rechazada" | Valor fuera de rango/formato — ver tabla de claves |
| Bomba no responde a comando manual | `GET /api/pump/events` → `pending_cmd`; ESP32 online (`device.online` en dashboard); ack perdido → TTL 10 min |
| Distancia siempre -1 | HC-SR04 cableado (TRIG 14 / ECHO 15 con divisor / 5 V), objeto a <4 m |
| `boots` sube solo | Brownout: fuente 5 V inestable (picos flash+WiFi+cámara) |

---

## Pruebas rápidas

```text
# Del lado del PC (simula un ingreso):
curl -X POST http://localhost:5000/upload -H "Content-Type: image/jpeg" \
     -H "X-Evento: ingreso" --data-binary @foto.jpg

# Bomba manual (llega al ESP32 en <=60 s):
curl -X POST http://localhost:5000/api/pump/run \
     -H "Content-Type: application/json" -d '{"seconds": 10}'

# Estado del comando:
curl http://localhost:5000/api/pump/events
```
