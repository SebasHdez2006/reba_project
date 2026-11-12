// ============================================================
// REBA — Nodo ESP32-CAM (modo push: fotos por evento)
// Placa en Arduino IDE: "AI Thinker ESP32-CAM"
//
// Funcionamiento:
//  1) El HC-SR04 vigila un rango de 0 a 23 cm. Cuando un objeto entra
//     al rango cuenta un "ingreso" (el objeto debe retirarse antes de
//     contar otro: ver LECTURAS_PARA_CONTAR / LECTURAS_PARA_REARMAR).
//  2) En cada ingreso: enciende el flash un instante, toma una foto JPG
//     y apaga el flash.
//  3) La foto se guarda TEMPORALMENTE en la memoria del ESP32-CAM
//     (cola en PSRAM, máx. MAX_COLA) y se envía por HTTP POST al
//     servidor FastAPI de la PC (SERVIDOR_URL, endpoint /upload).
//     Si no hay WiFi o el servidor no responde 2xx, la foto queda en
//     la cola y se reintenta después. Al enviarse con éxito, se borra.
//  4) Al acumular FOTOS_PARA_BOMBA fotos tomadas, la bomba trabaja
//     TIEMPO_BOMBA_MS milisegundos y se reinicia el conteo.
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
#include <HTTPClient.h>

// ---------------- Red y servidor ----------------
// WIFI_* : red local compartida con la PC (solo 2.4 GHz).
// SERVIDOR_URL: IP de la PC donde corre el servidor FastAPI + puerto
//               (PORT en .env) + ruta /upload. Cambiarla requiere reflashear.
const char* WIFI_SSID    = "TU_RED_WIFI";
const char* WIFI_PASS    = "TU_CONTRASENA";
const char* SERVIDOR_URL = "http://192.168.1.50:5000/upload";

// ---------------- Pines ----------------
#define PIN_RELE   13
#define PIN_TRIG   14
#define PIN_ECHO   15
#define PIN_FLASH   4     // LED blanco (flash)

#define RELE_ACTIVO_BAJO  true   // módulo típico: LOW = relé activo
#define BOMBA_EN_NC       true   // bomba en NC (false si la pasas a NO)

// ---------------- Detección ----------------
const float         DIST_MIN_CM           = 0.0;
const float         DIST_MAX_CM           = 23.0;   // rango de detección
const unsigned long PERIODO_SENSOR_MS     = 60;
const int           LECTURAS_PARA_CONTAR  = 2;      // lecturas seguidas dentro del rango
const int           LECTURAS_PARA_REARMAR = 3;      // lecturas seguidas fuera del rango

// ---------------- Foto y flash ----------------
const unsigned long FLASH_PREVIO_MS = 150;   // flash encendido antes de capturar
// El flash queda encendido solo durante la captura (~0.3-0.4 s en total)

// ---------------- Bomba ----------------
const int           FOTOS_PARA_BOMBA = 3;
const unsigned long TIEMPO_BOMBA_MS  = 10000;

// ---------------- Memoria temporal de fotos ----------------
const int           MAX_COLA          = 6;       // fotos pendientes en memoria
const unsigned long REINTENTO_ENVIO_MS = 15000;  // espera tras un envío fallido
const unsigned long REINTENTO_WIFI_MS  = 30000;  // espera entre reconexiones

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

// ---------------- Estado ----------------
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
void iniciarCamara() {
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

  if (psramFound()) {
    config.frame_size = FRAMESIZE_VGA;    // 640x480
    config.jpeg_quality = 12;             // menor número = más calidad
    config.fb_count = 2;
    config.fb_location = CAMERA_FB_IN_PSRAM;
  } else {
    config.frame_size = FRAMESIZE_QVGA;
    config.jpeg_quality = 15;
    config.fb_count = 1;
    config.fb_location = CAMERA_FB_IN_DRAM;
  }

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Error iniciando la cámara: 0x%x\n", err);
    while (true) delay(1000);
  }
}

// ============================================================
// WiFi
// ============================================================
bool asegurarWiFi() {
  if (WiFi.status() == WL_CONNECTED) return true;

  // No bloquear el programa intentando reconectar a cada rato
  if (ultimoIntentoWiFi != 0 && millis() - ultimoIntentoWiFi < REINTENTO_WIFI_MS) {
    return false;
  }
  ultimoIntentoWiFi = millis();

  Serial.print("Conectando a WiFi");
  WiFi.disconnect();
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  unsigned long inicio = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - inicio < 10000) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("Conectado. IP del ESP32-CAM: ");
    Serial.println(WiFi.localIP());
    return true;
  }
  Serial.println("Sin WiFi: las fotos quedan en la memoria temporal");
  return false;
}

// ============================================================
// Memoria temporal de fotos (cola en PSRAM)
// ============================================================
bool colaAgregar(const uint8_t* buf, size_t len) {
  uint8_t* copia = (uint8_t*)(psramFound() ? ps_malloc(len) : malloc(len));
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

  HTTPClient http;
  http.begin(SERVIDOR_URL);
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
  Serial.printf("Bomba ENCENDIDA %lu s\n", TIEMPO_BOMBA_MS / 1000);
  bombaEncender();
  delay(TIEMPO_BOMBA_MS);
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
    Serial.printf("Fotos tomadas: %d de %d\n", fotosTomadas, FOTOS_PARA_BOMBA);
  }

  colaEnviar();                            // intenta enviar lo que haya en memoria

  if (fotosTomadas >= FOTOS_PARA_BOMBA) {
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

  iniciarCamara();
  asegurarWiFi();

  // Dos destellos cortos: "listo"
  for (int i = 0; i < 2; i++) {
    digitalWrite(PIN_FLASH, HIGH); delay(100);
    digitalWrite(PIN_FLASH, LOW);  delay(150);
  }
  Serial.println("REBA listo: vigilando 0-23 cm");
}

void loop() {
  unsigned long ahora = millis();
  if (ahora - ultimaLectura < PERIODO_SENSOR_MS) return;
  ultimaLectura = ahora;

  float d = leerCm();
  bool enRango = (d >= DIST_MIN_CM && d <= DIST_MAX_CM);

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
