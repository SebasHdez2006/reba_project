# REBA — Manual de uso (operador)

Guía práctica para operar el sistema día a día: abrir el dashboard, leer lo que muestra y ejecutar las acciones disponibles. No requiere conocimientos de programación.

> **Requisito único:** el teléfono o la PC deben estar en la **misma red** que el servidor REBA (el hotspot `REBA-AP` o la Wi-Fi local donde corre la PC). El sistema **no necesita Internet**.

---

## 1. Acceso al dashboard

| Desde | URL |
|-------|-----|
| PC servidor | `http://localhost:5000` |
| Celular en hotspot `REBA-AP` | `http://192.168.137.1:5000` |
| Celular en la misma Wi-Fi que la PC | `http://<IP-de-la-PC>:5000` (ver con `ipconfig`) |

- Funciona en cualquier navegador moderno (Chrome, Edge, Safari).
- Si la página no carga: confirma que el servidor esté encendido y que el firewall de Windows permita el **puerto 5000**.
- La interfaz está diseñada **móvil primero**: en el teléfono usa la barra inferior con 3 pestañas; en PC todo se ve en una sola pantalla con secciones.

---

## 2. Qué ves al abrir

### Barra superior (siempre visible)

| Elemento | Significado |
|----------|-------------|
| **REBA** | Marca del sistema |
| **Píldora ESP32** | `EN LÍNEA` (verde) = el dispositivo reportó hace <2.5 min · `SIN SEÑAL` (ámbar) = no reporta — revisa Wi-Fi del ESP32 |
| **Píldora WS** | `EN VIVO` (verde) = conexión en tiempo real activa · `DESCONECTADO`/`REINTENTANDO` (rojo) = el dashboard no recibe eventos; recarga la página |
| **Ping** | Tiempo desde el último contacto del ESP32 ("hace 2 min") |

### Pestaña 1 — `En vivo`

**Última captura** — la foto más reciente de la cámara:

- Imagen en proporción 4:3 (la resolución real de la cámara).
- **Badge** sobre la imagen: clase dominante + confianza (`PLÁSTICO · 94%`, `TELA +1 · 73%`) o `SIN DETECCIÓN`.
- Debajo: fecha/hora, clases detectadas, número de objetos (`DET`), tiempo de inferencia (`INFERENCIA`, ~100 ms) y motor (`MOTOR`: `onnx` = IA activa, `none` = modelo no cargado).

**Tira de recientes** — hasta 8 miniaturas debajo de la foto principal. Toca una para verla en grande; la seleccionada queda marcada.

**Panel Bomba** — estado del ciclo de limpieza:

- `PROGRESO HACIA CICLO (1 / 3)` = fotos acumuladas hacia el próximo ciclo automático (la bomba se dispara sola cada 3 fotos).
- `DURACIÓN DEL CICLO (10 s)` = cuánto enciende la bomba por ciclo.
- Barra segmentada: cada segmento es una foto; el siguiente paso parpadea.
- Texto de referencia: cada N fotos → bomba ~M s (configurado en el dispositivo).

**Bomba manual** — para activar la bomba a mano:

1. Elige duración con los botones `5 s` / `10 s` / `20 s` / `30 s`, o `Otro` para escribir un valor (1–300 s).
2. Pulsa **Activar ahora** (botón verde).
3. Aparece `Comando en cola · el ESP32 lo recoge en su próximo poll (≤60 s)` — la bomba real se enciende cuando el dispositivo recoge la orden, **no al instante**.
4. El resultado queda registrado en **Registro de eventos** (despliega la sección; cada orden muestra hora y duración).

### Pestaña 2 — `Analítica`

- **KPIs**: `TOTAL OBJETOS` (detecciones acumuladas) y `FOTOS RECIBIDAS` (fotos subidas por el ESP32).
- **Gráfica de materiales (donut)**: distribución de las 5 clases — `plastico`, `tela`, `organico`, `unicel`, `metal`. Pasa el cursor (o toca) cada leyenda para ver conteo y porcentaje.
- **Gráfica de actividad**: barras apiladas de detecciones por día. Cambia el rango con `7 días` / `14 días` / `30 días`. Pasa el cursor sobre un día para ver el detalle por clase.
- **Galería de capturas**: todas las fotos clasificadas, más recientes primero.
  - Escribe en `Filtrar por clase…` (ej. `plastico`) para filtrar.
  - Toca una miniatura para abrirla **ampliada** con la lista de detecciones y su confianza.
  - **Cargar más** trae la siguiente página (24 por vez).
  - **Exportar YOLO** descarga un ZIP con las imágenes + etiquetas para reentrenar el modelo (ver §5).

### Pestaña 3 — `Dispositivo`

Configuración remota del ESP32-CAM — se aplica **sola** en el próximo poll del dispositivo (≤60 s), sin reflashear:

