# Changelog

## 0.2.0 (2026-07-15)

### Fixed

- **async_set_updated_data not awaited** — The `_forward` callback used `call_soon_threadsafe` to invoke the coroutine, which silently dropped the return value without scheduling it. Same issue in `_sync_data` (climate.py) and the panel command handler. The coordinator was never updated after MQTT responses, so entities always showed default (off) state.
- **Stale cloud IP** — Replaced hardcoded `GREE_CLOUD_IPS` (`3.71.159.59`) with `GREE_CLOUD_SERVERS` hostnames for reliable DNS resolution.

### Added

- **Energy persistence** — Model mappings and accumulated energy are saved to HA Store (`gree_ac_cloud.models`, `gree_ac_cloud.energy.{mac}`), survive restarts.
- **Energy counter freeze on power-off** — Energy no longer resets to 0 when AC turns off, satisfying `TOTAL_INCREASING` contract.
- **Integration icon** — Custom brand icon using Gree blue + white text.
- **Connection retry** — `async_setup_entry` retries 3 times with 5s sleep before raising `ConfigEntryNotReady`.
- **Live log tab** — Log tab in the panel auto-refreshes every 2s, with Copy-all button and auto-scroll.
- **Readme + Changelog tabs** — In-panel documentation tabs.

### Changed

- **MAC format handling** — Normalize MACs to lowercase without colons for consistent MQTT topic matching.
- Code cleanup: removed unused `GREE_CLOUD_IPS`, `DISPATCH_DEVICE_DISCOVERED`, stale SVG icon, invalid `icons.json`.

## 0.1.0 (2026-07-10)

- Initial release: cloud API authentication, MQTT polling, HA entities (climate, sensors, switches, binary sensors), energy estimation, panel UI.
