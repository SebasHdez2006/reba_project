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
#include <HTTPClient.h>
#include <Preferences.h>

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
unsigned long  ultimoPollConfig  = 0;
unsigned long  wifiRevertEn      = 0;   // millis() en que se revierte creds si sigue sin conectar
unsigned long  ultimoConfigOk    = 0;   // último GET /api/device/config exitoso
unsigned long  srvRevertEn       = 0;   // idem para server_url

// WiFiManager se crea SOLO cuando se abre el portal de configuración.
// Como global consumía ~45KB de heap desde el arranque y la cámara
// (que necesita ~250KB contiguos) no inicializaba en esta placa.
WiFiManager*           wmP   = nullptr;
WiFiManagerParameter*  pSrvP = nullptr;

void cargarConfig() {
  prefs.begin("reba", true);
  wifiSsid = prefs.getString("ssid", "");
  wifiPass = prefs.getString("pass", "");
  srvBase  = prefs.getString("srv", SRV_BASE_DEFAULT);
  // Las claves numéricas se persisten como string (guardarClave); leerlas
  // con getInt/getFloat devolvería el default por desajuste de tipo en NVS.
  pumpPhotos  = prefs.getString("pump_photos", "3").toInt();
  pumpSeconds = prefs.getString("pump_seconds", "10").toInt();
  distMinCm   = prefs.getString("dist_min_cm", "0").toFloat();
  distMaxCm   = prefs.getString("dist_max_cm", "23").toFloat();
  prefs.end();
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
  if (nuevoSsid.length() == 0) return;
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

void aplicarClaveRemota(const String& clave, const String& valor) {
  if (clave == "wifi_ssid") {
    pendWifiSsid = valor; pendWifiSsidOk = true;
    if (pendWifiPassOk) aplicarWifiRemoto();
  }
  else if (clave == "wifi_pass") {
    pendWifiPass = valor; pendWifiPassOk = true;
    if (pendWifiSsidOk) aplicarWifiRemoto();
  }
  else if (clave == "server_url") {
    String v = valor;
    while (v.endsWith("/")) v.remove(v.length() - 1);   // normalizar: sin / final
    if (v.length() > 0 && v != srvBase) {
      // Respaldo: si la URL nueva no responde en ~6 min se revierte.
      guardarClave("srv_bak", srvBase);
      srvBase = v;
      guardarClave("srv", srvBase);
      srvRevertEn = millis() + 360000UL;
      Serial.printf("Servidor remoto: %s — probando...\n", srvBase.c_str());
    }
  }
  else if (clave == "pump_photos") {
    int v = valor.toInt();
    if (v >= 1 && v != pumpPhotos) {
      pumpPhotos = v; guardarClave("pump_photos", String(v));
      Serial.printf("Fotos para bomba: %d\n", pumpPhotos);
    }
  }
  else if (clave == "pump_seconds") {
    unsigned long v = valor.toInt();
    if (v >= 1 && v != pumpSeconds) {
      pumpSeconds = v; guardarClave("pump_seconds", String(v));
      Serial.printf("Bomba segundos: %lu\n", pumpSeconds);
    }
  }
  else if (clave == "dist_min_cm") {
    float v = valor.toFloat();
    if (v != distMinCm) {
      distMinCm = v; guardarClave("dist_min_cm", String(v));
      Serial.printf("Dist min: %.1f cm\n", distMinCm);
    }
  }
  else if (clave == "dist_max_cm") {
    float v = valor.toFloat();
    if (v > 0 && v != distMaxCm) {
      distMaxCm = v; guardarClave("dist_max_cm", String(v));
      Serial.printf("Dist max: %.1f cm\n", distMaxCm);
    }
  }
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

  HTTPClient http;
  String url = srvBase + "/api/device/config";
  http.begin(url);
  http.setTimeout(5000);
  int codigo = http.GET();
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
    String body = http.getString();
    if (body.length() > 0) aplicarConfigRemota(body);
  } else if (codigo > 0) {
    Serial.printf("Config remota: HTTP %d\n", codigo);
  }
  http.end();
}

void revisarReversionSrv() {
  // Si la URL nueva lleva >6 min sin un GET exitoso, volver a la anterior.
  if (srvRevertEn == 0) return;
  if (millis() < srvRevertEn) return;
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
  if (t == 0) return -1;            // sin eco: se trata como "nada cerca"
  return t * 0.0343f / 2.0f;
}

