# REBA — Diagramas de arquitectura y UML

Resumen visual del sistema para el **pitch técnico**. Cada diagrama sintetiza la documentación existente (`README.md`, `docs/*.md`, `PROGRESO.md`) y el código real del repositorio.

**Cómo verlos:** abre el `.png` o `.svg` correspondiente. Los fuentes `.puml` (PlantUML) permiten regenerar o editar — descarga `plantuml.jar` (https://plantuml.com/download) y ejecuta:

```bash
java -jar plantuml.jar -tpng docs/diagramas/*.puml docs/diagramas/uml/*.puml
# o para SVG escalable:
java -jar plantuml.jar -tsvg docs/diagramas/*.puml docs/diagramas/uml/*.puml
```

> Nota UML: los tipos *comunicación*, *tiempos* y *visión general de interacción* no tienen soporte nativo en PlantUML; se aproximan con mensajes numerados, líneas temporales anotadas y un diagrama de actividad con referencias — indicado en cada archivo.

---

## Arquitectura de software

| Archivo | Qué muestra |
|---------|-------------|
| `arquitectura_software.puml` / `.png` / `.svg` | **Arquitectura detallada por capas**: presentación (dashboard vanilla + simulador ESP32), aplicación (FastAPI :5000, 26 endpoints REST + WS), dominio (InferenceEngine ONNX, Database SQLite, Settings), infraestructura (REBA.db, best.onnx v5 ép.30, datasets, firmware/, túnel cloudflared), pipeline IA offline y nodo edge ESP32-CAM con sus módulos internos (failover server_url, OTA, telemetría). Incluye los 5 flujos principales numerados. |

---

## Diagramas estructurales UML

| # | Archivo | Tipo UML | Sintetiza |
|---|---------|----------|-----------|
| 1 | `uml/01_diagrama_clases` | **Clases** | `server/` (Settings, Detection, InferenceResult, InferenceEngine, Database, RebaAPI), firmware `fotos_nc` y `app.js` — con atributos y métodos reales del código (incl. failover `conmutarSrv`, OTA, `reportarEstado`, tema claro/oscuro). |
| 2 | `uml/02_diagrama_objetos` | **Objetos** | Instancia real en operación: ESP32 (IP, config NVS con `server_url_alt`/`ota_last`, cola 1/6), server, engine ONNX, captura id=10 con 2 detecciones, pump_event manual, device_config, telemetría `device_status` y dashboard conectados por WS. |
| 3 | `uml/03_diagrama_componentes` | **Componentes** | Componentes de software y hardware con interfaces «provide/require»: contrato HTTP ESP32↔server (upload·config·ack·status·firmware.bin), REST+WS↔dashboard, predict↔ONNX, CRUD↔SQLite y túnel Cloudflare como acceso remoto. |
| 4 | `uml/04_diagrama_estructura_compuesta` | **Estructura compuesta** | Internos de `RebaAPI`: partes (upload, REST ~19 GET/5 POST/1 DELETE, device_config, pump_cmd, device_status, firmware/reset, export, WS, process_upload con sus 5 sub-pasos, retention, broadcast, heartbeat) y conectores con puertos HTTP/WS/config. |
| 5 | `uml/05_diagrama_paquetes` | **Paquetes** | Organización del repo: `server/`, `static/` (+simulador), `esp32_firmware/`, `firmware/` (bin OTA), `tools/` (túneles), `ai_pipeline/`, `models/`, `datasets/`, `data/`, `docs/`, `runs/` + dependencias reales (imports, datasets→modelo, firmware→contrato HTTP, cloudflared→:5000). |
| 6 | `uml/06_diagrama_despliegue` | **Despliegue** | Nodos físicos/lógicos: ESP32-CAM + sensores/relé, cadena de potencia (solar→TP4056→18650→XL6009→bomba, 5V dedicada), PC con artefactos (uvicorn, cloudflared, best.onnx, REBA.db, static/, firmware/, .env), red LAN Wi-Fi 2.4 GHz, edge Cloudflare `rebaserver.tech` (HTTP :80 ESP32 · HTTPS :443 dashboard) y navegadores clientes locales y remotos. |

## Diagramas de comportamiento UML

| # | Archivo | Tipo UML | Sintetiza |
|---|---------|----------|-----------|
| 7 | `uml/07_diagrama_casos_de_uso` | **Casos de uso** | 4 actores (Operador, Técnico, Nodo ESP32, Ingeniero IA), 21 casos de uso con `<<include>>`/`<<extend>>` — desde ver el dashboard hasta entrenar el modelo offline, OTA, failover y telemetría. |
| 8 | `uml/08_diagrama_actividades` | **Actividades** | Flujo E2E con swimlanes ESP32 / Red (LAN o túnel) / Servidor / Dashboard: debounce → flash → captura → cola → upload → validación → ONNX → persistencia → WS → UI, con caminos de fallo (reintento, 4xx, bomba) y tareas en paralelo (telemetría, OTA, failover). |
| 9 | `uml/09_maquina_estados` | **Máquina de estados** | Ciclo de vida de una captura: detección → captura → cola RAM → envío → entregada → procesada en server → notificada; estados de reintento, descarte anti poison-pill y rechazo. |

## Diagramas de interacción UML

| # | Archivo | Tipo UML | Sintetiza |
|---|---------|----------|-----------|
| 10 | `uml/10_diagrama_secuencia` | **Secuencia** | Flujo principal completo: trigger → debounce → flash → foto → cola → `POST /upload` → validación → `predict` ONNX → `_draw_boxes` → persistencia → broadcast WS → respuesta 200 → contador bomba; más camino alterno de fallo, poll de config con telemetría (`/api/device/status`), OTA, `queue_clear` y failover de `server_url`. |
| 11 | `uml/11_diagrama_comunicacion` | **Comunicación** *(aprox.)* | Activación manual de bomba: 19 mensajes numerados dashboard→`/api/pump/run`→`device_config`→poll ESP32→`pump_cmd`→ejecución no bloqueante→`ack`→`pending_cmd=null`. |
| 12 | `uml/12_diagrama_tiempos` | **Tiempos** *(aprox.)* | Restricciones temporales: sensor 60 ms, debounce ~120 ms, flash 150 ms, inferencia ~92 ms (p95 121 ms), reintento 15 s, config poll + telemetría 60 s, device online <150 s, bomba 10 s, pump_cmd TTL 10 min, retención 1 h, WS ping 25 s, failover 3 polls + probe 5 min, OTA ~1–2 min. |
| 13 | `uml/13_interaccion_overview` | **Visión general interacción** *(aprox.)* | Mapa de interacciones: actividad con lanes que referencia los diagramas §10 (secuencia) y §11 (comunicación) + procesos en background (poll config, retención). |

---

## Narrativa para el pitch (orden sugerido)

1. **`arquitectura_software`** — el sistema en una imagen: edge inteligente + PC con IA local + dashboard, offline-first con acceso remoto opcional por túnel Cloudflare.
2. **`06_despliegue`** — dónde corre cada cosa físicamente (ESP32, PC, red, energía).
3. **`10_diagrama_secuencia`** — el flujo estrella: objeto → foto → YOLO → dashboard.
4. **`07_casos_de_uso`** — qué puede hacer cada actor.
5. **`01_clases` + `05_paquetes`** — cómo está construido el software.
6. **`09_maquina_estados` + `08_actividades`** — robustez: colas, reintentos, descarte.
7. **`11_comunicacion` + `12_tiempos`** — el canal de comandos inverso (dashboard→bomba) y los deadlines.
8. **`02_objetos` + `03_componentes` + `04_estructura_compuesta` + `13_interaccion_overview`** — detalle de instancia y composición para preguntas técnicas.

**Ideas fuerza respaldadas por los diagramas:** offline-first (ONNX CPU local, dashboard sin build ni CDN) con acceso remoto opcional por túnel Cloudflare (`rebaserver.tech`), edge autónomo con cola anti-pérdida y failover automático de `server_url`, configuración sin reflashear con reversión automática, OTA por HTTP plano, telemetría del circuito, y separación estricta potencia/lógica (5V vs 12V, relé NC).
