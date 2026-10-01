#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

/**
 * AquaPulse - ESP32 Firmware for Groundwater Usage Metering & Quota Cutoff
 * 
 * Hardware:
 * - ESP32 NodeMCU / DevKit V1
 * - YF-S201 Flow Sensor (Interrupt on GPIO 18)
 * - 5V Relay Module (Control on GPIO 23)
 * 
 * Libraries Required (Install via Arduino Library Manager):
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

// Calibration Factor for YF-S201: 7.5 pulses/sec = 1 L/min (~450 pulses per Litre)
const float CALIBRATION_FACTOR = 7.5; 

// ================= RUNTIME STATE ======================
volatile int pulseCount = 0;
float totalLitres = 0.0;
unsigned long lastPublishTime = 0;
bool relayState = true; // true = ON, false = OFF

WiFiClientSecure tlsClient;
PubSubClient mqttClient(tlsClient);

// Interrupt Service Routine for Flow Sensor Pulse Counter
void IRAM_ATTR pulseCounter() {
  pulseCount++;
}

// MQTT Message Callback (Relay Control from Cloud)
void callback(char* topic, byte* payload, unsigned int length) {
  String message = "";
  for (unsigned int i = 0; i < length; i++) {
    message += (char)payload[i];
  }
  Serial.print("[MQTT] Received command on ");
  Serial.print(topic);
  Serial.print(": ");
  Serial.println(message);

  StaticJsonDocument<200> doc;
  DeserializationError error = deserializeJson(doc, message);
  if (!error && doc.containsKey("relay")) {
    const char* cmd = doc["relay"];
    if (String(cmd) == "ON") {
      relayState = true;
      digitalWrite(RELAY_PIN, HIGH); // Valve Open
      Serial.println(">> Motor/Valve status: OPEN (ON)");
    } else if (String(cmd) == "OFF") {
      relayState = false;
      digitalWrite(RELAY_PIN, LOW);  // Valve Shut
      Serial.println(">> Motor/Valve status: SHUT (OFF)");
    }
  }
}

void connectMQTT() {
  while (!mqttClient.connected()) {
    Serial.print("[MQTT] Connecting to EMQX Cloud...");
    String clientId = "ESP32-" + String(DEVICE_ID);
    String statusTopic = "gw/" + String(DEVICE_ID) + "/status";

    // Last Will & Testament (LWT) for automatic offline status detection
    if (mqttClient.connect(clientId.c_str(), MQTT_USER, MQTT_PASS, statusTopic.c_str(), 1, true, "offline")) {
      Serial.println(" Connected!");
      
      // Publish online status
      mqttClient.publish(statusTopic.c_str(), "online", true);

      // Subscribe to relay commands
      String cmdTopic = "gw/" + String(DEVICE_ID) + "/cmd";
      mqttClient.subscribe(cmdTopic.c_str());
    } else {
      Serial.print(" Failed, rc=");
      Serial.print(mqttClient.state());
      Serial.println(" retrying in 3 seconds...");
      delay(3000);
    }
  }
}

void setup() {
  Serial.begin(115200);

  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, HIGH); // Default Open

  pinMode(FLOW_SENSOR_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(FLOW_SENSOR_PIN), pulseCounter, FALLING);

  // Connect to Wi-Fi
  Serial.print("Connecting to Wi-Fi");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWi-Fi connected! IP: " + WiFi.localIP().toString());

  // Configure Secure TLS MQTT Client
  tlsClient.setInsecure(); // Skips certificate chain verification for quick onboarding
  mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
  mqttClient.setCallback(callback);
}

void loop() {
  if (!mqttClient.connected()) {
    connectMQTT();
  }
  mqttClient.loop();

  unsigned long now = millis();
  // Telemetry Transmission Cycle (every 5 seconds)
  if (now - lastPublishTime >= 5000) {
    detachInterrupt(digitalPinToInterrupt(FLOW_SENSOR_PIN));
    
    // Calculate Flow Rate in L/min
    float flowRateLpm = ((1000.0 / (now - lastPublishTime)) * pulseCount) / CALIBRATION_FACTOR;
    
    // Calculate Litres in this 5-second interval
    float deltaLitres = (flowRateLpm / 60.0) * ((now - lastPublishTime) / 1000.0);
    totalLitres += deltaLitres;
    
    pulseCount = 0;
    lastPublishTime = now;
    attachInterrupt(digitalPinToInterrupt(FLOW_SENSOR_PIN), pulseCounter, FALLING);

    // Build JSON payload matching project contract
    StaticJsonDocument<256> doc;
    doc["device"]   = DEVICE_ID;
    doc["flow_lpm"] = serialized(String(flowRateLpm, 2));
    doc["litres"]   = serialized(String(deltaLitres, 2));
    doc["total"]    = serialized(String(totalLitres, 2));

    char buffer[256];
    serializeJson(doc, buffer);

    String topic = "gw/" + String(DEVICE_ID) + "/usage";
    mqttClient.publish(topic.c_str(), buffer);
    
    Serial.print("[Published Telemetry]: ");
    Serial.println(buffer);
  }
}
