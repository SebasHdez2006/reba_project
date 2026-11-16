// ============================================================
// REBA — Nodo ESP32-CAM (modo push: fotos por evento)
// Placa en Arduino IDE: "AI Thinker ESP32-CAM"
//
// Funcionamiento:
//  1) El HC-SR04 vigila un rango de 0 a DIST_MAX_CM. Cuando un objeto
//     entra al rango cuenta un "ingreso" (el objeto debe retirarse
//     antes de contar otro: ver LECTURAS_PARA_CONTAR /
//     LECTURAS_PARA_REARMAR).
//  2) En cada ingreso: enciende el flash un instante, toma una foto JPG
//     y apaga el flash.
//  3) La foto se guarda TEMPORALMENTE en la memoria del ESP32-CAM
//     (cola en PSRAM, máx. MAX_COLA) y se envía por HTTP POST al
//     servidor FastAPI de la PC (SRV_BASE + "/upload").
//     Si no hay WiFi o el servidor no responde 2xx, la foto queda en
//     la cola y se reintenta después. Al enviarse con éxito, se borra.
//  4) Al acumular PUMP_PHOTOS_DEFAULT fotos tomadas, la bomba trabaja
//     PUMP_SECONDS_DEFAULT segundos (ajustables desde el dashboard;
//     ver sección "Configuración remota").
//
// Acceso remoto: el dispositivo habla HTTP plano (sin TLS — ver nota de
// HTTPClient.h abajo). Para alcanzarlo desde fuera de la LAN hace falta
// un tunel que acepte HTTP plano. Desde el 16-nov el tunel es PORTWARP
// con direccion fija: http://93togsum.free.tryportwarp.cc:16404
// (antes cloudflared quick tunnel — URL efimera que cambiaba por
// reinicio; ver docs/plan_tunel_fijo.md). El tunel HTTPS de zrok NO
// sirve para este firmware. Dashboard/celular si pueden usar zrok:
// https://<nombre>.shares.zrok.io.
//
// Configuración (SIN recompilar):
//  - Primera vez / sin WiFi guardada: el ESP32 levanta el AP
//    "REBA-Setup". Conéctate desde el celular, abre http://192.168.4.1
//    y captura: red WiFi, contraseña y URL del servidor.
//  - Después el dispositivo descarga cada POLL_CONFIG_MS la config del
//    servidor (GET /api/device/config) y aplica cambios en caliente:
//    WiFi, URL del servidor, umbrales y parámetros de la bomba.
//  - Si el WiFi guardado deja de funcionar ~10 min (cambiaste de red),
//    el AP "REBA-Setup" se reactiva solo para reconfigurar.
//  - OTA: el sketch puede auto-actualizarse descargando un .bin por HTTP
//    plano (ver sección "OTA" más abajo). Requiere partition scheme CON
//    OTA: en Arduino IDE → Tools → Partition Scheme → "Minimal SPIFFS
//    (1.9MB APP with OTA/128KB SPIFFS)". Disparo: serie `OTA` o clave
//    remota `ota_url` desde el dashboard.
//
// Bomba en NC (normalmente cerrado): COM = 12V del boost, bomba + en NC.
//    relé ACTIVO (clic, LED verde)  -> bomba APAGADA
//    relé en REPOSO                 -> bomba ENCENDIDA
//
// Pines:  GPIO13 -> IN del relé | GPIO14 -> TRIG | GPIO15 -> ECHO (con
//         divisor 1k+2k) | GPIO4 -> LED flash (interno de la placa)
// ============================================================

#include "esp_camera.h"
#include <WiFi.h>
#include <WiFiManager.h>
#include <Preferences.h>
#include <Update.h>
// NOTA: HTTPClient.h está PROHIBIDO en este firmware — enlaza
// WiFiClientSecure/mbedTLS cuyo init estático provoca FB-OVF permanente
// en el driver de cámara (verificado en banco: con HTTPClient la cámara
// daba "init OK pero sin frames"; con WiFiClient TCP plano funciona).
// El HTTP se hace con WiFiClient en httpSimple() más abajo.

// ---------------- Valores por defecto (se sobreescriben por config remota) ----------------
// SRV_BASE: URL base del servidor FastAPI SIN /upload.
// Default = tunel Portwarp de direccion fija (acceso remoto campo);
// para banco/LAN se sobreescribe por portal, serie o config remota
// (ej. http://192.168.1.3:5000). Solo se usa como valor inicial del
// portal y fallback si NVS queda vacio; en produccion la config remota
// puede cambiarla (ojo: si el ESP32 no alcanza el servidor no puede
// descargar la nueva URL — ver docs).
static const char* SRV_BASE_DEFAULT = "http://93togsum.free.tryportwarp.cc:16404";

// ---------------- Pines ----------------
#define PIN_RELE   13
#define PIN_TRIG   14
#define PIN_ECHO   15
#define PIN_FLASH   4     // LED blanco (flash)

#define RELE_ACTIVO_BAJO  true   // módulo típico: LOW = relé activo
#define BOMBA_EN_NC       true   // bomba en NC (false si la pasas a NO)

// ---------------- Detección (defaults; ajustables por config remota) ----------------
static float         distMinCm              = 0.0;
static float         distMaxCm              = 23.0;   // rango de detección
const unsigned long  PERIODO_SENSOR_MS      = 60;
const int            LECTURAS_PARA_CONTAR   = 2;      // lecturas seguidas dentro del rango
const int            LECTURAS_PARA_REARMAR  = 3;      // lecturas seguidas fuera del rango

// ---------------- Foto y flash ----------------
const unsigned long FLASH_PREVIO_MS = 150;   // flash encendido antes de capturar
// El flash queda encendido solo durante la captura (~0.3-0.4 s en total)

// ---------------- Bomba (defaults; ajustables por config remota) ----------------
static int           pumpPhotos    = 3;
static unsigned long pumpSeconds   = 10;

// ---------------- Memoria temporal de fotos ----------------
const int           MAX_COLA           = 6;       // fotos pendientes en memoria
const unsigned long REINTENTO_ENVIO_MS = 15000;   // espera tras un envío fallido
const unsigned long REINTENTO_WIFI_MS  = 30000;   // espera entre reconexiones

// ---------------- Config remota ----------------
const unsigned long POLL_CONFIG_MS      = 60000;  // descarga config del server cada 60 s
const unsigned long WIFI_FAILS_PARA_PORTAL = 20;  // ~10 min sin WiFi -> reabre portal
const unsigned long WIFI_REVERT_MS      = 90000;  // si creds nuevas no conectan en 90 s, revierte
const unsigned long SRV_FAILOVER_UMBRAL = 3;      // polls fallidos -> conmutar a srv_alt
const unsigned long SRV_PROBE_MS         = 300000; // reintentar URL principal cada 5 min
const unsigned long PORTAL_RETRY_MS     = 30000;  // reabrir portal si no hay creds
const unsigned long CAMARA_RETRY_MS     = 30000;  // reintentar init de cámara si falló
const unsigned long CAMARA_TEST_MS      = 900000; // health check silencioso cada 15 min
const int            FALLAS_CAMARA_REINIT = 5;    // fallos de captura seguidos -> re-init
const int            FOTO_MAX_INTENTOS  = 25;     // ~6 min de reintentos -> descartar (anti poison)
const size_t         HTTP_RESP_MAX      = 4096;   // tope body config remota (anti heap)
const size_t         COLA_HEAP_RESERVA  = 24576;  // RAM libre mínima para encolar foto

// ---------------- Pines de la cámara (AI-Thinker) ----------------
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27
#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

// ============================================================
// Config persistente (Preferences / NVS) y runtime
// ============================================================
Preferences prefs;

String         wifiSsid;
String         wifiPass;
String         srvBase;            // URL activa (srv_pri o srv_alt según srvActiva)
String         srvPri;              // URL principal (NVS "srv", clave server_url)
String         srvAlt;              // URL alternativa de respaldo (NVS "srv_alt")
int            srvActiva   = 0;     // 0 = srvPri, 1 = srvAlt
int            srvFallos   = 0;     // fallos de transporte consecutivos en srvBase
unsigned long  srvProximoProbe = 0; // cuándo reintentar srvPri si estamos en srvAlt

int            wifiFallos        = 0;
bool           portalActivo      = false;
bool           camaraOk          = false;
int            fallosCamara      = 0;      // fallos de captura seguidos
float          lastDistCm        = -1.0f; // última lectura HC-SR04 (-1 = sin eco)
unsigned long  ultimoPollConfig  = 0;
unsigned long  ultimoTestCamara  = 0;      // último health check silencioso
// RTC_DATA_ATTR sobrevive resets por software/brownout/watchdog (no por
// power-cycle): cuenta boots consecutivos para detectar un bootloop.
RTC_DATA_ATTR unsigned long bootCount = 0;
unsigned long  wifiRevertEn      = 0;   // millis() en que se revierte creds si sigue sin conectar
unsigned long  ultimoConfigOk    = 0;   // último GET /api/device/config exitoso

