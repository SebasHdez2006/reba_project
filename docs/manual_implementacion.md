# REBA — Manual de implementación

Guía paso a paso para llevar el sistema desde cero hasta operación verificada: **hardware → firmware → servidor → prueba end-to-end → despliegue en campo**. Cada fase indica qué se hace, qué verificar y a qué documento detallado ir si algo falla.

Tiempo estimado de banco (sin agua): ~2–3 h la primera vez. Con el checklist final puedes certificar el sistema listo para el agua.

---

## Fase 0 — Requisitos previos

| Requisito | Detalle |
|-----------|---------|
| PC de servidor | Windows/Linux, Python 3.10+, ~2 GB libres para venv + modelo |
| Arduino IDE 2.x | Core `esp32` by Espressif **3.3.x**, placa `AI Thinker ESP32-CAM` |
| Componentes | Lista completa en `docs/hardware.md` §BOM |
| Red Wi-Fi | **2.4 GHz** (el ESP32 no soporta 5 GHz); PC y ESP32 en la misma LAN |
| Herramientas | Multímetro, protoboard/cables, power bank 5 V para el ESP32 |

> **Decisión de red recomendada:** crea un hotspot `REBA-AP` en la PC (o un router dedicado). Aísla el sistema del tráfico normal y funciona sin Internet. Ver §Fase 5.

---

## Fase 1 — Montaje de hardware

**Referencia completa:** `docs/hardware.md` (BOM, diagrama, checklist).

Resumen del cableado crítico:

1. **Alimentación**: panel solar → TP4056 → 18650 → switch → XL6009 ajustado a **12.0 V** → bomba. **Nunca** alimentar el ESP32 desde el XL6009.
2. **ESP32**: fuente **5 V dedicada** en pines `5V`/`GND`.
3. **Relé**: GPIO13 → IN; +12 V → COM; bomba → **NC** (cableado normalmente cerrado: reposo = bomba ON).
4. **HC-SR04**: TRIG→GPIO14, ECHO→GPIO15 **vía divisor 1k+2k** (el eco sale a 5 V), VCC→5 V.
5. **Flash**: GPIO4 (interno, automático).
6. **Carcasa**: la ESP32-CAM **no es estanca** — monta en funda sellada con ventana para la cámara; el HC-SR04 va **fuera del agua** (el ultrasonido no la atraviesa).

**Verificación de la fase** (checklist de `hardware.md` §puesta en marcha):

- [ ] XL6009 = 12.0 V ±0.3 V medido con multímetro
- [ ] Relé conmuta al energizar GPIO13
- [ ] GPIO15 nunca recibe 5 V directo
- [ ] ESP32 estable a 5 V (sin resets al disparar flash/Wi-Fi)

---

## Fase 2 — Firmware ESP32-CAM

**Referencia completa:** `docs/firmware.md`.

```bash
# Compilar (desde la raíz del repo)
arduino-cli compile --fqbn esp32:esp32:esp32cam esp32_firmware/fotos_nc

# Subir por USB (solo banco — en campo va inalámbrico)
arduino-cli upload -p COMx --fqbn esp32:esp32:esp32cam esp32_firmware/fotos_nc
```

> **OTA (updates sin USB):** para que el ESP32 pueda auto-actualizarse,
> compila con partition scheme **`min_spiffs`** (`--fqbn
> "esp32:esp32:esp32cam:PartitionScheme=min_spiffs"` — la placa `esp32cam`
> defaultea a `huge_app` **sin** OTA). El repartition conserva las
> credenciales NVS. Los binarios listos para el flash USB inicial están
> en `firmware/` (`bootloader.bin`, `partitions.bin`, `fotos_nc.bin`)
> y se sirven por HTTP en `/firmware/`; las actualizaciones posteriores
> van por `ota_url` o el comando serie `OTA`. Detalle: `docs/firmware.md` → OTA.

Salida esperada en Serial (115200):

```text
heap_setup=264852 boots=1
Camara OK: frame_size=6 loc=DRAM
Conectando a WiFi...
Conectado. IP del ESP32-CAM: 192.168.137.161
REBA listo: vigilando rango configurable
```

### Configuración inicial (elegir UNA vía; las 3 conviven)

