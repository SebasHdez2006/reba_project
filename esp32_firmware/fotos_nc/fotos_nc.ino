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
// Configuración (SIN recompilar):
//  - Primera vez / sin WiFi guardada: el ESP32 levanta el AP
//    "REBA-Setup". Conéctate desde el celular, abre http://192.168.4.1
//    y captura: red WiFi, contraseña y URL del servidor.
//  - Después el dispositivo descarga cada POLL_CONFIG_MS la config del
//    servidor (GET /api/device/config) y aplica cambios en caliente:
//    WiFi, URL del servidor, umbrales y parámetros de la bomba.
//  - Si el WiFi guardado deja de funcionar ~10 min (cambiaste de red),
//    el AP "REBA-Setup" se reactiva solo para reconfigurar.
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
// NOTA: HTTPClient.h está PROHIBIDO en este firmware — enlaza
// WiFiClientSecure/mbedTLS cuyo init estático provoca FB-OVF permanente
// en el driver de cámara (ver test_cam2..5). El HTTP se hace con
// WiFiClient (socket TCP plano) en httpSimple() más abajo.

// ---------------- Valores por defecto (se sobreescriben por config remota) ----------------
// SRV_BASE: URL base del servidor FastAPI SIN /upload, ej. http://192.168.1.3:5000
// Solo se usa como valor inicial del portal; en producción la config
// remota puede cambiarla (ojo: si el ESP32 no alcanza el servidor no
// puede descargar la nueva URL — ver docs).
static const char* SRV_BASE_DEFAULT = "http://192.168.1.3:5000";

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
const unsigned long SRV_REVERT_MS       = 360000; // si server_url nuevo no responde en 6 min, revierte
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
String         srvBase;            // ej. "http://192.168.1.3:5000" (sin /upload)

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
unsigned long  srvRevertEn       = 0;   // idem para server_url

// --- Bomba manual (comando servidor -> ESP32 vía /api/device/config) ---
// El server encola "pump_cmd=<unix_ts>:<segundos>"; se ejecuta una sola vez
// (dedup por lastPumpCmdId en RTC) y se confirma con POST /api/device/ack.
// La ejecución es NO bloqueante: lazo normal sigue vivo durante la bomba.
RTC_DATA_ATTR unsigned long lastPumpCmdId = 0;
bool           cmdBombaPendiente = false;
unsigned long  cmdBombaId        = 0;
int            cmdBombaSecs      = 0;
bool           bombaManualActiva = false;
unsigned long  bombaManualFin    = 0;   // millis() deadline (wrap-safe)

// WiFiManager se crea SOLO cuando se abre el portal de configuración.
// Como global consumía ~45KB de heap desde el arranque y la cámara
// (que necesita ~250KB contiguos) no inicializaba en esta placa.
WiFiManager*           wmP   = nullptr;
WiFiManagerParameter*  pSrvP = nullptr;

