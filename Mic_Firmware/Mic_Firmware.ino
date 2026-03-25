#include <WiFi.h>
#include <WiFiUdp.h>
#include <driver/i2s.h>

// Name of the connection/phone name for hotspoit
const char* ssid = "";
// Password for the connection/hotspot
const char* password = "";
// IP of your laptop/server WHEN connected to the network
const char* laptopIP = ""; 
const int udpPort = 8002;

// GPIO pins on the S3
#define I2S_WS 15
#define I2S_SD 17
#define I2S_SCK 16
#define I2S_PORT I2S_NUM_0

WiFiUDP udp;

void setupI2S() {
  const i2s_config_t i2s_config = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
    .sample_rate = 16000, // Porcupine requires 16kHz
    .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT, // Hardware is 32-bit
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = I2S_COMM_FORMAT_STAND_I2S,
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = 8,
    .dma_buf_len = 256,
    .use_apll = false
  };

  const i2s_pin_config_t pin_config = {
    .bck_io_num = I2S_SCK,
    .ws_io_num = I2S_WS,
    .data_out_num = -1,
    .data_in_num = I2S_SD
  };

  i2s_driver_install(I2S_PORT, &i2s_config, 0, NULL);
  i2s_set_pin(I2S_PORT, &pin_config);
}

void setup() {
  Serial.begin(115200);
  setupI2S();
  
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nConnect Success");
}

void loop() {
  int32_t raw_buffer[128]; // Holds the raw 32-bit data from the mic
  int16_t pcm_buffer[128]; // Holds the clean 16-bit data for Porcupine
  size_t bytes_read;

  i2s_read(I2S_PORT, &raw_buffer, sizeof(raw_buffer), &bytes_read, portMAX_DELAY);

  int samples_read = bytes_read / 4; // 4 bytes per 32-bit sample

  if (samples_read > 0) {
    for (int i = 0; i < samples_read; i++) {
      pcm_buffer[i] = raw_buffer[i] >> 16; 
    }

    udp.beginPacket(laptopIP, udpPort);
    udp.write((const uint8_t*)pcm_buffer, samples_read * 2); // 2 bytes per 16-bit sample
    udp.endPacket();
  }
}