#!/usr/bin/env python3
"""
Typhur Bridge - Home Assistant Add-on
Connects Typhur Sync thermometers (Sync Quad / Sync Dual, and other WT-series
models) to Home Assistant via MQTT auto-discovery. Probe sensors are created
from whatever the device actually reports, so no per-model configuration is
needed. Fetches MQTT certificates automatically from the Typhur API.
"""
import json
import ssl
import time
import logging
import hashlib
import uuid
import os
import subprocess
import tempfile
import requests
import paho.mqtt.client as mqtt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("typhur_bridge")

OPTIONS_FILE = "/data/options.json"
DATA_DIR = "/data"
CERT_FILE = os.path.join(DATA_DIR, "typhur_client.crt")
KEY_FILE = os.path.join(DATA_DIR, "typhur_client.key")
CLIENT_ID_FILE = os.path.join(DATA_DIR, "typhur_client_id.txt")
TOKEN_FILE = os.path.join(DATA_DIR, "typhur_token.txt")
# device_id -> the subscribe topic AWS IoT accepted (SUBACK). Persisted so a
# restart doesn't have to re-probe the topic segment for every device.
TOPIC_CACHE_FILE = os.path.join(DATA_DIR, "typhur_topics.json")

TYPHUR_API_BY_REGION = {
    "eu": "https://api.iot.typhur.de",
    "us": "https://api.iot.typhur.com",
}
# Default ISO country code (x-region) per region. The Typhur API validates
# x-region against the endpoint's region set, so it must match the user's
# account country: the US endpoint rejects EU codes like "NO" and vice versa.
TYPHUR_COUNTRY_BY_REGION = {
    "eu": "NO",
    "us": "US",
}
# Resolved at startup in TyphurBridge.__init__; overridable via typhur_country.
TYPHUR_REGION_CODE = "NO"
# Public signing constant extracted from the Typhur APK — not a secret
TYPHUR_SIGN_CONSTANT = "7d02d81bd7f4483a9a0ac580f2b6ad44"
APP_ID = "ap206cba3069ed4a11"
APP_VERSION = "4200"
APP_DEVICE_SN = hashlib.md5(b"ha_typhur_bridge_v1").hexdigest()
HA_DISCOVERY_PREFIX = "homeassistant"
# Reconnect backoff for the Typhur cloud MQTT connection. Starts small for
# ordinary network blips and triples up to the max when the connection keeps
# dropping right after connecting (5s → 15s → 45s → … → 15min), so a broken
# setup never hammers Typhur's broker.
RECONNECT_BACKOFF_MIN = 5
RECONNECT_BACKOFF_MAX = 900


def load_options():
    with open(OPTIONS_FILE) as f:
        return json.load(f)


def sign_request(token, body_str="{}"):
    nonce = uuid.uuid4().hex
    timestamp = str(int(time.time() * 1000))
    # x-token is always included in the signature string (even as empty/none)
    headers_sorted = [
        ("x-appId", APP_ID), ("x-appVersion", APP_VERSION),
        ("x-deviceSn", APP_DEVICE_SN), ("x-lang", "en_US"),
        ("x-nonce", nonce), ("x-region", TYPHUR_REGION_CODE),
        ("x-timestamp", timestamp), ("x-token", token),
    ]
    parts = ";".join(f"{k}={v}" for k, v in headers_sorted)
    sign_str = f"{TYPHUR_SIGN_CONSTANT}|{parts}|{body_str}"
    sign = hashlib.md5(sign_str.encode()).hexdigest()
    # Only send headers with an actual value
    h = {k: v for k, v in headers_sorted if v}
    h["x-sign"] = sign
    h["Content-Type"] = "application/json"
    return h