// Definida más abajo (sección HTTP); la usa consultarConfigRemota().
int httpSimple(const String& method, const String& url,
               const char* headersExtra, const uint8_t* body, size_t bodyLen,
               String* respBody, unsigned long timeoutMs);

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
  srvBase  = prefs.getString("srv", SRV_BASE_DEFAULT);
  // Las claves numéricas se persisten como string (guardarClave); leerlas
  // con getInt/getFloat devolvería el default por desajuste de tipo en NVS.
  pumpPhotos  = clampInt(prefs.getString("pump_photos", "3").toInt(),   1, 50,   3);
  pumpSeconds = clampInt(prefs.getString("pump_seconds", "10").toInt(), 1, 300, 10);
  distMinCm   = clampFloat(prefs.getString("dist_min_cm", "0").toFloat(), 0.0f, 500.0f, 0.0f);
  distMaxCm   = clampFloat(prefs.getString("dist_max_cm", "23").toFloat(), 1.0f, 500.0f, 23.0f);
  prefs.end();
  // Rango coherente: si min >= max (NVS corrupto), restablecer defaults.
  if (distMinCm >= distMaxCm) { distMinCm = 0.0f; distMaxCm = 23.0f; }
  // server_url debe ser http:// alcanzable por httpSimple; si no, default.
  if (!srvBase.startsWith("http://")) srvBase = SRV_BASE_DEFAULT;
  while (srvBase.endsWith("/")) srvBase.remove(srvBase.length() - 1);
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
  if (s.length() > 0) prefs.putString("srv", s);
  prefs.end();

  wifiSsid = wmP->getWiFiSSID();
  wifiPass = wmP->getWiFiPass();
  if (s.length() > 0) srvBase = s;
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
    // Solo http:// plano (LAN): httpSimple no soporta https ni otros
    // esquemas; una URL mala dejaría el dispositivo inalcanzable.
    if (v.length() > 7 && v.length() <= 96 && v.startsWith("http://")
        && v.indexOf(' ') < 0 && v != srvBase) {
      // Respaldo: si la URL nueva no responde en ~6 min se revierte.
      guardarClave("srv_bak", srvBase);
      srvBase = v;
      guardarClave("srv", srvBase);
      srvRevertEn = millis() + SRV_REVERT_MS;
      Serial.printf("Servidor remoto: %s — probando...\n", srvBase.c_str());
      return true;
    }
    Serial.println("server_url rechazada (debe ser http:// válido, <=96 chars)");
    return false;
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

void consultarConfigRemota() {
  if (WiFi.status() != WL_CONNECTED) return;
  if (srvBase.length() == 0) return;

  String url = srvBase + "/api/device/config";
  String body;
  int codigo = httpSimple("GET", url, nullptr, nullptr, 0, &body, 8000);
  if (codigo == 200) {
    ultimoConfigOk = millis();
    // Servidor alcanzable: confirmar el cambio de URL (quitar respaldo)
    if (srvRevertEn != 0) {
      srvRevertEn = 0;
      prefs.begin("reba", false);
      prefs.remove("srv_bak");
      prefs.end();
      Serial.println("Nueva URL de servidor confirmada");
    }
    if (body.length() > 0) aplicarConfigRemota(body);
  } else if (codigo > 0) {
    Serial.printf("Config remota: HTTP %d\n", codigo);
  }
}

void revisarReversionSrv() {
  // Si la URL nueva lleva >6 min sin un GET exitoso, volver a la anterior.
  // (long)(millis()-deadline) es seguro ante el wrap de millis() (~49 días).
  if (srvRevertEn == 0) return;
  if ((long)(millis() - srvRevertEn) < 0) return;
  if (millis() - ultimoConfigOk < 180000UL) return;   // aún hay margen

  prefs.begin("reba", true);
  String srvBak = prefs.getString("srv_bak", "");
  prefs.end();
  if (srvBak.length() > 0) {
    Serial.printf("URL de servidor no responde: revirtiendo a %s\n", srvBak.c_str());
    srvBase = srvBak;
    guardarClave("srv", srvBase);
  }
  srvRevertEn = 0;
  prefs.begin("reba", false);
  prefs.remove("srv_bak");
  prefs.end();
  ultimoPollConfig = 0;               // forzar consulta inmediata
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
  // ("init OK pero sin frames" / FB-OVF). Ver test_cam4/5 vs 6/7.
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

  // Orden: QVGA/DRAM primero — es la config exacta verificada en test_cam2
  // (frame real capturado). VGA/DRAM inicializa pero no entrega frames en
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
    Serial.println(F("  REBOOT"));
  }
  else if (op == "GET") {
    Serial.printf("wifi_ssid=%s\nwifi_pass=%s\nserver_url=%s\n"
                  "pump_photos=%d\npump_seconds=%d\ndist_min_cm=%.1f\ndist_max_cm=%.1f\n",
                  wifiSsid.c_str(), wifiPass.c_str(), srvBase.c_str(),
                  pumpPhotos, pumpSeconds, distMinCm, distMaxCm);
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
      "pump_photos", "pump_seconds", "dist_min_cm", "dist_max_cm"
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
  revisarReversionSrv();
  procesarSerial();
  procesarCmdBomba();

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