// --- Bomba manual (comando servidor -> ESP32 vía /api/device/config) ---
// El server encola "pump_cmd=<unix_ts>:<segundos>"; se ejecuta una sola vez
// (dedup por lastPumpCmdId en RTC) y se confirma con POST /api/device/ack.
// La ejecución es NO bloqueante: lazo normal sigue vivo durante la bomba.
RTC_DATA_ATTR unsigned long lastPumpCmdId = 0;
RTC_DATA_ATTR unsigned long lastQueueClear = 0;  // dedup del comando queue_clear
unsigned long      queueClearPendiente = 0;      // ts pendiente de vaciar cola (loop)
bool           cmdBombaPendiente = false;
unsigned long  cmdBombaId        = 0;
int            cmdBombaSecs      = 0;
bool           bombaManualActiva = false;
unsigned long  bombaManualFin    = 0;   // millis() deadline (wrap-safe)

// --- OTA (auto-actualización por HTTP plano) ---
// otaUrlPendiente: URL .bin esperando ser flasheada (serie `OTA` o clave
// remota ota_url). otaReintentoEn: backoff RTC tras un intento fallido.
RTC_DATA_ATTR unsigned long otaReintentoEn = 0;
String         otaUrlPendiente;

// WiFiManager se crea SOLO cuando se abre el portal de configuración.
// Como global consumía ~45KB de heap desde el arranque y la cámara
// (que necesita ~250KB contiguos) no inicializaba en esta placa.
WiFiManager*           wmP   = nullptr;
WiFiManagerParameter*  pSrvP = nullptr;

// Definida más abajo (sección HTTP); la usa consultarConfigRemota().
int httpSimple(const String& method, const String& url,
               const char* headersExtra, const uint8_t* body, size_t bodyLen,
               String* respBody, unsigned long timeoutMs);

// Definida más abajo (sección OTA); la usa aplicarClaveRemota() y el
// comando serie SET ota_url.
bool otaUrlValida(const String& u);

// Definida más abajo (sección estado); la usa consultarConfigRemota().
void reportarEstado();

// Clamp de valores leídos de NVS: una clave corrupta no debe dejar el
// dispositivo en un estado inválido (p.ej. pump_photos=0 o dist_max=-5).
static int clampInt(long v, int lo, int hi, int def) {
  if (v < lo || v > hi) return def;
  return (int)v;
}
static float clampFloat(float v, float lo, float hi, float def) {
  if (isnan(v) || v < lo || v > hi) return def;
  return v;
}

void cargarConfig() {
  prefs.begin("reba", true);
  wifiSsid = prefs.getString("ssid", "");
  wifiPass = prefs.getString("pass", "");
  srvPri   = prefs.getString("srv", SRV_BASE_DEFAULT);
  srvAlt   = prefs.getString("srv_alt", "");
  srvActiva = prefs.getString("srv_act", "0") == "1" ? 1 : 0;
  // Las claves numéricas se persisten como string (guardarClave); leerlas
  // con getInt/getFloat devolvería el default por desajuste de tipo en NVS.
  pumpPhotos  = clampInt(prefs.getString("pump_photos", "3").toInt(),   1, 50,   3);
  pumpSeconds = clampInt(prefs.getString("pump_seconds", "10").toInt(), 1, 300, 10);
  distMinCm   = clampFloat(prefs.getString("dist_min_cm", "0").toFloat(), 0.0f, 500.0f, 0.0f);
  distMaxCm   = clampFloat(prefs.getString("dist_max_cm", "23").toFloat(), 1.0f, 500.0f, 23.0f);
  prefs.end();
  // Rango coherente: si min >= max (NVS corrupto), restablecer defaults.
  if (distMinCm >= distMaxCm) { distMinCm = 0.0f; distMaxCm = 23.0f; }
  // URLs de servidor deben ser http:// alcanzables por httpSimple.
  auto urlOk = [](String& u) {
    if (!u.startsWith("http://")) { u = ""; return; }
    while (u.endsWith("/")) u.remove(u.length() - 1);
  };
  urlOk(srvPri);
  if (srvPri.length() == 0) srvPri = SRV_BASE_DEFAULT;
  urlOk(srvAlt);
  if (srvAlt == srvPri) srvAlt = "";         // alternativa idéntica = inútil
  // Arranque en la URL que quedó activa; si no hay alternativa, siempre srvPri.
  srvBase = (srvActiva == 1 && srvAlt.length() > 0) ? srvAlt : srvPri;
}

void guardarClave(const String& clave, const String& valor) {
  prefs.begin("reba", false);
  prefs.putString(clave.c_str(), valor.c_str());
  prefs.end();
}

// ------------------------------------------------------------
// Portal de configuración (AP "REBA-Setup")
// ------------------------------------------------------------
void onParamsSaved() {
  // El usuario envió el formulario del portal: persistir todo.
  String s = pSrvP->getValue(); s.trim();

  prefs.begin("reba", false);
  prefs.putString("ssid", wmP->getWiFiSSID());
  prefs.putString("pass", wmP->getWiFiPass());
  if (s.length() > 0) {
    prefs.putString("srv", s);
    prefs.putString("srv_act", "0");       // portal define la URL principal
  }
  prefs.end();

  wifiSsid = wmP->getWiFiSSID();
  wifiPass = wmP->getWiFiPass();
  if (s.length() > 0) {
    srvPri = s; srvBase = s; srvActiva = 0; srvFallos = 0; srvProximoProbe = 0;
  }
  wifiFallos = 0;
  wifiRevertEn = 0;

  Serial.println("Config del portal guardada. Conectando WiFi...");
  WiFi.disconnect();
  WiFi.begin(wifiSsid.c_str(), wifiPass.c_str());
}

void iniciarPortal() {
  if (portalActivo) return;
  portalActivo = true;
  wifiFallos = 0;

  Serial.println("== MODO CONFIGURACION: AP 'REBA-Setup' ==");
  Serial.println("Conectate a 'REBA-Setup' y abre http://192.168.4.1");

  if (!wmP) {   // lazy init: no robar heap a la cámara
    wmP = new WiFiManager();
    pSrvP = new WiFiManagerParameter("srv", "URL servidor (http://IP:5000)",
                                     srvBase.c_str(), 96);
    wmP->addParameter(pSrvP);
    wmP->setSaveParamsCallback(onParamsSaved);
    wmP->setSaveConnect(false);              // conectamos nosotros en el callback
    wmP->setConfigPortalTimeout(300);        // portal abierto 5 min
    wmP->setConfigPortalBlocking(false);     // no bloquea: fotos/cola siguen
  }
  wmP->startConfigPortal("REBA-Setup");
}

void revisarPortal() {
  if (!portalActivo || !wmP) return;
  // En modo no-bloqueante hay que bombear el webserver/DNS del portal
  // cada loop — sin esto la página no carga aunque el AP esté arriba.
  wmP->process();
  if (wmP->getConfigPortalActive()) return;   // sigue esperando al usuario
  portalActivo = false;
  Serial.println("Portal de configuracion cerrado (timeout o guardado).");
}

// ------------------------------------------------------------
// Config remota: GET {srvBase}/api/device/config -> "clave=valor"
// ------------------------------------------------------------
// WiFi remoto: el server manda wifi_pass y wifi_ssid en el mismo body
// (orden alfabético: pass primero). Se acumulan y se aplica cuando llegan ambos.
static String pendWifiSsid, pendWifiPass;
static bool   pendWifiSsidOk = false, pendWifiPassOk = false;

void aplicarWifiRemoto() {
  pendWifiSsidOk = pendWifiPassOk = false;

  String nuevoSsid = pendWifiSsid;
  String nuevoPass = pendWifiPass;
  // SSID 1-32 chars, pass 0-63 (red abierta permitida). Fuera de rango se
  // ignora el cambio completo — nunca se guarda una config WiFi inválida.
  if (nuevoSsid.length() == 0 || nuevoSsid.length() > 32 || nuevoPass.length() > 63) {
    Serial.println("WiFi remoto rechazado: ssid/pass fuera de rango");
    return;
  }
  if (nuevoSsid == wifiSsid && nuevoPass == wifiPass) return;

  // Respaldo de las creds actuales: si las nuevas no conectan en
  // WIFI_REVERT_MS se revierten automáticamente.
  guardarClave("ssid_bak", wifiSsid);
  guardarClave("pass_bak", wifiPass);
  wifiSsid = nuevoSsid;
  wifiPass = nuevoPass;
  guardarClave("ssid", wifiSsid);
  guardarClave("pass", wifiPass);
  wifiRevertEn = millis() + WIFI_REVERT_MS;
  Serial.printf("WiFi remoto nuevo: %s — probando...\n", wifiSsid.c_str());
  WiFi.disconnect();
  WiFi.begin(wifiSsid.c_str(), wifiPass.c_str());
}

