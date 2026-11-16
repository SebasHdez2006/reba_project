# REBA — Checklist de 163 verificaciones (QA)

Guion de pruebas del sistema REBA. Cada fila resume la **pregunta**, el
**comportamiento esperado** según el código actual, la **prueba sugerida**
y una casilla para registrar el resultado durante la ejecución.

**Marcar en la última columna:** `OK` · `FALLO` · `N/A` · `PENDIENTE`

**Estados del documento:** `[✔]` ya verificado en banco · `[◐]` pendiente
de prueba física · `[📄]` solo documental

---

## 1. Arquitectura y visión general

| # | Pregunta | Comportamiento esperado | Prueba | Res. |
|---|----------|-------------------------|--------|------|
| 1 | ¿Qué es REBA? `[📄]` | Plataforma flotante: detecta objetos (ultrasónico 0–23 cm), foto+flash, sube a PC, clasifica YOLOv8/ONNX, bomba cada N fotos; LAN sin Internet + acceso remoto opcional por túnel `rebaserver.tech` | Revisar README/docs | ☐ |
| 2 | ¿Cuáles son los 3 componentes? `[✔]` | ESP32-CAM (sensor/foto/cola/upload), PC FastAPI (inferencia/SQLite/WS/config), relé+bomba 12V (actuador) | `curl localhost:5000/api/health` | ☐ |
| 3 | ¿Por qué el ESP32 no expone endpoints? `[✔]` | Es cliente push: solo `POST /upload`; menos superficie de ataque y complejidad | `curl http://<IP-ESP32>/` debe fallar | ☐ |
| 4 | ¿Funciona sin Internet? `[✔]` | ONNX local, SQLite local, dashboard servido por la PC; el túnel Cloudflare es solo acceso remoto — sin WAN sigue todo igual | Desconectar WAN y operar | ☐ |
| 5 | ¿PC apagada o Wi-Fi caído? `[✔]` | Fotos en cola RAM (máx. 6), reintento cada 15 s; si llena, descarta la más vieja | Apagar server, hacer `CAPTURE`, revisar `STATUS` | ☐ |
| 6 | ¿Qué sobrevive a un reboot del ESP32? `[✔]` | NVS (config), `bootCount`/`lastPumpCmdId` (RTC), backups; se pierde cola y contador de bomba | `REBOOT` por serie → `GET` | ☐ |
| 7 | ¿Dónde está la verdad del conteo de bomba? `[📄]` | En el firmware (`fotosTomadas`); el server solo infiere por uploads → puede desincronizarse con cola offline | Comparar `STATUS` vs dashboard | ☐ |
| 8 | ¿Canales ESP32↔PC? `[✔]` | HTTP plano TCP: `GET /api/device/config` (60 s), `POST /upload`, `POST /api/device/ack`, `POST /api/device/status` (telemetría), `GET /firmware.bin` (OTA) | Serial + `server.log` | ☐ |

## 2. Alimentación y potencia

| # | Pregunta | Comportamiento esperado | Prueba | Res. |
|---|----------|-------------------------|--------|------|
| 9 | ¿Por qué el ESP32 no usa el XL6009? `[✔]` | Picos de bomba/Wi-Fi degradan la lógica; 5 V dedicados en pines 5V/GND | Revisar cableado | ☐ |
| 10 | ¿Voltaje del XL6009? `[◐]` | 12.0 V ±0.3 V medido con multímetro antes de conectar la bomba | Multímetro en salida boost | ☐ |
| 11 | ¿ESP32 sin energía con bomba en NC? `[◐]` | Relé se desenergiza → bomba ON; mitigado por batería común; fail-safe real = cablear en NO | Quitar 5V al ESP32 con bomba en NC | ☐ |
| 12 | ¿Función de 18650+TP4056+solar? `[📄]` | Autonomía: batería 3.7 V/2800 mAh, recarga opcional desde panel 5 V | Verificar carga con multímetro | ☐ |
| 13 | ¿Corriente de la bomba? `[◐]` | ~350 mA a 12 V, IP68; boost y relé deben soportar el pico de arranque | Multímetro en arranque de bomba | ☐ |
| 14 | ¿Cómo debe ser la tierra común? `[◐]` | Unir GND ESP32↔relé↔sensor mínimo; potencia de bomba en estrella si aplica | Revisar continuidad GND | ☐ |
| 15 | ¿Checklist de arranque hardware? `[◐]` | 12 V OK, relé conmuta, divisor ECHO puesto, 5 V estable, bomba en NC, sensor <23 cm solo con objeto | Seguir `docs/hardware.md` §checklist | ☐ |
| 16 | ¿Síntoma de brownout/watchdog? `[✔]` | `bootCount` sube sin power-cycle; aviso si `boots > 8` | `STATUS` repetido sin apagar | ☐ |
| 17 | ¿Heap libre normal tras boot? `[✔]` | `heap_setup ≈ 265 KB`; muy inferior sugiere fuga/fragmentación | `STATUS` → heap | ☐ |
| 18 | ¿Por qué la cámara va antes que `cargarConfig()`? `[✔]` | NVS consume ~47 KB; la cámara necesita ~250 KB contiguos para framebuffer DMA | Revisión de código `setup()` | ☐ |