def login(email, password):
    """
    Log in with email and MD5-hashed password.
    Endpoint: /app/account/login
    x-token must be the literal string 'none' for unauthenticated requests.
    """
    md5_pw = hashlib.md5(password.encode()).hexdigest()
    body = json.dumps(
        {"accountName": email, "accountPassword": md5_pw, "deviceInfo": "HomeAssistant"},
        separators=(",", ":")
    )
    hdrs = sign_request("none", body)
    log.info(f"Logging in as {email}...")
    resp = requests.post(f"{TYPHUR_API}/app/account/login", headers=hdrs, data=body, timeout=15)
    log.debug(f"Login response {resp.status_code}: {resp.text}")
    data = resp.json()
    if data.get("code") == "0":
        token = data["data"]["token"]
        log.info("Login successful.")
        with open(TOKEN_FILE, "w") as f:
            f.write(token)
        os.chmod(TOKEN_FILE, 0o600)
        return token
    raise Exception(f"Login failed: {data.get('msg')} (code: {data.get('code')})")


def refresh_token(email, password, old_token):
    """Refresh an expired token — same endpoint as regular login."""
    return login(email, password)


def resolve_token(options):
    """
    Resolve the API token from config, cached file, or by logging in with email/password.
    Priority:
      1. Explicit token in config
      2. Cached token from previous login
      3. Login with email + password
    """
    email = (options.get("typhur_email") or "").strip()
    password = (options.get("typhur_password") or "").strip()

    token = (options.get("typhur_token") or "").strip()

    if not token and os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            token = f.read().strip()
        if token:
            log.info("Using cached token from /data/typhur_token.txt")

    if token:
        token = verify_or_refresh_token(token, email, password)
        return token

    if email and password:
        return login(email, password)

    raise Exception(
        "No token found. Set 'typhur_email' + 'typhur_password', or provide 'typhur_token' directly."
    )


def verify_or_refresh_token(token, email, password):
    """Verify token against the API; refresh automatically if expired."""
    resp = requests.post(
        f"{TYPHUR_API}/app/device/bind/list",
        headers=sign_request(token, "{}"),
        data="{}",
        timeout=10
    )
    data = resp.json()
    code = data.get("code")

    if code == "0":
        log.info("Token is valid.")
        return token

    if code == "52":  # Token expired
        log.warning("Token has expired.")
        if email and password:
            log.info(f"Refreshing token automatically for {email}...")
            new_token = refresh_token(email, password, token)
            if new_token:
                return new_token
            raise Exception("Automatic token refresh failed. Update typhur_token manually.")
        raise Exception(
            "Token has expired. Add 'typhur_email' and 'typhur_password' for automatic renewal, "
            "or provide a new token via 'typhur_token'."
        )

    log.warning(f"Token verification returned code {code}: {data.get('msg')} — proceeding anyway")
    return token


def fetch_and_save_certs(token):
    """Fetch MQTT client certificates from the Typhur API and save to /data/."""
    log.info("Fetching MQTT certificates from Typhur API...")
    resp = requests.post(
        f"{TYPHUR_API}/app/mqtt/cert/apply",
        headers=sign_request(token, "{}"),
        data="{}",
        timeout=15
    )
    data = resp.json()
    if data.get("code") != "0":
        raise Exception(f"Certificate request failed: {data.get('msg')}")

    cert_data = data["data"]
    p12_url = cert_data["p12Url"]
    p12_password = cert_data["p12Password"]
    client_id = cert_data["clientId"]

    p12_resp = requests.get(p12_url, timeout=15)
    p12_tmp = tempfile.NamedTemporaryFile(suffix=".p12", delete=False)
    p12_tmp.write(p12_resp.content)
    p12_tmp.close()

    subprocess.run([
        "openssl", "pkcs12", "-legacy",
        "-in", p12_tmp.name,
        "-passin", f"pass:{p12_password}",
        "-nokeys", "-out", CERT_FILE, "-nodes"
    ], check=True, capture_output=True)

    subprocess.run([
        "openssl", "pkcs12", "-legacy",
        "-in", p12_tmp.name,
        "-passin", f"pass:{p12_password}",
        "-nocerts", "-out", KEY_FILE, "-nodes"
    ], check=True, capture_output=True)

    os.unlink(p12_tmp.name)
    os.chmod(KEY_FILE, 0o600)

    with open(CLIENT_ID_FILE, "w") as f:
        f.write(client_id)

    log.info(f"Certificates saved. Client ID: {client_id}")
    return client_id