// Validación estricta de numéricos: String::toInt()/toFloat() aceptan
// prefijos ("3abc" -> 3, "abc" -> 0). Para config remota/serial un valor
// malformado debe rechazarse, no reinterpretarse.
static bool esEntero(const String& s) {
  if (s.length() == 0) return false;
  for (unsigned i = 0; i < s.length(); i++) {
    char c = s[i];
    if (i == 0 && (c == '-' || c == '+')) continue;
    if (c < '0' || c > '9') return false;
  }
  return true;
}
static bool esFloat(const String& s) {
  int puntos = 0;
  for (unsigned i = 0; i < s.length(); i++) {
    char c = s[i];
    if (i == 0 && (c == '-' || c == '+')) continue;
    if (c == '.') { if (++puntos > 1) return false; continue; }
    if (c < '0' || c > '9') return false;
  }
  return puntos <= 1 && s.length() > 0;
}

// Devuelve true si la clave se aplicó (o quedó pendiente de su pareja
// wifi_ssid/wifi_pass); false si fue rechazada por validación.
bool aplicarClaveRemota(const String& clave, const String& valor) {
  if (clave == "wifi_ssid") {
    if (valor.length() == 0 || valor.length() > 32) return false;
    pendWifiSsid = valor; pendWifiSsidOk = true;
    if (pendWifiPassOk) aplicarWifiRemoto();
    return true;
  }
  else if (clave == "wifi_pass") {
    if (valor.length() > 63) return false;
    pendWifiPass = valor; pendWifiPassOk = true;
    if (pendWifiSsidOk) aplicarWifiRemoto();
    return true;
  }
  else if (clave == "server_url") {
    String v = valor;
    while (v.endsWith("/")) v.remove(v.length() - 1);   // normalizar: sin / final
    // Solo http:// plano (LAN o tunel HTTP): httpSimple no soporta https.
    // https:// se rechaza A PROPOSITO — TLS (WiFiClientSecure/mbedTLS)
    // esta prohibido en esta placa: su init estatico provoca FB-OVF
    // permanente en el driver de camara (verificado en banco, ver nota
    // del include). Por eso la URL de zrok (https://*.shares.zrok.io)
    // NO sirve como server_url: el dispostivo remoto necesita un tunel
    // que acepte HTTP plano (Portwarp u otro).
    // Una URL mala dejaria el dispositivo inalcanzable.
    if (v.length() > 7 && v.length() <= 96 && v.startsWith("http://")
        && v.indexOf(' ') < 0 && v != srvPri) {
      // Nueva URL principal: se activa de inmediato; si no responde,
      // el failover la cambia por srv_alt tras SRV_FAILOVER_UMBRAL polls.
      srvPri = v;
      srvActiva = 0; srvFallos = 0; srvProximoProbe = 0;
      srvBase = v;
      guardarClave("srv", v);
      guardarClave("srv_act", "0");
      Serial.printf("Servidor remoto (principal): %s\n", srvBase.c_str());
      return true;
    }
    Serial.println("server_url rechazada (debe ser http:// válido, <=96 chars)");
    return false;
  }
  else if (clave == "server_url_alt") {
    // URL alternativa de respaldo: se usa automáticamente cuando la
    // principal falla SRV_FAILOVER_UMBRAL polls seguidos, y la principal
    // se re-sondea cada SRV_PROBE_MS para volver cuando se recupere.
    // Cubre el escenario multi-red: p.ej. principal=túnel (Internet),
    // alternativa=LAN del hotspot REBA-AP (sin Internet).
    String v = valor;
    while (v.endsWith("/")) v.remove(v.length() - 1);
    if (v.length() <= 7 || v.length() > 96 || v.indexOf(' ') >= 0
        || !v.startsWith("http://") || v == srvAlt) {
      Serial.println("server_url_alt rechazada (debe ser http:// válido, <=96 chars)");
      return false;
    }
    srvAlt = v;
    guardarClave("srv_alt", v);
    if (srvActiva == 1) srvBase = v;   // actualizar la activa en caliente
    Serial.printf("Servidor remoto (alternativo): %s\n", srvAlt.c_str());
    return true;
  }
  else if (clave == "pump_photos") {
    if (!esEntero(valor)) return false;
    int v = clampInt(valor.toInt(), 1, 50, -1);
    if (v <= 0) return false;
    if (v != pumpPhotos) {
      pumpPhotos = v; guardarClave("pump_photos", String(v));
      Serial.printf("Fotos para bomba: %d\n", pumpPhotos);
    }
    return true;
  }
  else if (clave == "pump_seconds") {
    if (!esEntero(valor)) return false;
    int v = clampInt(valor.toInt(), 1, 300, -1);
    if (v <= 0) return false;
    if ((unsigned long)v != pumpSeconds) {
      pumpSeconds = (unsigned long)v; guardarClave("pump_seconds", String(v));
      Serial.printf("Bomba segundos: %d\n", v);
    }
    return true;
  }
  else if (clave == "dist_min_cm") {
    if (!esFloat(valor)) return false;
    float v = clampFloat(valor.toFloat(), 0.0f, 500.0f, -1.0f);
    if (v < 0.0f || v >= distMaxCm) return false;
    if (v != distMinCm) {
      distMinCm = v; guardarClave("dist_min_cm", String(v));
      Serial.printf("Dist min: %.1f cm\n", distMinCm);
    }
    return true;
  }
  else if (clave == "dist_max_cm") {
    if (!esFloat(valor)) return false;
    float v = clampFloat(valor.toFloat(), 1.0f, 500.0f, -1.0f);
    if (v <= distMinCm) return false;
    if (v != distMaxCm) {
      distMaxCm = v; guardarClave("dist_max_cm", String(v));
      Serial.printf("Dist max: %.1f cm\n", distMaxCm);
    }
    return true;
  }
  else if (clave == "pump_cmd") {
    // "<unix_ts>:<segundos>" — comando de un solo uso desde el dashboard.
    // Se valida formato y se deja PENDIENTE: la ejecución (no bloqueante)
    // y el ack ocurren en loop(), no aquí dentro del parseo de config.
    int sep = valor.indexOf(':');
    if (sep <= 0 || valor.indexOf(':', sep + 1) >= 0) return false;
    String sId = valor.substring(0, sep);
    String sSec = valor.substring(sep + 1);
    if (!esEntero(sId) || !esEntero(sSec)) return false;
    unsigned long id = sId.toInt();
    int secs = sSec.toInt();
    if (id == 0 || id <= lastPumpCmdId || secs < 1 || secs > 300) return false;
    cmdBombaId = id;
    cmdBombaSecs = secs;
    cmdBombaPendiente = true;
    Serial.printf("Cmd bomba pendiente: id=%lu %ds\n", cmdBombaId, cmdBombaSecs);
    return true;
  }
  else if (clave == "ota_url") {
    // URL .bin para auto-actualizacion (OTA por HTTP plano). Dedup por
    // NVS "ota_last": si ya se aplico esta URL no se re-flashea en cada
    // poll — el server puede dejar la clave puesta sin efecto.
    String v = valor;
    while (v.endsWith("/")) v.remove(v.length() - 1);
    if (!otaUrlValida(v)) {
      Serial.println("ota_url rechazada (debe ser http:// <=128 chars)");
      return false;
    }
    prefs.begin("reba", true);
    String last = prefs.getString("ota_last", "");
    prefs.end();
    if (last == v) return true;                  // ya aplicada: no-op
    otaUrlPendiente = v;
    Serial.printf("OTA remoto pendiente: %s\n", v.c_str());
    return true;
  }
  else if (clave == "queue_clear") {
    // "<unix_ts>": vacía la cola de fotos pendientes UNA vez por ts nuevo
    // (dedup RTC — el server puede dejar la clave puesta sin re-disparar).
    // La cola se declara más abajo en el archivo: se marca pendiente y
    // loop() ejecuta el vaciado.
    if (!esEntero(valor)) return false;
    unsigned long ts = valor.toInt();
    if (ts == 0 || ts <= lastQueueClear) return true;   // ya aplicado: no-op
    queueClearPendiente = ts;
    Serial.printf("Cola vacía pendiente (cmd %lu)\n", ts);
    return true;
  }
  return false;   // clave desconocida
}