## 3. Sensor ultrasónico HC-SR04

| # | Pregunta | Comportamiento esperado | Prueba | Res. |
|---|----------|-------------------------|--------|------|
| 19 | ¿Rango de detección? `[✔]` | 0–23 cm por defecto; configurable 0–500 cm (min < max) por NVS/serie/dashboard | `STATUS` → rango | ☐ |
| 20 | ¿TRIG directo a GPIO14? `[✔]` | Salida 3.3 V del ESP32 basta para gatillar el sensor de 5 V | Continuidad TRIG→GPIO14 | ☐ |
| 21 | ¿Por qué divisor en ECHO? `[✔]` | Eco sale a 5 V; 1k+2k → ~3.33 V en GPIO15 (máx 3.3 V) | Medir voltaje en GPIO15 | ☐ |
| 22 | ¿Qué pasa si ECHO va directo? `[◐]` | 5 V sostenido degrada/daña el GPIO15 con el tiempo | Nunca hacerlo; inspección visual | ☐ |
| 23 | ¿Conversión pulso→distancia? `[✔]` | `pulseIn` HIGH timeout 25 ms; `d = t × 0.0343 / 2` cm | Objeto a distancia conocida | ☐ |
| 24 | ¿Qué lecturas se descartan? `[✔]` | `t=0` (sin eco) o `d<0`/`>450 cm` → -1 | `STATUS` → dist_ultima=-1 sin objeto | ☐ |
| 25 | ¿Por qué el piso de 1 cm? `[✔]` | Lecturas ~0 son ruido del sensor, no objeto real | Acercar objeto al sensor | ☐ |
| 26 | ¿Cómo es el debounce? `[✔]` | 2 lecturas dentro (cada 60 ms) cuentan ingreso; 3 fuera rearman | Mover objeto lento en borde | ☐ |
| 27 | ¿Por qué el sensor va fuera del agua? `[◐]` | Ultrasonido no atraviesa agua; apunta a la superficie | Montaje físico | ☐ |
| 28 | ¿Estado del sensor en banco? `[◐]` | `dist_ultima=-1` >60 s — TRIG/ECHO conmutan pero sin eco | **PRUEBA FÍSICA:** objeto a <23 cm | ☐ |

## 4. Relé, bomba y montaje físico

| # | Pregunta | Comportamiento esperado | Prueba | Res. |
|---|----------|-------------------------|--------|------|
| 29 | ¿GPIO del relé y lógica? `[✔]` | GPIO13, `RELE_ACTIVO_BAJO`: LOW = energizado, HIGH = reposo | `STATUS` + multímetro bobina | ☐ |
| 30 | ¿Qué significa bomba en NC? `[✔]` | COM=+12 V, bomba en NC: relé reposo → ON; energizado → OFF | Forzar `CAPTURE`×3 y observar | ☐ |
| 31 | ¿Primera acción de `setup()`? `[✔]` | `bombaApagar()` — estado seguro desde el arranque | Boot + escuchar "Bomba" | ☐ |
| 32 | ¿Activación automática de la bomba? `[✔]` | Cada `pump_photos` (3) fotos → ON `pump_seconds` (10 s) con `delay()` | 3 `CAPTURE` por serie | ☐ |
| 33 | ¿Reset tras ciclo de bomba? `[✔]` | `fotosTomadas/dentro/fuera=0`, `armado=false` — objeto debe alejarse | `STATUS` tras ciclo | ☐ |
| 34 | ¿Por qué relé de 1 canal 5 V? `[📄]` | Aísla 12 V de la lógica 3.3 V; conmuta potencia con GPIO | Inspección módulo | ☐ |
| 35 | ¿Specs de la bomba? `[◐]` | 12 VDC ~350 mA 3.6 W IP68; ciclos para ahorrar batería | Datasheet/multímetro | ☐ |
| 36 | ¿Montaje del sensor? `[◐]` | A la zona de ingreso; si el fondo queda <23 cm → fotos en falso | Medir geometría de cesta | ☐ |
| 37 | ¿Requisito de carcasa estanca? `[◐]` | ESP32 no sumergible: funda sellada, ventana al lente, HC-SR04 fuera del agua | Inspección mecánica | ☐ |
| 38 | ¿Prueba física pendiente del relé? `[◐]` | Multímetro en GPIO13/bobina, ciclo 3 fotos→10 s, sentido NC correcto | Banco con bomba o simulada | ☐ |

## 5. Firmware: arranque y configuración