def fetch_mqtt_params(token):
    """Fetch MQTT broker parameters from the Typhur API dict/list endpoint."""
    log.info("Fetching MQTT connection parameters from Typhur API...")
    resp = requests.post(
        f"{TYPHUR_API}/app/dict/list",
        headers=sign_request(token, "{}"),
        data="{}",
        timeout=15
    )
    data = resp.json()
    if data.get("code") != "0":
        raise Exception(f"dict/list failed: {data.get('msg')}")
    for entry in data.get("data", []):
        if entry.get("dictKey") == "mqtt_conn_param":
            params = entry["dictValue"]
            endpoint = params["endpoint"]
            port = int(params.get("port", 8883))
            log.info(f"MQTT broker: {endpoint}:{port}")
            return endpoint, port
    raise Exception("mqtt_conn_param not found in dict/list response")


def get_devices(token):
    resp = requests.post(
        f"{TYPHUR_API}/app/device/bind/list",
        headers=sign_request(token, "{}"),
        data="{}",
        timeout=10
    )
    data = resp.json()
    if data.get("code") == "0":
        return data.get("data", [])
    return []


def device_display_name(device):
    return device.get("deviceName") or "Typhur Sync"


def device_info_block(device):
    device_id = str(device["deviceId"])
    return {
        "identifiers": [f"typhur_{device_id}"],
        "name": device_display_name(device),
        "manufacturer": "Typhur",
        # deviceModel is informational only — the bridge no longer depends on it.
        "model": device.get("deviceModel") or "Typhur Sync",
    }


def probe_sensor_defs(device_id, device_name, color):
    """Sensor definitions for a single probe, keyed by probeColor (probe1..probeN)."""
    label = color.replace("probe", "Probe ")
    base = f"(value_json.cmdData.probes | selectattr('probeColor','eq','{color}') | list | first)"
    return [
        {
            "uid": f"typhur_{device_id}_{color}_temp",
            "name": f"{device_name} {label} Temperature",
            "unit": "°C",
            "device_class": "temperature",
            "state_class": "measurement",
            "value_template": f"{{{{ (({base}.curTemperature | float) / 10 - 32) * 5 / 9 | round(1) }}}}",
        },
        {
            "uid": f"typhur_{device_id}_{color}_ambient",
            "name": f"{device_name} {label} Ambient Temperature",
            "unit": "°C",
            "device_class": "temperature",
            "state_class": "measurement",
            "value_template": f"{{{{ (({base}.curAmbientTemperature | float) / 10 - 32) * 5 / 9 | round(1) }}}}",
        },
        {
            "uid": f"typhur_{device_id}_{color}_battery",
            "name": f"{device_name} {label} Battery",
            "unit": "%",
            "device_class": "battery",
            "state_class": "measurement",
            "value_template": f"{{{{ {base}.batteryValue }}}}",
        },
        {
            "uid": f"typhur_{device_id}_{color}_state",
            "name": f"{device_name} {label} State",
            "unit": None,
            "device_class": None,
            "state_class": None,
            "value_template": f"{{{{ {base}.cookingState }}}}",
        },
    ]


def device_sensor_defs(device_id, device_name):
    """Device-level sensors that exist regardless of how many probes are attached."""
    return [
        {
            "uid": f"typhur_{device_id}_battery",
            "name": f"{device_name} Battery",
            "unit": "%",
            "device_class": "battery",
            "state_class": "measurement",
            "value_template": "{{ value_json.cmdData.batteryValue }}",
        },
        {
            "uid": f"typhur_{device_id}_wifi",
            "name": f"{device_name} WiFi Signal",
            "unit": "dBm",
            "device_class": "signal_strength",
            "state_class": "measurement",
            "value_template": "{{ value_json.cmdData.wifiRssi }}",
        },
    ]


