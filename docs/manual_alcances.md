# REBA — Manual de alcances

Documento de alcance del proyecto: **qué hace el sistema, qué no hace, con qué calidad medida y bajo qué limitaciones**. Sirve como referencia para defensas académicas, planificación de trabajo futuro y decisiones de diseño.

> **Contexto:** proyecto académico ARC115 (UES) — prototipo de plataforma flotante autónoma de recolección de residuos acuáticos con visión artificial local. Uso educativo y de prototipo; **no** es un producto certificado para operación comercial.

---

## 1. Propósito

Demostrar un sistema IoT + visión artificial **100% offline** que:

1. Detecta la entrada de objetos a una zona de captura mediante ultrasonido.
2. Fotografía el evento, lo clasifica en 5 tipos de residuo con YOLOv8 local y lo archiva.
3. Acciona una bomba en ciclos para retirar la basura acumulada.
4. Presenta el estado del sistema en un dashboard web accesible desde un celular en la misma red.

---

## 2. Alcance funcional — qué SÍ hace

### 2.1 Detección y captura (ESP32-CAM + HC-SR04)

| Capacidad | Detalle |
|-----------|---------|
| Trigger por proximidad | HC-SR04 dispara cuando un objeto entra al rango configurable (**0–23 cm** por defecto) |
| Anti-rebote | 2 lecturas consecutivas dentro / 3 fuera para ignorar ruido y salpicaduras |
| Captura por evento | JPEG QVGA con flash LED (~150 ms) — no video continuo |
| Cola de contingencia | Hasta 6 fotos en RAM se reintentan cada 15 s si el Wi-Fi cae; descarta en error permanente (anti poison-pill) |
| Ciclo de bomba automático | Cada `pump_photos` fotos (default 3) → relé energiza ~`pump_seconds` (default 10 s) |
| Autonomía | Batería 18650 + panel solar + boost 12 V; el ESP32 a 5 V dedicado |

### 2.2 Servidor de inferencia (PC)

| Capacidad | Detalle |
|-----------|---------|
| Clasificación local | YOLOv8n exportado a ONNX, CPU — 3 clases: `plastico`, `unicel`, `metal` |
| Latencia | ~50–130 ms por foto (p95 ~120 ms) en la PC de desarrollo |
| Persistencia | SQLite local: doble JPEG por captura (anotado para dashboard + crudo para dataset), detecciones con confianza y caja, contadores, eventos de bomba, config del dispositivo |
| Retención | Poda horaria de capturas > `CAPTURE_RETENTION` (default 2000 ≈ 10 MB) |
| Tiempo real | WebSocket `/ws` empuja capturas, detecciones y eventos al dashboard |
| Respaldo de evidencia | Fotos sin detección se guardan igual (útiles como negativos para reentrenar) |

### 2.3 Dashboard web (móvil primero)

| Capacidad | Detalle |
|-----------|---------|
| 3 vistas | `En vivo` (captura + bomba), `Analítica` (KPIs + gráficas + galería), `Dispositivo` (config remota) |
| Tiempo real | WebSocket: nuevas capturas aparecen solas; pill de estado ESP32/WS |
| Control de bomba | Progreso visual segmentado + disparo manual (5/10/20/30 s u otro) con confirmación por ack del dispositivo |
| Analítica | Donut por clase, barras apiladas 7/14/30 días con tooltips, KPIs |
| Galería | Paginada (24/pág), filtro por clase, lightbox con detecciones y confianza |
| Export YOLO | ZIP con `images/` + `labels/` + `data.yaml` para reentrenar |
| Config remota | WiFi, URL servidor, bomba, distancias — aplicadas en caliente por el ESP32 (≤60 s) con reversión automática si fallan |
| Offline-first | Sin CDN ni recursos externos; fuentes woff2 locales; funciona en LAN sin Internet |

### 2.4 Configuración sin reflashear

Tres vías redundantes, todas con la misma validación estricta y **reversión automática** ante configuraciones malas:

- Portal Wi-Fi `REBA-Setup` (celular, sin instalar nada)
- Comandos serie USB (`GET`/`SET`/`CAPTURE`/`STATUS`/`REBOOT`)
- Dashboard web (canal `device_config` servidor→ESP32 con heartbeat)

### 2.5 Pipeline de IA

| Capacidad | Detalle |
|-----------|---------|
| Datasets | Sintético acuático, boosts, TACO + EcoSort + FloatWaste (agua real) + copy-paste, eval degradado — ~4,580 imgs train combinadas |
| Entrenamiento | `train_outdoor.py` con augmentations de intemperie (brillo, sombra, niebla, lluvia, blur, HSV, dropout, cutmix) + early stopping |
| Evaluación | Offline (clean/outdoor/TACO) + **path de producción real** vía `eval_upload_path.py` contra `/upload` |
| Reentrenamiento | Botón Exportar YOLO → mezcla con datasets → nuevo `best.onnx` |

