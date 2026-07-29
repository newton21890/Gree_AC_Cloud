# Changelog

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
