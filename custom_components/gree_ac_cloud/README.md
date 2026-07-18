# Gree AC Cloud

Custom component for Home Assistant that controls Gree cloud-only VRF devices (e.g., XE7A-24/HC wired controllers, U-Match ducted series) via MQTT.

## Architecture

```
HA (gree_ac_cloud) ─→ Gree MQTT (mqtt-eu.gree.com:1984) ←→ Devices
                  ─→ Gree Cloud API (eugrih.gree.com)
```

The integration:
1. Authenticates to the Gree Cloud API with your email/password
2. Discovers registered VRF devices and their encryption keys
3. Connects to the Gree MQTT broker (async via aiomqtt) to poll device state and receive real-time status pushes
4. Exposes climate, sensor, switch, and binary_sensor entities in HA

## Setup

- **Requires `aiomqtt`**: `pip install aiomqtt` in your HA Python environment
- Go to **Settings → Devices & Services → Add Integration → Gree AC Cloud**
- Select your region and enter your Gree+ account credentials
- Two devices appear: the parent controllers (12-char MAC)

## Features

- Climate control (mode, fan, swing, temperature)
- Sensors: Indoor/Outdoor temperature, humidity, setpoint (decimal)
- Switches: Health, Quiet, Turbo, Strong Heat, Blow, Energy Saving, Sleep, Light
- Binary sensors: Error status, Filter reminder
- Energy consumption estimation (based on model, mode, fan speed, load)
- Panel UI with live controls, log viewer, Wiki reference, energy monitoring, and device info tab
- Async MQTT (aiomqtt) — reliable auto-reconnect, no threads, real-time status pushes

## Hosted Regions

| Region | Cloud API | MQTT Broker |
|---|---|---|
| Europe | eugrih.gree.com | mqtt-eu.gree.com |
| North America | nagrih.gree.com | mqtt-us.gree.com |
| China | grih.gree.com | mqtt-cn.gree.com |
| Australia | augrih.gree.com | mqtt-au.gree.com |
| East South Asia | hkgrih.gree.com | mqtt-as.gree.com |
| India | ingrih.gree.com | mqtt-in.gree.com |
| Latin America | lagrih.gree.com | mqtt-la.gree.com |
| Middle East | megrih.gree.com | mqtt-me.gree.com |
| Russia | rugrih.gree.com | mqtt-ru.gree.com |
| South America | sagrih.gree.com | mqtt-sa.gree.com |

## Notes

- Only 12-char parent MACs respond via MQTT (14-char sub-unit MACs are aliases)
- The integration disconnects the Gree+ mobile app (one session per account)
- AES-128-ECB encryption with device-specific key
