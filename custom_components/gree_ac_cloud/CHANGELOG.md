# Changelog

## 0.2.14

### Sensori ambiente HA e preset climatici

- Le opzioni dell'integrazione permettono di associare a ogni condizionatore un sensore di temperatura e uno di umidità già presenti in Home Assistant.
- La temperatura esterna scelta sostituisce `TemSen` come `current_temperature` dell'entità climate; `TemSen` resta il fallback. L'umidità scelta viene esposta come `current_humidity`.
- Aggiunti preset configurabili Giorno, Notte e Assente, ciascuno abilitabile separatamente.
- Ogni preset può definire temperatura target, temperatura di spegnimento automatico, soglie minima/massima ambiente, soglia umidità e livello I-Demand.
- I preset abilitati compaiono direttamente nell'entità climate e reagiscono agli aggiornamenti in tempo reale dei sensori HA associati.
- Le soglie sono opzionali: se lasciate vuote non producono alcuna azione automatica.

## 0.2.13

### Preferenze per l'accensione successiva

- Aggiunto il selettore HA persistente `I-Demand all'accensione` con `No action`, `Off`, `D1`, `D2` e `D3` per ogni unità compatibile.
- La preferenza viene applicata a ogni nuova accensione in modalità Cool, sia quando parte da Home Assistant sia quando parte dal monitor/comando a muro.
- `No action` conserva il comportamento del dispositivo; `Off` azzera esplicitamente DRED, mentre D1/D2/D3 applicano il relativo limite.
- La preferenza resta memorizzata dopo riavvii e aggiornamenti di Home Assistant ed è disponibile anche a condizionatore spento.
- Aggiunti gli stessi controlli al pannello personalizzato dell'integrazione.

## 0.2.12

### Controlli I-Demand in Home Assistant

- Il selettore `Livello I-Demand / DRED` è ora abilitato per impostazione predefinita e compare nei controlli del dispositivo in Home Assistant.
- Le entità create dalle versioni precedenti come disabilitate dall'integrazione vengono abilitate automaticamente durante l'aggiornamento; le scelte di disabilitazione effettuate manualmente dall'utente vengono rispettate.
- Il controllo può essere usato dalla UI, dalle dashboard, dalle automazioni e tramite il servizio `select.select_option` con `Off`, `D1`, `D2` o `D3`.
- Rimangono applicate le condizioni verificate del dispositivo: il controllo è disponibile quando l'unità supporta DRED, è accesa ed è in modalità raffrescamento.

## 0.2.11

### Allineamento I-Demand nel pannello

- Il backend espone ora al pannello il livello DRED effettivo già normalizzato, incluso il firmware della zona giorno che riporta D1 come `DRED=0, Idemand=1`.
- Il pannello evidenzia D1 e mostra esplicitamente `Stato effettivo: D1 attivo (I-Demand)` in questo caso.
- La pagina del pannello usa intestazioni `no-cache` e un URL legato alla versione per impedire che Home Assistant conservi il vecchio JavaScript dopo un aggiornamento.
- La normalizzazione accetta valori numerici e stringhe restituiti dai diversi firmware.

## 0.2.10

### DRED, logs, temperatures and estimates

- Normalized the two verified D1 representations (`DRED=1` and `Idemand=1,DRED=0`); the panel shows the separate I-Demand flag and highlights D1 correctly.
- Added D1/D2/D3 descriptions and DRED-aware estimated power.
- Restored the panel's live log capture.
- Reclassified unidentified `InTem`/`OutTem` values as raw diagnostic probes instead of room/outdoor ambient temperatures.
- Climate current temperature now uses only documented `TemSen`; the panel shows unavailable when it is absent and keeps raw probes separate.
- The panel uses persistent backend estimated power/energy and labels both explicitly as estimates rather than relying on a browser-only counter.

## 0.2.9

### U-Match verification

- Added a disabled-by-default `I-Demand / DRED Level` select with Off, D1, D2 and D3.
- Confirmed on both XE7A wired controllers that all levels are available. Firmware can report D1 as either `DRED=1` or `Idemand=1,DRED=0`; both forms are now normalized to D1.
- Added descriptions: D1 disables the compressor, D2 caps demand at 50%, and D3 caps demand at 75%. These are ceilings, not power measurements.
- Confirmed that selecting a DRED level cancels Quiet and that the control is available only while the unit is on in Cool mode.
- Restored panel log capture after reload by setting the component logger level and avoiding duplicate in-memory handlers.
- Renamed `InTem`/`OutTem` as unverified raw IDU/ODU probes; they are no longer presented as actual room/outdoor ambient temperatures.
- Climate current temperature now uses only documented `TemSen` (`raw - 40 °C`) and remains unavailable when the device does not provide it.
- Reworked estimated power to avoid using unidentified temperature probes and to account for DRED demand ceilings. Energy entities are explicitly marked as estimates, not meters.

## 0.2.8

### Security

- All panel data and command APIs now require Home Assistant authentication; mutating endpoints require an administrator.
- Device keys are redacted in the Info tab and API responses.
- Added command, MAC, model, and device-name validation and hardened dynamic panel rendering.
- Enabled certificate verification for the Cloud API and MQTT broker.
- Kept dependency ranges compatible with Home Assistant's Python environment.

### U-Match documentation

- Analysed the supplied XE7A-24/HC and U-Match 6 manuals and added an U-Match feature matrix to the custom panel.
- Corrected `Blo` to X-Fan/coil drying and `Air` to optional fresh-air control.
- Added documented external-static-pressure P30 tables as read-only installer reference.
- Corrected nominal energy-estimation data for GUD35, GUD50 and GUD85.
- Added read-only diagnostics for error code/type, refrigerant warnings, system status, Auto Clean status and filter counters when reported by the device.
- The Devices panel now displays available U-Match diagnostic values without enabling unverified write commands.

### Fixed

- Fixed the coordinator forward annotation that could prevent the integration from importing.
- Fixed invalid `await` calls on `async_set_updated_data()`.
- Added MQTT reconnect with exponential backoff and accurate connection status.
- Command publishing no longer counts as a real device response for staleness tracking.
- Staleness timeout now follows the configurable poll interval and no longer forces an unconfirmed OFF state.
- Energy integration uses a monotonic session clock and no longer counts Home Assistant downtime.
- Panel registration and coordinator data now support multiple config entries safely.
- Device discovery now includes all homes in the Gree account.

## 0.2.7 (2026-07-29)

### Fixed

- **send_command updates staleness timer** — `send_command()` now updates `_last_seen` on successful publish, preventing the coordinator staleness check from immediately reverting `Pow=0` after a turn-on command or extra parameter toggle.
- **Staleness only resets Pow if previously ON** — The coordinator now only sets `Pow=0` on stale data when the device was previously ON (`Pow=1`). If already OFF, stale data is left untouched.
- **Cipher reset on key update** — `GreeDevice._cipher` is reset when the device key is updated via re-authentication, ensuring new keys are used immediately.

### Added

- **Re-authenticate & Update Keys** — New button in the 🔧 Info tab that re-fetches device keys from the Gree Cloud API, updates running devices, and shows old → new key changes in a table.

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

- **Wrong device key in docs** — Corrected CLAUDE.md: device `REDACTED_DEVICE_IDENTIFIER` uses key `REDACTED_DEVICE_KEY` (not `REDACTED_DEVICE_KEY`).

## 0.1.0 (2026-07-10)

- Initial release: cloud API authentication, MQTT polling, HA entities (climate, sensors, switches, binary sensors), energy estimation, panel UI.
