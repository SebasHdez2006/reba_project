# Plan de apagado y retorno — REBA

Contexto: laptop Dell Vostro 3400 (Windows, Modern Standby S0). Entrenamiento
`REBA_outdoor_v5` en curso (~54 min/época, CPU). Servidor uvicorn :5000 +
túneles **Cloudflare** (`rebaserver.tech`, definitivo) y **zrok** (respaldo)
activos. ESP32-CAM **apagada** durante el cierre.

Scripts creados para esto:

| Script | Cuándo | Qué hace |
|---|---|---|
| `detener_entrenamiento.bat` | Antes de apagar | Respalda `last.pt`/`best.pt` → `.bak` y mata los procesos de `train_outdoor.py` |
| `retomar.bat` | Al encender la PC | Levanta server + túnel Cloudflare (si no corre) + túnel zrok + `--resume` de v5 + monitor |
| `setup_zrok.bat` | Una sola vez (ya hecho) | Reserva el nombre público fijo del túnel zrok (respaldo) |
| `reba-cloudflared.vbs` (Startup folder) | Automático al iniciar sesión | Lanza el túnel nombrado Cloudflare → `rebaserver.tech` |
| `train_outdoor.py --resume` | (lo usa retomar.bat) | Reanuda el run desde `weights/last.pt` y reexporta ONNX al terminar |

## Respuestas rápidas

### ¿Cambia la URL del túnel?

- **Cloudflare `rebaserver.tech` (principal)**: NO — es dominio propio,
  fija para siempre (mientras se renueve el dominio .tech). Sirve
  `http://` a la ESP32 y `https://` al dashboard desde el mismo túnel.
  Se lanza solo al iniciar sesión (Startup folder →
  `tools/run_cloudflared.bat`); `retomar.bat` lo relanza si falta.
- **zrok (respaldo)**: con `ZROK_RESERVED_NAME` en `.env` la URL es fija
  `https://reba-cam.shares.zrok.io`; sin reserva cambia por arranque
  (`logs/tunnel.log`, `logs/zrok_url.txt`, ventana `REBA-Tunnel`).
- **El servidor uvicorn puede reiniciarse** sin afectar a los túneles
  (procesos independientes).
- **La ESP32 usa el túnel Cloudflare**: `server_url_alt=http://rebaserver.tech`
  (HTTP plano — el edge acepta HTTP sin redirect). zrok sigue siendo
  HTTPS-only → solo dashboard/simulador.

### ¿Cómo interfiere con el entrenamiento v5?

- Apagar la PC mata el proceso. Pesos a salvo en disco:
  `best.pt` = época 6 (mAP50-95 0.7404), `last.pt` = última época completa.
- Se pierde solo la época parcial en curso (máx ~55 min de CPU).
- Reanudación: `python ai_pipeline/train_outdoor.py --resume --name REBA_outdoor_v5`
  — restaura epochs/patience/LR schedule/augmentations Albumentations desde el
  checkpoint (todo está serializado en `args.yaml` del run).
- **NO** correr `train_outdoor.py` sin `--resume` con `--name REBA_outdoor_v5`:
  empezaría de cero y pisaría `results.csv` y los pesos del run.
- Si el run ya terminó (50 épocas o early stop), `--resume` detecta
  `results.csv` completo y solo reexporta el ONNX — no reentrena.
  Ojo: si terminó por *early stop* antes de las 50, `--resume` SÍ seguiría
  entrenando (el contador de patience no se restaura) — en ese caso editar
  `retomar.bat` y quitar la línea de REBA-Train.

### ¿Qué más cambia?

- **uvicorn y túneles**: uvicorn lo relanza `retomar.bat`; cloudflared se
  auto-lanza al iniciar sesión (Startup folder) y `retomar.bat` lo
  verifica; zrok lo relanza `retomar.bat`.
- **Monitor GUI**: se reabre con `retomar.bat` (opcional, se puede cerrar).
- **Suspensión**: ya desactivada en AC y batería (verificado con powercfg).
  OJO: es laptop con Modern Standby — **"cerrar la tapa" NO es apagar**;
  puede suspender la sesión y congelar/throttle el entrenamiento.
  Apagar desde Inicio → Apagar.
- **Hotspot REBA-AP** de Windows: se apaga por inactividad; irrelevante
  porque la ESP32 estará apagada.
- **IP LAN de la PC**: puede cambiar tras reinicio (DHCP); solo afecta
  acceso local directo, el túnel no depende de ella.
- **Dashboard**: `device.online=false` hasta que la ESP32 vuelva a encender
  (o hasta que el simulador `simulador_esp32.html` haga su primer poll).
- **Cola de fotos de la ESP32**: solo RAM — si estuviera encendida se
  perderían pendientes; apagada no hay nada que perder.
- **Git**: hay cambios sin commitear (docs + .bat nuevos) → commit
  recomendado antes de apagar.

## Secuencia de apagado (cuando avises)

1. Revisar progreso:
   `.venv-reba\Scripts\python ai_pipeline\train_monitor.py --run REBA_outdoor_v5 --once`
2. Decidir según la época en curso:
   - Si falta **>45 min** para que cierre la época → esperar a que
     `results.csv` sume una fila y luego apagar (no se pierde nada).
   - Si no → doble clic / ejecutar `detener_entrenamiento.bat`.