| Vía | Cuándo usarla | Cómo |
|-----|---------------|------|
| **Portal `REBA-Setup`** | Primera vez sin credenciales | Enciende el AP solo; conecta el celular a `REBA-Setup`, abre `http://192.168.4.1`, llena WiFi + URL servidor |
| **Serie USB** | Banco/debug | `SET wifi_ssid=…`, `SET wifi_pass=…`, `SET server_url=…` |
| **Dashboard** | Ya hay un server corriendo | Panel `Dispositivo` → `Guardar y aplicar` |

Valores mínimos: `wifi_ssid`, `wifi_pass`, `server_url=http://<IP-PC>:5000`.

> **Truco de campo:** si el ESP32 no tiene Wi-Fi ~10 min, el portal se reabre solo — puedes reconfigurarlo sin cables.

---

## Fase 3 — Servidor PC

**Referencia completa:** `docs/server.md`.

```bash
cd REBA
python -m venv .venv
.venv\Scripts\activate          # Windows (o source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env
```

Edita `.env` solo si cambias puerto, umbral o retención (los defaults ya son válidos). Verifica que `PORT` coincida con el puerto de `server_url` del ESP32.

```bash
uvicorn server.main:app --host 0.0.0.0 --port 5000
```

- Dashboard: `http://localhost:5000`
- API docs: `http://localhost:5000/docs`
- El modelo `models/best.onnx` ya está incluido; si falta, el server arranca igual pero reporta `backend: none` en `/api/health`.

### Firewall (Windows)

Permite entrada **TCP 5000** para que el ESP32 y el celular lleguen al server:

```powershell
# PowerShell como administrador (una sola vez)
New-NetFirewallRule -DisplayName "REBA 5000" -Direction Inbound -Protocol TCP -LocalPort 5000 -Action Allow
```

O: Panel de control → Firewall → Regla de entrada → Puerto 5000/TCP.

---

## Fase 4 — Verificación end-to-end (obligatoria antes del agua)

Ejecuta en orden; cada paso tiene su prueba y lo que debe verse.

### 4.1 Servidor sano

```bash
curl http://localhost:5000/api/health
```

Esperado: `"ok":true`, `inference.backend:"onnx"`, `model_exists:true`, `device.online:false` (aún sin heartbeat).

### 4.2 Upload simulado (sin ESP32)

```bash
# Toma cualquier JPEG (puede ser una foto del dataset)
curl -X POST http://localhost:5000/upload -H "Content-Type: image/jpeg" -H "X-Evento: ingreso" --data-binary @foto.jpg
```

Esperado: HTTP 200 con `capture_id`, `detections` (puede ser `[]`), `inference_ms` ~50–150. En el dashboard aparece en `En vivo` en ~2 s y `FOTOS RECIBIDAS` sube.

### 4.3 ESP32 conectado

Enciende el ESP32 con la config del paso 2 → Serial debe mostrar `Conectado` + `REBA listo`. Luego:

```bash
curl http://localhost:5000/api/health
# → device.online: true, device.ip: <IP del ESP32>
```

En el dashboard la píldora ESP32 pasa a verde (`EN LÍNEA`) y el texto `Ping · hace X s`.

### 4.4 Trigger ultrasónico

Acerca un objeto a **<23 cm** del HC-SR04 → Serial: `>> Ingreso de objeto detectado` → foto sube sola → dashboard la muestra con badge de clase/confianza.

### 4.5 Ciclo de bomba automático

Acumula **3 fotos** (o usa `CAPTURE` por serial 3 veces) → `pump_events` registra el ciclo → relé conmuta ~10 s. Verifica con multímetro en el relé o escuchando la bomba.

### 4.6 Bomba manual + config remota

1. Dashboard → `Dispositivo` → cambia `Fotos para bomba` a `5` → `Guardar y aplicar` → píldora `Guardado ✓`.
2. En ≤60 s el ESP32 aplica el cambio (verificar por serial `GET` → `pump_photos=5`).
3. `En vivo` → `Otro` → `7` → `Activar ahora` → `Comando en cola` → en ≤60 s la bomba enciende 7 s → `Registro de eventos` muestra la orden y `pending_cmd` queda limpio tras el ack.

### 4.7 Export YOLO (opcional pero recomendado)

Dashboard → `Analítica` → **Exportar YOLO** → ZIP con imágenes + labels — confirma que el dataset de reentrenamiento funciona.

**Checklist de certificación:**