| # | Pregunta | Comportamiento esperado | Prueba | Res. |
|---|----------|-------------------------|--------|------|
| 39 | ¿Los 3 modos de configuración? `[✔]` | Dashboard→server→poll 60 s; serie (HELP/GET/SET/CAPTURE/STATUS/REBOOT); portal `REBA-Setup` | Probar los 3 | ☐ |
| 40 | ¿Cuándo abre el portal? `[✔]` | Sin credenciales o ~20 fallos Wi-Fi (~10 min); timeout 5 min; reabre cada 30 s sin creds | Borrar NVS/Borrar red | ☐ |
| 41 | ¿Qué persiste en NVS? `[✔]` | `ssid, pass, srv, pump_photos, pump_seconds, dist_min/max_cm` + backups `ssid_bak/pass_bak/srv_bak` | `GET` por serie | ☐ |
| 42 | ¿Validaciones de claves remotas? `[✔]` | `esEntero/esFloat` estricto; rangos; min<max; `server_url` solo `http://` ≤96; ssid≤32 pass≤63 | `SET` con valores malos | ☐ |
| 43 | ¿Reversión de Wi-Fi nuevo? `[✔]` | `ssid_bak/pass_bak`; revierte en 90 s si no conecta; limpia al conectar | Dashboard→creds malas | ☐ |
| 44 | ¿Reversión de `server_url`? `[✔]` | `srv_bak`; revierte en 6 min sin GET exitoso; limpia al confirmar URL nueva | Dashboard→URL inválida | ☐ |
| 45 | ¿Por qué WiFiManager es lazy? `[✔]` | Global consumía ~45 KB heap y la cámara no inicializaba; se crea al abrir portal | Revisar `iniciarPortal()` | ☐ |
| 46 | ¿Comandos serie? `[✔]` | `HELP, GET, SET` (7 claves whitelist, reporta aplicado/rechazada real), `CAPTURE, STATUS, REBOOT`; línea ≤160 | Escribir HELP en monitor | ☐ |
| 47 | ¿Qué muestra `STATUS`? `[✔]` | uptime, boots, heap, cámara, Wi-Fi/IP, portal, cola, fotos_bomba, distancia, rango | `STATUS` por serie | ☐ |
| 48 | ¿`Serial.begin` duplicado? `[📄]` | Líneas 988 y 1002 — redundante pero inofensivo (limpieza menor) | Revisar `setup()` | ☐ |

## 6. Firmware: detección y captura

| # | Pregunta | Comportamiento esperado | Prueba | Res. |
|---|----------|-------------------------|--------|------|
| 49 | ¿Secuencia de un ingreso? `[✔]` | `leerCm` cada 60 ms → 2 dentro → flash 150 ms → `fb_get` ≤3 → valida JPEG → cola → envía → evalúa bomba | Objeto a <23 cm + serial | ☐ |
| 50 | ¿Flash 150 ms antes de capturar? `[✔]` | Ajuste de exposición con luz artificial (fotos nocturnas/acuáticas) | Observar flash en captura | ☐ |
| 51 | ¿Reintentos de captura? `[✔]` | `fb_get` ×3 (120 ms); `fallosCamara++`; ≥5 → deinit + re-init cada 30 s | Provocar fallo de cámara | ☐ |
| 52 | ¿Validación JPEG en firmware? `[✔]` | `len≥128` + magic `FF D8 FF`; frame corrupto no cuenta ni sube | Frame corrupto simulado | ☐ |
| 53 | ¿Cámara muere en operación? `[📄]` | `tomarFoto` false; health check cada 15 min; re-init ≤30 s; resto sigue vivo | `STATUS` → camara=CAIDA | ☐ |
| 54 | ¿Por qué QVGA/DRAM primero? `[✔]` | Única config verificada en banco; VGA/DRAM init OK sin frames | Revisar `iniciarCamara()` | ☐ |
| 55 | ¿Resolución y peso del JPEG? `[✔]` | QVGA 320×240, quality 15, `fb_count=1` → ~3.7 KB/foto | `server.log` tamaño upload | ☐ |
| 56 | ¿Qué cuenta para la bomba? `[✔]` | Solo `tomarFoto()` exitoso (frame válido encolado) | Capturas vs `fotosTomadas` | ☐ |
| 57 | ¿Tras encolar la foto? `[✔]` | `colaEnviar()` inmediato + evalúa `fotosTomadas ≥ pumpPhotos` | Serial tras CAPTURE | ☐ |
| 58 | ¿Objeto quieto en rango? `[✔]` | Solo 1 foto; `armado=false` hasta 3 lecturas fuera de rango | Objeto fijo <23 cm 10 s | ☐ |

## 7. Firmware: cola de fotos y HTTP

