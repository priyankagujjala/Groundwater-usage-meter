# AquaPulse — Hardware Connection & ESP32 Firmware Guide

## 1. Overview
AquaPulse connects IoT flow sensors and motor relay valves to a cloud telemetry & pay-on-excess quota system. When water consumption crosses the monthly quota limit, the motor valve is automatically shut off via MQTT until excess usage is settled via Razorpay checkout.

---

## 2. Hardware Bill of Materials (BOM)

| Component | Specification | Purpose |
| :--- | :--- | :--- |
| **Microcontroller** | ESP32 NodeMCU / DevKit V1 (ESP-WROOM-32) | Wi-Fi connectivity, MQTT telemetry, relay control |
| **Water Flow Sensor** | YF-S201 (Hall-effect, 1/2" pipe) | Measures pulse counts & flow velocity (L/min) |
| **Relay Module** | 5V 1-Channel Relay (Optocoupler isolated) | Physically cuts or restores power to the motor/pump |
| **Water Pump / Motor** | 12V DC Submersible Pump or Solenoid Valve | Simulates or acts as the groundwater extraction pump |
| **Power Supply** | 12V DC Power Adapter + 5V Micro-USB | Powers the pump motor and the ESP32 board |

---

## 3. Circuit Wiring & Pin Mapping

```
  +-------------------------------------------------------------+
  |                        ESP32 DevKit                         |
  |                                                             |
  |   [ GPIO 18 ] <-------- Yellow (Signal) --- [ Flow Sensor ] |
  |   [ 5V / VIN] --------> Red    (VCC)        | (YF-S201)     |
  |   [ GND     ] --------> Black  (GND)        +---------------+
  |                                                             |
  |   [ GPIO 23 ] --------> IN (Signal) ------- [ 5V Relay ]    |
  |   [ 5V / VIN] --------> VCC                 | Module        |
  |   [ GND     ] --------> GND                 +-------+-------+
  +-----------------------------------------------------+-------+
                                                        | COM & NO
                                              [ 12V Motor / Pump ]
                                                        |
                                                [ 12V Power Supply ]
```

### Pin Assignment Table:
- **YF-S201 Flow Sensor**:
  - `Red (VCC)` ➔ `VIN / 5V` (or 3.3V)
  - `Black (GND)` ➔ `GND`
  - `Yellow (Signal)` ➔ `GPIO 18` (Hardware Interrupt)
- **5V Relay Module**:
  - `VCC` ➔ `VIN / 5V`
  - `GND` ➔ `GND`
  - `IN (Trigger)` ➔ `GPIO 23`
- **12V Water Pump / Motor**:
  - Positive 12V line is routed through the Relay's **COM** (Common) and **NO** (Normally Open) contacts.

---

## 4. MQTT Communication Contract

| Topic | Direction | Payload Example | Description |
| :--- | :--- | :--- | :--- |
| `gw/device1/usage` | ESP32 ➔ Cloud | `{"device":"device1","flow_lpm":2.4,"litres":0.2,"total":132.5}` | Published every 5s |
| `gw/device1/cmd` | Cloud ➔ ESP32 | `{"relay":"ON"}` or `{"relay":"OFF"}` | Motor valve trigger |
| `gw/device1/status` | ESP32 ➔ Cloud | `online` / `offline` | MQTT Last Will & Testament |

---

## 5. ESP32 Arduino Firmware (`AquaPulse_ESP32.ino`)

```cpp
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

// ================= USER CONFIGURATION =================
const char* WIFI_SSID     = "YOUR_WIFI_NAME";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

// EMQX Cloud Serverless Broker Details (from your .env)
const char* MQTT_BROKER   = "your-emqx-host.emqxsl.cn";
const int   MQTT_PORT     = 8883;                       // TLS port
const char* MQTT_USER     = "your_mqtt_username";
const char* MQTT_PASS     = "your_mqtt_password";
const char* DEVICE_ID     = "device1";

// Hardware Pin Definitions
#define FLOW_SENSOR_PIN   18
#define RELAY_PIN         23

// Calibration Factor for YF-S201: 7.5 pulses/sec = 1 L/min (~450 pulses/Litre)
const float CALIBRATION_FACTOR = 7.5; 

// ================= RUNTIME STATE ======================
volatile int pulseCount = 0;
float totalLitres = 0.0;
unsigned long lastPublishTime = 0;
bool relayState = true;

WiFiClientSecure tlsClient;
PubSubClient mqttClient(tlsClient);

// Interrupt Service Routine (ISR) for Flow Sensor
void IRAM_ATTR pulseCounter() {
  pulseCount++;
}

// MQTT Callback: Handles incoming relay commands from Flask Cloud Backend
void callback(char* topic, byte* payload, unsigned int length) {
  String message = "";
  for (int i = 0; i < length; i++) {
    message += (char)payload[i];
  }
  Serial.print("[MQTT Command]: ");
  Serial.println(message);

  StaticJsonDocument<200> doc;
  DeserializationError error = deserializeJson(doc, message);
  if (!error && doc.containsKey("relay")) {
    const char* cmd = doc["relay"];
    if (String(cmd) == "ON") {
      relayState = true;
      digitalWrite(RELAY_PIN, HIGH); // Valve Open
      Serial.println(">> Motor Valve: OPEN (ON)");
    } else if (String(cmd) == "OFF") {
      relayState = false;
      digitalWrite(RELAY_PIN, LOW);  // Valve Shut
      Serial.println(">> Motor Valve: SHUT (OFF)");
    }
  }
}

void connectMQTT() {
  while (!mqttClient.connected()) {
    Serial.print("[MQTT] Connecting to EMQX Cloud...");
    String clientId = "ESP32-" + String(DEVICE_ID);
    String statusTopic = "gw/" + String(DEVICE_ID) + "/status";

    if (mqttClient.connect(clientId.c_str(), MQTT_USER, MQTT_PASS, statusTopic.c_str(), 1, true, "offline")) {
      Serial.println(" Connected!");
      mqttClient.publish(statusTopic.c_str(), "online", true);
      String cmdTopic = "gw/" + String(DEVICE_ID) + "/cmd";
      mqttClient.subscribe(cmdTopic.c_str());
    } else {
      Serial.print(" Failed, rc=");
      Serial.print(mqttClient.state());
      Serial.println(" retrying in 3s...");
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

  // Connect to Local Wi-Fi
  Serial.print("Connecting to Wi-Fi");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWi-Fi Connected! IP: " + WiFi.localIP().toString());

  // Setup Secure MQTT TLS Client
  tlsClient.setInsecure(); // Skips certificate chain validation
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
    
    // Compute flow rate in Litres/minute
    float flowRateLpm = ((1000.0 / (now - lastPublishTime)) * pulseCount) / CALIBRATION_FACTOR;
    
    // Compute volume increment in Litres
    float deltaLitres = (flowRateLpm / 60.0) * ((now - lastPublishTime) / 1000.0);
    totalLitres += deltaLitres;
    
    pulseCount = 0;
    lastPublishTime = now;
    attachInterrupt(digitalPinToInterrupt(FLOW_SENSOR_PIN), pulseCounter, FALLING);

    // Formulate JSON telemetry
    StaticJsonDocument<256> doc;
    doc["device"]   = DEVICE_ID;
    doc["flow_lpm"] = serialized(String(flowRateLpm, 2));
    doc["litres"]   = serialized(String(deltaLitres, 2));
    doc["total"]    = serialized(String(totalLitres, 2));

    char buffer[256];
    serializeJson(doc, buffer);

    String topic = "gw/" + String(DEVICE_ID) + "/usage";
    mqttClient.publish(topic.c_str(), buffer);
    Serial.print("[Published]: ");
    Serial.println(buffer);
  }
}
```

---

## 6. End-to-End Operational Lifecycle

1. **Extraction**: Flow sensor detects water movement; ESP32 samples pulse interrupts.
2. **Telemetry Ingestion**: Every 5s, telemetry is sent to EMQX Cloud and processed by Flask on Render.
3. **Dashboard Visualization**: Netlify web app reflects real-time flow rate, consumption gauge, and history charts.
4. **Quota Breach & Cutoff**: When quota (e.g. 500L) is reached, Flask generates a PostgreSQL invoice and publishes `{"relay":"OFF"}` to `gw/device1/cmd`. The ESP32 immediately turns the relay OFF.
5. **Settlement**: The user pays the bill via Razorpay on the web portal. Upon signature validation, Flask sends `{"relay":"ON"}` to reopen the valve.