- [ ] `/api/health` → `onnx` + `device.online:true`
- [ ] Foto real del sensor llega al dashboard con detecciones
- [ ] 3 fotos → ciclo de bomba automático
- [ ] Bomba manual ejecutada y confirmada (ack)
- [ ] Config remota aplicada por el ESP32 sin reflashear
- [ ] Dashboard accesible desde el celular en `http://<IP>:5000`

---

## Fase 5 — Despliegue en campo

### Red

| Escenario | Configuración |
|-----------|---------------|
| **Hotspot `REBA-AP`** (recomendado) | PC crea el hotspot; ESP32 y celular se conectan a él; server en `192.168.137.1:5000`. **Ojo:** Windows apaga el hotspot por inactividad — en campo usar router local o mantenerlo activo |
| **Router Wi-Fi local** | PC y ESP32 en la misma LAN 2.4 GHz; `server_url=http://<IP-PC>:5000` |

> Fija una **IP estática o reserva DHCP** para la PC — `server_url` queda grabada en el ESP32 y cambiarla exige reconfigurar (aunque ahora es remoto, mejor no depender).

### Puesta en marcha diaria

1. Encender la PC + hotspot/router + servidor (`uvicorn ... --port 5000`).
2. Encender el ESP32 (batería propia).
3. Abrir `http://<IP>:5000` en el celular → verificar píldora ESP32 verde.
4. Dejar operar; revisar `Analítica` periódicamente.

### Checklist pre-lanzamiento al agua

- [ ] Carcasa sellada (cámara con ventana, cableados con prensaestopas)
- [ ] HC-SR04 **fuera del agua**, apuntando a la zona de ingreso
- [ ] Bomba en **NC** confirmada (si el ESP32 pierde energía, bomba queda ON — verificar que la cadena de 12 V también está sana)
- [ ] XL6009 en 12.0 V, batería cargada
- [ ] `dist_min/max` ajustados a la geometría real de la cesta (el fondo no debe quedar siempre <23 cm)
- [ ] Servidor + red verificados con el checklist de Fase 4

---

## Fase 6 — Mantenimiento

| Tarea | Frecuencia | Cómo |
|-------|-----------|------|
| Revisar capturas/detecciones | Diario en campo | Dashboard → `Analítica` |
| Exportar dataset | Semanal/mensual | Botón **Exportar YOLO** → reentrenar (`docs/ai_pipeline.md`) |
| Verificar batería/carga solar | Semanal | Multímetro en TP4056/XL6009 |
| Limpiar sensor ultrasónico | Semanal | El agua/salinidad ensucia el transductor |
| Revisar `device.online` | Al abrir el dashboard | Si `SIN SEÑAL` >1 h, revisar ESP32 |
| Backup de `data/REBA.db` | Antes de reentrenar/mover PC | Copiar el archivo SQLite |

---

## Troubleshooting rápido (resumen)

| Síntoma | Causa probable | Ir a |
|---------|---------------|------|
| ESP32 no conecta Wi-Fi | SSID 5 GHz, clave mal, portal `REBA-Setup` | `firmware.md` §troubleshooting |
| Upload falla siempre | Firewall 5000, IP incorrecta en `server_url` | `server.md` §troubleshooting |
| `source:none` en health | Falta `models/best.onnx` | `server.md` |
| Cámara "init OK sin frames" | `camera_config_t` sin `= {}` (bug ya corregido) o alimentación | `firmware.md` |
| Cola de fotos crece | Server inalcanzable; se descarta a los 25 intentos | `firmware.md` |
| Bomba no enciende | Relé NC mal cableado, 12 V ausente, `pending_cmd` expirado | `hardware.md` + `componentes.md` |
| `boots` sube solo | Brownout por fuente 5 V inestable | `firmware.md` §diagnóstico |

---

## Documentos de apoyo

- [`hardware.md`](hardware.md) — BOM, cableado, relé NC, divisor ECHO
- [`firmware.md`](firmware.md) — constantes, comandos serie, robustez
- [`server.md`](server.md) — API REST/WS completa, `.env`, SQLite
- [`ai_pipeline.md`](ai_pipeline.md) — datasets y reentrenamiento
- [`dashboard.md`](dashboard.md) — referencia técnica del frontend
- [`manual_uso.md`](manual_uso.md) — operación cotidiana del dashboard
- [`manual_alcances.md`](manual_alcances.md) — qué hace y qué no hace el sistema
