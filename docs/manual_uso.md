# REBA — Manual de uso (operador)

Guía práctica para operar el sistema día a día: abrir el dashboard, leer lo que muestra y ejecutar las acciones disponibles. No requiere conocimientos de programación.

> **Formas de conexión:** el dashboard se abre por la **red local** (misma Wi-Fi o hotspot `REBA-AP`, sin Internet) o por el **túnel zrok** desde cualquier lugar con Internet. La ESP32 se conecta por **HTTP plano en la LAN/hotspot** — el túnel zrok es solo HTTPS y no lo usa el firmware (ver §1 y §4). También existe `/simulador_esp32.html`: un emulador del nodo ESP32-CAM que corre en el navegador del celular usando su cámara real.

---

## 1. Acceso al dashboard — todas las formas de conexión

| # | Escenario | URL | Requisito |
|---|-----------|-----|-----------|
| 1 | En la PC servidor | `http://localhost:5000` | uvicorn corriendo |
| 2 | Celular/laptop en la **misma Wi-Fi** que la PC | `http://<IP-de-la-PC>:5000` (ver `ipconfig`) | firewall Windows permite TCP 5000 |
| 3 | Celular en el **hotspot de la PC** (`REBA-AP`) | `http://192.168.137.1:5000` | hotspot móvil activo; **no requiere Internet** |
| 4 | **Túnel zrok** — cualquier dispositivo con Internet | `https://<nombre>.shares.zrok.io` | `zrok` corriendo (ver abajo) |
| 5 | **Simulador ESP32** en el celular | `/simulador_esp32.html` en la misma base | cámara del celular + contexto HTTPS |

- Funciona en cualquier navegador moderno (Chrome, Edge, Safari).
- Si la página no carga por LAN: confirma que el servidor esté encendido y que el firewall permita el **puerto 5000**.
- La interfaz está diseñada **móvil primero**: en el teléfono usa la barra inferior con 3 pestañas; en PC todo se ve en una sola pantalla con secciones.

### Sobre el túnel zrok (escenario 4)

- Expone el servidor como `https://<nombre>.shares.zrok.io` — **URL fija y gratuita** si se reservó una vez con `setup_zrok.bat` (cuenta gratuita en zrok.io, sin dominio). Se levanta con `campo.bat`/`retomar.bat` o manualmente vía `tools/zrok_tunnel.ps1`.
- **Sin reserva** el share es efímero: la URL cambia en cada arranque del túnel (reiniciar `uvicorn` **no** la cambia — son procesos independientes). Queda en `logs/tunnel.log` y `logs/zrok_url.txt`.
- **HTTPS obligatorio**: zrok solo sirve HTTPS → sirve para el navegador (dashboard, simulador), **no para la ESP32** (firmware sin TLS). La ESP32 va por la LAN/hotspot directo a `http://<IP-PC>:5000`.
- **Regla de oro**: con reserva configurada la URL no cambia nunca; sin ella, anota la URL nueva tras cada reinicio del túnel.
- Cuentas gratuitas sin verificar muestran una página intermedia anti-phishing ~1 vez por semana **solo en navegadores** — se salta con un clic; la ESP32 y `curl` la evitan.
- Gratuito hasta 5 GB/día; sin auth → tratar la URL como secreta.

> **ESP32 remoto (fuera de tu LAN):** zrok no le sirve (HTTPS-only y el
> firmware rechaza `https://` por el bug de cámara documentado en
> `fotos_nc.ino`). Si el dispositivo está en otra casa, hoy se mantiene
> reachable vía un **cloudflared quick tunnel** que sigue corriendo en la
> PC (URL `*.trycloudflare.com`, HTTP plano). Si ese proceso muere, la URL
> cambia y el ESP32 queda inalcanzable hasta reconfigurar `server_url`
> presencialmente. Detalle: `docs/plan_apagado_retorno.md` §topología.

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
- **Gráfica de materiales (donut)**: distribución de las 3 clases — `plastico`, `unicel`, `metal`. Pasa el cursor (o toca) cada leyenda para ver conteo y porcentaje.
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

### Prueba en campo sin Wi-Fi (todo en el hotspot del celular)

Para cuando el sitio no tiene red local — **PC, ESP32 y celular en el mismo
hotspot del celular** (el hotspot comparte los datos móviles, así que la PC
también tiene Internet si lo necesita):

**Antes de salir (en el laboratorio):**

1. En el ESP32, configura por el portal `REBA-Setup` (conecta tu celular a su AP → `http://192.168.4.1`) o por dashboard si aún es alcanzable:
   - `WiFi SSID` / `WiFi contraseña` = los de tu hotspot de celular
   - `URL del servidor` = `http://<IP-de-la-PC>:5000` (la IP que la PC tenga **dentro del hotspot** — ver `ipconfig` con el hotspot activo)
2. En el celular: activa el **hotspot** y conéctale la **PC** (Wi-Fi o USB
tethering) y el ESP32. Los tres quedan en la misma LAN.

**En el campo:**

3. Enciende el ESP32 → asocia al hotspot → en ≤2 min la píldora ESP32 pasa a `EN LÍNEA` en el dashboard.
4. Abre el dashboard en el celular por `http://<IP-de-la-PC>:5000` (misma red — **no hace falta túnel ni Internet para el ESP32**).
   - Si quieres ver el dashboard desde **otra red** (datos de otro chip, otra ubicación), abre el túnel zrok (`campo.bat`) y usa `https://<nombre>.shares.zrok.io` — la URL es fija si se reservó con `setup_zrok.bat`.
5. Acerca un objeto a <23 cm → la foto sube por la LAN del hotspot y aparece en `En vivo`.
6. Verifica bomba automática (3 fotos → evento) y manual (dashboard → Activar → el ESP32 lo recoge en ≤60 s).

**Si algo falla:**

- `SIN SEÑAL` permanente → el ESP32 no asoció al hotspot: revisa SSID/pass (2.4 GHz), datos del celular con saldo, o señal débil.
- Foto encolada que no sube → la PC cambió de IP al unirse al hotspot: reconfigura `server_url` con la IP nueva.
- La cola del ESP32 guarda máx. 6 fotos en RAM — si la señal es muy mala, prioriza acercarte al ESP32 o reducir la distancia de trabajo.

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
- **Jornada de campo**: si el ESP32 está en el hotspot del celular junto a la PC, no hace falta túnel. Si el dashboard se abre por zrok, la URL reservada es fija y no cambia al reiniciar — aun así, no apagues la PC hasta terminar (la ESP32 no alcanza el servidor apagado y su cola RAM es limitada).
- **Suspensión**: la PC debe quedar con suspensión desactivada en AC (`powercfg -change -standby-timeout-ac 0`, ya aplicado); el monitor de entrenamiento (`train_monitor.py`) puede apagarse sin efecto sobre el train.

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
