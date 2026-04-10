/*
  ============================================================
  Project : Time-Series Forecasting for Urban Air Quality
  Hardware: Arduino Uno R4 WiFi + MQ135 + DHT22
  Pipeline: Arduino → ThingSpeak → Python Flask → HTML Dashboard
  ============================================================

  WIRING GUIDE
  ─────────────────────────────────────────────────────────────
  MQ135 Gas Sensor:
    VCC  → 5V
    GND  → GND
    AOUT → A0   (analog output)
    DOUT → (not used)

  DHT22 Temperature & Humidity Sensor:
    VCC  → 5V
    GND  → GND
    DATA → D2
    (Place 10kΩ pull-up resistor between DATA and VCC)
  ─────────────────────────────────────────────────────────────

  LIBRARIES REQUIRED  (install via Arduino Library Manager)
    1. WiFiS3          – built-in for UNO R4 WiFi
    2. DHT sensor library by Adafruit
    3. Adafruit Unified Sensor

  THINGSPEAK CHANNEL FIELDS
    Field 1 → Air Quality Index (AQI)
    Field 2 → Raw MQ135 PPM
    Field 3 → Temperature (°C)
    Field 4 → Humidity (%)
    Field 5 → CO2 estimate (PPM)
  ============================================================
*/

#include <WiFiS3.h>
#include <DHT.h>

// ── USER CONFIG ─────────────────────────────────────────────
const char* WIFI_SSID     = "Naveen";   
const char* WIFI_PASSWORD = "naveen11";  

const char* TS_API_KEY    = "BQ7F524DC7MK7HDC";
const char* TS_HOST       = "api.thingspeak.com";
const int   TS_PORT       = 80;

#define DHTPIN  2           // DHT22 data pin
#define DHTTYPE DHT22
#define MQ135PIN A0         // MQ135 analog pin
// ────────────────────────────────────────────────────────────

DHT dht(DHTPIN, DHTTYPE);
WiFiClient client;

// MQ135 calibration constants (adjust after warm-up in clean air)
const float R0          = 76.63;  // sensor resistance in clean air (kΩ)
const float RL          = 10.0;   // load resistance on board (kΩ)
const float PARA        = 116.6020682;
const float PARB        = 2.769034857;

unsigned long lastSend  = 0;
const unsigned long SEND_INTERVAL = 20000; // 20 seconds (ThingSpeak free limit)

// ── SETUP ────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println(F("\n=== Urban Air Quality Monitor ==="));

  dht.begin();

  // Connect to WiFi
  // Connect to WiFi (FIXED VERSION)
WiFi.disconnect();
delay(2000);

Serial.print("Connecting to WiFi: ");
Serial.println(WIFI_SSID);

WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

// HARD RESET LOOP
int retry = 0;
while (WiFi.status() != WL_CONNECTED) {
  delay(1000);
  Serial.print(".");
  retry++;

  if (retry > 20) {
    Serial.println("\n❌ Failed to connect. Restarting WiFi...");
    WiFi.disconnect();
    delay(2000);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    retry = 0;
  }
  Serial.print("WiFi Status: ");
Serial.println(WiFi.status());
}

Serial.println("\n✅ WiFi CONNECTED!");
Serial.print("IP Address: ");
Serial.println(WiFi.localIP());
  Serial.println(F("Warming up MQ135 sensor (30 seconds)..."));
  delay(30000); // MQ135 needs ~30s warm-up
  Serial.println(F("Sensor ready. Starting measurements.\n"));
}