---

## 3. Alcance NO funcional — qué NO hace

| Exclusión | Justificación |
|-----------|---------------|
| **Navegación / evasión de obstáculos** | La plataforma es **pasiva**: la corriente lleva la basura a la cesta; el sistema solo detecta, clasifica y bombea. Sin GPS, sin planeación de ruta, sin sensores de colisión |
| **Video en vivo / streaming** | La ESP32 solo sube fotos por evento; no hay stream RTSP ni visualización continua (limitación de ancho de banda y CPU del ESP32-CAM) |
| **Sumergibilidad** | La ESP32-CAM **no es estanca**; requiere carcasa sellada externa. El HC-SR04 va fuera del agua por física del ultrasonido |
| **Autenticación / seguridad** | Dashboard y API **sin login** — decisión deliberada: la red `REBA-AP` aísla el sistema. **No exponer el puerto 5000 a redes no confiables** |
| **Control de potencia de la bomba** | Bomba binaria ON/OFF por relé; sin PWM, sin control de caudal, sin realimentación de presión/flujo |
| **Telemetría remota** | Sin Internet: no hay nube, MQTT ni acceso fuera de la LAN. Todo vive en la PC servidor |
| **Multi-dispositivo** | Un solo ESP32-CAM por servidor; no hay gestión de flotas ni balanceo |
| **Identificación individual de objetos** | Clasifica **tipo de material** por foto, no cuenta objetos únicos ni sigue trayectorias (frame-a-frame independiente) |
| **Garantía de captura total** | Si el Wi-Fi cae, la cola RAM retiene máx. 6 fotos; un reboot las pierde. El conteo de bomba del servidor es **inferido**, no exacto |

---

## 4. Limitaciones conocidas y riesgos

### 4.1 Hardware

| Limitación | Impacto | Mitigación actual |
|------------|---------|-------------------|
| **PSRAM defectuoso** en la placa | `ps_malloc` falla aunque `psramFound=1` | Framebuffer en DRAM (QVGA); ~265 KB heap libre verificado |
| **Relé NC** (fail-ON) | Si el ESP32 pierde energía, la bomba queda ON mientras haya 12 V | Toda la cadena comparte batería — sin 12 V no hay bomba; `bombaApagar()` al boot; alternativa NO documentada |
| **HC-SR04 sin validar en banco** | Trigger ultrasónico no probado físicamente aún | `CAPTURE` por serial prueba todo el path de imagen; falta solo el trigger |
| **Cola de fotos solo RAM** | Un reboot/brownout pierde pendientes | Aceptable (evidencia transitoria); LittleFS evaluable a futuro |
| **Batería/solar sin dimensionar** | Autonomía real desconocida | Mediciones de consumo pendientes en agua |
| **Rango fijo 0–23 cm** | Si el fondo de la cesta queda dentro del rango, cuenta "siempre objeto" | Ajustable por config remota según geometría real |

### 4.2 Modelo de IA

| Limitación | Impacto | Estado |
|------------|---------|--------|
| **Dominio sim→real** | El modelo se entrenó con sintético + TACO terrestre; las fotos acuáticas reales pueden diferir | Pendiente: primeras capturas REBA = dataset real para reentrenar |
| **`plastico` retrocede** en outdoor | mAP50-95 -0.097 vs baseline | Clase más débil; candidato a más datos de entrenamiento |
| **Umbral 0.35** | Balance precisión/recall; escenas ambiguas pueden dar falsos negativos | Afinado por `tune_confidence.py` (máx F1); recalibrar con datos acuáticos |
| **Sin oclusión severa / noche extrema** | La basura muy tapada o con poca luz puede no detectarse | El flash mitiga poca luz; `CoarseDropout`/`RandomShadow` en augmentations |

### 4.3 Sistema

| Limitación | Impacto |
|------------|---------|
| **Sin auth** | Cualquiera en la LAN puede ver datos y disparar la bomba — aceptable en red aislada, **no** en Wi-Fi público |
| **`server_url` grabada en ESP32** | Cambiar de red exige reconfigurar (remoto desde dashboard, pero requiere que el ESP32 siga alcanzando *algún* server) |
| **`device.online` es por heartbeat** | Una PC que apaga el server >2.5 min marca el ESP32 como offline aunque esté bien |
| **Conteo de bomba inferido** | Si la cola retiene fotos sin Wi-Fi, el progreso mostrado se atrasa respecto al real del firmware |

---

## 5. Métricas de desempeño medidas

### 5.1 Reconocimiento (modelo outdoor v2, época 13)

> **Nota histórica:** estas métricas corresponden al modelo de 5 clases
> (`plastico, tela, organico, unicel, metal`) anterior al pivote a 3
> clases. `tela` y `organico` fueron eliminadas; las cifras de abajo se
> conservan como referencia del baseline. El modelo actual entrena solo
> `plastico, unicel, metal` — ver `reports/experimentos.md` EXP-3.

