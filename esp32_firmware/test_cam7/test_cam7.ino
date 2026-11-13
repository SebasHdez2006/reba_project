// test_cam6: replica EXACTA del path de cámara de fotos_nc.ino
// (mismos defines, configCamara, iniciarCamara con reintentos fb_get,
//  y el mismo preamble de setup) pero SIN WiFiManager/Preferences ni
//  el resto del firmware. Sirve para bisectar por qué fotos_nc da
//  "init OK pero sin frames" mientras test_cam4 da "fb OK".
#include "esp_camera.h"
#include <WiFi.h>

// ---- defines idénticos a fotos_nc ----
#define PIN_RELE   13
#define PIN_TRIG   14
#define PIN_ECHO   15
#define PIN_FLASH   4

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

bool camaraOk = false;

// ---- configCamara copiada verbatim de fotos_nc ----
camera_config_t configCamara(int frameSize) {
  camera_config_t config = {};   // FIX: zero-init campos no asignados
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
    case 0:
      config.frame_size = FRAMESIZE_QVGA;
      config.jpeg_quality = 15;
      config.fb_count = 1;
      config.fb_location = CAMERA_FB_IN_DRAM;
      break;
    case 1:
      config.frame_size = FRAMESIZE_VGA;
      config.jpeg_quality = 12;
      config.fb_count = 1;
      config.fb_location = CAMERA_FB_IN_DRAM;
      break;
    case 2:
      config.frame_size = FRAMESIZE_QQVGA;
      config.jpeg_quality = 15;
      config.fb_count = 1;
      config.fb_location = CAMERA_FB_IN_DRAM;
      break;
    default:
      config.frame_size = FRAMESIZE_VGA;
      config.jpeg_quality = 12;
      config.fb_count = 1;
      config.fb_location = CAMERA_FB_IN_PSRAM;
      break;
  }
  return config;
}

// ---- iniciarCamara copiada verbatim de fotos_nc ----
void iniciarCamara() {
  Serial.printf("heap total=%u mayor_bloque_dma=%u psram=%d\n",
                (unsigned)ESP.getFreeHeap(),
                (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL),
                (int)psramFound());
  for (int intento = 0; intento < 4; intento++) {
    if (intento == 3 && !psramFound()) break;
    camera_config_t cfg = configCamara(intento);
    esp_err_t err = esp_camera_init(&cfg);
    if (err != ESP_OK) {
      Serial.printf("Camara intento %d fallo (0x%x)\n", intento, err);
      continue;
    }
    delay(300);
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

void setup() {
  // setup MINIMAL como test_cam4: sin pinMode extra, sin 2do Serial.begin,
  // sin setDebugOutput, sin delay(500). Solo cámara.
  Serial.begin(115200);
  Serial.printf("heap_setup=%u\n", (unsigned)ESP.getFreeHeap());

  iniciarCamara();

  if (camaraOk) {
    Serial.println("TEST6: camara OK");
    camera_fb_t* fb = esp_camera_fb_get();
    if (fb) { Serial.printf("fb2 OK: %u bytes\n", (unsigned)fb->len); esp_camera_fb_return(fb); }
    else Serial.println("fb2 NULL");
  } else {
    Serial.println("TEST6: camara FALLA");
  }
  while (true) delay(1000);
}
void loop() {}