void aplicarConfigRemota(const String& body) {
  int start = 0;
  while (start < (int)body.length()) {
    int nl = body.indexOf('\n', start);
    String linea = (nl == -1) ? body.substring(start)
                              : body.substring(start, nl);
    start = (nl == -1) ? (int)body.length() : nl + 1;
    linea.trim();
    if (linea.length() == 0 || linea[0] == '#') continue;
    int eq = linea.indexOf('=');
    if (eq <= 0) continue;
    String clave = linea.substring(0, eq); clave.trim();
    String valor = linea.substring(eq + 1); valor.trim();
    aplicarClaveRemota(clave, valor);
  }
}

// Conmuta srvBase entre la URL principal (srvPri) y la alternativa
// (srvAlt). Persiste la elección en NVS "srv_act" para sobrevivir reboots.
void conmutarSrv() {
  if (srvActiva == 0 && srvAlt.length() > 0 && srvAlt != srvPri) {
    srvActiva = 1; srvBase = srvAlt;
  } else if (srvActiva == 1) {
    srvActiva = 0; srvBase = srvPri;
  } else {
    return;                       // sin alternativa útil: nada que hacer
  }
  srvFallos = 0;
  // Si quedamos en la alternativa, agendar la sonda de la principal.
  srvProximoProbe = (srvActiva == 1) ? millis() + SRV_PROBE_MS : 0;
  guardarClave("srv_act", srvActiva ? "1" : "0");
  Serial.printf(">> Server URL conmutada -> %s\n", srvBase.c_str());
}

void consultarConfigRemota() {
  if (WiFi.status() != WL_CONNECTED) return;
  if (srvBase.length() == 0) return;

  // Sonda de recuperación: si estamos en la URL alternativa, probar la
  // principal cada SRV_PROBE_MS. Si responde, volver a ella de inmediato
  // (el body traído es config válida: se aplica igual que un poll normal).
  if (srvActiva == 1 && srvAlt.length() > 0 && srvPri.length() > 0
      && millis() >= srvProximoProbe) {
    srvProximoProbe = millis() + SRV_PROBE_MS;
    String pbody;
    int pc = httpSimple("GET", srvPri + "/api/device/config",
                        nullptr, nullptr, 0, &pbody, 8000);
    if (pc == 200) {
      srvActiva = 0; srvBase = srvPri; srvFallos = 0; srvProximoProbe = 0;
      guardarClave("srv_act", "0");
      Serial.println(">> URL principal recuperada: volviendo a ella");
      if (pbody.length() > 0) aplicarConfigRemota(pbody);
      reportarEstado();
      return;
    }
  }

  String url = srvBase + "/api/device/config";
  String body;
  int codigo = httpSimple("GET", url, nullptr, nullptr, 0, &body, 8000);
  if (codigo == 200) {
    ultimoConfigOk = millis();
    srvFallos = 0;
    if (body.length() > 0) aplicarConfigRemota(body);
    reportarEstado();
  } else {
    // Solo fallos de transporte (timeout/rechazo) cuentan para conmutar:
    // un HTTP 4xx/5xx significa que el host sí es alcanzable.
    if (codigo <= 0) {
      srvFallos++;
      if (srvFallos >= (int)SRV_FAILOVER_UMBRAL) conmutarSrv();
    } else {
      Serial.printf("Config remota: HTTP %d\n", codigo);
    }
  }
}


// ============================================================
// Estado
// ============================================================
struct Foto {
  uint8_t* datos;
  size_t   tam;
  int      intentos;   // reintentos de envío; al llegar a FOTO_MAX_INTENTOS se descarta
};
Foto cola[MAX_COLA];
int  colaInicio = 0;
int  colaCantidad = 0;

int  fotosTomadas = 0;
int  dentro = 0;
int  fuera = 0;
bool armado = true;
unsigned long ultimaLectura = 0;
unsigned long ultimoFalloEnvio = 0;
unsigned long ultimoIntentoWiFi = 0;
bool huboFalloEnvio = false;

// ============================================================
// Relé y bomba
// ============================================================
void releActivar() {
  pinMode(PIN_RELE, OUTPUT);
  digitalWrite(PIN_RELE, RELE_ACTIVO_BAJO ? LOW : HIGH);
}

void releReposo() {
  pinMode(PIN_RELE, OUTPUT);
  digitalWrite(PIN_RELE, RELE_ACTIVO_BAJO ? HIGH : LOW);
}

void bombaEncender() { if (BOMBA_EN_NC) releReposo();  else releActivar(); }
void bombaApagar()   { if (BOMBA_EN_NC) releActivar(); else releReposo();  }

// ============================================================
// Sensor
// ============================================================
float leerCm() {
  digitalWrite(PIN_TRIG, LOW);
  delayMicroseconds(2);
  digitalWrite(PIN_TRIG, HIGH);
  delayMicroseconds(10);
  digitalWrite(PIN_TRIG, LOW);
  unsigned long t = pulseIn(PIN_ECHO, HIGH, 25000);
  if (t == 0) { lastDistCm = -1.0f; return -1.0f; }   // sin eco
  float d = t * 0.0343f / 2.0f;
  // Sanity: el HC-SR04 es fiable hasta ~400 cm; lecturas >450 cm son ruido
  // (eco fantasma/tardío) y se tratan como "sin objeto".
  if (d < 0.0f || d > 450.0f) { lastDistCm = -1.0f; return -1.0f; }
  lastDistCm = d;
  return d;
}

// ============================================================
// Cámara
// ============================================================
camera_config_t configCamara(int frameSize) {
  // IMPORTANTE: memset a 0 — camera_config_t tiene campos que no se
  // asignan abajo (sccb_freq, dma_half, dma_thresh...). Sin esto quedan
  // con basura de stack y el driver SCCB/DMA falla de forma intermitente
  // ("init OK pero sin frames" / FB-OVF). Verificado en banco: el mismo
  // sketch con `= {}` captura frames de forma estable.
  camera_config_t config = {};
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.grab_mode = CAMERA_GRAB_LATEST;

  // Orden: QVGA/DRAM primero — verificado en banco con frame real
  // (320x240). VGA/DRAM inicializa pero no entrega frames en
  // esta placa; un intento fallido seguido de esp_camera_deinit() deja el
  // sensor inutilizable para los intentos siguientes ("init OK sin frames").
  switch (frameSize) {
    case 0:   // QVGA + DRAM (probado: fb OK 320x240)
      config.frame_size = FRAMESIZE_QVGA;
      config.jpeg_quality = 15;
      config.fb_count = 1;
      config.fb_location = CAMERA_FB_IN_DRAM;
      break;
    case 1:   // VGA + DRAM (intento de mayor resolución)
      config.frame_size = FRAMESIZE_VGA;
      config.jpeg_quality = 12;
      config.fb_count = 1;
      config.fb_location = CAMERA_FB_IN_DRAM;
      break;
    case 2:   // QQVGA + DRAM (fallback seguro)
      config.frame_size = FRAMESIZE_QQVGA;
      config.jpeg_quality = 15;
      config.fb_count = 1;
      config.fb_location = CAMERA_FB_IN_DRAM;
      break;
    default:  // VGA + PSRAM último recurso (fb_count=1 para minimizar PSRAM)
      config.frame_size = FRAMESIZE_VGA;
      config.jpeg_quality = 12;
      config.fb_count = 1;
      config.fb_location = CAMERA_FB_IN_PSRAM;
      break;
  }
  return config;
}

void iniciarCamara() {
  // Diagnóstico: RAM disponible para framebuffer DMA
  Serial.printf("heap total=%u mayor_bloque_dma=%u psram=%d\n",
                (unsigned)ESP.getFreeHeap(),
                (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL),
                (int)psramFound());
  // Cadena de fallback: QVGA/DRAM -> VGA/DRAM -> QQVGA/DRAM -> VGA/PSRAM.
  // El sensor OV2640 a veces no entrega frame en el PRIMER fb_get tras
  // init (calentamiento/SCCB): se reintenta fb_get unas veces SIN deinit —
  // un intento fallido seguido de esp_camera_deinit() deja el sensor
  // inutilizable para los intentos siguientes ("init OK sin frames").
  for (int intento = 0; intento < 4; intento++) {
    if (intento == 3 && !psramFound()) break;   // PSRAM es el último recurso
    camera_config_t cfg = configCamara(intento);
    esp_err_t err = esp_camera_init(&cfg);
    if (err != ESP_OK) {
      Serial.printf("Camara intento %d fallo (0x%x)\n", intento, err);
      continue;
    }
    delay(300);   // margen para que el sensor empiece a entregar frames
    camera_fb_t* prueba = nullptr;
    for (int k = 0; k < 4 && !prueba; k++) {
      prueba = esp_camera_fb_get();
      if (!prueba) delay(250);
    }
    if (prueba) {
      esp_camera_fb_return(prueba);
      camaraOk = true;
      Serial.printf("Camara OK: frame_size=%d loc=%s\n",
                    cfg.frame_size, intento == 3 ? "PSRAM" : "DRAM");
      return;
    }
    Serial.printf("Camara intento %d: init OK pero sin frames\n", intento);
    esp_camera_deinit();
    delay(200);
  }
  camaraOk = false;
  Serial.println("Camara no disponible; reintentando cada 30 s");
}

