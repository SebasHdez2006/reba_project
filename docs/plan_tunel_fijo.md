# Plan — Túnel de dirección fija para la ESP32-CAM

> Estado: **DESPLEGADO** (16-nov noche) — Cloudflare named tunnel sobre
> dominio propio `rebaserver.tech` (.tech gratis del GitHub Student
> Pack). HTTP plano puerto 80 para la ESP32 + HTTPS para el dashboard,
> mismo túnel. Autostart vía Startup folder (`reba-cloudflared.vbs` →
> `tools/run_cloudflared.bat`, `--protocol http2`). ESP32:
> `server_url_alt=http://rebaserver.tech` + `ota_url` ya configurados.
>
> Descartados en el camino: Portwarp (listener público muerto, agente
> sin API), playit.gg (auth roto + TCP requiere Premium), Oracle VPS
> (tarjeta rechazada en verificación), Azure for Students (UES no está
> en la base de instituciones de Microsoft), DigitalOcean (oferta
> eliminada del pack 2026). zrok sigue de respaldo para el dashboard.
>
> Historial: la ESP32 migra primero por **LAN directa**
> (`http://192.168.137.1:5000` en REBA-AP); el túnel es el fallback
> automático del firmware cuando hay Internet.

## 0. Problema actual

- `cloudflared` quick tunnel (`trycloudflare.com`) da HTTP plano — lo
  único que la ESP32 soporta (TLS/mbedTLS → FB-OVF con cámara) — pero la
  URL **cambia en cada arranque**. PC apagada = túnel muerto = ESP32
  inalcanzable; PC encendida + túnel relanzado = URL nueva = ESP32 sigue
  apuntando a la vieja → hay que reconfigurarla en físico.
- `zrok` (`reba-cam.shares.zrok.io`) tiene URL fija pero es **HTTPS-only**
  → inútil para la ESP32 (verificado: `http://` → 301 a HTTPS).
  Sigue sirviendo al dashboard del celular — no tocar.
- `zrok share private --backend-mode tcpTunnel` existe pero exige agente
  zrok en el consumidor — la ESP32 no puede correrlo. Descartado.
- `playit.gg`: los túneles **TCP/UDP genéricos requieren Premium**
  ($3/mes o $30/año) — el tier gratis solo da tipos de juego seleccionados
  y UDP.

## 1. Opciones evaluadas (todas: dirección fija + HTTP/TCP plano)

| Opción | Costo | Fiabilidad | Esfuerzo | Veredicto |
|---|---|---|---|---|
| **Portwarp Free** | $0 | Servicio nuevo — verificar empíricamente | ~15 min | **PRIMERO** — TCP en tier gratis confirmado (1 túnel, subdominio estable, bandwidth ilimitado, sin tarjeta) |
| **playit.gg Premium** | $3/mes ($30/año) | Probado, agente ya instalado (`C:\Program Files\playit_gg` corriendo) | ~10 min | Fallback de menor esfuerzo |
| **Oracle Cloud Always Free + ssh -R** | $0 | Muy robusto (IP pública fija real) | ~1-2 h + cuenta Oracle (tarjeta solo verifica, sin cargo) | Opción $0 seria; ver §4 variante server-completo |
| **Cloudflare named tunnel + dominio propio** | ~$10/año | El más sólido | Dominio + `cert.pem` + `config.yml` + `cloudflared service install` | Fallback robusto; sigue siendo cloudflare pero URL fija |
| serveo.net / localhost.run / bore.pub / localtunnel / ngrok free / zrok / Tailscale Funnel / hypertunnel | $0 | HTTPS-only, URLs random por arranque, página de password, o túneles que mueren a las 24 h | — | Descartadas |

**Regla de migración**: NINGUNA opción se activa en la ESP32 hasta que el
túnel pase la verificación de Fase B desde fuera del LAN. Si la primera
opción falla → siguiente del menú, sin compromiso.

## 2. Implementación — Portwarp (camino principal) — HISTÓRICO, no ejecutado

### Fase A — cuenta y túnel (~15 min, humano + agente)

1. Crear cuenta en portwarp.com (sin tarjeta) — **paso humano**.
2. Instalar agente: descargar de portwarp.com/download (Windows app o
   CLI single-binary) o Docker.
