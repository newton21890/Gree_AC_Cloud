# Changelog

## 0.2.6 (2026-07-29)

### Fixed

- **Assume OFF on stale data** — When a device stops responding to MQTT polls (common when off), the coordinator no longer raises `UpdateFailed` (entities become `unavailable`). Instead it sets `Pow=0` and returns data normally, so HA shows the device as OFF. When the device responds again, the real state is restored.
- **Panel footer** — Version and cloud server host now display dynamically from manifest.json and config entry instead of being hardcoded.

## 0.2.4 (2026-07-29)

### Added

- **Auto version in panel** — Footer version number now reads from `manifest.json` dynamically instead of being hardcoded. Bump the version in one place and the panel reflects it automatically.

## 0.2.3 (2026-07-29)

### Fixed

- **Stale device state** — Devices that stop responding to MQTT polls (e.g. WiFi module idle when off) now correctly become `unavailable` instead of showing the last cached state forever. `coordinator.py` checks `seconds_since_last_seen()` against `STALE_AFTER_SECONDS` (60s) and raises `UpdateFailed` if exceeded.

### Added

- **gree_mqtt.py** — Tracks `_last_seen[mac]` timestamp on every real MQTT response; exposes `seconds_since_last_seen(mac)` for staleness checks.
- **const.py** — `STALE_AFTER_SECONDS = UPDATE_INTERVAL * 4` (60s).

## 0.2.2 (2026-07-29)

### Added

- **hacs.json** — Added HACS configuration file for automatic update notifications via HACS.

### Fixed

- **manifest.json documentation URL** — Fixed to point to correct GitHub repo `newton21890/Gree_AC_Cloud`.

## 0.2.1 (2026-07-29)

### Added

- **LCD icon reference in Wiki** — Enhanced HA Entities table with LCD display icon mappings (Table 3.1 from XE7A-24/HC manual). New "ICONE Display" section documenting all 33 LCD symbols with HA entity correlations.
- **TemSen sensor** — Added `TemSen` (local controller temperature sensor) as a sensor entity. Confirmed not available via cloud API (always `None`).

### Changed

- **Wiki parameter tables** — Added descriptions, practical examples, and range info for all C00-C23 monitor codes and P01-P87 settings parameters.

## 0.2.0 (2026-07-18)

### Changed

- **MQTT driver rewritten with aiomqtt** — Replaced paho-mqtt (threaded) with aiomqtt (async). Eliminates paho v2 auto-reconnect bugs in threaded HA environments. Connection is now fully async and integrates natively with the HA event loop.
- **Fire-and-forget polling** — `poll_device_sync()` removed. Poll requests are fire-and-forget; responses arrive via the async listener. No more blocking sleep-loops, `_data_seq`, or response queues.
- **Async MQTT callbacks** — `_on_data` is now called from the event loop directly. Removed all `asyncio.run_coroutine_threadsafe` and `async_add_executor_job` wrappers for MQTT operations.
- **Panel Info tab** — New "🔧 Info" tab showing device keys, MACs, MQTT topics, firmware versions, and a "Re-discover from Cloud" button to re-fetch device info from the Gree API.

### Fixed

- **Wrong device key in docs** — Corrected CLAUDE.md: device `580d0d334f3d` uses key `6PWCusP394IfZ8DI` (not `Mi9d7k040l70dP5i`).

## 0.1.0 (2026-07-10)

- Initial release: cloud API authentication, MQTT polling, HA entities (climate, sensors, switches, binary sensors), energy estimation, panel UI.