// ============================================================
// WiFi
// ============================================================
bool asegurarWiFi() {
  if (WiFi.status() == WL_CONNECTED) {
    wifiFallos = 0;
    wifiRevertEn = 0;
    // Limpiar respaldo de creds si la conexión actual es estable
    prefs.begin("reba", true);
    bool hayBak = prefs.isKey("ssid_bak");
    prefs.end();
    if (hayBak) {
      prefs.begin("reba", false);
      prefs.remove("ssid_bak"); prefs.remove("pass_bak");
      prefs.end();
    }
    return true;
  }

  // Revierte a las últimas creds que funcionaron si las nuevas fallaron
  // (comparación con signo: segura ante wrap de millis()).
  if (wifiRevertEn != 0 && (long)(millis() - wifiRevertEn) >= 0) {
    prefs.begin("reba", true);
    String ssidBak = prefs.getString("ssid_bak", "");
    String passBak = prefs.getString("pass_bak", "");
    prefs.end();
    if (ssidBak.length() > 0) {
      Serial.println("Credenciales nuevas no funcionaron: revirtiendo a las anteriores");
      wifiSsid = ssidBak; wifiPass = passBak;
      guardarClave("ssid", wifiSsid);
      guardarClave("pass", wifiPass);
      prefs.begin("reba", false);
      prefs.remove("ssid_bak"); prefs.remove("pass_bak");
      prefs.end();
    }
    wifiRevertEn = 0;
    ultimoIntentoWiFi = 0;
  }

  // No bloquear el programa intentando reconectar a cada rato
  if (ultimoIntentoWiFi != 0 && millis() - ultimoIntentoWiFi < REINTENTO_WIFI_MS) {
    return false;
  }
  ultimoIntentoWiFi = millis();

  if (wifiSsid.length() == 0) {
    iniciarPortal();          // sin credenciales: modo configuración
    return false;
  }

  Serial.print("Conectando a WiFi");
  WiFi.disconnect();
  // Si el portal WiFiManager dejó el chip en modo AP, asegurar STA antes
  // de reconectar (sin portal activo no hay razón para mantener AP).
  if (!portalActivo) WiFi.mode(WIFI_STA);
  WiFi.begin(wifiSsid.c_str(), wifiPass.c_str());
  unsigned long inicio = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - inicio < 10000) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();

  // WL_CONNECTED no garantiza IP (DHCP aún pendiente o portal cautivo):
  // exigir una IPv4 válida antes de declarar el enlace usable.
  IPAddress ip = WiFi.localIP();
  bool ipValida = (WiFi.status() == WL_CONNECTED) && ip != IPAddress(0, 0, 0, 0);
  if (ipValida) {
    wifiFallos = 0;
    Serial.print("Conectado. IP del ESP32-CAM: ");
    Serial.println(ip);
    return true;
  }

  wifiFallos++;
  Serial.printf("Sin WiFi (intento %d). Fotos quedan en memoria temporal\n", wifiFallos);
  if (wifiFallos >= WIFI_FAILS_PARA_PORTAL) {
    iniciarPortal();          // red cambió: reabrir portal de configuración
  }
  return false;
}

// ============================================================
// Memoria temporal de fotos (cola en PSRAM)
// ============================================================
bool colaAgregar(const uint8_t* buf, size_t len) {
  // Guard de heap: reservar COLA_HEAP_RESERVA para WiFi/HTTP/driver — una
  // foto enorme (VGA ~30 KB) no debe dejar el sistema sin memoria.
  if (len == 0 || (size_t)ESP.getFreeHeap() < len + COLA_HEAP_RESERVA) {
    Serial.printf("Heap bajo: foto descartada (%u bytes, libres %u)\n",
                  (unsigned)len, (unsigned)ESP.getFreeHeap());
    return false;
  }
  uint8_t* copia = nullptr;
  if (psramFound()) copia = (uint8_t*)ps_malloc(len);
  if (!copia) copia = (uint8_t*)malloc(len);   // fallback si no hay PSRAM
  if (!copia) {
    Serial.println("Sin memoria para guardar la foto");
    return false;
  }
  memcpy(copia, buf, len);

  if (colaCantidad == MAX_COLA) {          // cola llena: se descarta la más vieja
    free(cola[colaInicio].datos);
    cola[colaInicio].datos = nullptr;
    colaInicio = (colaInicio + 1) % MAX_COLA;
    colaCantidad--;
    Serial.println("Cola llena: se descartó la foto más vieja");
  }

  int pos = (colaInicio + colaCantidad) % MAX_COLA;
  cola[pos].datos = copia;
  cola[pos].tam = len;
  cola[pos].intentos = 0;
  colaCantidad++;
  Serial.printf("Foto guardada en memoria (%u bytes). Pendientes: %d\n", (unsigned)len, colaCantidad);
  return true;
}

// ---------- HTTP mínimo sobre WiFiClient ----------
// Reemplaza a HTTPClient (prohibido: rompe la cámara — ver nota arriba).
// Solo soporta http:// plano, que es todo lo que usa REBA en LAN.

bool parseHttpUrl(const String& url, String& host, uint16_t& port, String& path) {
  String u = url;
  u.trim();
  if (u.startsWith("http://")) u = u.substring(7);
  else return false;                       // https u otro esquema: no soportado
  int slash = u.indexOf('/');
  String hp = (slash < 0) ? u : u.substring(0, slash);
  path = (slash < 0) ? "/" : u.substring(slash);
  int colon = hp.indexOf(':');
  if (colon >= 0) {
    host = hp.substring(0, colon);
    port = (uint16_t)hp.substring(colon + 1).toInt();
  } else {
    host = hp; port = 80;
  }
  return host.length() > 0 && port > 0;
}

// Request HTTP/1.1 mínimo. Devuelve el código de estado (200, 404...) o
// -1 si falló conexión/envío. Si respBody != nullptr llena el cuerpo.
int httpSimple(const String& method, const String& url,
               const char* headersExtra, const uint8_t* body, size_t bodyLen,
               String* respBody, unsigned long timeoutMs) {
  String host, path;
  uint16_t port;
  if (!parseHttpUrl(url, host, port, path)) return -1;

  WiFiClient c;
  c.setConnectionTimeout(6000);
  if (c.connect(host.c_str(), port) != 1) { c.stop(); return -1; }

  c.printf("%s %s HTTP/1.1\r\n", method.c_str(), path.c_str());
  c.printf("Host: %s\r\n", host.c_str());
  c.print("Connection: close\r\n");
  c.print("User-Agent: REBA-ESP32\r\n");
  if (headersExtra) c.print(headersExtra);
  if (body && bodyLen) c.printf("Content-Length: %u\r\n", (unsigned)bodyLen);
  c.print("\r\n");
  if (body && bodyLen && c.write(body, bodyLen) != bodyLen) {
    c.stop(); return -1;
  }

  unsigned long deadline = millis() + timeoutMs;
  String line;

  // Status line: "HTTP/1.1 200 OK"
  int status = -1;
  while (millis() < deadline) {
    if (c.available() > 0) {
      line = c.readStringUntil('\n');
      line.trim();
      if (line.length() > 0) {
        int sp = line.indexOf(' ');
        if (sp > 0) status = line.substring(sp + 1, sp + 4).toInt();
        break;
      }
    } else if (!c.connected()) break;   // server cerró sin responder
    else delay(1);
  }

  // Headers hasta la línea vacía
  while (millis() < deadline && status > 0) {
    if (c.available() > 0) {
      line = c.readStringUntil('\n');
      line.trim();
      if (line.length() == 0) break;
    } else if (!c.connected()) break;
    else delay(1);
  }

  // Cuerpo (solo si se pidió) — con tope: un server que responda basura
  // no debe inflar el String hasta agotar el heap.
  if (respBody) {
    respBody->reserve(512);
    while (millis() < deadline) {
      int avail = c.available();
      if (avail > 0) {
        uint8_t buf[256];
        int leido = c.read(buf, sizeof(buf));
        if (leido > 0 && respBody->length() < HTTP_RESP_MAX) {
          respBody->concat((const char*)buf, leido);
        }
      } else if (!c.connected()) break;
      else delay(2);
    }
  }
  c.stop();
  return status;
}