| # | Pregunta | Comportamiento esperado | Prueba | Res. |
|---|----------|-------------------------|--------|------|
| 59 | ¿Capacidad y política de cola? `[✔]` | `MAX_COLA=6` ring buffer; llena → descarta la más vieja | 7+ fotos sin server | ☐ |
| 60 | ¿Dónde reside la cola? `[✔]` | RAM: `ps_malloc`→`malloc` fallback; heap guard 24 KB reserva | `STATUS` → heap/cola | ☐ |
| 61 | ¿Cola ante Wi-Fi caído? `[✔]` | Reintento 15 s; al volver drena FIFO; reboot la pierde | Apagar Wi-Fi + `CAPTURE` | ☐ |
| 62 | ¿Cuándo se descarta una foto? `[✔]` | HTTP 4xx o ≥25 intentos (~6 min) — anti poison-pill | JPEG que dé 400 siempre | ☐ |
| 63 | ¿Qué códigos reintentan? `[✔]` | 2xx entregada; 4xx descarta; -1/5xx/timeout reintenta | Probar con server caído/erróneo | ☐ |
| 64 | ¿Qué hace `httpSimple()`? `[✔]` | HTTP/1.1 mínimo TCP plano: solo `http://`, connect 6 s, status line, headers, body cap 4 KB | Revisar código / logs | ☐ |
| 65 | ¿Por qué no `HTTPClient.h`? `[✔]` | mbedTLS estático → FB-OVF en cámara; ahorra ~122 KB flash | Nunca incluirlo | ☐ |
| 66 | ¿Timeouts HTTP? `[✔]` | connect 6 s, upload 15 s, config 8 s, ack 8 s | Medir con server lento | ☐ |
| 67 | ¿Headers del POST /upload? `[✔]` | `Content-Type: image/jpeg`, `X-Evento: ingreso`, `User-Agent: REBA-ESP32`, `Connection: close` | `server.log` / proxy | ☐ |
| 68 | ¿Fallo a mitad de lote de cola? `[✔]` | Detiene el lote y espera 15 s — no martillea el servidor caído | Server intermitente | ☐ |

## 8. Firmware: bomba (automática y manual)

| # | Pregunta | Comportamiento esperado | Prueba | Res. |
|---|----------|-------------------------|--------|------|
| 69 | ¿La bomba automática bloquea? `[✔]` | Sí: `delay()` ~10 s pausa sensor/cola/Wi-Fi (aceptable por evento) | Cronometrar ciclo | ☐ |
| 70 | ¿Llegada de bomba manual? `[✔]` | `POST /api/pump/run` → `pump_cmd` → poll ≤60 s → valida → pendiente → `loop()` ejecuta | Dashboard "Activar ahora" | ☐ |
| 71 | ¿La bomba manual bloquea? `[✔]` | No: deadline `bombaManualFin`, loop apaga wrap-safe; resto sigue vivo | `STATUS` durante bomba | ☐ |
| 72 | ¿Dedup de `pump_cmd`? `[✔]` | `lastPumpCmdId` RTC sobrevive resets; `id` debe ser > último | Reenviar mismo cmd_id | ☐ |
| 73 | ¿Formato y TTL de `pump_cmd`? `[✔]` | `<unix_ts>:<s>`, s 1–300; server solo sirve si <10 min | `GET /api/device/config` | ☐ |
| 74 | ¿ACK de la bomba? `[✔]` | `POST /api/device/ack {cmd_id}`; server borra override; si falla, TTL expira | `GET /api/pump/events` | ☐ |
| 75 | ¿`pump_cmd` durante bomba automática? `[◐]` | `correrBomba()` apaga `bombaManualActiva` — el ciclo automático gana | Forzar ambos a la vez | ☐ |
| 76 | ¿`SET pump_cmd` por serie? `[◐]` ⚠️ | Doc dice que sí pero la whitelist de `SET` lo **rechaza** — discrepancia doc/código | `SET pump_cmd=...` → "Clave no permitida" | ☐ |

## 9. Firmware: robustez y diagnóstico

| # | Pregunta | Comportamiento esperado | Prueba | Res. |
|---|----------|-------------------------|--------|------|
| 77 | ¿Seguro ante wrap de `millis()`? `[✔]` | `(long)(millis()-t) < 0` en todos los deadlines; correcto tras ~49 días | Revisión de código | ☐ |
| 78 | ¿Qué sobrevive en RTC_DATA_ATTR? `[✔]` | `bootCount` y `lastPumpCmdId` (resets soft/brownout; no power-cycle) | `REBOOT` → `STATUS` boots | ☐ |
| 79 | ¿`boots > 8` qué indica? `[✔]` | Aviso de brownout/watchdog — fuente 5 V inestable | Provocar resets | ☐ |
| 80 | ¿NVS corrupta o fuera de rango? `[✔]` | Clamps + `distMin≥distMax`→defaults + `srvBase` sin `http://`→default | Escribir NVS mala | ☐ |
| 81 | ¿Heap guard de la cola? `[✔]` | Rechaza foto si heap < len + 24 KB reserva | Foto grande + heap bajo | ☐ |
| 82 | ¿Límite del buffer serie? `[✔]` | 160 chars/línea; `\n` o `\r` ejecuta | Enviar línea larga | ☐ |
| 83 | ¿Wi-Fi "conectada" válida? `[✔]` | Exige `WL_CONNECTED` **y** IP ≠ 0.0.0.0; reintento cada 30 s | `STATUS` → wifi= | ☐ |
| 84 | ¿El portal bloquea la operación? `[✔]` | No: `wmP->process()` en `loop()`; fotos/cola siguen; timeout 5 min | Abrir portal y capturar | ☐ |