def publish_sensor_configs(ha_client, device_info, state_topic, sensors):
    for s in sensors:
        payload = {
            "name": s["name"],
            "unique_id": s["uid"],
            "state_topic": state_topic,
            "value_template": s["value_template"],
            "device": device_info,
        }
        if s.get("unit"):
            payload["unit_of_measurement"] = s["unit"]
        if s.get("device_class"):
            payload["device_class"] = s["device_class"]
        if s.get("state_class"):
            payload["state_class"] = s["state_class"]

        ha_client.publish(
            f"{HA_DISCOVERY_PREFIX}/sensor/{s['uid']}/config",
            json.dumps(payload),
            retain=True
        )


def probe_colors_from_status(data):
    """Extract probeColor values from a status:report payload, in order."""
    probes = ((data or {}).get("cmdData") or {}).get("probes") or []
    colors = []
    for i, probe in enumerate(probes, start=1):
        colors.append(probe.get("probeColor") or f"probe{i}")
    return colors


def publish_device_discovery(ha_client, device):
    """Publish device-level sensors + any probes already known from the bind list.

    Probe sensors are model-agnostic: whatever probes appear in the API snapshot
    (or later in live status messages) get sensors. Returns (state_topic, known_probe_colors).
    """
    device_id = str(device["deviceId"])
    device_name = device_display_name(device)
    state_topic = f"typhur/{device_id}/state"
    device_info = device_info_block(device)

    publish_sensor_configs(
        ha_client, device_info, state_topic,
        device_sensor_defs(device_id, device_name),
    )

    known = probe_colors_from_status(device.get("lastStatusCmd"))
    for color in known:
        publish_sensor_configs(
            ha_client, device_info, state_topic,
            probe_sensor_defs(device_id, device_name, color),
        )

    log.info(
        f"Discovery published for {device_name}: device sensors"
        + (f" + {len(known)} probe(s) {known}" if known else " (probes will be added as they report)")
    )
    return state_topic, set(known)


def publish_probe_discovery(ha_client, device, color):
    """Publish sensors for one probe discovered from a live status message."""
    device_id = str(device["deviceId"])
    device_name = device_display_name(device)
    state_topic = f"typhur/{device_id}/state"
    publish_sensor_configs(
        ha_client, device_info_block(device), state_topic,
        probe_sensor_defs(device_id, device_name, color),
    )
    log.info(f"Discovered new probe '{color}' for {device_name}")


