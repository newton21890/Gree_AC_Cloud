[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

# Gree AC Cloud

Custom component for Home Assistant that controls Gree cloud-only VRF devices (e.g., XE7A-24/HC wired controllers, U-Match ducted series) via MQTT.

> **⚠️ Important**: This integration is for **cloud-only** Gree devices that do NOT speak the local UDP protocol. If your AC works with the standard Gree HACS integration, you don't need this.

## Architecture

```
HA (gree_ac_cloud) ─→ Gree MQTT (mqtt-eu.gree.com:1984) ←→ Devices
                  ─→ Gree Cloud API (eugrih.gree.com)
```

## Features

- Climate control (mode, fan, swing, temperature)
- Sensors: Indoor/Outdoor temperature, humidity, setpoint (decimal)
- Switches: Health, Quiet, Turbo, Strong Heat, Blow, Energy Saving, Sleep, Light
- Binary sensors: Error status, Filter reminder
- Energy consumption estimation (based on model, mode, fan speed, load)
- Custom panel UI with live controls, log viewer, Wiki reference, and energy monitoring
- Device rename support (double-click name in panel)
- Live log viewer with auto-refresh

## Setup

1. Add this repository as a custom repository in HACS
2. Go to **Settings → Devices & Services → Add Integration → Gree AC Cloud**
3. Select your region and enter your Gree+ account credentials
4. Two devices appear: the parent controllers (12-char MAC)

## Regions

| Region | Cloud API | MQTT Broker |
|--------|-----------|-------------|
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