## 10. Servidor: POST /upload

| # | Pregunta | Comportamiento esperado | Prueba | Res. |
|---|----------|-------------------------|--------|------|
| 85 | ¿Contrato de `/upload`? `[✔]` | POST body=JPEG, `Content-Type: image/jpeg`, `X-Evento` opcional; 2xx = sale de cola | `curl -X POST .../upload --data-binary @f.jpg` | ☐ |
| 86 | ¿Validaciones antes de decodificar? `[✔]` | vacío→400, <128 B o magic malo→400, >2 MB→413, decode falla→400 | curl con body basura | ☐ |
| 87 | ¿Qué guarda por captura? `[✔]` | `image_jpeg` anotado + `image_orig_jpeg` crudo + `detections` ligadas — 1 transacción | Revisar DB / endpoints | ☐ |
| 88 | ¿Respuesta exitosa? `[✔]` | `{ok, capture_id, detections, inference_ms, photos_toward_pump, pump_cycle_triggered}` | curl upload | ☐ |
| 89 | ¿Foto sin detecciones? `[✔]` | Se guarda igual (`detections:[]`) = evidencia y dataset background | Subir foto sin basura | ☐ |
| 90 | ¿Heartbeat del dispositivo? `[✔]` | `_touch_device` en `/upload` y `/api/device/config`; online si <150 s | `GET /api/health` → device | ☐ |
| 91 | ¿Bomba inferida por el server? `[✔]` | `photos_since_pump ≥ 3` → `pump_event` + reset + broadcast WS | 3 uploads → `/api/pump-events` | ☐ |
| 92 | ¿Puede mentir el progreso de bomba? `[◐]` | Sí: cola offline retrasa el conteo; restart del server resetea `pump_state` | Comparar tras cortes | ☐ |
| 93 | ¿Sin ONNX disponible? `[✔]` | `backend=none`; foto se guarda `source=none`, `detections:[]`, `error` en payload | Renombrar best.onnx y reiniciar | ☐ |
| 94 | ¿Eventos WS por upload? `[✔]` | `capture` + `detections` (+`pump_event` si ciclo) | Dashboard abierto + upload | ☐ |

## 11. Servidor: API REST y config de dispositivo

