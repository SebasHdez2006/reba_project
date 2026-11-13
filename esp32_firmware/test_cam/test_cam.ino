// Test mínimo: solo cámara, sin WiFi ni WiFiManager.
// Aísla si esp_camera_init funciona en esta placa.
#include "esp_camera.h"
#include <WiFi.h>
#include <WiFiManager.h>
#include <HTTPClient.h>
#include <Preferences.h>

// Réplica de los globales del sketch completo para medir su costo de heap
WiFiManager*          wmP   = nullptr;
WiFiManagerParameter* pSrvP = nullptr;
Preferences            prefs;
String                wifiSsid, wifiPass, srvBase;
String                pendWifiSsid, pendWifiPass;

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

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.printf("heap=%u dma_max=%u psram=%d\n",
    (unsigned)ESP.getFreeHeap(),
    (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL),
    (int)psramFound());

  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM; config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM; config.pin_href = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM; config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM; config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.grab_mode = CAMERA_GRAB_LATEST;
  config.frame_size = FRAMESIZE_QVGA;
  config.jpeg_quality = 15;
  config.fb_count = 1;
  config.fb_location = CAMERA_FB_IN_DRAM;

  esp_err_t err = esp_camera_init(&config);
  Serial.printf("esp_camera_init QVGA/DRAM -> 0x%x\n", err);

  if (err == ESP_OK) {
    camera_fb_t* fb = esp_camera_fb_get();
    if (fb) {
      Serial.printf("fb OK: %u bytes %dx%d\n", (unsigned)fb->len, fb->width, fb->height);
      esp_camera_fb_return(fb);
    } else {
      Serial.println("fb_get NULL");
    }
  }
  Serial.println("FIN DEL TEST");
  while (true) delay(1000);
}

void loop() {}
