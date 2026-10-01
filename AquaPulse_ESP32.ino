#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

/**
 * AquaPulse - ESP32 Firmware for Groundwater Usage Metering & Remote Quota Cutoff
 * 
 * Hardware:
 * - ESP32 NodeMCU / DevKit V1
 * - YF-S201 Hall-Effect Flow Sensor (Interrupt on GPIO 18)
 * - 5V / 3.3V Relay Module (Control on GPIO 23)
 * 
 * Required Libraries (Install via Arduino IDE Library Manager):
 * 1. PubSubClient by Nick O'Leary
 * 2. ArduinoJson by Benoit Blanchon (v6 or v7)
 */

// ================= USER CONFIGURATION =================
const char* WIFI_SSID     = "YOUR_WIFI_NAME";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

// EMQX Cloud Serverless Broker Details (from your backend .env)
const char* MQTT_BROKER   = "your-emqx-host.emqxsl.cn";
const int   MQTT_PORT     = 8883;                       // TLS port
const char* MQTT_USER     = "your_mqtt_username";
const char* MQTT_PASS     = "your_mqtt_password";
const char* DEVICE_ID     = "device1";

// Pin Assignments
#define FLOW_SENSOR_PIN   18
#define RELAY_PIN         23

// Relay Module Polarity:
// Most 5V/3.3V Arduino relay modules are Active-LOW (LOW = Relay Energized / Valve Open)
// Set to false if your relay module is Active-HIGH (HIGH = Relay Energized)
const bool RELAY_ACTIVE_LOW = true;

// Calibration Factor for YF-S201:
// 7.5 pulses/sec = 1 L/min (~450 pulses per 1 Litre of water)
const float CALIBRATION_FACTOR = 7.5; 

// ================= RUNTIME STATE ======================
volatile unsigned long pulseCount = 0;
volatile unsigned long lastPulseTime = 0;
float totalLitres = 0.0;
unsigned long lastPublishTime = 0;
bool relayState = true; // true = ON (Valve Open), false = OFF (Valve Shut)

WiFiClientSecure tlsClient;
PubSubClient mqttClient(tlsClient);

// Helper function to actuate the physical relay pin according to module polarity
void applyRelayHardware(bool turnOn) {
  relayState = turnOn;
  if (RELAY_ACTIVE_LOW) {
    digitalWrite(RELAY_PIN, turnOn ? LOW : HIGH);
  } else {
    digitalWrite(RELAY_PIN, turnOn ? HIGH : LOW);
  }
}

// Interrupt Service Routine for Flow Sensor Pulse Counter with 2ms hardware debounce
void IRAM_ATTR pulseCounter() {
  unsigned long nowMicro = micros();
  if (nowMicro - lastPulseTime > 2000) { // 2ms debounce
    pulseCount++;
    lastPulseTime = nowMicro;
  }
}

// MQTT Message Callback (Relay Control & Quota Reset from Cloud Backend)
void callback(char* topic, byte* payload, unsigned int length) {
  String message = "";
  for (unsigned int i = 0; i < length; i++) {
    message += (char)payload[i];
  }
  Serial.print("[MQTT] Received command on ");
  Serial.print(topic);
  Serial.print(": ");
  Serial.println(message);

  StaticJsonDocument<256> doc;
  DeserializationError error = deserializeJson(doc, message);
  if (!error && doc.containsKey("relay")) {
    const char* cmd = doc["relay"];
    if (String(cmd) == "ON") {
      // If turning ON from an OFF state (e.g. bill paid or admin reset), reset local cycle usage counter
      if (!relayState || doc.containsKey("reset")) {
        totalLitres = 0.0;
        Serial.println(">> Quota cycle reset: totalLitres reset to 0.0 L");
      }
      applyRelayHardware(true);
      Serial.println(">> Motor/Valve status: OPEN (ON)");
    } else if (String(cmd) == "OFF") {
      applyRelayHardware(false);
      Serial.println(">> Motor/Valve status: SHUT (OFF - Quota Cutoff)");
    }
  }
}