| # | Pregunta | Comportamiento esperado | Prueba | Res. |
|---|----------|-------------------------|--------|------|
| 95 | ¿Endpoints REST? `[✔]` | health, config, stats, stats/timeseries, pump-status, pump-events, pump/run, device/config, device/ack, admin/device-config, export/yolo, detections/*, captures/* | `curl` cada uno | ☐ |
| 96 | ¿Formato de `GET /api/device/config`? `[✔]` | Texto `clave=valor` ordenado; solo overrides; `pump_cmd` solo si <10 min | curl + comparar ESP32 `GET` | ☐ |
| 97 | ¿Validación de `admin/device-config`? `[✔]` | JSON con whitelist de claves; vacío/null borra; 400 si clave inválida | POST clave falsa | ☐ |
| 98 | ¿`POST /api/pump/run`? `[✔]` | `seconds` 1–300 → `pump_cmd=<ts>:<s>` + `pump_event` manual + `cmd_id` | curl pump/run | ☐ |
| 99 | ¿`POST /api/device/ack`? `[✔]` | `cmd_id` int; borra solo si pending empieza `"<id>:"`; responde `cleared` | curl ack | ☐ |
| 100 | ¿`/api/stats/timeseries`? `[✔]` | `days` 1–365; detecciones/día por clase + captures + pump_events (UTC) | `?days=7` | ☐ |
| 101 | ¿`/api/export/yolo`? `[✔]` | ZIP: `images/*.jpg` originales + `labels/*.txt` YOLO + `data.yaml`; `min_conf` filtra; ≤2000 | Descargar y abrir ZIP | ☐ |
| 102 | ¿Paginación de `/api/captures`? `[✔]` | `limit`≤100, `offset`≤100000; `{items,total,offset}`; JPEG aparte | `?limit=5&offset=0` | ☐ |
| 103 | ¿`image?orig=1` vs anotado? `[✔]` | `orig`=JPEG crudo ESP32; sin parámetro=anotado; fallback en capturas viejas; 404 si no existe | curl ambas variantes | ☐ |
| 104 | ¿Riesgo de seguridad actual? `[◐]` | Sin auth, CORS `*`, `wifi_pass` en claro en SQLite → solo LAN aislada; no exponer puerto 5000 | Revisar config | ☐ |

## 12. Servidor: WebSocket, SQLite y retención

| # | Pregunta | Comportamiento esperado | Prueba | Res. |
|---|----------|-------------------------|--------|------|
| 105 | ¿Flujo del WS `/ws`? `[✔]` | accept → `hello` snapshot → ping/pong; push `capture`/`detections`/`pump_event` | Dashboard + devtools | ☐ |
| 106 | ¿Clientes WS caídos? `[✔]` | `broadcast()` los descarta de `ws_clients`; `finally` también limpia | Cerrar pestaña y subir foto | ☐ |
| 107 | ¿Tablas de la DB? `[✔]` | `captures, detections, counters, pump_events, device_config`; WAL + FK ON | `sqlite3 data/REBA.db .tables` | ☐ |
| 108 | ¿Migraciones automáticas? `[✔]` | `ALTER TABLE` si falta `capture_id` o `image_orig_jpeg` | DB antigua + arranque | ☐ |
| 109 | ¿Retención de capturas? `[✔]` | Poda horaria keep=2000 (`0`=off); detections sobreviven con `capture_id=NULL`; `to_thread` | `CAPTURE_RETENTION=5` + reinicio | ☐ |
| 110 | ¿Estado perdido al reiniciar server? `[📄]` | `pump_state`, `latest_inference`, `device_state`, `ws_clients` (pump_events persisten; device vuelve online al próximo poll) | Reiniciar uvicorn | ☐ |
| 111 | ¿Thread-safety de SQLite? `[◐]` | `check_same_thread=False` + commits cortos + WAL + poda en `to_thread` | Estrés de uploads | ☐ |
| 112 | ¿Se pueden resetear contadores? `[◐]` | No hay endpoint; `%` = histórico total; usar `/api/stats/timeseries` para ventana | Revisar API | ☐ |

## 13. IA: inferencia ONNX

| # | Pregunta | Comportamiento esperado | Prueba | Res. |
|---|----------|-------------------------|--------|------|
| 113 | ¿Preprocesado antes del ONNX? `[✔]` | resize 640 → letterbox pad 114 → BGR2RGB `/255` → NCHW float32 batch 1 | Revisar `inference.py` | ☐ |
| 114 | ¿Postproceso YOLOv8? `[✔]` | `(1,4+nc,N)`→T→argmax→conf≥0.35→xywh→xyxy→despad→clip→NMS IoU 0.45 | Upload con 2 objetos | ☐ |
| 115 | ¿Umbral de confianza? `[✔]` | 0.35 = máx F1 (0.781) medido; curva plana 0.30–0.45 | `tune_confidence.py` | ☐ |
| 116 | ¿Tiempo de inferencia? `[✔]` | ~98 ms media, p95 ~121 ms en CPU | `inference_ms` en respuestas | ☐ |
| 117 | ¿Performance en TACO real? `[◐]` | P=0.394, R=0.198 — piso pesimista; dominio agua es más simple | `eval_upload_path.py` | ☐ |
| 118 | ¿Excepción durante `predict`? `[✔]` | Devuelve `source=none` + `error`; el upload completa igual | Romper ONNX temporal | ☐ |
| 119 | ¿Métricas v2 vs baseline? `[✔]` | outdoor +0.183, taco +0.120, clean −0.036 mAP50; plastico −0.097 | `outdoor_training_report.md` | ☐ |
| 120 | ¿Selección de `best.pt`? `[✔]` | Fitness ≈ mAP50-95; v2 mejor época 13 (0.6533), patience 15/60 | `runs/detect/` | ☐ |

## 14. IA: datasets y entrenamiento

| # | Pregunta | Comportamiento esperado | Prueba | Res. |
|---|----------|-------------------------|--------|------|
| 121 | ¿Qué datasets alimentan el train? `[✔]` | synthetic 360, boost 180, taco 750, outdoor_eval 120; train combinado 1022 | `ls datasets/*/images` | ☐ |
| 122 | ¿Mapeo TACO→REBA? `[✔]` | `TACO_TO_REBA`: 60 cats COCO→3 clases; sin equivalente = fondo | `download_taco.py` | ☐ |
| 123 | ¿Aumentaciones outdoor? `[✔]` | brightness/gamma/shadow/sunflare/fog/rain/defocus/motionblur/HSV/gray/noise/jpeg/dropout + cutmix/mixup/erasing | `outdoor_transforms.py` | ☐ |
| 124 | ¿Por qué solo pixel-level? `[✔]` | No cambian cajas → labels YOLO exactas | Revisión del pipeline | ☐ |
| 125 | ¿Resume de entrenamiento? `[◐]` | `last.pt` + `resume=True`; interrumpido 3 veces; patience 15 | `runs/detect/REBA_outdoor_v2` | ☐ |
| 126 | ¿`evaluate_outdoor.py` produce? `[✔]` | baseline vs nuevo en clean/outdoor/taco → `outdoor_training_report.md` | Correr script | ☐ |
| 127 | ¿`eval_upload_path.py` mide? `[✔]` | Sube a `/upload` real, AP@0.5 vs GT → `eval_upload_report.json` | Correr script | ☐ |
| 128 | ¿Dataset faltante? `[◐]` | ~50–100 capturas acuáticas reales (DB solo ~4 fotos) | Contar captures reales | ☐ |

## 15. Dashboard

| # | Pregunta | Comportamiento esperado | Prueba | Res. |
|---|----------|-------------------------|--------|------|
| 129 | ¿Acceso al dashboard? `[✔]` | `http://IP-PC:5000` u `http://192.168.137.1:5000` (REBA-AP); firewall TCP 5000; nunca `file://` | Celular + PC | ☐ |
| 130 | ¿Cómo se actualiza? `[✔]` | Snapshot REST + WS `hello` + eventos; ping 25 s; poll health 30 s | DevTools Network/WS | ☐ |
| 131 | ¿Reconexión del WS? `[✔]` | Backoff 1.5 s ×1.5, tope 10 s; muestra "Reconectando" | Cortar server y volver | ☐ |
| 132 | ¿Módulos del dashboard? `[✔]` | Pill device, última captura, donut, galería+lightbox, bomba, actividad, export YOLO, config ESP32 | Recorrer la UI | ☐ |
| 133 | ¿Bomba manual desde UI? `[✔]` | Segundos 1–300 → `POST /api/pump/run` → `pending_cmd` → ack limpia | Activar 5 s | ☐ |
| 134 | ¿Galería de capturas? `[✔]` | 24/pág, filtro por clase, lazy images, lightbox con detections; WS pone nuevas arriba | Paginar y filtrar | ☐ |
| 135 | ¿Panel Actividad? `[✔]` | Barras por día/clase de `/api/stats/timeseries` (7/14/30 d), refresh 30 s, canvas DPR | Cambiar selector días | ☐ |
| 136 | ¿Responsive móvil? `[✔]` | <900 px → 1 columna, táctil ≥44 px, lightbox móvil | Celular en REBA-AP | ☐ |

