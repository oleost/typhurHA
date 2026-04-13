#include <BLEDevice.h>
#include <BLEUtils.h>
#include <BLEScan.h>
#include <BLEAdvertisedDevice.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>

// WiFi credentials
const char* ssid = "YOUR_WIFI_SSID";
const char* password = "YOUR_WIFI_PASSWORD";

// MQTT settings
const char* mqtt_server = "YOUR_MQTT_SERVER_IP";
const char* mqtt_user = "YOUR_MQTT_USER";
const char* mqtt_password = "YOUR_MQTT_PASSWORD";

// Typhur device UUIDs
#define TYPHUR_NOTIFICATION_UUID "0000ff02-0000-1000-8000-00805f9b34fb"
#define TYPHUR_SERVICE_UUID "00001800-0000-1000-8000-00805f9b34fb"

// Device information
String deviceId = "";
String deviceModel = "WT08";

WiFiClient espClient;
PubSubClient client(espClient);

// BLE scanning variables
int scanTime = 5; // In seconds
BLEScan* pBLEScan;

// Forward declaration
void setup_wifi();
void reconnect_mqtt();
void ble_scan();
void connect_to_typhur_device(BLEAdvertisedDevice* device);
void handle_ble_notification(BLERemoteCharacteristic* pCharacteristic, uint8_t* data, size_t length);
void publish_discovery_data();
void forward_data_to_mqtt(String payload);

void setup() {
  Serial.begin(115200);
  Serial.println("Typhur BLE Bridge starting...");

  setup_wifi();
  client.setServer(mqtt_server, 1883);

  // Initialize BLE
  BLEDevice::init("TyphurBridge");
  pBLEScan = BLEDevice::getScan(); //create new scan
  pBLEScan->setActiveScan(true); //active scan uses more power, but get results faster
  pBLEScan->setInterval(100);
  pBLEScan->setWindow(99);  // less or equal setInterval value
}

void loop() {
  if (!client.connected()) {
    reconnect_mqtt();
  }
  client.loop();

  // Scan for BLE devices
  Serial.println("Scanning for Typhur devices...");
  BLEScanResults foundDevices = pBLEScan->start(scanTime, false);

  if (foundDevices.getCount() > 0) {
    Serial.println("Found " + String(foundDevices.getCount()) + " devices");

    for (int i = 0; i < foundDevices.getCount(); i++) {
      BLEAdvertisedDevice* device = foundDevices.getDevice(i);

      // Check if this is a Typhur device
      if (device->haveName()) {
        String deviceName = device->getName().c_str();
        Serial.println("Device name: " + deviceName);

        // Look for Typhur devices
        if (deviceName.indexOf("Typhur") != -1 || deviceName.indexOf("WT08") != -1) {
          Serial.println("Found Typhur device: " + deviceName);
          connect_to_typhur_device(device);
          break;
        }
      }
    }
  }

  // Wait before next scan
  delay(30000); // Scan every 30 seconds
}

void setup_wifi() {
  delay(10);
  Serial.println();
  Serial.print("Connecting to ");
  Serial.println(ssid);

  WiFi.begin(ssid, password);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println("");
  Serial.println("WiFi connected");
  Serial.println("IP address: ");
  Serial.println(WiFi.localIP());
}

void reconnect_mqtt() {
  while (!client.connected()) {
    Serial.print("Attempting MQTT connection...");

    // Use a random client ID
    String clientId = "TyphurBridge-";
    clientId += String(random(0xffff), HEX);

    // Attempt to connect
    if (client.connect(clientId.c_str(), mqtt_user, mqtt_password)) {
      Serial.println("connected");
      // Publish discovery data
      publish_discovery_data();
    } else {
      Serial.print("failed, rc=");
      Serial.print(client.state());
      Serial.println(" try again in 5 seconds");
      delay(5000);
    }
  }
}

void connect_to_typhur_device(BLEAdvertisedDevice* device) {
  Serial.println("Connecting to Typhur device...");

  BLEClient* pClient = BLEDevice::createClient();
  Serial.println(" - Created client");

  // Connect to the device
  if (!pClient->connect(device)) {
    Serial.println("Failed to connect to device");
    pClient->disconnect();
    return;
  }
  Serial.println(" - Connected to device");

  // Get services
  BLERemoteService* pRemoteService = pClient->getService(TYPHUR_SERVICE_UUID);
  if (pRemoteService == nullptr) {
    Serial.println("Failed to find service");
    pClient->disconnect();
    return;
  }

  Serial.println(" - Found service");

  // Get characteristic
  BLERemoteCharacteristic* pRemoteCharacteristic = pClient->getCharacteristic(TYPHUR_NOTIFICATION_UUID);
  if (pRemoteCharacteristic == nullptr) {
    Serial.println("Failed to find characteristic");
    pClient->disconnect();
    return;
  }

  Serial.println(" - Found characteristic");

  // Enable notifications
  if (pRemoteCharacteristic->canNotify()) {
    pRemoteCharacteristic->registerForNotify([](BLERemoteCharacteristic* pCharacteristic, uint8_t* data, size_t length) {
      Serial.print("Received notification: ");
      for (int i = 0; i < length; i++) {
        Serial.print(data[i], HEX);
        Serial.print(" ");
      }
      Serial.println();

      // Convert data to string and forward to MQTT
      String payload = "";
      for (int i = 0; i < length; i++) {
        payload += (char)data[i];
      }

      forward_data_to_mqtt(payload);
    });
    Serial.println(" - Notifications enabled");
  }

  // Read data once
  std::string value = pRemoteCharacteristic->readValue();
  Serial.println(" - Read value: " + String(value.c_str()));

  // Disconnect
  pClient->disconnect();
  Serial.println(" - Disconnected");
}

void forward_data_to_mqtt(String payload) {
  // Parse the JSON payload to extract device information
  DynamicJsonDocument doc(1024);
  DeserializationError error = deserializeJson(doc, payload);

  if (!error) {
    // Extract device ID from the payload if available
    if (doc.containsKey("deviceId")) {
      deviceId = doc["deviceId"].as<String>();
    }

    // Forward data to MQTT
    String topic = "typhur/" + deviceId + "/state";
    client.publish(topic.c_str(), payload.c_str());
    Serial.println("Published to MQTT: " + topic);
  } else {
    Serial.println("Failed to parse JSON from BLE data");
    // Try to forward raw data anyway
    String topic = "typhur/unknown/state";
    client.publish(topic.c_str(), payload.c_str());
  }
}

void publish_discovery_data() {
  // Publish discovery information for Home Assistant
  // This would create the sensor definitions in HA
  Serial.println("Publishing discovery data...");

  // This is a simplified example - in a real implementation
  // you would need to send proper Home Assistant discovery payloads
  // for each sensor type (temperature, battery, etc.)

  // For now, we just show that we'd publish discovery data
  Serial.println("Discovery data would be published here");
}

void callback(char* topic, byte* payload, unsigned int length) {
  Serial.print("Message arrived [");
  Serial.print(topic);
  Serial.print("] ");

  String message;
  for (int i = 0; i < length; i++) {
    message += (char)payload[i];
  }
  Serial.println(message);
}