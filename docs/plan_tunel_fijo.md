# Plan — Túnel de dirección fija para la ESP32-CAM (reemplazo cloudflared)

> Estado: **propuesta** — ejecutar cuando se apruebe. Objetivo: que la
> ESP32 remota (casa de Nabiki) reconecte sola cada vez que el server
> esté encendido, sin reconfiguración física ni URLs efímeras.

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

## 2. Implementación — Portwarp (camino principal)

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

## 4. Variante VPS (si se quiere $0 a largo plazo)

Oracle Cloud Always Free (VM ARM 4c/24GB o AMD micro, IP pública fija):

- **C1 — relay TCP**: `ssh -R 5000:localhost:5000 usuario@VPS` persistente
  desde la PC (autossh/Task Scheduler) → ESP32 apunta a
  `http://IP_VPS:5000`. Mínimo cambio; la laptop sigue siendo el server.
- **C2 — server completo en la VM**: uvicorn + ONNX + SQLite viven en la
  VM → ESP32 alcanzable **24/7 con la laptop apagada**. Migración mayor
  (copiar `server/`, `models/best.onnx`, `.env`, `data/` inicial) pero
  elimina el problema de raíz; la laptop queda para entrenar/dashboard.
  zrok podría reapuntarse a `http://IP_VPS:5000` para el dashboard.

## 5. Checklist de ejecución

- [ ] Opción elegida (Portwarp → playit Premium → Oracle → CF+dominio)
- [ ] Cuenta/servicio creado + túnel TCP → 127.0.0.1:5000 activo
- [ ] `curl http://<dir>/api/health` = 200 desde fuera del LAN (datos móviles)
- [ ] Dirección estable verificada (sobrevive reinicio del agente/PC)
- [ ] `server_url` + `ota_url` aplicados en ESP32 (físico o device-config)
- [ ] `device.online=true` en `/api/health` por la nueva ruta
- [ ] cloudflared.exe + ventana cmd eliminados (post-migración)
- [ ] Autostart del agente configurado (Task Scheduler/Startup)
- [ ] PROGRESO.md + docs/server.md + docs/componentes.md actualizados
- [ ] `retomar.bat`/`campo.bat` revisados: NO relanzan train_outdoor
      (v5 terminó — `--resume` reentrenaría solo)

## 6. Riesgos

| Riesgo | Mitigación |
|---|---|
| Portwarp/servicio nuevo cae o cambia dirección | Fase B verifica antes de migrar; fallbacks en §1 |
| Agente cae tras reboot de la PC | Autostart Task Scheduler; verificar manual tras primer reboot |
| PC apagada = todo cae igual | Inherente: el server vive en la laptop. El túnel solo arregla la URL; para disponibilidad 24/7 → variante C2 |
| Datos de la ESP32 remota esta noche | cloudflared sigue vivo hasta migrar — las capturas siguen llegando (candidatas `REBA_field`) |