## 16. Escenarios de fallo: respuesta del sistema

| # | Pregunta | Comportamiento esperado | Prueba | Res. |
|---|----------|-------------------------|--------|------|
| 137 | ¿Wi-Fi cae con fotos en cola? `[✔]` | Reintentan cada 15 s; al volver drenan FIFO; >~6 min/foto se descarta | Apagar Wi-Fi + capturas | ☐ |
| 138 | ¿Server apagado/reiniciado? `[◐]` | Cola del ESP32; device offline >150 s; `pump_state` a 0; al volver heartbeat restaura | Apagar uvicorn 3 min | ☐ |
| 139 | ¿`server_url` a host inexistente? `[✔]` | `srv_bak` guarda anterior; revierte en 6 min; uploads a cola mientras tanto | Dashboard→URL mala | ☐ |
| 140 | ¿Credenciales Wi-Fi malas? `[✔]` | `ssid_bak/pass_bak`; revierte en 90 s; ssid+pass viajan como par atómico | Dashboard→creds malas | ☐ |
| 141 | ¿Objeto quieto en rango? `[✔]` | Solo 1 foto; rearma tras 3 lecturas fuera | Objeto fijo 10 s | ☐ |
| 142 | ¿Objetos seguidos? `[◐]` | Debe retirarse el primero (~180 ms) para rearmar; posible submuestreo en corriente continua | Dos objetos rápidos | ☐ |
| 143 | ¿Objeto a 30 cm? `[✔]` | Fuera de rango → no dispara aunque el sensor lo detecte (hasta ~4 m) | Objeto a 30 cm | ☐ |
| 144 | ¿JPEG corrupto llega al server? `[✔]` | Firmware valida magic; server re-valida; 400 → ESP32 descarta sin reintentar | Subir basura con magic malo | ☐ |

## 17. Pruebas y recomendaciones actuales

| # | Pregunta / acción | Detalle y resultado esperado | Prioridad | Res. |
|---|-------------------|------------------------------|-----------|------|
| 145 | Pruebas físicas de banco pendientes | HC-SR04 objeto <23 cm → serial `>> Ingreso`; 3 `CAPTURE` → relé con multímetro; manual E2E; estrés cola >6 fotos | Alta | ☐ |
| 146 | Prueba de config remota | `dist_max_cm=30` → aplica ≤60 s en `STATUS`; inválidos (`pump_photos=999`, `server_url=ftp://x`) rechazados; reversión Wi-Fi con creds malas | Media | ☐ |
| 147 | Probar retención sin esperar 1 h | `CAPTURE_RETENTION=5` + reinicio, o `db.prune_captures(5)` manual; detections quedan `capture_id=NULL` | Media | ☐ |
| 148 | Métricas a registrar en campo | `inference_ms` por captura, latencia upload→UI, tasa `source=none`, distribución de confianza por clase | Media | ☐ |
| 149 | Pendientes antes del agua real | HC-SR04 cableado+trigger, ciclo bomba físico, carcasa estanca, ~50–100 fotos acuáticas para dataset, +datos de `plastico`, decidir persistencia de cola (LittleFS) | Alta | ☐ |
| 150 | Hallazgos para el informe | `SET pump_cmd` doc≠código; `wifi_pass` en claro + CORS `*`; counters sin reset; cola solo RAM; `Serial.begin` duplicado; época 13 vs 16 del ONNX | Baja | ☐ |

## 18. Pruebas de campo — software y modelo