void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  
  Serial.print("[Wi-Fi] Connecting to ");
  Serial.print(WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  
  unsigned long startAttemptTime = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - startAttemptTime < 15000) {
    delay(500);
    Serial.print(".");
  }
  
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n[Wi-Fi] Connected! IP: " + WiFi.localIP().toString());
  } else {
    Serial.println("\n[Wi-Fi] Connection timed out, will retry...");
  }
}

void connectMQTT() {
  while (WiFi.status() == WL_CONNECTED && !mqttClient.connected()) {
    Serial.print("[MQTT] Connecting to EMQX Broker...");
    String clientId = "AquaPulse-ESP32-" + String(DEVICE_ID) + "-" + String(random(1000, 9999));
    String statusTopic = "gw/" + String(DEVICE_ID) + "/status";

    // Last Will & Testament (LWT) for automatic offline status detection
    if (mqttClient.connect(clientId.c_str(), MQTT_USER, MQTT_PASS, statusTopic.c_str(), 1, true, "offline")) {
      Serial.println(" Connected successfully!");
      
      // Publish retained online status
      mqttClient.publish(statusTopic.c_str(), "online", true);

      // Subscribe to remote relay control commands
      String cmdTopic = "gw/" + String(DEVICE_ID) + "/cmd";
      mqttClient.subscribe(cmdTopic.c_str());
      Serial.println("[MQTT] Subscribed to " + cmdTopic);
    } else {
      Serial.print(" Failed, rc=");
      Serial.print(mqttClient.state());
      Serial.println(" Retrying in 3 seconds...");
      delay(3000);
    }
  }
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n==========================================");
  Serial.println("  AquaPulse ESP32 Smart Water Meter Init  ");
  Serial.println("==========================================");

  // Initialize Relay Pin
  pinMode(RELAY_PIN, OUTPUT);
  applyRelayHardware(true); // Default Valve OPEN on boot

  // Initialize Flow Sensor Pin with internal pull-up and interrupt
  pinMode(FLOW_SENSOR_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(FLOW_SENSOR_PIN), pulseCounter, FALLING);

  // Connect to Wi-Fi network
  connectWiFi();

  // Configure Secure TLS MQTT Client
  tlsClient.setInsecure(); // Skips manual CA certificate verification for fast deployment
  mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
  mqttClient.setCallback(callback);
  mqttClient.setBufferSize(512);
}

void loop() {
  // Ensure Wi-Fi connection is healthy
  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();
  }

  // Ensure MQTT connection is healthy
  if (WiFi.status() == WL_CONNECTED && !mqttClient.connected()) {
    connectMQTT();
  }
  mqttClient.loop();

  unsigned long now = millis();
  // Telemetry Transmission Cycle (every 5 seconds)
  if (now - lastPublishTime >= 5000) {
    noInterrupts();
    unsigned long currentPulses = pulseCount;
    pulseCount = 0;
    interrupts();

    unsigned long elapsedMs = now - lastPublishTime;
    lastPublishTime = now;

    // Calculate Flow Rate in L/min
    float flowRateLpm = 0.0;
    if (elapsedMs > 0 && currentPulses > 0) {
      flowRateLpm = ((1000.0 / (float)elapsedMs) * (float)currentPulses) / CALIBRATION_FACTOR;
    }

    // Calculate Litres in this 5-second sampling interval
    float deltaLitres = (flowRateLpm / 60.0) * ((float)elapsedMs / 1000.0);
    totalLitres += deltaLitres;

    // Build JSON payload strictly matching backend parser requirements
    StaticJsonDocument<256> doc;
    doc["device"]   = DEVICE_ID;
    doc["flow_lpm"] = round(flowRateLpm * 100.0) / 100.0;
    doc["litres"]   = round(deltaLitres * 1000.0) / 1000.0;
    doc["total"]    = round(totalLitres * 1000.0) / 1000.0;

    char buffer[256];
    serializeJson(doc, buffer);

    String topic = "gw/" + String(DEVICE_ID) + "/usage";
    bool published = mqttClient.publish(topic.c_str(), buffer);

    Serial.print("[Telemetry Published] ");
    Serial.print(topic);
    Serial.print(" -> ");
    Serial.print(buffer);
    Serial.println(published ? " (OK)" : " (FAILED)");
  }
}
