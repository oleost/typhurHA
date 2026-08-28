# typhurHA

Home Assistant add-on that connects **Typhur Sync** thermometers (Sync Quad,
Sync Dual, and other WT-series models) to Home Assistant — without the Typhur
phone app.

```
Typhur probe → Typhur cloud (AWS IoT MQTT) → typhur_bridge → local MQTT → Home Assistant
```

The bridge logs in to the Typhur cloud API with your account credentials,
fetches the MQTT client certificate and broker endpoint automatically, and
forwards live readings to your local MQTT broker. All sensors are created via
Home Assistant MQTT discovery.

It is model-agnostic: the device model comes from the API and probe sensors are
created from whatever the device actually reports, so no per-model
configuration is needed.

> **Tested on the Typhur Sync Quad (WT08) only.** The Sync Dual and other
> WT-series models *should* work through the same model-agnostic path, but they
> haven't been verified — feedback from other-model owners is very welcome
> ([open an issue](https://github.com/oleost/typhurHA/issues)).

## Install

1. **Settings → Add-ons → Add-on Store → ⋮ → Repositories**
2. Add `https://github.com/oleost/typhurHA`
3. Install **Typhur Bridge**, set `typhur_email` + `typhur_password` (and
   `typhur_region`: `eu` or `us`), and start it.

Add-on details and all config options: [`typhur_bridge/README.md`](typhur_bridge/README.md).

## Sensors

Per probe: temperature, ambient temperature, battery, state.
Per device: battery, WiFi signal.
Probe sensors appear the first time a probe sends a reading.

## Notes

- Data is routed through Typhur's cloud (AWS IoT); there is no local-only connection.
- Certificates and token are cached in `/data/` and refreshed automatically.
- The `SIGN_CONSTANT`, `APP_ID`, and `APP_VERSION` constants in `run.py` are
  extracted from the Typhur APK and are the same for all users — not secrets.