// Devuelve el código HTTP (200, 404...) o -1 si no hubo respuesta.
// 2xx = entregada; 4xx = error permanente del cliente (no reintentar);
// -1/5xx = transitorio (reintentar).
int enviarJPG(const uint8_t* datos, size_t tam) {
  if (!asegurarWiFi()) return -1;
  if (srvBase.length() == 0) return -1;

  String url = srvBase + "/upload";
  const char* hdrs =
    "Content-Type: image/jpeg\r\n"
    "X-Evento: ingreso\r\n";
  int codigo = httpSimple("POST", url, hdrs, datos, tam, nullptr, 15000);
  if (codigo > 0) {
    Serial.printf("Foto enviada (%u bytes) -> HTTP %d\n", (unsigned)tam, codigo);
  } else {
    Serial.println("Error enviando foto (sin respuesta del servidor)");
  }
  return codigo;
}

void colaEnviar() {
  if (colaCantidad == 0) return;
  if (huboFalloEnvio && millis() - ultimoFalloEnvio < REINTENTO_ENVIO_MS) return;

  while (colaCantidad > 0) {
    Foto& f = cola[colaInicio];
    int codigo = enviarJPG(f.datos, f.tam);
    f.intentos++;

    // Entregada (2xx) -> borrar. Error 4xx o demasiados reintentos ->
    // descartar: una foto "venenosa" (p.ej. JPEG que el server rechaza
    // siempre con 400) no debe bloquear el resto de la cola para siempre.
    bool entregada = (codigo >= 200 && codigo < 300);
    bool permanente = (codigo >= 400 && codigo < 500) || f.intentos >= FOTO_MAX_INTENTOS;
    if (entregada || permanente) {
      if (permanente && !entregada) {
        Serial.printf("Foto descartada (HTTP %d, intentos %d)\n", codigo, f.intentos);
      }
      free(f.datos);                       // procesada: se borra de la memoria
      f.datos = nullptr;
      colaInicio = (colaInicio + 1) % MAX_COLA;
      colaCantidad--;
      if (!entregada) huboFalloEnvio = false;  // no es fallo de red: seguir con la siguiente
      continue;
    }

    // Fallo transitorio: frenar el lote y reintentar en REINTENTO_ENVIO_MS
    huboFalloEnvio = true;
    ultimoFalloEnvio = millis();
    Serial.printf("Se reintentará en %lu s. Pendientes: %d\n",
                  REINTENTO_ENVIO_MS / 1000, colaCantidad);
    return;
  }
  huboFalloEnvio = false;
}

// ============================================================
// Telemetría del circuito -> POST /api/device/status
// ============================================================
// Se reporta tras cada GET /api/device/config exitoso (~60 s): estado
// del sensor, relé, cola de fotos, memoria y radio. El dashboard lo
// muestra en la sección "Circuito". Sin TLS: mismo httpSimple plano.
void reportarEstado() {
  if (WiFi.status() != WL_CONNECTED || srvBase.length() == 0) return;
  String body = "{\"uptime_s\":" + String((unsigned long)(millis() / 1000))
    + ",\"boots\":" + String(bootCount)
    + ",\"heap\":" + String((unsigned)ESP.getFreeHeap())
    + ",\"rssi\":" + String(WiFi.RSSI())
    + ",\"camara_ok\":" + String(camaraOk ? 1 : 0)
    + ",\"dist_cm\":" + String(lastDistCm, 1)
    + ",\"cola_n\":" + String(colaCantidad)
    + ",\"cola_max\":" + String(MAX_COLA)
    + ",\"pump_photos_n\":" + String(fotosTomadas)
    + ",\"pump_photos_max\":" + String(pumpPhotos)
    + ",\"pump_manual\":" + String(bombaManualActiva ? 1 : 0)
    + ",\"rele_pin\":" + String(digitalRead(PIN_RELE))
    + "}";
  String url = srvBase + "/api/device/status";
  const char* hdrs = "Content-Type: application/json\r\n";
  int codigo = httpSimple("POST", url, hdrs,
                          (const uint8_t*)body.c_str(), body.length(),
                          nullptr, 8000);
  if (codigo != 200) Serial.printf("Status -> HTTP %d\n", codigo);
}

// ============================================================
// OTA: auto-actualización por HTTP plano (sin TLS, sin HTTPClient)
// ============================================================
// Descarga un .bin desde una URL http:// y lo escribe en la partición
// OTA inactiva con Update (flash directo, no necesita heap grande).
// Requiere partition scheme CON OTA (no huge_app): "Minimal SPIFFS
// (1.9MB APP with OTA/128KB SPIFFS)" en Arduino IDE.
//   Disparo serie:  OTA              -> {srvBase}/firmware.bin
//                   OTA http://IP:5000/otro.bin
//   Disparo remoto: clave ota_url=http://... en /api/device/config
//                   (dedup NVS "ota_last": no re-flashea la misma URL).
// Seguridad: Update.end() estricto — una descarga truncada aborta y la
// app actual queda intacta. Si el bin nuevo no bootea, recovery =
// reflashear por USB (OTA no tiene rollback automático en este core).
// Durante la descarga el loop() se pausa ~1-2 min (mantenimiento).
// ============================================================

bool otaUrlValida(const String& u) {
  return u.length() > 7 && u.length() <= 128 &&
         u.startsWith("http://") && u.indexOf(' ') < 0;
}

// Devuelve true si el flash terminó OK (reinicia solo). false = fallo
// (Update.abort() ya llamado; la app actual sigue intacta).
bool otaDescargarYFlashear(const String& url, unsigned long timeoutMs) {
  if (!asegurarWiFi()) { Serial.println("OTA: sin WiFi"); return false; }
  String host, path;
  uint16_t port;
  if (!parseHttpUrl(url, host, port, path)) {
    Serial.println("OTA: URL invalida (debe ser http://)");
    return false;
  }

  WiFiClient c;
  c.setConnectionTimeout(8000);
  if (c.connect(host.c_str(), port) != 1) {
    c.stop(); Serial.println("OTA: no se pudo conectar"); return false;
  }
  c.printf("GET %s HTTP/1.1\r\n", path.c_str());
  c.printf("Host: %s\r\n", host.c_str());
  c.print("Connection: close\r\n");
  c.print("User-Agent: REBA-ESP32\r\n");
  c.print("Accept: application/octet-stream\r\n");
  c.print("\r\n");

  unsigned long deadline = millis() + timeoutMs;
  String line;

  // Status line
  int status = -1;
  while (millis() < deadline) {
    if (c.available() > 0) {
      line = c.readStringUntil('\n'); line.trim();
      if (line.length() > 0) {
        int sp = line.indexOf(' ');
        if (sp > 0) status = line.substring(sp + 1, sp + 4).toInt();
        break;
      }
    } else if (!c.connected()) break;
    else delay(1);
  }
  if (status != 200) {
    c.stop();
    Serial.printf("OTA: HTTP %d\n", status);
    return false;
  }

  // Headers: capturar Content-Length si viene (0 = desconocido/chunked)
  size_t contentLen = 0;
  while (millis() < deadline) {
    if (c.available() > 0) {
      line = c.readStringUntil('\n'); line.trim();
      if (line.length() == 0) break;
      int cl = line.indexOf(':');
      if (cl > 0 && line.substring(0, cl).equalsIgnoreCase("Content-Length")) {
        contentLen = (size_t)line.substring(cl + 1).toInt();
      }
    } else if (!c.connected()) break;
    else delay(1);
  }

  if (!Update.begin(contentLen ? contentLen : UPDATE_SIZE_UNKNOWN)) {
    c.stop();
    Serial.printf("OTA: Update.begin fallo (%s)\n", Update.errorString());
    return false;
  }

  // Stream del cuerpo -> flash. Watchdog de 15 s sin datos = abortar.
  uint8_t buf[1024];
  size_t total = 0, ultimoMb = 0;
  unsigned long ultimoDato = millis();
  bool ok = true;
  while (c.connected() && millis() < deadline) {
    int avail = c.available();
    if (avail > 0) {
      int leido = c.read(buf, sizeof(buf));
      if (leido > 0) {
        if (Update.write(buf, leido) != (size_t)leido) { ok = false; break; }
        total += leido;
        ultimoDato = millis();
        if (total - ultimoMb >= 262144) {          // progreso cada 256 KB
          ultimoMb = total;
          Serial.printf("OTA: %u KB...\n", (unsigned)(total / 1024));
        }
      }
    } else {
      if (millis() - ultimoDato > 15000) { ok = false; break; }
      delay(1);
    }
  }
  c.stop();

  if (!ok || !Update.end()) {                     // end() estricto: tamano exacto
    Update.abort();
    Serial.printf("OTA: descarga/integridad fallo (%s) tras %u bytes\n",
                  Update.errorString(), (unsigned)total);
    return false;
  }

  // Flash OK: marcar URL como aplicada (dedup de ota_url remoto) y reboot.
  guardarClave("ota_last", url);
  Serial.printf("OTA: flash OK (%u bytes). Reiniciando en 1 s...\n",
                (unsigned)total);
  delay(1000);
  ESP.restart();
  return true;                                    // no llega (restart)
}

