# Bluetooth Sniffing with ADB

This document explains how to use your Android phone connected via ADB to investigate the Bluetooth communication protocol used by the Typhur app.

## Prerequisites

1. Android phone with ADB enabled
2. USB cable for connecting phone to computer
3. Typhur app installed and running
4. Typhur device paired with the phone
5. Bluetooth debugging enabled (if available)

## Approach for Investigation

### 1. Enable Bluetooth Logging

First, enable Bluetooth logging on your Android device:

```bash
adb shell
su
setprop persist.bluetooth.log.level 3
```

### 2. Monitor Bluetooth Traffic

Use ADB to monitor Bluetooth traffic:

```bash
# Monitor Bluetooth system logs
adb logcat | grep -i bluetooth

# Monitor network traffic (if applicable)
adb shell
netstat -an
```

### 3. Use Bluetooth Sniffing Tools

You can also use specialized tools to capture Bluetooth packets:

```bash
# Install tcpdump if available
adb shell
tcpdump -i any -w bluetooth.pcap

# Or use the built-in Android logging
adb shell dumpsys bluetooth
```

### 4. Analyze Typhur App Traffic

```bash
# Monitor the Typhur app specifically
adb logcat | grep -i typhur

# Monitor network traffic from the app
adb shell
netstat -an | grep typhur
```

### 5. Extract Protocol Information

From the logs, look for:
- Bluetooth UUIDs being used
- Data formats (JSON, binary)
- Command structures
- Notification patterns

## Important Notes

1. The Typhur device uses a specific notification UUID: `0000ff02-0000-1000-8000-00805f9b34fb`
2. The data format is likely JSON with fields like `cmdType` and `cmdData`
3. Temperature values are in tenths of Fahrenheit and need conversion to Celsius
4. The protocol may require specific authentication or connection sequences

## Example Data Structure

Based on the cloud API documentation, the BLE messages should follow a similar structure:

```json
{
  "cmdType": "status:report",
  "cmdData": {
    "probes": [
      {
        "probeColor": "probe1",
        "curTemperature": 1234,
        "curAmbientTemperature": 567,
        "batteryValue": 85,
        "cookingState": "cooking"
      }
    ],
    "batteryValue": 92,
    "wifiRssi": -65
  }
}
```

## Next Steps

Once you've captured the Bluetooth traffic, you can:
1. Analyze the packet structure
2. Identify the exact data format
3. Understand connection sequence requirements
4. Implement the same protocol in ESP32 firmware

This information will be crucial for implementing the ESP32 bridge that can communicate directly with the Typhur device without requiring the cloud connection.