3. Confirmar que no quedan procesos `train_outdoor` (el .bat los lista).
   - Si `last.pt` llegara a corromperse (kill justo en la escritura,
     ventana de <1 s por época): `copy weights\last.pt.bak weights\last.pt`.
4. (Opcional) commit de los cambios pendientes.
5. Inicio → **Apagar** (no solo cerrar la tapa).
6. Apagar también la ESP32-CAM si está encendida.

## Secuencia de retorno

1. Encender PC e iniciar sesión en Windows.
2. Doble clic `retomar.bat` → abre 4 ventanas: Server, Tunnel, Train, Monitor.
3. Verificar:
   - `http://localhost:5000/api/health` → `backend: onnx`, `ok: true`
   - `http://rebaserver.tech/api/health` → 200 (túnel Cloudflare arriba)
   - Ventana `REBA-Train` → "Reanudando REBA_outdoor_v5: N/50 épocas hechas"
   - `logs\cloudflared.log` → "Registered tunnel connection"
4. Dashboard desde el celular: abrir **`https://rebaserver.tech`** (fija
   para siempre). Respaldo: `https://reba-cam.shares.zrok.io`.

## Cuando termine v5

- `retomar.bat` ya reexporta ONNX automáticamente al finalizar el run
  (`train_outdoor.py` exporta `best.pt` → `models/best.onnx` y archiva el
  modelo previo en `models/archive/`).
- **Reiniciar uvicorn** después para que cargue el nuevo ONNX
  (el server solo lee el modelo al arrancar).
- Evaluación decisiva pendiente:
  `python ai_pipeline/evaluate_outdoor.py` (set `real_water`)
  → `python ai_pipeline/tune_confidence.py` → ajustar `.env` si cambia
  el umbral → `python ai_pipeline/eval_upload_path.py`.

## Setup del túnel zrok (una sola vez — referencia)

Migrado desde Cloudflare quick tunnel → **zrok** (openziti/zrok, cuenta
gratuita en zrok.io, sin dominio). Ventaja: **URL fija gratuita**
(`https://<nombre>.shares.zrok.io`) mediante *reserved share*, algo que
Cloudflare solo daba con dominio propio de pago.

1. Crear cuenta en https://zrok.io (gratis, sin tarjeta).
2. Descargar el binario Windows (`zrok` v1.x o `zrok2` v2.x) desde
   https://github.com/openziti/zrok/releases → descomprimir → agregar al PATH.
3. Ejecutar `setup_zrok.bat` → pega el token de la cuenta cuando lo pida,
   elige el nombre público (default `reba-cam`) y el script reserva el
   nombre y escribe `ZROK_RESERVED_NAME` en `.env`.
4. `campo.bat` / `retomar.bat` detectan `zrok` o `zrok2` en el PATH y
   arrancan el share headless; si no hay reserva configurada caen al
   modo efímero automáticamente (`tools/zrok_tunnel.ps1`).

**Topología dual (importante):**

| Cliente | Vía | Protocolo |
|---|---|---|
| ESP32-CAM física | IP LAN de la PC o hotspot compartido | `http://` plano :5000 (sin TLS) |
| Dashboard / simulador en el celular | `https://<nombre>.shares.zrok.io` | HTTPS + `wss://` automáticos |

El túnel zrok **no** sirve para la ESP32 (sin TLS); si el ESP32 está fuera
de la LAN de la PC, la vía es conectarla al mismo hotspot que la PC.
Detalle operativo: `docs/manual_uso.md` §1 y §4.

**ESP32 físicamente remota (resuelto 16-nov):**

La ESP32 en otra ubicación (ej. casa de un compañero) se resuelve con el
túnel Cloudflare nombrado — **ya desplegado**:

- `server_url` = `http://192.168.137.1:5000` (LAN/hotspot REBA-AP,
  primaria) · `server_url_alt` = `http://rebaserver.tech` (túnel,
  respaldo).
- El edge de Cloudflare acepta **HTTP plano en puerto 80** sin redirect
  forzado → el firmware (`http://` solamente, sin TLS por el bug de
  cámara) lo usa directo.
- **Failover automático del firmware**: 3 polls fallidos en la LAN →
  conmuta a `server_url_alt` (~3 min tras boot en red con Internet);
  re-sondea la LAN cada 5 min y vuelve si reaparece.
- `ota_url=http://rebaserver.tech/firmware.bin` — OTA remoto por el
  mismo túnel.
- Verificado: `http://rebaserver.tech/api/health` → 200 y
  `https://rebaserver.tech/` → 200 desde fuera de la LAN.

**Historial de intentos** (para no repetir — detalle en
`docs/plan_tunel_fijo.md`): Portwarp (listener público murió, agente sin
API), playit.gg (auth roto; TCP requiere Premium), Oracle VPS (tarjeta
rechazada), Azure for Students (UES no está en la base de instituciones
de Microsoft), DigitalOcean (oferta eliminada del pack 2026), cloudflared
quick tunnel (URL efímera — solo respaldo histórico).

**Notas zrok:**

- Cuentas gratuitas sin verificar muestran una *interstitial page* ~1 vez
  por semana a navegadores (anti-phishing). La ESP32 y `curl` la evitan;
  en el celular se salta con un clic. Se puede quitar verificando la
  cuenta con tarjeta en zrok.io.
- Free tier: 5 GB/día — de sobra para fotos de ~30 KB.
- Si el share reservado falla al arrancar, `zrok_tunnel.ps1` reintenta en
  modo efímero y lo deja anotado en `logs/tunnel.log`.
