# REBA — Firmware ESP32-CAM

Código en `esp32_firmware/fotos_nc/fotos_nc.ino`. Toda la configuración editable vive en el **bloque de constantes al inicio del sketch** (ya no hay `Config.h` separado).

> La ESP32 es un **cliente**: detecta ingreso por ultrasónico, toma fotos y las sube por `POST /upload` al PC. **No expone ningún endpoint HTTP** ni stream de video.

---

## Requisitos de herramienta

| Item | Valor recomendado |
|------|-------------------|
| IDE | Arduino IDE 2.x |
| Core | esp32 by Espressif Systems ≥ 2.0 |
| Placa | **AI Thinker ESP32-CAM** |
| Partición | Default / Huge APP (si el sketch crece) |
| Puerto | USB-TTL (IO0 a GND para flashear; quitar para correr) |
| Baud Serial | 115200 |

Librerías: vienen con el core (`esp_camera`, `WiFi`, `HTTPClient`).

---

## Cómo flashear

1. Conecta el adaptador USB-TTL:
   - U0R ← TX del USB-TTL
   - U0T ← RX del USB-TTL
   - GND ↔ GND
   - 5V ↔ 5V (o alimenta la placa por su pin 5V)
   - **GPIO0 a GND** durante el upload
2. Abre `esp32_firmware/fotos_nc/fotos_nc.ino` (Arduino IDE exige que el `.ino` lleve el nombre de su carpeta).
3. Edita el bloque de constantes: `WIFI_SSID`, `WIFI_PASS`, `SERVIDOR_URL`.
4. Sketch → Upload.
5. Desconecta GPIO0 de GND, pulsa RESET.
6. Serial Monitor 115200 → espera `Conectado. IP del ESP32-CAM: ...` y **dos destellos** del flash ("listo").

---

## Constantes — referencia de parámetros

| Constante | Default | Descripción |
|-----------|---------|-------------|
| `WIFI_SSID` / `WIFI_PASS` | placeholders | Credenciales de la red local compartida con la PC (**solo 2.4 GHz**) |
| `SERVIDOR_URL` | `http://192.168.1.50:5000/upload` | IP del PC + puerto (`PORT` en `.env`) + ruta `/upload` |
| `PIN_RELE` | `13` | IN del relé (bomba) |
| `PIN_TRIG` / `PIN_ECHO` | `14` / `15` | Ultrasónico (ECHO **solo vía divisor**) |
| `PIN_FLASH` | `4` | LED blanco interno (flash de la foto) |
| `RELE_ACTIVO_BAJO` | `true` | LOW = relé energizado |
| `BOMBA_EN_NC` | `true` | Bomba cableada en NC: relé energizado → bomba **OFF** |
| `DIST_MIN_CM` / `DIST_MAX_CM` | `0` / `23` | Rango de detección del ingreso (cm) |
| `PERIODO_SENSOR_MS` | `60` | Periodo entre lecturas ultrasónicas |
| `LECTURAS_PARA_CONTAR` | `2` | Lecturas seguidas en rango para contar ingreso |
| `LECTURAS_PARA_REARMAR` | `3` | Lecturas fuera de rango para rearmar el conteo |
| `FLASH_PREVIO_MS` | `150` | Flash encendido antes de capturar |
| `FOTOS_PARA_BOMBA` | `3` | Fotos acumuladas que disparan la bomba |
| `TIEMPO_BOMBA_MS` | `10000` | Duración del ciclo de bomba |
| `MAX_COLA` | `6` | Fotos pendientes en PSRAM (cola llena → descarta la más vieja) |
| `REINTENTO_ENVIO_MS` | `15000` | Espera tras un envío fallido |
| `REINTENTO_WIFI_MS` | `30000` | Espera entre reconexiones Wi-Fi |

Tras cambiar `FOTOS_PARA_BOMBA` / `TIEMPO_BOMBA_MS` / `DIST_*_CM`, **actualiza también** `PUMP_TRIGGER_PHOTOS` / `PUMP_RUN_SECONDS` / `DETECT_*_CM` en el `.env` del PC para que el dashboard coincida.

---

## Contrato con el servidor — `POST /upload`

El ESP32 envía cada foto así:

```http
POST http://<IP_PC>:<PUERTO>/upload
Content-Type: image/jpeg
X-Evento: ingreso

<JPEG binario>
```

- **Respuesta 2xx** → la foto se considera entregada y sale de la cola.
- **Error o sin respuesta (timeout 8 s)** → la foto permanece en la cola; se reintenta tras `REINTENTO_ENVIO_MS`.
- El servidor responde JSON (`ok`, `capture_id`, `photos_toward_pump`, `pump_cycle_triggered`) — útil para debug, el firmware no lo parsea.

### Cambiar el puerto del servidor

- **Opción A (recomendada):** `PORT=5000` en `.env` — no requiere reflashear.
- **Opción B:** mantener el servidor en `8000` y editar `SERVIDOR_URL` a `http://<IP>:8000/upload` → **requiere reflashear**.

---

## Lógica de la bomba (NC)

- `correrBomba()`: bomba ON durante `TIEMPO_BOMBA_MS` → OFF → resetea contadores y exige que el objeto se aleje antes del siguiente conteo.
- `BOMBA_EN_NC = true`: `bombaEncender()` = relé en **reposo**; `bombaApagar()` = relé **energizado**. Al boot siempre se llama `bombaApagar()` (estado seguro).
- Si el módulo resulta activo HIGH, cambia `RELE_ACTIVO_BAJO` a `false` y reflashea.

---

## Troubleshooting

| Síntoma | Qué revisar |
|---------|-------------|
| No conecta Wi-Fi | SSID/password, 2.4 GHz (ESP32 no es 5 GHz), Serial log |
| Cámara falla `0x...` | Alimentación 5 V estable; PSRAM; cableado |
| "Cola llena" frecuente | PC apagada o `SERVIDOR_URL` incorrecto; el servidor debe responder 2xx |
| Fotos no llegan | IP del PC en `SERVIDOR_URL`, firewall de Windows (permitir puerto 5000), misma LAN |
| Distancia siempre -1 | TRIG/ECHO, divisor, VCC 5 V sensor, orientación |
| Bomba invertida (ON/OFF al revés) | Revisa `BOMBA_EN_NC` / `RELE_ACTIVO_BAJO` |
| No flashea | GPIO0 a GND, drivers CH340/CP2102, baud upload |

---

## Pruebas rápidas

```text
# Del lado del PC (simula un ingreso):
curl -X POST http://localhost:5000/upload -H "Content-Type: image/jpeg" -H "X-Evento: ingreso" --data-binary @foto.jpg

# Del lado del ESP32: observa el Serial Monitor —
#   ">> Ingreso de objeto detectado"
#   "Foto guardada en memoria (...). Pendientes: N"
#   "Foto enviada (...) -> HTTP 200"
```
