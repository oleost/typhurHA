#### 
NOT WORKING; JUST DRAFT AI CODING!

# Typhur BLE Bridge for ESP32

This is a firmware implementation for ESP32 that connects directly to Typhur thermometers via Bluetooth Low Energy (BLE) and forwards data to Home Assistant via MQTT.

## Features

- Direct BLE connection to Typhur devices (WT08)
- Automatic discovery of Typhur devices
- Data forwarding to Home Assistant MQTT broker
- Support for multiple probes and sensors
- Automatic reconnection to WiFi and MQTT

## Hardware Requirements

- ESP32 microcontroller
- WiFi network access
- Home Assistant MQTT broker

## Setup Instructions

1. **Configure WiFi credentials** in the sketch:
   ```cpp
   const char* ssid = "YOUR_WIFI_SSID";
   const char* password = "YOUR_WIFI_PASSWORD";
   ```

2. **Configure MQTT settings** in the sketch:
   ```cpp
   const char* mqtt_server = "YOUR_MQTT_SERVER_IP";
   const char* mqtt_user = "YOUR_MQTT_USER";
   const char* mqtt_password = "YOUR_MQTT_PASSWORD";
   ```

3. **Upload the sketch** to your ESP32 using Arduino IDE or PlatformIO

## Implementation Details

Based on the Typhur API documentation:
- The notification UUID is `0000ff02-0000-1000-8000-00805f9b34fb`
- BLE messages follow the same JSON structure as cloud messages (`cmdType`, `cmdData`)
- Temperature values are in tenths of Fahrenheit and need conversion to Celsius:
  ```
  celsius = (value / 10.0 - 32) * 5 / 9
  ```

## MQTT Topics

- **Subscribe**: `typhur/+/state` (incoming data from Typhur devices)
- **Publish**: `typhur/{device_id}/state` (forwarded data to Home Assistant)

## Sensor Data

For each probe, the following sensors are created:
- **Temperature** (°C)
- **Ambient Temperature** (°C)
- **Battery** (%)
- **State** (cooking / charging / idle)

For the device itself:
- **Battery** (%)
- **WiFi Signal** (dBm)

## Troubleshooting

1. **Device not found**: Make sure the Typhur device is powered on and within range
2. **Connection issues**: Check WiFi and MQTT credentials
3. **Data format issues**: Verify that the BLE data matches expected JSON format

## Notes

This implementation assumes the Typhur device broadcasts its information via BLE notifications. The actual implementation may need adjustments based on the specific BLE protocol used by the Typhur device.