- **Red y servidor**: `WiFi SSID`, `WiFi contraseña` (botón `ver`/`ocultar`), `URL del servidor`.
- **Parámetros de operación**: `Fotos para bomba` y `Bomba` (segundos) en una fila; `Dist. mín` / `Dist. máx` (cm) en otra.
- Pulsa **Guardar y aplicar** → la píldora muestra `Guardado ✓` (o `Cargado` al abrir).
- `Recargar` trae los valores guardados de nuevo.
- El icono **?** despliega la explicación del modo de configuración del dispositivo.

> **Campos vacíos = sin cambio.** El firmware conserva su valor local; solo se sobrescribe lo que escribas. Para WiFi: si llenas el SSID debes llenar también la contraseña (o dejar ambos vacíos).

---

## 3. Interpretar lo que muestra

| Lo que ves | Significado | Acción |
|------------|-------------|--------|
| Píldora ESP32 `SIN SEÑAL` | El dispositivo no ha reportado en >2.5 min | Verificar que el ESP32 tenga Wi-Fi y que el servidor esté encendido |
| Píldora WS `DESCONECTADO` | El navegador perdió el canal en vivo | Recargar la página (F5) |
| Badge `SIN DETECCIÓN` | La foto llegó pero la IA no reconoció basura | Normal en escenas vacías; la foto igual se guarda como evidencia |
| `MOTOR: none` | El modelo ONNX no cargó en el servidor | Revisar `models/best.onnx` y `/api/health` |
| `INFERENCIA` alto (>500 ms) | PC ocupada o lenta | Esperar; la cola del ESP32 retiene fotos hasta 6 |
| `Progreso 0 / N` tras mucho tiempo | El ESP32 no está subiendo fotos | Revisar sensor ultrasónico / trigger |
| `Comando en cola` que nunca se ejecuta | ESP32 offline o `pending_cmd` expirado (>10 min) | Verificar píldora ESP32; reintentar la orden |
| Galería vacía | Aún no hay capturas | Normal en instalación nueva; hacer una prueba (§4) |

---

## 4. Procedimientos comunes

### Probar el sistema sin esperar al agua

1. Acerca la mano u objeto a **<23 cm** del sensor ultrasónico → debe disparar flash + foto.
2. En ~5–15 s aparece en `En vivo` (subida HTTP + inferencia ~100 ms).
3. Cada 3 fotos → ciclo de bomba automático. Para forzar una ahora: bomba manual §2.

### Verificar que la bomba responde

Dashboard → `En vivo` → escribe `5` en `Otro` → **Activar ahora** → en ≤60 s el relé conmuta y la bomba enciende ~5 s. El evento queda en `Registro de eventos` como "Bomba manual solicitada".

### Cambiar la red Wi-Fi del ESP32 (sin cables)

Dashboard → `Dispositivo` → llena `WiFi SSID` + `WiFi contraseña` + `URL del servidor` correctos → **Guardar y aplicar** → espera ~60 s. Si la red nueva falla, el ESP32 **revierte solo** a la anterior en ~90 s.

### Sacar fotos del agua para reentrenar el modelo

1. Deja el sistema capturando un tiempo razonable (decenas de fotos).
2. `Analítica` → **Exportar YOLO** → se descarga `reba_dataset_yolo.zip`.
3. Descomprime y úsalo como dataset en `ai_pipeline/train_outdoor.py` (ver `docs/ai_pipeline.md`).

### Diagnóstico rápido sin dashboard

En la PC con el servidor encendido:

```bash
curl http://localhost:5000/api/health     # motor ONNX + device.online
curl http://localhost:5000/api/stats      # contadores por clase
curl http://localhost:5000/api/pump/events # eventos de bomba + pending_cmd
```

---

## 5. Buenas prácticas de operación

- **Deja el servidor encendido** mientras el ESP32 esté en campo — sin servidor, las fotos se acumulan en la cola del ESP32 (máx. 6) y se pierden al reiniciarse.
- **No apagues el PC a mitad de un ciclo de bomba**: el comando manual expira solo a los 10 min (no hay bomba fantasma), pero el progreso de fotos se retrasa.
- La base de datos crece ~5 KB por foto; la retención automática conserva las últimas **2000** capturas (configurable en `.env` → `CAPTURE_RETENTION`).
- Si vas a mover el sistema a otra red: cambia `server_url` desde el dashboard **antes** de desconectar la PC actual.

---

## 6. Señales de que algo requiere atención técnica

| Señal | Dónde mirar |
|-------|-------------|
| `SIN SEÑAL` permanente del ESP32 | `docs/firmware.md` → troubleshooting (Wi-Fi, portal `REBA-Setup`) |
| Fotos llegan pero `detections` siempre vacío | `docs/server.md` → verificar `MODEL_PATH` / ONNX |
| La bomba nunca enciende | `docs/componentes.md` → cableado NC del relé, alimentación 12 V |
| Sensor no dispara | `docs/firmware.md` → comandos serie `STATUS`/`GET`, divisor ECHO |
| Dashboard sin WS | Recargar página; revisar que se abre por `http://IP:5000` y no `file://` |

Para detalles de cada subsistema: [firmware.md](firmware.md) · [server.md](server.md) · [hardware.md](hardware.md) · [componentes.md](componentes.md).
