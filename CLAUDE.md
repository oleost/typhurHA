# Typhur Bridge — Project Context

## Goal

Home Assistant app that connects **Typhur Sync thermometers** (Sync Quad / WT08, Sync Dual / WT03, and other WT-series models) to Home Assistant via MQTT auto-discovery, without requiring the Typhur phone app.

The bridge is model-agnostic: it takes `deviceModel` straight from the API and probe sensors are created from whatever probes the device actually reports (2, 4, …) rather than a hardcoded count. (The MQTT subscribe topic must contain the real model — AWS IoT drops the connection if the cert policy doesn't authorize the exact topic filter, so a `+` wildcard doesn't work there.)

**Only verified on the Sync Quad (WT08).** Other WT-series models take the same code path but are untested — treat other-model bug reports as the primary signal for whether the model-agnostic assumptions hold.

## Architecture

```
Typhur probe → Typhur cloud (AWS IoT MQTT) → typhur_bridge → Local MQTT → Home Assistant
```

The bridge authenticates with the Typhur cloud API, subscribes to the device's real-time data stream, and forwards readings to the local HA MQTT broker. All sensors are created automatically via HA discovery. Data is routed through Typhur's cloud; there is no local-only connection.

## Key files

- `typhur_bridge/run.py` — main bridge logic
- `typhur_bridge/config.yaml` — HA add-on config schema and defaults
- `typhur_bridge/translations/en.yaml` — UI labels and field descriptions
- `typhur_bridge/Dockerfile` — container build

## Typhur API

Base URLs:
- EU: `https://api.iot.typhur.de`
- US: `https://api.iot.typhur.com`

Region is set via `typhur_region` in config (`eu` or `us`). Known regions:
- `eu` — Europe (DE, NO, FR, UK, SE, etc.)
- `us` — United States, Canada, Australia, New Zealand

### Endpoints used

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/app/account/login` | POST | Login with email + MD5(password) |
| `/app/device/bind/list` | POST | List bound devices |
| `/app/mqtt/cert/apply` | POST | Fetch MQTT client certificate (p12) |
| `/app/dict/list` | POST | Fetch server config — includes `mqtt_conn_param` |

### Request signing

All requests are signed via MD5:

```
sign = MD5( SIGN_CONSTANT | "x-appId=...;x-appVersion=...;x-deviceSn=...;x-lang=...;x-nonce=...;x-region=...;x-timestamp=...;x-token=..." | BODY )
```

The `SIGN_CONSTANT`, `APP_ID`, and `APP_VERSION` are defined as constants in `run.py`. They are extracted from the Typhur APK and are not secret — they are the same for all users.

### MQTT broker

The broker endpoint is **not hardcoded** — it is fetched dynamically from `/app/dict/list` under the key `mqtt_conn_param`. This ensures the correct regional AWS IoT endpoint is used for any account.

Example response:
```json
{
  "endpoint": "a2qg0p56us3mxs-ats.iot.eu-central-1.amazonaws.com",
  "region": "eu-central-1",
  "port": 8883
}
```

### MQTT topics

- Subscribe: `device/{deviceModel}/{deviceId}/pub` (`deviceModel` comes from the API; a `+` wildcard here gets the connection dropped by AWS IoT)
- Relevant messages: `cmdType` contains `"status:report"`

### Temperature encoding

Values are in tenths of Fahrenheit. Convert to Celsius:
```python
celsius = (value / 10.0 - 32) * 5 / 9
```