| Conjunto | ΔP | ΔR | ΔmAP50 | ΔmAP50-95 |
|----------|-----|-----|--------|-----------|
| clean (sintético) | -0.106 | -0.046 | -0.036 | -0.151 |
| outdoor (degradado) | +0.079 | +0.180 | +0.183 | +0.044 |
| taco_real | +0.099 | +0.271 | +0.120 | +0.062 |

- mAP50-95 en val: **0.6533** · F1 máximo medido: **0.781** (umbral 0.35)
- Por clase outdoor (mAP50-95): metal +0.187, unicel +0.070, tela +0.053, organico +0.004, **plastico -0.097**

### 5.2 Path de producción real (`POST /upload`, TACO test 74 imgs, conf 0.35)

| Métrica | Valor |
|---------|-------|
| Precision | 0.394 |
| Recall | 0.198 |
| F1 | 0.264 |
| Inferencia media | 97.8 ms (p95 121 ms) |

> **Lectura honesta:** en escenas terrestres reales el sistema es **conservador** (pierde ~76% de objetos). El dominio objetivo (basura flotando, vista cenital QVGA) es más simple — estas cifras son un **piso pesimista**, no la expectativa en agua. Las capturas acuáticas reales decidirán el desempeño final.

### 5.3 Sistema

| Métrica | Valor |
|---------|-------|
| Flash firmware | 1 134 541 B (**36%** del ESP32) |
| Heap ESP32 | ~265 KB libre en boot |
| Retención | 2000 capturas ≈ 10 MB SQLite |
| Poll de config | 60 s (canal servidor→ESP32) |
| TTL comando bomba | 10 min |
| Ventana "online" | 150 s desde último heartbeat |

---

## 6. Requisitos no funcionales cumplidos

| Requisito | Estado |
|-----------|--------|
| **100% offline** | ✅ Sin Internet en operación: ONNX local, SQLite local, fuentes woff2 locales, sin CDN |
| **Móvil primero** | ✅ Dashboard táctil 375×812 verificado; controles ≥44 px; bottom nav |
| **Sin infraestructura** | ✅ SQLite embebido, FastAPI single-process, sin Docker ni servicios externos |
| **Auto-recuperación** | ✅ Reversión de config WiFi/URL, anti poison-pill en cola, re-init de cámara, heartbeat |
| **Tolerancia a fallos de red** | ✅ Cola de 6 fotos, reintentos, TTL de comandos, ack de bomba |

---

## 7. Trabajo futuro (roadmap)

| Prioridad | Ítem | Por qué |
|-----------|------|---------|
| **Alta** | Capturas acuáticas reales + reentrenamiento | El dataset actual es sintético+terrestre; el dominio real falta — es la brecha #1 de precisión |
| **Alta** | Prueba física HC-SR04 + ciclo 3-fotos→bomba | Verificado en código y banco parcial; falta la prueba en agua |
| Media | Dimensionar autonomía (batería/solar) | Sin datos de consumo real en campo |
| Media | Evaluar persistencia de cola en LittleFS | Hoy un reboot pierde hasta 6 fotos pendientes |
| Media | Reforzar clase `plastico` | Única clase que retrocede en outdoor |
| Baja | Fail-safe de relé (cablear NO) | Si se requiere OFF garantizado ante pérdida total de ESP32 |
| Baja | Estimación de caudal/eficiencia de bomba | Requiere sensor de flujo adicional |

---

## 8. Criterios de aceptación del proyecto

El sistema se considera **funcionalmente completo** cuando:

- [x] Captura por trigger ultrasónico y sube JPEG al servidor (verificado en banco: HTTP 200 → ONNX → SQLite)
- [x] Clasifica en las 3 clases con ONNX local (~100 ms)
- [x] Dashboard operativo en móvil y escritorio, sin Internet
- [x] Bomba automática por conteo de fotos + manual remota con ack
- [x] Config remota sin reflashear con reversión automática
- [ ] **Pendiente:** operación continua en agua real con carcasa sellada
- [ ] **Pendiente:** métricas de precisión sobre capturas acuáticas reales

---

## Referencias

- [`componentes.md`](componentes.md) — función/decisión por componente
- [`manual_implementacion.md`](manual_implementacion.md) — despliegue paso a paso
- [`manual_uso.md`](manual_uso.md) — operación del dashboard
- [`server.md`](server.md) · [`firmware.md`](firmware.md) · [`hardware.md`](hardware.md) · [`ai_pipeline.md`](ai_pipeline.md)
- [`../PROGRESO.md`](../PROGRESO.md) — bitácora de sesiones de desarrollo
- [`../outdoor_training_report.md`](../outdoor_training_report.md) — métricas detalladas del modelo