// ── MAIN LOOP ────────────────────────────────────────────────
void loop() {
     // 🔍 STEP 1: Read sensor
  int rawADC = analogRead(A0);

  Serial.print("RAW ADC: ");
  Serial.println(rawADC);
  unsigned long now = millis();

  if (now - lastSend >= SEND_INTERVAL) {
    lastSend = now;

    // ── Read DHT22
    float humidity    = dht.readHumidity();
    float temperature = dht.readTemperature(); // Celsius

    if (isnan(humidity) || isnan(temperature)) {
      Serial.println(F("✗ DHT22 read failed — skipping"));
      return;
    }

    // ── Read MQ135
    int   rawADC   = analogRead(MQ135PIN);
    float voltage  = rawADC * (5.0 / 1023.0);

// 🔥 FIX: Avoid division by zero
    if (voltage == 0) {
    Serial.println("⚠️ Voltage is 0 — skipping reading");
    return;
    }

    float RS = RL * (5.0 - voltage) / voltage; // sensor resistance
    float ratio    = RS / R0;                          // RS/R0 ratio

    // Estimate CO2 PPM using power curve (calibrated for MQ135)
    float co2PPM   = PARA * pow(ratio, -PARB);

    // Clamp to realistic range
    co2PPM = constrain(co2PPM, 400.0, 5000.0);

    // ── Compute simple AQI (0-500 scale based on CO2 level)
    int aqi = map(rawADC, 0, 1023, 0, 500);

    // ── Print to Serial Monitor
    Serial.println(F("─────────────────────────────────"));
    Serial.print(F("Temperature : ")); Serial.print(temperature); Serial.println(F(" °C"));
    Serial.print(F("Humidity    : ")); Serial.print(humidity);    Serial.println(F(" %"));
    Serial.print(F("MQ135 Raw   : ")); Serial.println(rawADC);
    Serial.print(F("CO2 estimate: ")); Serial.print(co2PPM);      Serial.println(F(" PPM"));
    Serial.print(F("AQI         : ")); Serial.println(aqi);
    Serial.println(F("─────────────────────────────────"));

    // ── Send to ThingSpeak
    sendToThingSpeak(aqi, rawADC, temperature, humidity, co2PPM);
  }
}

// ── COMPUTE SIMPLE AQI ───────────────────────────────────────
float computeAQI(float co2) {
  // Maps CO2 PPM to AQI 0-500
  if (co2 <= 400)  return 0;
  if (co2 <= 700)  return map(co2, 400, 700, 0, 50);    // Good
  if (co2 <= 1000) return map(co2, 700, 1000, 50, 100); // Moderate
  if (co2 <= 1500) return map(co2, 1000, 1500, 100, 150); // Unhealthy (sensitive)
  if (co2 <= 2000) return map(co2, 1500, 2000, 150, 200); // Unhealthy
  if (co2 <= 3000) return map(co2, 2000, 3000, 200, 300); // Very Unhealthy
  return constrain(map(co2, 3000, 5000, 300, 500), 300, 500); // Hazardous
}

// ── SEND DATA TO THINGSPEAK ───────────────────────────────────
void sendToThingSpeak(float aqi, int rawPPM, float temp, float hum, float co2) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println(F("✗ WiFi disconnected — skipping send"));
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    return;
  }

  String postData = "api_key=" + String(TS_API_KEY)
                  + "&field1=" + String(aqi, 1)
                  + "&field2=" + String(rawPPM)
                  + "&field3=" + String(temp, 2)
                  + "&field4=" + String(hum, 2)
                  + "&field5=" + String(co2, 1);

  if (client.connect(TS_HOST, TS_PORT)) {
    client.println("POST /update HTTP/1.1");
    client.println("Host: api.thingspeak.com");
    client.println("Connection: close");
    client.println("Content-Type: application/x-www-form-urlencoded");
    client.print("Content-Length: ");
    client.println(postData.length());
    client.println();
    client.print(postData);

    delay(500);
    String response = "";
    while (client.available()) {
      response += (char)client.read();
    }
    client.stop();

    if (response.indexOf("200 OK") >= 0) {
      Serial.println(F("✓ Data sent to ThingSpeak"));
    } else {
      Serial.println(F("✗ ThingSpeak send failed"));
      Serial.println(response.substring(0, 100));
    }
  } else {
    Serial.println(F("✗ Could not connect to ThingSpeak"));
  }
}