Plan de la jornada en intemperie (**PC + ESP32 + celular en el hotspot del
celular** — misma LAN, sin depender de Internet para el ESP32; el dashboard
del celular puede abrirse por `http://<IP-PC>:5000`, por `https://rebaserver.tech`
(túnel Cloudflare) o por el respaldo zrok si se quiere acceso desde otra red;
la ESP32 remota usa `server_url_alt=http://rebaserver.tech` por failover).
Procedimiento operativo
en `docs/manual_uso.md` §4. Marcar resultados al terminar el día.

| # | Pregunta | Comportamiento esperado | Prueba | Res. |
|---|----------|-------------------------|--------|------|
| 151 | ¿El ESP32 asocia al hotspot del celular? `[◐]` | Wi-Fi 2.4 GHz del hotspot + datos móviles → `STATUS` wifi=OK / píldora EN LÍNEA | `STATUS` por serie / dashboard | ☐ |
| 152 | ¿El upload funciona en la LAN del hotspot? `[◐]` | Objeto <23 cm → foto → `POST /upload` a `http://<IP-PC>:5000` → capture en DB ≤60 s | Objeto real + dashboard | ☐ |
| 153 | ¿El dashboard remoto recibe WS? `[◐]` | Eventos `capture`/`detections` en vivo desde datos móviles (`wss://` automático) | DevTools WS en el celular | ☐ |
| 154 | ¿La bomba automática dispara en campo? `[◐]` | 3 fotos válidas → `pump_event` inferido + relé conmuta ~10 s | Objeto 3 veces + registro eventos | ☐ |
| 155 | ¿La bomba manual remota funciona por túnel? `[◐]` | `pump/run` → `pump_cmd` → ESP32 lo recoge ≤60 s → `ack` → `cleared:true` | Dashboard "Activar ahora" | ☐ |
| 156 | ¿La config remota aplica por túnel? `[◐]` | `dist_max_cm` u otra clave → poll ≤60 s → valor nuevo en `GET`/`STATUS` | Dashboard → Dispositivo | ☐ |
| 157 | ¿La cola retiene fotos sin señal? `[◐]` | Apagar datos del hotspot → fotos en cola (máx 6); al volver drenan FIFO; la más vieja se pierde si se llena | Cortar señal + capturas | ☐ |
| 158 | ¿El server local sigue operativo? `[◐]` | `localhost:5000` en la PC sirve igual aunque el ESP32 venga por Internet | `curl /api/health` en la PC | ☐ |
| 159 | ¿Las fotos de campo quedan limpias? `[◐]` | `image_orig_jpeg` crudo en DB → base para reentrenar; `image_jpeg` anotado para el dashboard | `GET /api/captures/{id}/image?orig=1` | ☐ |
| 160 | ¿Tasa de falsos positivos en agua limpia? `[◐]` | Escenas sin objetos → anotar detecciones espurias (clase + confianza) para calibrar umbral | Fotos de agua limpia | ☐ |
| 161 | ¿Latencia de inferencia en campo? `[◐]` | `inference_ms` puede subir (CPU compartida con entrenamiento v5): registrar media/rango real | `inference_ms` en uploads | ☐ |
| 162 | ¿Confianza de detecciones reales? `[◐]` | Distribución por clase en objetos acuáticos reales — insumo para `tune_confidence.py` posterior | Captures + detections de la jornada | ☐ |
| 163 | ¿Export YOLO post-jornada? `[◐]` | `Exportar YOLO` descarga ZIP con fotos reales de campo + labels → dataset para próximo entrenamiento | Botón en dashboard → Analítica | ☐ |
| 164 | ¿OTA actualiza sin USB? `[◐]` | `ota_url` (config remota o `SET` serie) → ESP32 descarga `firmware.bin`, flashea partición inactiva, reinicia y reporta `ota_last` en `GET` | Tras el flash USB inicial de Nabiki | ☐ |

---

## Anexo — Prioridades de prueba

| Prioridad | Prueba | Estado |
|-----------|--------|--------|
| Alta | Trigger HC-SR04 con objeto <23 cm | Pendiente (sin lecturas en banco) |
| Alta | Ciclo automático 3 fotos → relé/bomba | Pendiente verificación física |
| Alta | Capturas acuáticas reales → dataset | Pendiente (~4 fotos en DB) — **en curso: jornada de campo 14-nov** |
| Alta | ESP32 + PC + celular en hotspot compartido (sin Wi-Fi local) | Preparado — pendiente ejecución en campo; remoto con Internet: `server_url_alt=http://rebaserver.tech` (Cloudflare, HTTP plano) |
| Media | Estrés de cola offline (>6 fotos) | Parcialmente probado |
| Media | Reversión Wi-Fi/server_url remotos | Verificado en banco |
| Media | Retención `CAPTURE_RETENTION` | Verificado con prune manual |
| Media | OTA: primer flash USB (Nabiki) + update remoto posterior | Binarios listos en `firmware/`; update OTA compilado, sin probar en hardware aún |
| Baja | `SET pump_cmd` por serie | Discrepancia doc/código |
| Baja | Carcasa estanca y montaje | Pendiente fabricación |