// Procesa otaUrlPendiente (config remota). Backoff 10 min tras fallo:
// la clave sigue en el server, asi que el proximo poll la re-encola —
// el gate de tiempo evita martillear una URL muerta cada 60 s.
void aplicarOtaPendiente() {
  if (otaUrlPendiente.length() == 0) return;
  if (!otaUrlValida(otaUrlPendiente)) { otaUrlPendiente = ""; return; }
  if ((long)(millis() - otaReintentoEn) < 0) return;   // backoff activo
  String url = otaUrlPendiente;
  otaUrlPendiente = "";
  Serial.printf("OTA remoto: descargando %s\n", url.c_str());
  if (!otaDescargarYFlashear(url, 180000)) {
    otaReintentoEn = millis() + 600000;
    Serial.println("OTA fallo; reintento en 10 min");
  }
}

// ============================================================
// Foto con flash corto
// ============================================================
bool tomarFoto() {
  if (!camaraOk) { Serial.println("Camara no disponible aun"); return false; }
  digitalWrite(PIN_FLASH, HIGH);           // flash ON
  delay(FLASH_PREVIO_MS);                  // la cámara ajusta la exposición

  camera_fb_t* viejo = esp_camera_fb_get();   // descarta un frame anterior al flash
  if (viejo) esp_camera_fb_return(viejo);

  // El frame real puede caer de forma transitoria (sombra, I2C glitch):
  // reintentar hasta 3 veces antes de declarar fallo.
  camera_fb_t* fb = nullptr;
  for (int k = 0; k < 3 && !fb; k++) {
    fb = esp_camera_fb_get();
    if (!fb) delay(120);
  }
  digitalWrite(PIN_FLASH, LOW);            // flash OFF apenas se capturó

  if (!fb) {
    fallosCamara++;
    Serial.printf("Error: no se pudo capturar (fallo %d). heap=%u dma_max=%u\n",
                  fallosCamara,
                  (unsigned)ESP.getFreeHeap(),
                  (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL));
    // Fallos consecutivos -> marcar cámara caída: el loop la re-inicializa.
    if (fallosCamara >= FALLAS_CAMARA_REINIT) {
      Serial.println("Camara marcada como caida; re-inicializando...");
      camaraOk = false;
      fallosCamara = 0;
      esp_camera_deinit();
    }
    return false;
  }

  // Validar que el buffer sea un JPEG real antes de encolarlo: un frame
  // corrupto (FB-OVF, DMA glitch) no debe subirse ni contar para la bomba.
  bool jpegValido = fb->len >= 128 && fb->buf[0] == 0xFF &&
                    fb->buf[1] == 0xD8 && fb->buf[2] == 0xFF;
  if (!jpegValido) {
    Serial.printf("Frame corrupto descartado (%u bytes, magic %02x%02x%02x)\n",
                  (unsigned)fb->len, fb->buf[0], fb->buf[1], fb->buf[2]);
    esp_camera_fb_return(fb);
    fallosCamara++;
    if (fallosCamara >= FALLAS_CAMARA_REINIT) {
      camaraOk = false; fallosCamara = 0; esp_camera_deinit();
    }
    return false;
  }

  fallosCamara = 0;                        // captura sana
  bool ok = colaAgregar(fb->buf, fb->len); // copia a la memoria temporal
  esp_camera_fb_return(fb);                // libera el buffer de la cámara
  return ok;
}

// ============================================================
// Bomba
// ============================================================
void correrBomba() {
  Serial.printf("Bomba ENCENDIDA %lu s\n", pumpSeconds);
  bombaEncender();
  delay(pumpSeconds * 1000UL);
  bombaApagar();
  bombaManualActiva = false;   // el ciclo automático también apaga la manual
  Serial.println("Bomba APAGADA");

  fotosTomadas = 0;
  dentro = 0;
  fuera = 0;
  armado = false;                          // el objeto debe alejarse antes de otro conteo
}

// ============================================================
// Cada vez que entra un objeto
// ============================================================
void ingresoDetectado() {
  Serial.println(">> Ingreso de objeto detectado");

  if (tomarFoto()) {
    fotosTomadas++;
    Serial.printf("Fotos tomadas: %d de %d\n", fotosTomadas, pumpPhotos);
  }

  colaEnviar();                            // intenta enviar lo que haya en memoria

  if (fotosTomadas >= pumpPhotos) {
    correrBomba();
  }
}

// ============================================================
void setup() {
  Serial.begin(115200);
  bootCount++;
  Serial.printf("heap_setup=%u boots=%lu\n", (unsigned)ESP.getFreeHeap(), bootCount);
  if (bootCount > 8) {
    Serial.println("AVISO: posibles resets repetidos (brownout/watchdog)");
  }
  bombaApagar();                           // lo primero: bomba apagada

  pinMode(PIN_FLASH, OUTPUT);
  digitalWrite(PIN_FLASH, LOW);
  pinMode(PIN_TRIG, OUTPUT);
  pinMode(PIN_ECHO, INPUT);
  digitalWrite(PIN_TRIG, LOW);

  Serial.begin(115200);
  Serial.setDebugOutput(false);
  delay(500);

  // La cámara va PRIMERO: prefs.begin() (NVS) consume ~47KB de heap y el
  // driver de cámara necesita ~250KB contiguos — si NVS inicializa antes,
  // cam_dma_config falla en placas con heap justo.
  iniciarCamara();
  ultimoTestCamara = millis();        // primer health check en 15 min

  cargarConfig();
  ultimoConfigOk = millis();          // margen inicial para el primer poll
  WiFi.mode(WIFI_STA);
  asegurarWiFi();                          // si no hay creds -> abre portal

  // Dos destellos cortos: "listo"
  for (int i = 0; i < 2; i++) {
    digitalWrite(PIN_FLASH, HIGH); delay(100);
    digitalWrite(PIN_FLASH, LOW);  delay(150);
  }
  Serial.println("REBA listo: vigilando rango configurable");
  Serial.println("Config USB: HELP para ver comandos");
}