// ============================================================
// Cámara
// ============================================================
camera_config_t configCamara(int frameSize) {
  camera_config_t config;
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

  switch (frameSize) {
    case 0:   // VGA + PSRAM (solo si hay PSRAM funcional)
      config.frame_size = FRAMESIZE_VGA;
      config.jpeg_quality = 12;
      config.fb_count = 2;
      config.fb_location = CAMERA_FB_IN_PSRAM;
      break;
    case 1:   // QVGA + DRAM (~150 KB DMA: puede no alcanzar)
      config.frame_size = FRAMESIZE_QVGA;
      config.jpeg_quality = 15;
      config.fb_count = 1;
      config.fb_location = CAMERA_FB_IN_DRAM;
      break;
    default:  // QQVGA/96x96 + DRAM (fallbacks según RAM disponible)
      config.frame_size = (frameSize == 2) ? FRAMESIZE_QQVGA : FRAMESIZE_96X96;
      config.jpeg_quality = 15;
      config.fb_count = 1;
      config.fb_location = CAMERA_FB_IN_DRAM;
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
  // Cadena de fallback: VGA+PSRAM -> QVGA+DRAM -> QQVGA+DRAM.
  // Placas ESP32-CAM sin PSRAM funcional solo arrancan en QQVGA.
  for (int intento = 0; intento < 4; intento++) {
    if (intento == 0 && !psramFound()) continue;   // sin PSRAM: saltar VGA
    camera_config_t cfg = configCamara(intento);
    esp_err_t err = esp_camera_init(&cfg);
    if (err == ESP_OK) {
      Serial.printf("Camara OK: frame_size=%d loc=%s\n",
                    cfg.frame_size, intento == 0 ? "PSRAM" : "DRAM");
      return;
    }
    Serial.printf("Camara intento %d fallo (0x%x)\n", intento, err);
  }
  Serial.println("Error iniciando la cámara: sin configuración viable");
  while (true) delay(1000);
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
  if (wifiRevertEn != 0 && millis() > wifiRevertEn) {
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
  WiFi.begin(wifiSsid.c_str(), wifiPass.c_str());
  unsigned long inicio = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - inicio < 10000) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    wifiFallos = 0;
    Serial.print("Conectado. IP del ESP32-CAM: ");
    Serial.println(WiFi.localIP());
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
  colaCantidad++;
  Serial.printf("Foto guardada en memoria (%u bytes). Pendientes: %d\n", (unsigned)len, colaCantidad);
  return true;
}

bool enviarJPG(const uint8_t* datos, size_t tam) {
  if (!asegurarWiFi()) return false;
  if (srvBase.length() == 0) return false;

  HTTPClient http;
  String url = srvBase + "/upload";
  http.begin(url);
  http.setTimeout(8000);
  http.addHeader("Content-Type", "image/jpeg");
  http.addHeader("X-Evento", "ingreso");

  int codigo = http.POST((uint8_t*)datos, tam);
  bool ok = (codigo >= 200 && codigo < 300);
  if (codigo > 0) {
    Serial.printf("Foto enviada (%u bytes) -> HTTP %d\n", (unsigned)tam, codigo);
  } else {
    Serial.printf("Error enviando: %s\n", http.errorToString(codigo).c_str());
  }
  http.end();
  return ok;
}

void colaEnviar() {
  if (colaCantidad == 0) return;
  if (huboFalloEnvio && millis() - ultimoFalloEnvio < REINTENTO_ENVIO_MS) return;

  while (colaCantidad > 0) {
    Foto& f = cola[colaInicio];
    if (!enviarJPG(f.datos, f.tam)) {
      huboFalloEnvio = true;
      ultimoFalloEnvio = millis();
      Serial.printf("Se reintentará en %lu s. Pendientes: %d\n",
                    REINTENTO_ENVIO_MS / 1000, colaCantidad);
      return;
    }
    free(f.datos);                         // enviada: se borra de la memoria
    f.datos = nullptr;
    colaInicio = (colaInicio + 1) % MAX_COLA;
    colaCantidad--;
  }
  huboFalloEnvio = false;
}

// ============================================================
// Foto con flash corto
// ============================================================
bool tomarFoto() {
  digitalWrite(PIN_FLASH, HIGH);           // flash ON
  delay(FLASH_PREVIO_MS);                  // la cámara ajusta la exposición

  camera_fb_t* viejo = esp_camera_fb_get();   // descarta un frame anterior al flash
  if (viejo) esp_camera_fb_return(viejo);

  camera_fb_t* fb = esp_camera_fb_get();   // la foto real, con el flash encendido
  digitalWrite(PIN_FLASH, LOW);            // flash OFF apenas se capturó

  if (!fb) {
    Serial.println("Error: no se pudo capturar la imagen");
    return false;
  }

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
    Serial.println(F("  REBOOT"));
  }
  else if (op == "GET") {
    Serial.printf("wifi_ssid=%s\nwifi_pass=%s\nserver_url=%s\n"
                  "pump_photos=%d\npump_seconds=%d\ndist_min_cm=%.1f\ndist_max_cm=%.1f\n",
                  wifiSsid.c_str(), wifiPass.c_str(), srvBase.c_str(),
                  pumpPhotos, pumpSeconds, distMinCm, distMaxCm);
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
    aplicarClaveRemota(clave, valor);
    Serial.printf("SET %s aplicado (GET para ver estado)\n", clave.c_str());
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

void loop() {
  unsigned long ahora = millis();

  revisarPortal();
  revisarReversionSrv();
  procesarSerial();

  // Sin credenciales guardadas: mantener el AP de configuración disponible
  // (el portal se cierra a los 5 min; reabrirlo periódicamente).
  if (wifiSsid.length() == 0 && WiFi.status() != WL_CONNECTED && !portalActivo) {
    static unsigned long proxPortal = 0;
    if (millis() >= proxPortal) {
      proxPortal = millis() + 30000;      // reintentar cada 30 s
      iniciarPortal();
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
  bool enRango = (d >= distMinCm && d <= distMaxCm);

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