class TyphurBridge:
    def __init__(self, options):
        self.options = options
        region = (options.get("typhur_region") or "eu").strip().lower()
        global TYPHUR_API, TYPHUR_REGION_CODE
        TYPHUR_API = TYPHUR_API_BY_REGION.get(region, TYPHUR_API_BY_REGION["eu"])
        # x-region must be the account's ISO country code. Use an explicit
        # override if given, otherwise fall back to the region's default.
        country = (options.get("typhur_country") or "").strip().upper()
        TYPHUR_REGION_CODE = country or TYPHUR_COUNTRY_BY_REGION.get(region, "NO")
        log.info(f"Typhur API region: {region} → {TYPHUR_API} (x-region={TYPHUR_REGION_CODE})")
        self.token = resolve_token(options)
        self.ha_client = None
        self.typhur_client = None
        self.devices = []
        # device_id -> set of probeColor values already published to HA discovery
        self.discovered_probes = {}
        self._typhur_conn = None  # (broker, port) for reconnects
        self._last_connect_at = 0.0
        self._backoff = RECONNECT_BACKOFF_MIN
        # Adaptive subscribe-topic state. The topic's model segment is usually
        # the deviceModel (Sync Quad / WT08) but not always — the Sync Dual
        # (WT03) subscribes on 'device/thermometer/<id>/pub'. We try candidates
        # in order across reconnects and cache whichever one the broker SUBACKs.
        self.topic_cache = self._load_topic_cache()  # device_id -> working topic
        self._topic_idx = {}       # device_id -> index into its candidate list
        self._pending_sub = {}     # mid -> device_id awaiting SUBACK
        self._current_topic = {}   # device_id -> topic used on this connection

    def setup_ha_mqtt(self):
        self.ha_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="typhur_bridge_ha")
        if self.options.get("mqtt_username"):
            self.ha_client.username_pw_set(
                self.options["mqtt_username"],
                self.options.get("mqtt_password", "")
            )
        self.ha_client.connect(self.options["mqtt_host"], self.options["mqtt_port"], 60)
        self.ha_client.loop_start()
        log.info(f"Connected to HA MQTT: {self.options['mqtt_host']}:{self.options['mqtt_port']}")

    def _device_by_id(self, device_id):
        for dev in self.devices:
            if str(dev["deviceId"]) == str(device_id):
                return dev
        return None

    def _sync_probe_discovery(self, dev, data):
        """Publish discovery for any probe in this message we haven't seen yet."""
        device_id = str(dev["deviceId"])
        seen = self.discovered_probes.setdefault(device_id, set())
        for color in probe_colors_from_status(data):
            if color not in seen:
                publish_probe_discovery(self.ha_client, dev, color)
                seen.add(color)

    def _load_topic_cache(self):
        try:
            with open(TOPIC_CACHE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items() if v}
        except (OSError, ValueError):
            pass
        return {}

    def _save_topic_cache(self):
        try:
            with open(TOPIC_CACHE_FILE, "w") as f:
                json.dump(self.topic_cache, f)
        except OSError as e:
            log.warning(f"Could not persist topic cache: {e}")

    def _topic_candidates(self, dev):
        """Ordered list of subscribe topics to try for one device.

        AWS IoT drops the whole connection on an unauthorized SUBSCRIBE (it does
        not NACK), so the bridge walks these candidates across reconnects until
        one gets a SUBACK, then caches it.
        """
        device_id = str(dev["deviceId"])
        topics = []

        # 1. Explicit topic(s) from the API, if the account returns them.
        sub_topics = dev.get("subTopics")
        if isinstance(sub_topics, (list, tuple)):
            topics += [str(t) for t in sub_topics if t]
        elif isinstance(sub_topics, str) and sub_topics:
            topics.append(sub_topics)

        # 2. device/{segment}/{id}/pub for each plausible segment. deviceModel
        #    works for the Sync Quad (WT08); 'thermometer' for the Sync Dual
        #    (WT03). deviceType/productType are tried if the API provides them.
        segments = []
        for key in ("deviceModel", "deviceType", "productType"):
            val = dev.get(key)
            if val and str(val) not in segments:
                segments.append(str(val))
        if "thermometer" not in segments:
            segments.append("thermometer")
        for seg in segments:
            topic = f"device/{seg}/{device_id}/pub"
            if topic not in topics:
                topics.append(topic)

        # 3. A previously confirmed topic always goes first.
        cached = self.topic_cache.get(device_id)
        if cached:
            topics = [cached] + [t for t in topics if t != cached]
        return topics

    def _current_topic_for(self, dev):
        device_id = str(dev["deviceId"])
        candidates = self._topic_candidates(dev)
        idx = self._topic_idx.get(device_id, 0) % len(candidates)
        return candidates[idx]

    def _remember_topic(self, device_id, topic):
        """Persist a topic the broker accepted so restarts skip re-probing."""
        device_id = str(device_id)
        if not topic or self.topic_cache.get(device_id) == topic:
            return
        self.topic_cache[device_id] = topic
        self._save_topic_cache()
        log.info(f"Confirmed subscribe topic for {device_id}: {topic}")

    def _advance_unacked_topics(self):
        """After a fast drop, step each un-SUBACKed device to its next candidate.

        A SUBACK means the topic filter was authorized, so any device still
        pending when the connection dropped is one AWS IoT rejected.
        """
        for device_id in list(self._pending_sub.values()):
            dev = self._device_by_id(device_id)
            if dev is None:
                continue
            candidates = self._topic_candidates(dev)
            if len(candidates) <= 1:
                continue
            self._topic_idx[device_id] = (
                self._topic_idx.get(device_id, 0) + 1
            ) % len(candidates)
            log.warning(
                f"No SUBACK for {device_id} before the drop — AWS IoT likely "
                f"rejected '{self._current_topic.get(device_id)}'. "
                f"Next attempt: {candidates[self._topic_idx[device_id]]}"
            )
        self._pending_sub = {}

    def subscribe_all(self, client):
        self._pending_sub = {}
        for dev in self.devices:
            device_id = str(dev["deviceId"])
            candidates = self._topic_candidates(dev)
            topic = self._current_topic_for(dev)
            self._current_topic[device_id] = topic
            _result, mid = client.subscribe(topic)
            if mid is not None:
                self._pending_sub[mid] = device_id
            suffix = ""
            if len(candidates) > 1:
                idx = self._topic_idx.get(device_id, 0) % len(candidates)
                suffix = f"  (topic candidate {idx + 1}/{len(candidates)})"
            log.info(f"Subscribing to: {topic}{suffix}")

    def handle_typhur_message(self, topic, payload):
        """Forward one Typhur cloud message to HA and keep probe discovery in sync.

        Returns the resolved device_id if the message was forwarded, else None.
        """
        try:
            data = json.loads(payload)
        except ValueError as e:
            log.error(f"Message error: {e}")
            return None
        if "status:report" not in data.get("cmdType", ""):
            return None
        # topic layout: device/{deviceModel}/{deviceId}/pub
        parts = topic.split("/")
        topic_device_id = parts[2] if len(parts) >= 4 else None
        device_id = topic_device_id or str(data.get("deviceId", ""))
        dev = self._device_by_id(device_id)
        if dev is None:
            return None
        # A message actually arrived on this topic — strongest possible proof
        # that the subscribe filter is authorized.
        self._remember_topic(device_id, topic)
        self.ha_client.publish(f"typhur/{device_id}/state", payload)
        self._sync_probe_discovery(dev, data)
        return device_id

    def next_reconnect_backoff(self, uptime):
        """Escalate the reconnect delay when the connection keeps flapping.

        paho's own backoff never escalates for our failure mode: every reconnect
        "succeeds" at the MQTT level before the broker drops us. So a connection
        that barely stayed up (usually an unauthorized SUBSCRIBE topic) backs off
        hard; one that lasted a while was a normal blip and resets to the minimum.
        """
        if uptime < 30:
            self._backoff = min(self._backoff * 3, RECONNECT_BACKOFF_MAX)
        else:
            self._backoff = RECONNECT_BACKOFF_MIN
        return self._backoff

    def setup_typhur_mqtt(self, client_id, broker, port):
        self._typhur_conn = (broker, port)

        def on_connect(client, userdata, flags, rc, properties=None):
            if rc != 0:
                log.error(f"Typhur MQTT connection failed: rc={rc}")
                return
            self._last_connect_at = time.time()
            log.info("Connected to Typhur cloud MQTT")
            self.subscribe_all(client)

        def on_message(client, userdata, msg):
            try:
                self.handle_typhur_message(msg.topic, msg.payload.decode())
            except Exception as e:
                log.error(f"Message error: {e}")

        def on_subscribe(client, userdata, mid, reason_code_list, properties=None):
            device_id = self._pending_sub.pop(mid, None)
            if device_id is None:
                return
            rc = reason_code_list[0] if reason_code_list else None
            if rc is not None and getattr(rc, "is_failure", False):
                log.warning(
                    f"Typhur MQTT SUBACK reported failure for {device_id}: {rc}"
                )
                return
            topic = self._current_topic.get(device_id)
            log.info(f"Subscribed to: {topic}")
            self._remember_topic(device_id, topic)

        # paho 2.x VERSION2 signature: (client, userdata, disconnect_flags,
        # reason_code, properties). This bridge never disconnects on purpose, so
        # every call here is an unexpected drop.
        def on_disconnect(client, userdata, disconnect_flags=None,
                          reason_code=None, properties=None):
            uptime = time.time() - self._last_connect_at
            # A drop right after connecting is almost always an unauthorized
            # SUBSCRIBE. Step any un-SUBACKed device to its next topic candidate
            # so the reconnect tries a different segment.
            if uptime < 30 and self._pending_sub:
                self._advance_unacked_topics()
            delay = self.next_reconnect_backoff(uptime)
            if delay >= 60:
                log.error(
                    f"Typhur MQTT dropped after only {uptime:.0f}s connected "
                    f"(reason={reason_code}) — AWS IoT most likely rejected the "
                    f"SUBSCRIBE topic. The bridge will try the next topic "
                    f"candidate on reconnect. Waiting {delay}s before retrying."
                )
            else:
                log.warning(
                    f"Typhur MQTT disconnected (reason={reason_code}); "
                    f"reconnecting in {delay}s."
                )
            time.sleep(delay)

        self.typhur_client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id
        )
        self.typhur_client.on_connect = on_connect
        self.typhur_client.on_message = on_message
        self.typhur_client.on_subscribe = on_subscribe
        self.typhur_client.on_disconnect = on_disconnect
        # We handle the real backoff in on_disconnect; keep paho's own delay tiny.
        self.typhur_client.reconnect_delay_set(min_delay=1, max_delay=2)

        ssl_ctx = ssl.create_default_context()
        ssl_ctx.load_cert_chain(CERT_FILE, KEY_FILE)
        self.typhur_client.tls_set_context(ssl_ctx)
        self.typhur_client.connect(broker, port, keepalive=60)
        self.typhur_client.loop_start()

    def run(self):
        log.info("=== Typhur Bridge starting ===")

        if not (os.path.exists(CERT_FILE) and os.path.exists(KEY_FILE)):
            client_id = fetch_and_save_certs(self.token)
        else:
            log.info("Using cached certificates")
            if os.path.exists(CLIENT_ID_FILE):
                with open(CLIENT_ID_FILE) as f:
                    client_id = f.read().strip()
            else:
                client_id = fetch_and_save_certs(self.token)

        log.info("Fetching device list...")
        self.devices = get_devices(self.token)
        if not self.devices:
            log.error("No devices found. Check your credentials or token.")
            raise SystemExit(1)
        log.info(f"Found {len(self.devices)} device(s)")
        for dev in self.devices:
            # Logged so other-model bug reports carry the fields we'd need to
            # pin down the right subscribe topic without guessing.
            log.info(
                "Device %s: model=%s type=%s subTopics=%s"
                % (dev.get("deviceId"), dev.get("deviceModel"),
                   dev.get("deviceType"), dev.get("subTopics"))
            )
            log.debug(f"Full device payload: {json.dumps(dev, default=str)}")

        broker, port = fetch_mqtt_params(self.token)
        self.setup_ha_mqtt()

        # Publish device-level discovery (and any probes already known) before
        # subscribing, so live probe messages only ever add what's missing.
        time.sleep(1)
        for dev in self.devices:
            _, known = publish_device_discovery(self.ha_client, dev)
            self.discovered_probes[str(dev["deviceId"])] = known

        self.setup_typhur_mqtt(client_id, broker, port)

        log.info("Bridge running. Temperature data is being forwarded to Home Assistant.")

        while True:
            time.sleep(60)


if __name__ == "__main__":
    options = load_options()
    bridge = TyphurBridge(options)
    bridge.run()