3. Crear túnel **TCP** → local `127.0.0.1:5000`. Anotar la dirección
   pública estable (formato `nombre.pwrp.cc[:puerto]` o similar).
   OJO: el tier Free pone el túnel en una **cola de creación (~2 min)**
   — es solo al crear; una vez activo la dirección es permanente.
4. Confirmar que la dirección es permanente (subdominio del cuenta,
   no random por sesión).

### Fase B — verificación (antes de tocar la ESP32)

```powershell
# Desde esta PC y desde el celular con DATOS (fuera del LAN):
curl http://<dir-portwarp>/api/health        # esperado: {"ok":true,...}
curl http://<dir-portwarp>/api/device/config # formato clave=valor
```

- Si el endpoint público exige HTTPS o responde 301 → Portwarp no sirve
  para la ESP32 → pasar a playit Premium.
- Probar `POST /upload` con una foto de prueba → purgar la captura
  después (rango de id como la vez anterior).
- El firmware acepta `host:puerto` (`parseHttpUrl`, fotos_nc.ino ~L796)
  y `server_url` admite ≤96 chars con `http://` — válido.

### Fase C — migración de la ESP32 remota

**Ventana ideal: madrugada del 16-nov con Nabiki en físico** (decisión
del usuario: PC encendida hasta entonces; cloudflared sigue vivo y la
ESP32 sigue subiendo datos de campo — candidatas para `REBA_field`).

Como estarán físicamente con el dispositivo, la migración es directa:

```text
# Por USB serie (monitor a 115200) o portal WiFiManager:
SET server_url=http://<dir-portwarp>
SET ota_url=http://<dir-portwarp>/firmware.bin
GET    # confirmar valores aplicados
```

- Si se prefiriera remoto: `POST /api/admin/device-config` con
  `{"server_url": "http://<dir>"}` mientras el túnel actual viva —
  la ESP32 lo toma en su poll de ~60 s y revierte solo si >6 min sin
  contacto.
- Verificar `/api/health` → `device.online=true`, `last_seen` fresco.

### Fase D — retiro de cloudflared y autostart

1. Con la ESP32 verificada en la nueva URL: matar `cloudflared.exe`
   (PID 22352) y cerrar su ventana `cmd /k` (PID 22156) — la URL
   trycloudflare muere, ya nadie la usa.
2. **Autostart del agente** en Windows (Task Scheduler "At log on" o
   carpeta Startup) — equivalente al servicio manual de cloudflared.
3. zrok se queda para el dashboard HTTPS del celular
   (`reba-cam.shares.zrok.io`) — no tocar.

### Fase E — OTA con dirección fija

- `GET /firmware.bin` ya existe en el server (bins commiteados en
  `firmware/`). `ota_url=http://<dir>/firmware.bin` se fija una vez →
  la ESP32 auto-actualiza cuando haya binario nuevo.
- Respaldo físico: bins en GitHub (`firmware/*.bin`) + esptool USB.

## 3. Respuesta: actualización de firmware por GitHub

**Sí, con matices:**

- **GitHub = distribución de binarios** (los `.bin` ya están
  commiteados). Nabiki descarga con el navegador (HTTPS no importa para
  ella) y flashea por **USB + esptool** — siempre funciona, sin server.
- **OTA remoto = necesita HTTP plano**: la ESP32 baja `ota_url` con
  `WiFiClient` TCP. GitHub raw/releases son **HTTPS** → no puede jalar
  directo de GitHub. El puente es el server: `/firmware.bin` por el
  túnel. Con dirección fija, `ota_url` se configura **una sola vez**.
- Flujo: dev compila (arduino-cli, min_spiffs) → commit de
  `firmware/*.bin` a GitHub (respaldo) → server sirve el mismo `.bin`
  → Nabiki dispara OTA por `ota_url` o serie `OTA`. Si la ESP32 no
  alcanza el server → camino USB.

## 4. Solución desplegada — Cloudflare named tunnel + dominio propio

Implementación final (16-nov noche, **verificada end-to-end**):

- **Dominio**: `rebaserver.tech` — .tech gratis 1 año vía GitHub
  Student Pack (Namecheap/get.tech). Nameservers → Cloudflare.
  Renovación ~$10-15/año si el proyecto sigue vivo.
- **Túnel**: `cloudflared tunnel reba`
  (`8b5b1785-5afc-42f8-a075-a3a607b615fc`), ingress
  `rebaserver.tech → http://localhost:5000`.
  - `http://rebaserver.tech` → HTTP plano → **ESP32**
    (`server_url_alt=http://rebaserver.tech`, puerto 80 default).
  - `https://rebaserver.tech` → dashboard remoto (reemplaza zrok).