// ------------------------------------------------------------
// Config por puerto serie (USB): alternativa al portal WiFi —
// no requiere celular ni red, ideal para aprovisionar en campo.
//   GET
//   SET wifi_ssid=REBA-AP
//   SET wifi_pass=clave
//   SET server_url=http://192.168.137.1:5000
//   SET pump_photos=3  pump_seconds=10  dist_min_cm=0  dist_max_cm=23
//   REBOOT
// Reutiliza aplicarClaveRemota: mismas claves, validación, respaldos
// y reversión automática que la configuración remota del servidor.
// ------------------------------------------------------------
void ejecutarComandoSerial(String cmd) {
  cmd.trim();
  if (cmd.length() == 0) return;

  String op = cmd, resto = "";
  int sp = cmd.indexOf(' ');
  if (sp >= 0) { op = cmd.substring(0, sp); resto = cmd.substring(sp + 1); resto.trim(); }
  op.toUpperCase();

  if (op == "HELP") {
    Serial.println(F("Comandos serie:"));
    Serial.println(F("  GET"));
    Serial.println(F("  SET wifi_ssid=... wifi_pass=... server_url=..."));
    Serial.println(F("  SET pump_photos=N pump_seconds=N dist_min_cm=F dist_max_cm=F"));
    Serial.println(F("  CAPTURE   (foto manual: flash + cola + upload + contador bomba)"));
    Serial.println(F("  STATUS    (diagnostico: heap, camara, wifi, cola, sensor)"));
    Serial.println(F("  OTA       (actualizacion de firmware: {server_url}/firmware.bin)"));
    Serial.println(F("  OTA http://IP:5000/otro.bin   (URL explicita)"));
    Serial.println(F("  REBOOT"));
  }
  else if (op == "GET") {
    prefs.begin("reba", true);
    String otaLast = prefs.getString("ota_last", "-");
    prefs.end();
    Serial.printf("wifi_ssid=%s\nwifi_pass=%s\nserver_url=%s\nserver_url_alt=%s\nsrv_activa=%d\n"
                  "pump_photos=%d\npump_seconds=%d\ndist_min_cm=%.1f\ndist_max_cm=%.1f\n"
                  "ota_last=%s\n",
                  wifiSsid.c_str(), wifiPass.c_str(), srvPri.c_str(), srvAlt.c_str(), srvActiva,
                  pumpPhotos, pumpSeconds, distMinCm, distMaxCm,
                  otaLast.c_str());
  }
  else if (op == "STATUS") {
    Serial.printf("uptime=%lus boots=%lu heap=%u/%u\n",
                  (unsigned long)(millis() / 1000), bootCount,
                  (unsigned)ESP.getFreeHeap(),
                  (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL));
    Serial.printf("camara=%s fallos=%d wifi=%s ip=%s portal=%d\n",
                  camaraOk ? "OK" : "CAIDA", fallosCamara,
                  WiFi.status() == WL_CONNECTED ? "conectada" : "caida",
                  WiFi.localIP().toString().c_str(), (int)portalActivo);
    Serial.printf("cola=%d/%d fotos_bomba=%d/%d dist_ultima=%.1fcm rango=[%.1f,%.1f]\n",
                  colaCantidad, MAX_COLA, fotosTomadas, pumpPhotos,
                  lastDistCm, distMinCm, distMaxCm);
  }
  else if (op == "SET") {
    int eq = resto.indexOf('=');
    if (eq <= 0) { Serial.println("Formato: SET clave=valor"); return; }
    String clave = resto.substring(0, eq); clave.trim();
    String valor = resto.substring(eq + 1); valor.trim();
    static const char* permitidas[] = {
      "wifi_ssid", "wifi_pass", "server_url",
      "pump_photos", "pump_seconds", "dist_min_cm", "dist_max_cm",
      "ota_url", "queue_clear", "server_url_alt"
    };
    bool ok = false;
    for (const char* k : permitidas) if (clave == k) { ok = true; break; }
    if (!ok) { Serial.println("Clave no permitida. Usa HELP."); return; }
    if (aplicarClaveRemota(clave, valor))
      Serial.printf("SET %s aplicado (GET para ver estado)\n", clave.c_str());
    else
      Serial.printf("SET %s rechazada\n", clave.c_str());
  }
  else if (op == "CAPTURE") {
    Serial.println("CAPTURE manual por USB...");
    ingresoDetectado();
  }
  else if (op == "OTA") {
    // Manual: fuerza la actualizacion (ignora dedup ota_last).
    // `OTA` solo -> {srvBase}/firmware.bin ; `OTA <url>` -> URL explicita.
    String url = (resto.length() > 0) ? resto : (srvBase + "/firmware.bin");
    if (!otaUrlValida(url)) {
      Serial.println("OTA: URL invalida (usa http://...)"); return;
    }
    if (!asegurarWiFi()) { Serial.println("OTA: sin WiFi"); return; }
    Serial.printf("OTA manual: %s\n", url.c_str());
    otaDescargarYFlashear(url, 180000);
  }
  else if (op == "REBOOT") {
    Serial.println("Reiniciando...");
    delay(300);
    ESP.restart();
  }
  else {
    Serial.println("Comando desconocido. HELP para ver opciones.");
  }
}

void procesarSerial() {
  static String linea;
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (linea.length() > 0) { ejecutarComandoSerial(linea); linea = ""; }
    } else if (linea.length() < 160) {
      linea += c;
    }
  }
}

// Ejecuta el comando de bomba pendiente (llega por config remota o SET).
// NO bloquea: la bomba se apaga sola al cumplirse el plazo, verificado en
// loop() — así el sensor, la cola y el WiFi siguen vivos durante el ciclo.
void procesarCmdBomba() {
  if (!cmdBombaPendiente) return;
  cmdBombaPendiente = false;
  if (cmdBombaId <= lastPumpCmdId) return;      // dedup: ya se ejecutó
  lastPumpCmdId = cmdBombaId;                   // RTC: sobrevive resets

  bombaManualActiva = true;
  bombaManualFin = millis() + (unsigned long)cmdBombaSecs * 1000UL;
  Serial.printf("Bomba MANUAL %ds (cmd %lu)\n", cmdBombaSecs, cmdBombaId);
  bombaEncender();

  // ACK al servidor: borra el override para que no se re-entregue.
  // Si falla (sin WiFi), el TTL del server lo expira; el dedup RTC evita
  // re-ejecución si el comando resucita en un poll posterior.
  if (WiFi.status() == WL_CONNECTED && srvBase.length() > 0) {
    String url = srvBase + "/api/device/ack";
    String body = "{\"cmd_id\":" + String(cmdBombaId) + "}";
    const char* hdrs = "Content-Type: application/json\r\n";
    int codigo = httpSimple("POST", url, hdrs,
                            (const uint8_t*)body.c_str(), body.length(),
                            nullptr, 8000);
    Serial.printf("Ack bomba cmd %lu -> HTTP %d\n", cmdBombaId, codigo);
  }
}

void loop() {
  unsigned long ahora = millis();

  revisarPortal();
  procesarSerial();
  procesarCmdBomba();
  aplicarOtaPendiente();

  // Vaciado de cola de fotos pedido por el server (queue_clear).
  if (queueClearPendiente != 0) {
    lastQueueClear = queueClearPendiente;
    queueClearPendiente = 0;
    int n = colaCantidad;
    while (colaCantidad > 0) {
      free(cola[colaInicio].datos);
      cola[colaInicio].datos = nullptr;
      colaInicio = (colaInicio + 1) % MAX_COLA;
      colaCantidad--;
    }
    Serial.printf("Cola vaciada por comando remoto (%d fotos descartadas)\n", n);
  }

  // Apagado de la bomba manual al cumplirse el plazo (wrap-safe).
  if (bombaManualActiva && (long)(ahora - bombaManualFin) >= 0) {
    bombaManualActiva = false;
    bombaApagar();
    Serial.println("Bomba manual APAGADA");
  }

  // Sin credenciales guardadas: mantener el AP de configuración disponible
  // (el portal se cierra a los 5 min; reabrirlo periódicamente).
  // Patrón "último intento + intervalo": seguro ante wrap de millis().
  static unsigned long ultimoIntentoPortal = 0;
  if (wifiSsid.length() == 0 && WiFi.status() != WL_CONNECTED && !portalActivo
      && millis() - ultimoIntentoPortal >= PORTAL_RETRY_MS) {
    ultimoIntentoPortal = millis();
    iniciarPortal();
  }

  // Cámara caída: reintentar init cada 30 s (cubre fallos transitorios de
  // alimentación/sensor sin reiniciar el dispositivo).
  static unsigned long ultimoIntentoCamara = 0;
  if (!camaraOk && millis() - ultimoIntentoCamara >= CAMARA_RETRY_MS) {
    ultimoIntentoCamara = millis();
    iniciarCamara();
  }

  // Health check silencioso de la cámara cada 15 min: detecta muerte
  // silenciosa (glitch I2C/power) aunque no haya triggers del sensor.
  if (camaraOk && ahora - ultimoTestCamara >= CAMARA_TEST_MS) {
    ultimoTestCamara = ahora;
    camera_fb_t* t = esp_camera_fb_get();
    if (t) {
      esp_camera_fb_return(t);
    } else {
      Serial.println("Health check camara: sin frame -> re-inicializando");
      camaraOk = false;
      fallosCamara = 0;
      esp_camera_deinit();
    }
  }

  // Descarga periódica de la configuración remota del servidor
  if (ahora - ultimoPollConfig >= POLL_CONFIG_MS) {
    ultimoPollConfig = ahora;
    consultarConfigRemota();
  }

  if (ahora - ultimaLectura < PERIODO_SENSOR_MS) return;
  ultimaLectura = ahora;

  float d = leerCm();
  // Piso de 1 cm: lecturas ~0 son ruido del sensor, no un objeto real.
  bool enRango = (d >= 1.0f) && (d >= distMinCm) && (d <= distMaxCm);

  if (enRango) { if (dentro < 100) dentro++; fuera = 0; }
  else         { if (fuera < 100)  fuera++;  dentro = 0; }

  if (!armado) {
    if (fuera >= LECTURAS_PARA_REARMAR) armado = true;
  } else if (dentro >= LECTURAS_PARA_CONTAR) {
    armado = false;
    ingresoDetectado();
  }

  // Si hay fotos pendientes (falló el envío antes), reintenta en segundo plano
  if (colaCantidad > 0) colaEnviar();
}