- **Protocolo**: `--protocol http2` obligatorio — QUIC/UDP hace
  timeout en la red institucional (PROGRESO sesión 11).
- **Autostart**: `reba-cloudflared.vbs` en
  `%APPDATA%\...\Startup` → `tools/run_cloudflared.bat` (oculto,
  log `logs/cloudflared.log`). cloudflared reconecta solo.
- **Archivos**: `tools/cloudflared_config.yml` (plantilla con IDs
  reales), `tools/run_cloudflared.bat`, `tools/cloudflared_tunnel_hidden.vbs`.
- **Credenciales**: `~/.cloudflared/cert.pem` + `<tunnel-id>.json`
  (NO commitear — son secretos de la cuenta Cloudflare).
- **Pendiente E2E**: ESP32 en WiFi con Internet → failover LAN→
  `rebaserver.tech` (el firmware ya lo hace solo tras 3 polls
  fallidos en la principal).

### Variantes evaluadas y descartadas (resumen)

- **Oracle Cloud Always Free + ssh -R**: tarjeta rechazada en la
  retención de verificación (fondos insuficientes). Scripts quedaron
  en `tools/vps_*.{sh,bat,vbs}` por si se reintenta con otra tarjeta.
- **Azure for Students**: $100 sin tarjeta pero UES no figura en la
  base de instituciones de Microsoft — verificación académica falló.
- **C2 — server completo en VPS** (descartada): uvicorn + ONNX +
  SQLite en la VM → ESP32 alcanzable 24/7 con la laptop apagada.
  Reevaluar si el relay resulta insuficiente.

## 5. Checklist de ejecución (histórico — Portwarp, superado por §4)

> Las secciones §1–§3 y este checklist describen el plan Portwarp que
> quedó **sin ejecutar**: el listener público murió y el agente no tiene
> API de control. La implementación real fue §4 (Cloudflare). Se
> conserva como historial de la decisión.

- [x] Opción elegida → **Portwarp Free**
- [x] Cuenta/servicio creado + túnel TCP → 127.0.0.1:5000 activo
      (`93togsum.free.tryportwarp.cc:16404`)
- [~] `curl http://<dir>/api/health` = 200 — verificado, pero **flapea**
      (conexión inestable; reintentar hasta estabilizar o cambiar de opción)
- [ ] Dirección estable verificada (sobrevive reinicio del agente/PC)
- [~] `server_url` en ESP32: firmware flasheado con default Portwarp +
      NVS reseteada (16-nov); falta credenciales WiFi por portal y
      `ota_url` (opcional: `/firmware.bin` ya se sirve por la misma URL)
- [ ] `device.online=true` en `/api/health` por la nueva ruta
- [ ] cloudflared.exe + ventana cmd eliminados (post-migración;
      sigue vivo como respaldo — URL nueva `related-surge-...`)
- [x] Autostart del agente: `launch_at_login` propio de la app +
      tarea `REBA-Portwarp` (Task Scheduler) — agente arrancó solo
- [~] Docs: PROGRESO.md (16-nov) + plan_tunel_fijo + firmware.md +
      componentes.md + manual_uso.md actualizados; server.md pendiente
      de la ruta definitiva
- [ ] `retomar.bat`/`campo.bat` revisados: NO relanzan train_outdoor
      (v5 terminó — `--resume` reentrenaría solo)
      → **Estado real (16-nov noche)**: v5 quedó en época 30/50 (no
      terminó); `retomar.bat` SÍ lanza `REBA-Train --resume` a propósito
      para continuarla. Si no se quiere reanudar, cerrar esa ventana.

## 6. Riesgos

| Riesgo | Mitigación |
|---|---|
| Portwarp/servicio nuevo cae o cambia dirección | Fase B verifica antes de migrar; fallbacks en §1 |
| Agente cae tras reboot de la PC | Autostart Task Scheduler; verificar manual tras primer reboot |
| PC apagada = todo cae igual | Inherente: el server vive en la laptop. El túnel solo arregla la URL; para disponibilidad 24/7 → variante C2 |
| Datos de la ESP32 remota esta noche | cloudflared sigue vivo hasta migrar — las capturas siguen llegando (candidatas `REBA_field`) |
