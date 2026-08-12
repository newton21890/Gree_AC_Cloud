"""Gree AC Cloud panel.

Registers a sidebar panel in HA with a custom web interface
for monitoring and controlling Gree cloud VRF devices.
"""

from __future__ import annotations

import json
import logging
import re
from collections import deque
from datetime import datetime

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from .const import (
    COMMAND_OPTIONS,
    CONF_ACTUAL_ENERGY_SENSOR,
    CONF_ACTUAL_POWER_SENSOR,
    CONF_DEVICES,
    CONF_HUMIDITY_SENSOR,
    CONF_HUMIDITY_SENSORS,
    CONF_OUTDOOR_HUMIDITY_SENSOR,
    CONF_OUTDOOR_TEMPERATURE_SENSOR,
    CONF_PRESET_ADAPTIVE,
    CONF_PRESET_ALLOWED_MODES,
    CONF_PRESET_AUTO_OFF,
    CONF_PRESET_DEADBAND,
    CONF_PRESET_DRED,
    CONF_PRESET_ENABLED,
    CONF_PRESET_FAN,
    CONF_PRESET_HUMIDITY,
    CONF_PRESET_MAX_TEMP,
    CONF_PRESET_MIN_TEMP,
    CONF_PRESET_MODE,
    CONF_PRESET_QUIET,
    CONF_PRESET_SMART,
    CONF_PRESET_TARGET,
    CONF_PRESETS,
    CONF_PROFILE_CONTROL_ENABLED,
    CONF_TEMPERATURE_SENSOR,
    CONF_TEMPERATURE_SENSORS,
    DOMAIN,
    ENERGY_MODELS,
    GREE_CLOUD_SERVERS,
    GREE_MQTT_HOSTS,
    GREE_MQTT_PORTS,
    PRESET_DRED_ALIASES,
    PRESET_FAN_ALIASES,
    STORAGE_KEY_INSTALLATIONS,
    STORAGE_KEY_MODELS,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

PANEL_URL = "/api/gree_ac_cloud/panel"
PANEL_DATA_URL = "/api/gree_ac_cloud/panel/data"
PANEL_CMD_URL = "/api/gree_ac_cloud/panel/command"
PANEL_LOG_URL = "/api/gree_ac_cloud/panel/log"
PANEL_ACTION_LOG_URL = "/api/gree_ac_cloud/panel/action-log"
PANEL_README_URL = "/api/gree_ac_cloud/panel/readme"
PANEL_CHANGELOG_URL = "/api/gree_ac_cloud/panel/changelog"
PANEL_ROOM_SENSORS_URL = "/api/gree_ac_cloud/panel/room-sensors"
PANEL_ENERGY_SENSORS_URL = "/api/gree_ac_cloud/panel/energy-sensors"
PANEL_INSTALLATION_URL = "/api/gree_ac_cloud/panel/installation"


def _safe_json_for_script(value) -> str:
    """Serialize JSON without allowing a value to terminate the script tag."""
    return json.dumps(value).replace("<", "\\u003c")


def _valid_mac(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9A-Fa-f]{12,14}", value) is not None


def _redact_secret(value: str) -> str:
    if not value:
        return ""
    return f"{value[:4]}…{value[-4:]}"


def _is_admin(request: web.Request) -> bool:
    user = request.get("hass_user")
    return bool(user and user.is_admin)


def _all_coordinators(hass: HomeAssistant) -> list:
    entries = hass.data.get(DOMAIN, {}).get("entries", {})
    return [coordinator for coordinators in entries.values() for coordinator in coordinators]


# ── In-memory log capture ─────────────────────────────


class _GreeLogHandler(logging.Handler):
    def __init__(self, maxlen: int = 200):
        super().__init__()
        self.logs: deque[dict] = deque(maxlen=maxlen)
        self.setLevel(logging.DEBUG)

    def emit(self, record: logging.LogRecord):
        self.logs.append(
            {
                "t": datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
                "l": record.levelname,
                "m": record.getMessage(),
            }
        )


_log_handler = _GreeLogHandler()
_logger_root = logging.getLogger("custom_components.gree_ac_cloud")
# Keep the component logger verbose enough for the panel's in-memory handler.
# The handler is private to this component and does not change HA's root level.
_logger_root.setLevel(logging.DEBUG)
if not any(isinstance(handler, _GreeLogHandler) for handler in _logger_root.handlers):
    _logger_root.addHandler(_log_handler)

# ── Cached file content ──────────────────────────────
import os as _os

_README_CACHE = "# README\n(file not found)"
_CHANGELOG_CACHE = "# Changelog\n(file not found)"
_VERSION_CACHE = "0.0.0"
_PANEL_HISTORY_JS = ""
_PANEL_PROFILES_JS = ""
_APEXCHARTS_JS = ""
_PANEL_ASSETS_LOADED = False
_changelog_path = _os.path.join(_os.path.dirname(__file__), "CHANGELOG.md")
_readme_path = _os.path.join(_os.path.dirname(__file__), "README.md")
_manifest_path = _os.path.join(_os.path.dirname(__file__), "manifest.json")
_panel_history_js_path = _os.path.join(_os.path.dirname(__file__), "frontend", "panel_history.js")
_panel_profiles_js_path = _os.path.join(_os.path.dirname(__file__), "frontend", "panel_profiles.js")
_apexcharts_js_path = _os.path.join(_os.path.dirname(__file__), "frontend", "apexcharts.min.js")


def _load_panel_assets_sync() -> None:
    """Load packaged panel assets outside Home Assistant's event loop."""
    global _APEXCHARTS_JS
    global _CHANGELOG_CACHE
    global _PANEL_ASSETS_LOADED
    global _PANEL_HISTORY_JS
    global _PANEL_PROFILES_JS
    global _README_CACHE
    global _VERSION_CACHE

    try:
        with open(_readme_path, encoding="utf-8") as file:
            _README_CACHE = file.read()
    except OSError:
        _LOGGER.exception("Unable to load panel README")
    try:
        with open(_changelog_path, encoding="utf-8") as file:
            _CHANGELOG_CACHE = file.read()
    except OSError:
        _LOGGER.exception("Unable to load panel changelog")
    try:
        with open(_manifest_path, encoding="utf-8") as file:
            _VERSION_CACHE = json.load(file).get("version", "0.0.0")
    except (OSError, ValueError):
        _LOGGER.exception("Unable to load integration manifest")
    try:
        with open(_panel_history_js_path, encoding="utf-8") as file:
            _PANEL_HISTORY_JS = file.read()
    except OSError:
        _LOGGER.exception("Unable to load panel history frontend module")
    try:
        with open(_panel_profiles_js_path, encoding="utf-8") as file:
            _PANEL_PROFILES_JS = file.read()
    except OSError:
        _LOGGER.exception("Unable to load panel profiles frontend module")
    try:
        with open(_apexcharts_js_path, encoding="utf-8") as file:
            _APEXCHARTS_JS = file.read()
    except OSError:
        _LOGGER.exception("Unable to load packaged ApexCharts module")
    _PANEL_ASSETS_LOADED = True


async def async_register_panel(hass: HomeAssistant):
    """Register the sidebar panel and API views once."""
    from homeassistant.components import frontend

    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get("panel_registered"):
        return
    if not _PANEL_ASSETS_LOADED:
        await hass.async_add_executor_job(_load_panel_assets_sync)

    # HTTP routes cannot be unregistered. Keep them for the HA process lifetime
    # and only recreate the sidebar item after an integration reload.
    if not domain_data.get("panel_views_registered"):
        hass.http.register_view(GreePanelView)
        hass.http.register_view(GreePanelDataView)
        from .panel_history import GreePanelHistoryView

        hass.http.register_view(GreePanelHistoryView)
        from .panel_profiles import GreePanelProfileView

        hass.http.register_view(GreePanelProfileView)
        hass.http.register_view(GreePanelCommandView)
        hass.http.register_view(GreePanelLogView)
        hass.http.register_view(GreePanelActionLogView)
        hass.http.register_view(GreePanelModelsView)
        hass.http.register_view(GreePanelInstallationView)
        hass.http.register_view(GreePanelNamesView)
        hass.http.register_view(GreePanelSettingsView)
        hass.http.register_view(GreePanelRefreshView)
        hass.http.register_view(GreePanelDevicesInfoView)
        hass.http.register_view(GreePanelRoomSensorsView)
        hass.http.register_view(GreePanelEnergySensorsView)
        domain_data["panel_views_registered"] = True

    domain_data["panel_registered"] = True
    if "frontend" in hass.config.components:
        try:
            frontend.async_register_built_in_panel(
                hass,
                component_name="iframe",
                sidebar_title="Gree AC Cloud",
                sidebar_icon="mdi:air-conditioner",
                frontend_url_path="gree-ac-cloud",
                # Version the iframe URL so HA clients do not retain an old
                # panel shell after an integration update.
                config={"url": f"{PANEL_URL}?v={_VERSION_CACHE}"},
                require_admin=True,
            )
            _LOGGER.info("Panel registered in sidebar")
        except ValueError:
            _LOGGER.debug("Panel gree-ac-cloud already registered")


async def async_unregister_panel(hass: HomeAssistant):
    """Remove the panel."""
    from homeassistant.components import frontend

    frontend.async_remove_panel(hass, "gree-ac-cloud")
    hass.data.get(DOMAIN, {}).pop("panel_registered", None)


# ── Views ─────────────────────────────────────────────


class GreePanelView(HomeAssistantView):
    """Serve the non-sensitive panel shell; all data APIs require auth."""

    url = PANEL_URL
    name = "api:gree_ac_cloud:panel"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        html = PANEL_HTML
        html = html.replace("__README_JSON__", _safe_json_for_script(_README_CACHE))
        html = html.replace("__CHANGELOG_JSON__", _safe_json_for_script(_CHANGELOG_CACHE))
        html = html.replace("__VERSION__", _VERSION_CACHE)
        html = html.replace("__DEVICE_NAMES_JSON__", "{}")
        html = html.replace("__APEXCHARTS_JS__", _APEXCHARTS_JS)
        html = html.replace("__PANEL_HISTORY_JS__", _PANEL_HISTORY_JS)
        html = html.replace("__PANEL_PROFILES_JS__", _PANEL_PROFILES_JS)
        return web.Response(
            text=html,
            content_type="text/html",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
            },
        )


class GreePanelDataView(HomeAssistantView):
    """Returns device data as JSON for the panel."""

    url = PANEL_DATA_URL
    name = "api:gree_ac_cloud:panel_data"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        data = []
        for entry in hass.config_entries.async_entries(DOMAIN):
            runtime = getattr(entry, "runtime_data", None)
            if not runtime:
                continue
            server = entry.data.get("server", "Europe")
            mqtt_host = GREE_MQTT_HOSTS.get(server, "mqtt-eu.gree.com")
            cloud_host = GREE_CLOUD_SERVERS.get(server, "eugrih.gree.com")
            coordinators = runtime.get("coordinators", [])
            for coord in coordinators:
                device = coord.device
                state = dict(coord.data or device.properties or {})
                try:
                    raw_dred = int(state.get("DRED", 0))
                except (TypeError, ValueError):
                    raw_dred = 0
                try:
                    idemand_active = int(state.get("Idemand", 0)) == 1
                except (TypeError, ValueError):
                    idemand_active = False
                # Some firmware exposes D1 through Idemand instead of DRED.
                # Send the normalized value explicitly to avoid UI ambiguity.
                state["DREDEffective"] = 1 if raw_dred == 0 and idemand_active else raw_dred
                state["IdemandActive"] = idemand_active
                state["StartupDRED"] = coord.startup_dred
                room = entry.options.get(CONF_DEVICES, {}).get(device.mac, {})

                def _average(entity_ids):
                    values = []
                    for entity_id in entity_ids:
                        sensor_state = hass.states.get(entity_id)
                        if sensor_state is None or sensor_state.state in (
                            "unknown",
                            "unavailable",
                        ):
                            continue
                        try:
                            values.append(float(sensor_state.state))
                        except (TypeError, ValueError):
                            continue
                    return round(sum(values) / len(values), 2) if values else None

                temperature_ids = room.get(CONF_TEMPERATURE_SENSORS) or (
                    [room[CONF_TEMPERATURE_SENSOR]] if room.get(CONF_TEMPERATURE_SENSOR) else []
                )
                humidity_ids = room.get(CONF_HUMIDITY_SENSORS) or (
                    [room[CONF_HUMIDITY_SENSOR]] if room.get(CONF_HUMIDITY_SENSOR) else []
                )
                state["RoomTemperature"] = _average(temperature_ids)
                state["RoomHumidity"] = _average(humidity_ids)
                state["RoomTemperatureSensors"] = temperature_ids
                state["RoomHumiditySensors"] = humidity_ids
                raw_in_tem = state.get("InTem")
                raw_tem_sen = state.get("TemSen")
                raw_in_humi = state.get("InHumi")
                state["InTemEnableRaw"] = state.get("InTemEn")
                state["InHumiEnableRaw"] = state.get("InHumiEn")
                state["InTemEnabled"] = isinstance(raw_in_tem, (int, float)) and raw_in_tem != 0
                state["TemSenEnabled"] = isinstance(raw_tem_sen, (int, float)) and raw_tem_sen != 0
                state["InHumiEnabled"] = (
                    state.get("InHumiEn") == 1
                    and isinstance(raw_in_humi, (int, float))
                    and 0 < raw_in_humi <= 100
                )
                state["RoomTemperatureEnabled"] = state["RoomTemperature"] is not None
                state["RoomHumidityEnabled"] = state["RoomHumidity"] is not None
                state["Presets"] = room.get(CONF_PRESETS, {})
                outdoor_entity = entry.options.get(CONF_OUTDOOR_TEMPERATURE_SENSOR)
                outdoor_state = hass.states.get(outdoor_entity) if outdoor_entity else None
                try:
                    state["OutdoorTemperature"] = (
                        float(outdoor_state.state)
                        if outdoor_state and outdoor_state.state not in ("unknown", "unavailable")
                        else None
                    )
                except (TypeError, ValueError):
                    state["OutdoorTemperature"] = None
                outdoor_humidity_entity = entry.options.get(CONF_OUTDOOR_HUMIDITY_SENSOR)
                outdoor_humidity_state = (
                    hass.states.get(outdoor_humidity_entity) if outdoor_humidity_entity else None
                )
                try:
                    state["OutdoorHumidity"] = (
                        float(outdoor_humidity_state.state)
                        if outdoor_humidity_state
                        and outdoor_humidity_state.state not in ("unknown", "unavailable")
                        else None
                    )
                except (TypeError, ValueError):
                    state["OutdoorHumidity"] = None
                registry = er.async_get(hass)
                climate_entity_id = registry.async_get_entity_id(
                    "climate", DOMAIN, f"climate_{device.mac}"
                )
                climate_state = hass.states.get(climate_entity_id) if climate_entity_id else None
                state["ClimateEntityId"] = climate_entity_id
                state["ActivePreset"] = (
                    climate_state.attributes.get("preset_mode") if climate_state else None
                )
                if climate_state:
                    state["ClimateTargetTemperature"] = climate_state.attributes.get("temperature")
                    for key in (
                        "smart_profile_active",
                        "smart_manual_power_override",
                        "smart_last_action",
                        "smart_fan_speed",
                        "smart_work_curve",
                        "smart_dred_level",
                        "smart_dred_applied",
                        "smart_dred_verified",
                        "profile_control_enabled",
                        "smart_effective_target",
                        "smart_temperature_trend_c_per_hour",
                        "smart_temperature_trend_samples",
                        "smart_unmet_minutes",
                        "smart_stall_boost",
                    ):
                        state[key] = climate_state.attributes.get(key)
                state["Installation"] = (
                    hass.data.get(DOMAIN, {}).get("installations", {}).get(device.mac, {})
                )
                for option, state_key, entity_key in (
                    (CONF_ACTUAL_POWER_SENSOR, "ActualPowerW", "ActualPowerSensor"),
                    (CONF_ACTUAL_ENERGY_SENSOR, "ActualEnergyKWh", "ActualEnergySensor"),
                ):
                    entity_id = room.get(option)
                    state[entity_key] = entity_id
                    sensor_state = hass.states.get(entity_id) if entity_id else None
                    value = None
                    if sensor_state and sensor_state.state not in ("unknown", "unavailable"):
                        try:
                            value = float(sensor_state.state)
                            unit = sensor_state.attributes.get("unit_of_measurement")
                            if option == CONF_ACTUAL_POWER_SENSOR and unit == "kW":
                                value *= 1000
                            elif option == CONF_ACTUAL_ENERGY_SENSOR and unit == "Wh":
                                value /= 1000
                        except (TypeError, ValueError):
                            value = None
                    state[state_key] = value
                state["EstimatedBaselinePowerW"] = state.get("estimated_baseline_power_w")
                state["EstimatedSavingPowerW"] = state.get("estimated_saving_power_w")
                data.append(
                    {
                        "mac": device.mac,
                        "name": device.name,
                        "connected": (
                            coord._mqtt.connected
                            and coord._mqtt.seconds_since_last_seen(device.mac) is not None
                        )
                        if hasattr(coord, "_mqtt")
                        else False,
                        "state": state,
                        "server": server,
                        "mqtt_host": mqtt_host,
                        "cloud_host": cloud_host,
                    }
                )
        return self.json(data)


class GreePanelEnergySensorsView(HomeAssistantView):
    """Associate real HA power and energy meters with each Gree unit."""

    url = PANEL_ENERGY_SENSORS_URL
    name = "api:gree_ac_cloud:panel_energy_sensors"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        sensors = []
        for state in hass.states.async_all("sensor"):
            device_class = state.attributes.get("device_class")
            if device_class not in ("power", "energy"):
                continue
            sensors.append(
                {
                    "entity_id": state.entity_id,
                    "name": state.attributes.get("friendly_name", state.entity_id),
                    "device_class": device_class,
                    "state": state.state,
                    "unit": state.attributes.get("unit_of_measurement"),
                }
            )
        devices = []
        for entry in hass.config_entries.async_entries(DOMAIN):
            runtime = getattr(entry, "runtime_data", None) or {}
            configured = entry.options.get(CONF_DEVICES, {})
            for coordinator in runtime.get("coordinators", []):
                device = coordinator.device
                room = configured.get(device.mac, {})
                devices.append(
                    {
                        "entry_id": entry.entry_id,
                        "mac": device.mac,
                        "name": device.name,
                        "actual_power_sensor": room.get(CONF_ACTUAL_POWER_SENSOR),
                        "actual_energy_sensor": room.get(CONF_ACTUAL_ENERGY_SENSOR),
                    }
                )
        sensors.sort(key=lambda sensor: sensor["name"].lower())
        return self.json({"devices": devices, "sensors": sensors})

    async def post(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        if not _is_admin(request):
            return self.json({"error": "admin required"}, status=403)
        try:
            body = await request.json()
        except Exception:
            return self.json({"error": "invalid JSON"}, status=400)
        entry_id = body.get("entry_id")
        mac = body.get("mac")
        power_sensor = body.get("actual_power_sensor") or None
        energy_sensor = body.get("actual_energy_sensor") or None
        if not entry_id or not _valid_mac(mac):
            return self.json({"error": "invalid device"}, status=400)
        for entity_id, expected_class in (
            (power_sensor, "power"),
            (energy_sensor, "energy"),
        ):
            if not entity_id:
                continue
            state = hass.states.get(entity_id)
            if state is None or state.attributes.get("device_class") != expected_class:
                return self.json({"error": f"invalid {expected_class} sensor"}, status=400)
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            return self.json({"error": "entry not found"}, status=404)
        runtime = getattr(entry, "runtime_data", None) or {}
        if not any(coord.device.mac == mac for coord in runtime.get("coordinators", [])):
            return self.json({"error": "device not found"}, status=404)
        devices = dict(entry.options.get(CONF_DEVICES, {}))
        room = dict(devices.get(mac, {}))
        if power_sensor:
            room[CONF_ACTUAL_POWER_SENSOR] = power_sensor
        else:
            room.pop(CONF_ACTUAL_POWER_SENSOR, None)
        if energy_sensor:
            room[CONF_ACTUAL_ENERGY_SENSOR] = energy_sensor
        else:
            room.pop(CONF_ACTUAL_ENERGY_SENSOR, None)
        devices[mac] = room
        entry.runtime_data["skip_next_options_reload"] = True
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_DEVICES: devices}
        )
        for coordinator in runtime.get("coordinators", []):
            coordinator.async_update_listeners()
        return self.json({"ok": True})


class GreePanelRoomSensorsView(HomeAssistantView):
    """Read and update external HA room sensors without relying on Options UI."""

    url = PANEL_ROOM_SENSORS_URL
    name = "api:gree_ac_cloud:panel_room_sensors"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        sensors = []
        for state in hass.states.async_all("sensor"):
            device_class = state.attributes.get("device_class")
            if device_class in ("temperature", "humidity"):
                sensors.append(
                    {
                        "entity_id": state.entity_id,
                        "name": state.attributes.get("friendly_name", state.entity_id),
                        "device_class": device_class,
                        "state": state.state,
                        "unit": state.attributes.get("unit_of_measurement"),
                    }
                )
        devices = []
        for entry in hass.config_entries.async_entries(DOMAIN):
            runtime = getattr(entry, "runtime_data", None) or {}
            configured = entry.options.get(CONF_DEVICES, {})
            outdoor = entry.options.get(CONF_OUTDOOR_TEMPERATURE_SENSOR)
            outdoor_humidity = entry.options.get(CONF_OUTDOOR_HUMIDITY_SENSOR)
            for coord in runtime.get("coordinators", []):
                room = configured.get(coord.device.mac, {})
                devices.append(
                    {
                        "entry_id": entry.entry_id,
                        "mac": coord.device.mac,
                        "name": coord.device.name,
                        "temperature_sensors": room.get(CONF_TEMPERATURE_SENSORS)
                        or (
                            [room[CONF_TEMPERATURE_SENSOR]]
                            if room.get(CONF_TEMPERATURE_SENSOR)
                            else []
                        ),
                        "humidity_sensors": room.get(CONF_HUMIDITY_SENSORS)
                        or ([room[CONF_HUMIDITY_SENSOR]] if room.get(CONF_HUMIDITY_SENSOR) else []),
                        "outdoor_temperature_sensor": outdoor,
                        "outdoor_humidity_sensor": outdoor_humidity,
                        "profile_control_enabled": room.get(CONF_PROFILE_CONTROL_ENABLED, True),
                        "presets": room.get(CONF_PRESETS, {}),
                    }
                )
        return self.json({"devices": devices, "sensors": sensors})

    async def post(self, request: web.Request) -> web.Response:
        if not _is_admin(request):
            return self.json({"error": "admin required"}, status=403)
        hass = request.app["hass"]
        try:
            body = await request.json()
        except Exception:
            return self.json({"error": "invalid JSON"}, status=400)
        entry_id = body.get("entry_id")
        mac = body.get("mac")
        temperatures = body.get("temperature_sensors", [])
        humidities = body.get("humidity_sensors", [])
        outdoor = body.get("outdoor_temperature_sensor") or None
        outdoor_humidity = body.get("outdoor_humidity_sensor") or None
        presets = body.get("presets")
        profile_control = body.get("profile_control_enabled")
        if not entry_id or not _valid_mac(mac):
            return self.json({"error": "invalid device"}, status=400)
        if not isinstance(temperatures, list) or not isinstance(humidities, list):
            return self.json({"error": "sensor selections must be lists"}, status=400)
        for entity_ids, expected_class in (
            (temperatures, "temperature"),
            (humidities, "humidity"),
            ([outdoor] if outdoor else [], "temperature"),
            ([outdoor_humidity] if outdoor_humidity else [], "humidity"),
        ):
            for entity_id in entity_ids:
                state = hass.states.get(entity_id)
                if state is None or state.attributes.get("device_class") != expected_class:
                    return self.json({"error": f"invalid {expected_class} sensor"}, status=400)
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            return self.json({"error": "entry not found"}, status=404)
        devices = dict(entry.options.get(CONF_DEVICES, {}))
        room = dict(devices.get(mac, {}))
        room[CONF_TEMPERATURE_SENSORS] = temperatures
        room[CONF_HUMIDITY_SENSORS] = humidities
        if profile_control is not None:
            room[CONF_PROFILE_CONTROL_ENABLED] = bool(profile_control)
        if presets is not None:
            if not isinstance(presets, dict):
                return self.json({"error": "invalid presets"}, status=400)
            clean_presets = {}
            for name in ("day", "night", "away"):
                preset = presets.get(name, {})
                if not isinstance(preset, dict):
                    return self.json({"error": "invalid preset"}, status=400)
                clean_presets[name] = {
                    CONF_PRESET_ENABLED: bool(preset.get(CONF_PRESET_ENABLED)),
                    CONF_PRESET_TARGET: float(preset.get(CONF_PRESET_TARGET, 26)),
                    CONF_PRESET_DRED: (
                        PRESET_DRED_ALIASES.get(preset.get(CONF_PRESET_DRED))
                        or preset.get(CONF_PRESET_DRED)
                        or "No action"
                    ),
                    CONF_PRESET_SMART: bool(preset.get(CONF_PRESET_SMART, True)),
                    CONF_PRESET_MODE: preset.get(CONF_PRESET_MODE, "auto"),
                    CONF_PRESET_ALLOWED_MODES: [
                        mode
                        for mode in preset.get(CONF_PRESET_ALLOWED_MODES, [])
                        if mode in ("cool", "heat", "dry")
                    ],
                    CONF_PRESET_ADAPTIVE: bool(preset.get(CONF_PRESET_ADAPTIVE, True)),
                    CONF_PRESET_FAN: (
                        PRESET_FAN_ALIASES.get(preset.get(CONF_PRESET_FAN))
                        or preset.get(CONF_PRESET_FAN)
                        or "Smart"
                    ),
                    CONF_PRESET_QUIET: bool(preset.get(CONF_PRESET_QUIET, False)),
                }
                for key in (
                    CONF_PRESET_AUTO_OFF,
                    CONF_PRESET_HUMIDITY,
                    CONF_PRESET_MIN_TEMP,
                    CONF_PRESET_MAX_TEMP,
                    CONF_PRESET_DEADBAND,
                ):
                    value = preset.get(key)
                    if value not in (None, ""):
                        clean_presets[name][key] = float(value)
            room[CONF_PRESETS] = clean_presets
        room.pop(CONF_TEMPERATURE_SENSOR, None)
        room.pop(CONF_HUMIDITY_SENSOR, None)
        devices[mac] = room
        new_options = {**entry.options, CONF_DEVICES: devices}
        if outdoor:
            new_options[CONF_OUTDOOR_TEMPERATURE_SENSOR] = outdoor
        else:
            new_options.pop(CONF_OUTDOOR_TEMPERATURE_SENSOR, None)
        if outdoor_humidity:
            new_options[CONF_OUTDOOR_HUMIDITY_SENSOR] = outdoor_humidity
        else:
            new_options.pop(CONF_OUTDOOR_HUMIDITY_SENSOR, None)
        hass.config_entries.async_update_entry(entry, options=new_options)
        return self.json({"ok": True})


class GreePanelCommandView(HomeAssistantView):
    """Receives commands from the panel."""

    url = PANEL_CMD_URL
    name = "api:gree_ac_cloud:panel_command"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        if not _is_admin(request):
            return self.json({"error": "admin required"}, status=403)
        try:
            body = await request.json()
        except Exception:
            return self.json({"error": "invalid JSON"}, status=400)

        mac = body.get("mac")
        options = body.get("options", [])
        values = body.get("values", [])
        has_startup_dred = "startup_dred" in body
        reset_runtime = body.get("reset_runtime") is True

        if not mac or (not has_startup_dred and not reset_runtime and (not options or not values)):
            return self.json({"error": "missing command data"}, status=400)

        for entry in hass.config_entries.async_entries(DOMAIN):
            runtime = entry.runtime_data if hasattr(entry, "runtime_data") else {}
            mqtt = runtime.get("mqtt")
            coordinators = runtime.get("coordinators", [])
            if mqtt:
                for coord in coordinators:
                    if coord.device.mac == mac:
                        if not _valid_mac(mac):
                            return self.json({"error": "invalid mac"}, status=400)
                        if reset_runtime:
                            await coord.async_reset_runtime()
                            _LOGGER.info("Runtime counter reset for %s", mac)
                            return self.json({"ok": True})
                        if has_startup_dred:
                            startup_dred = body.get("startup_dred")
                            if isinstance(startup_dred, bool) or startup_dred not in (
                                None,
                                0,
                                1,
                                2,
                                3,
                            ):
                                return self.json({"error": "invalid startup DRED"}, status=400)
                            await coord.async_set_startup_dred(startup_dred)
                            return self.json({"ok": True})
                        if (
                            not isinstance(options, list)
                            or not isinstance(values, list)
                            or len(options) != len(values)
                            or len(options) > 20
                            or any(opt not in COMMAND_OPTIONS for opt in options)
                            or any(
                                isinstance(value, bool)
                                or not isinstance(value, (int, float))
                                or not -100 <= value <= 1000
                                for value in values
                            )
                        ):
                            return self.json({"error": "invalid command"}, status=400)
                        ok = await mqtt.send_command(
                            mac,
                            options,
                            values,
                            source="panel_manual",
                            action="panel_command",
                        )
                        if ok:
                            for opt, val in zip(options, values):
                                coord.device.properties[opt] = val
                            coord.async_set_updated_data(dict(coord.device.properties))
                            if any(opt in ("InTemEn", "InHumiEn") for opt in options):
                                await mqtt.refresh_device(
                                    mac,
                                    [
                                        "InTemEn",
                                        "TemSen",
                                        "InTem",
                                        "InHumiEn",
                                        "InHumi",
                                    ],
                                )
                            if any(opt == "Pow" and val == 1 for opt, val in zip(options, values)):
                                await coord.async_apply_startup_settings()
                        return self.json({"ok": ok})

        return self.json({"error": "device not found"}, status=404)


class GreePanelActionLogView(HomeAssistantView):
    """Return and manage the persistent operating-action audit trail."""

    url = PANEL_ACTION_LOG_URL
    name = "api:gree_ac_cloud:panel_action_log"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        action_log = hass.data.get(DOMAIN, {}).get("action_log")
        if action_log is None:
            return self.json([])
        mac = request.query.get("mac") or None
        source = request.query.get("source") or None
        try:
            limit = int(request.query.get("limit", 500))
        except (TypeError, ValueError):
            limit = 500
        return self.json(action_log.entries(mac=mac, source=source, limit=limit))

    async def delete(self, request: web.Request) -> web.Response:
        if not _is_admin(request):
            return self.json({"error": "admin required"}, status=403)
        hass = request.app["hass"]
        action_log = hass.data.get(DOMAIN, {}).get("action_log")
        if action_log is None:
            return self.json({"ok": True, "removed": 0})
        mac = request.query.get("mac") or None
        if mac and not _valid_mac(mac):
            return self.json({"error": "invalid mac"}, status=400)
        removed = await action_log.async_clear(mac)
        return self.json({"ok": True, "removed": removed})


class GreePanelLogView(HomeAssistantView):
    """Returns recent integration logs."""

    url = PANEL_LOG_URL
    name = "api:gree_ac_cloud:panel_log"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        return self.json(list(_log_handler.logs))


class GreePanelModelsView(HomeAssistantView):
    """Get/set device model mappings for energy estimation."""

    url = "/api/gree_ac_cloud/panel/models"
    name = "api:gree_ac_cloud:panel_models"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        models = hass.data.get(DOMAIN, {}).get("models", {})
        return self.json(models)

    async def post(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        if not _is_admin(request):
            return self.json({"error": "admin required"}, status=403)
        try:
            body = await request.json()
        except Exception:
            return self.json({"error": "invalid JSON"}, status=400)
        mac = body.get("mac")
        model = body.get("model", "")
        if not _valid_mac(mac):
            return self.json({"error": "invalid mac"}, status=400)
        if model and model not in ENERGY_MODELS:
            return self.json({"error": "invalid model"}, status=400)
        hass.data.setdefault(DOMAIN, {}).setdefault("models", {})
        if model:
            hass.data[DOMAIN]["models"][mac] = model
        else:
            hass.data[DOMAIN]["models"].pop(mac, None)
        store = Store(hass, STORAGE_VERSION, STORAGE_KEY_MODELS)
        await store.async_save(hass.data[DOMAIN].get("models", {}))
        _LOGGER.info("Model set: %s → %s", mac, model or "(unset)")
        return self.json({"ok": True, "mac": mac, "model": model})


class GreePanelInstallationView(HomeAssistantView):
    """Persist descriptive ducted-installation data without reloading MQTT."""

    url = PANEL_INSTALLATION_URL
    name = "api:gree_ac_cloud:panel_installation"
    requires_auth = True

    FIELDS = {
        "static_pressure_pa": (float, 0, 300),
        "static_pressure_level": (int, 1, 9),
        "main_duct_length_m": (float, 0, 500),
        "total_duct_length_m": (float, 0, 2000),
        "served_rooms": (int, 1, 100),
        "supply_outlets": (int, 1, 200),
        "return_grilles": (int, 1, 100),
        "duct_diameter_mm": (float, 0, 2000),
        "duct_section_cm2": (float, 0, 100000),
    }
    CHOICES = {
        "duct_type": {"rigid", "flexible", "mixed", "other"},
        "supply_outlet_type": {"grille", "diffuser", "slot", "mixed", "other"},
        "return_grille_type": {"grille", "filter_grille", "mixed", "other"},
        "filter_type": {"none", "standard", "high_efficiency", "other"},
    }

    async def get(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        return self.json(hass.data.get(DOMAIN, {}).get("installations", {}))

    async def post(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        if not _is_admin(request):
            return self.json({"error": "admin required"}, status=403)
        try:
            body = await request.json()
        except Exception:
            return self.json({"error": "invalid JSON"}, status=400)
        mac = body.get("mac")
        if not _valid_mac(mac):
            return self.json({"error": "invalid mac"}, status=400)

        clean = {}
        for field, (cast, minimum, maximum) in self.FIELDS.items():
            value = body.get(field)
            if value in (None, ""):
                continue
            try:
                number = cast(value)
            except (TypeError, ValueError):
                return self.json({"error": f"invalid {field}"}, status=400)
            if isinstance(value, bool) or not minimum <= number <= maximum:
                return self.json({"error": f"invalid {field}"}, status=400)
            clean[field] = number
        for field, choices in self.CHOICES.items():
            value = body.get(field)
            if value in (None, ""):
                continue
            if value not in choices:
                return self.json({"error": f"invalid {field}"}, status=400)
            clean[field] = value
        notes = body.get("notes", "")
        if not isinstance(notes, str) or len(notes) > 1000:
            return self.json({"error": "invalid notes"}, status=400)
        if notes.strip():
            clean["notes"] = notes.strip()

        hass.data.setdefault(DOMAIN, {}).setdefault("installations", {})
        if clean:
            hass.data[DOMAIN]["installations"][mac] = clean
        else:
            hass.data[DOMAIN]["installations"].pop(mac, None)
        store = Store(hass, STORAGE_VERSION, STORAGE_KEY_INSTALLATIONS)
        await store.async_save(hass.data[DOMAIN]["installations"])
        _LOGGER.info("Installation profile updated for %s", mac)
        return self.json({"ok": True, "mac": mac, "installation": clean})


class GreePanelNamesView(HomeAssistantView):
    """Get/set device custom names."""

    url = "/api/gree_ac_cloud/panel/names"
    name = "api:gree_ac_cloud:panel_names"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        names = hass.data.get(DOMAIN, {}).get("device_names", {})
        return self.json(names)

    async def post(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        if not _is_admin(request):
            return self.json({"error": "admin required"}, status=403)
        try:
            body = await request.json()
        except Exception:
            return self.json({"error": "invalid JSON"}, status=400)
        mac = body.get("mac")
        name = body.get("name", "")
        if not _valid_mac(mac):
            return self.json({"error": "invalid mac"}, status=400)
        if not isinstance(name, str):
            return self.json({"error": "invalid name"}, status=400)
        name = name.strip()
        if len(name) > 64:
            return self.json({"error": "name too long"}, status=400)
        hass.data.setdefault(DOMAIN, {}).setdefault("device_names", {})
        if name:
            hass.data[DOMAIN]["device_names"][mac] = name
        else:
            hass.data[DOMAIN]["device_names"].pop(mac, None)
        store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.names")
        await store.async_save(hass.data[DOMAIN].get("device_names", {}))
        _LOGGER.info("Device name set: %s → %s", mac, name or "(unset)")
        return self.json({"ok": True, "mac": mac, "name": name})


class GreePanelSettingsView(HomeAssistantView):
    """Get/set panel settings."""

    url = "/api/gree_ac_cloud/panel/settings"
    name = "api:gree_ac_cloud:panel_settings"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        settings = hass.data.get(DOMAIN, {}).get("settings", {"update_interval": 15})
        return self.json(settings)

    async def post(self, request: web.Request) -> web.Response:
        from datetime import timedelta

        hass = request.app["hass"]
        if not _is_admin(request):
            return self.json({"error": "admin required"}, status=403)
        try:
            body = await request.json()
        except Exception:
            return self.json({"error": "invalid JSON"}, status=400)

        interval = body.get("update_interval")
        if (
            isinstance(interval, bool)
            or not isinstance(interval, (int, float))
            or not 5 <= interval <= 300
        ):
            return self.json({"error": "invalid interval"}, status=400)

        interval = int(interval)
        hass.data.setdefault(DOMAIN, {}).setdefault("settings", {})
        hass.data[DOMAIN]["settings"]["update_interval"] = interval
        for coord in _all_coordinators(hass):
            coord.update_interval = timedelta(seconds=interval)
        _LOGGER.info("Poll interval changed to %ds", interval)

        store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.settings")
        await store.async_save(hass.data[DOMAIN]["settings"])
        return self.json({"ok": True, "update_interval": interval})


class GreePanelRefreshView(HomeAssistantView):
    """Triggers immediate data refresh."""

    url = "/api/gree_ac_cloud/panel/refresh"
    name = "api:gree_ac_cloud:panel_refresh"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        if not _is_admin(request):
            return self.json({"error": "admin required"}, status=403)
        coordinators = _all_coordinators(hass)
        for coord in coordinators:
            await coord.async_request_refresh()
        return self.json({"ok": True, "refreshed": len(coordinators)})


class GreePanelDevicesInfoView(HomeAssistantView):
    """Returns raw device info (keys, MACs, etc.) from the cloud API."""

    url = "/api/gree_ac_cloud/panel/devices-info"
    name = "api:gree_ac_cloud:panel_devices_info"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        result = []
        for entry in hass.config_entries.async_entries(DOMAIN):
            runtime = getattr(entry, "runtime_data", None)
            if not runtime:
                continue
            uid = runtime.get("uid", "?")
            mqtt = runtime.get("mqtt")
            server = entry.data.get("server", "Europe")
            mqtt_host = GREE_MQTT_HOSTS.get(server, "?")
            mqtt_port = GREE_MQTT_PORTS.get(server, 1984)
            cloud_host = GREE_CLOUD_SERVERS.get(server, "?")

            devices_info = []
            coordinators = runtime.get("coordinators", [])
            for coord in coordinators:
                dev = coord.device
                devices_info.append(
                    {
                        "mac": dev.mac,
                        "name": dev.name,
                        "key": _redact_secret(dev.key),
                        "parent_mac": dev.parent_mac,
                        "hid": dev.hid,
                        "mqtt_topic_request": f"request/{dev.parent_mac}",
                        "mqtt_topic_status": f"status/{dev.parent_mac}/#",
                        "mqtt_topic_response": f"response/{dev.parent_mac}/#",
                        "properties_count": len(dev.properties) if dev.properties else 0,
                        "connected": mqtt.connected if hasattr(mqtt, "connected") else False,
                    }
                )

            result.append(
                {
                    "uid": uid,
                    "server_region": server,
                    "cloud_host": cloud_host,
                    "mqtt_host": mqtt_host,
                    "mqtt_port": mqtt_port,
                    "devices": devices_info,
                }
            )

        return self.json(result)

    async def post(self, request: web.Request) -> web.Response:
        """Re-discover devices from cloud API and update keys on running integration."""
        from .gree_api import discover_devices

        hass = request.app["hass"]
        if not _is_admin(request):
            return self.json({"error": "admin required"}, status=403)
        for entry in hass.config_entries.async_entries(DOMAIN):
            server = entry.data.get("server", "Europe")
            username = entry.data.get("username", "")
            password = entry.data.get("password", "")
            cloud_host = GREE_CLOUD_SERVERS.get(server, "eugrih.gree.com")

            try:
                uid, token, devices = await hass.async_add_executor_job(
                    discover_devices, cloud_host, username, password
                )

                # Update keys on running MQTT devices (same objects as coordinators)
                runtime = getattr(entry, "runtime_data", None)
                mqtt = runtime.get("mqtt") if runtime else None
                key_changes = []
                for d in devices:
                    existing = mqtt.devices.get(d.mac) if mqtt else None
                    if existing and existing.key != d.key:
                        old_key = existing.key
                        existing.key = d.key
                        existing._cipher = None  # force cipher re-creation with new key
                        key_changes.append(
                            {
                                "mac": d.mac,
                                "name": d.name,
                                "old_key": _redact_secret(old_key),
                                "new_key": _redact_secret(d.key),
                            }
                        )
                        _LOGGER.info("Re-auth: key updated for %s (%s)", d.mac, d.name)

                if mqtt:
                    mqtt.uid = uid
                    mqtt.token = token
                if runtime:
                    runtime["uid"] = uid

                result = {
                    "uid": uid,
                    "token": "updated",
                    "server_region": server,
                    "cloud_host": cloud_host,
                    "key_changes": key_changes,
                    "devices": [
                        {
                            "mac": d.mac,
                            "name": d.name,
                            "key": _redact_secret(d.key),
                            "parent_mac": d.parent_mac,
                            "hid": d.hid,
                        }
                        for d in devices
                    ],
                }

                if key_changes:
                    _LOGGER.info(
                        "Re-auth: %d key(s) updated: %s",
                        len(key_changes),
                        ", ".join(c["mac"] for c in key_changes),
                    )
                else:
                    _LOGGER.info("Re-auth: no key changes detected")

                return self.json(result)
            except Exception as exc:
                return self.json({"error": str(exc)}, status=500)

        return self.json({"error": "No integration found"}, status=404)


# ── Panel HTML ────────────────────────────────────────

PANEL_HTML = r"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Gree AC Cloud</title>
<style>
:root {
  --primary: #03a9f4;
  --primary-glow: rgba(3, 169, 244, 0.25);
  --green: #4caf50;
  --red: #ef5350;
  --yellow: #ffa726;
  --mode-cool: #29b6f6;
  --mode-heat: #ef5350;
  --mode-fan: #66bb6a;
  --mode-dry: #ffa726;
  --mode-auto: #b0bec5;
  --bg: #0f1117;
  --card-bg: linear-gradient(145deg, #1a1d27, #14171f);
  --card-border: rgba(255,255,255,0.06);
  --text: #e8eaed;
  --text2: #9aa0a6;
  --border: rgba(255,255,255,0.08);
}

* { margin:0; padding:0; box-sizing:border-box; }

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: var(--bg);
  color: var(--text);
  padding: 12px;
  min-height: 100vh;
  overflow-x: hidden;
  max-width: 100vw;
}

/* Keyboard focus must stay visible on the custom dark theme. */
:focus-visible {
  outline: 2px solid var(--primary);
  outline-offset: 2px;
  border-radius: 4px;
}

/* ── header ─────────────────────────────────── */
.header {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 0;
  border-bottom: 1px solid var(--border);
  margin-bottom: 14px;
}
.header-top {
  display: flex;
  align-items: center;
  gap: 8px;
}
.header .icon-ac { font-size: 26px; color: var(--primary); flex-shrink: 0; }
.icon-ac { width:1em; height:1em; display:inline-block; vertical-align:middle; }
.icon-ac svg { width:100%; height:100%; fill:currentColor; }
.header h1 { font-size: 17px; font-weight: 600; letter-spacing: -0.3px; flex-shrink: 0; }
.header .status-badge {
  margin-left: auto;
  font-size: 10px;
  padding: 3px 10px;
  border-radius: 20px;
  background: var(--green);
  color: #0b1515;
  font-weight: 600;
  white-space: nowrap;
}

/* ── tab nav ─────────────────────────────────── */
.tab-nav {
  display: flex;
  gap: 4px;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  scrollbar-width: none;
}
.tab-nav::-webkit-scrollbar { display: none; }
.header-controls { display: flex; align-items: center; gap: 8px; margin: 4px 0; }
.interval-label { font-size: 10px; color: var(--text2); display: flex; align-items: center; gap: 4px; }
.interval-label select { font-size: 10px; padding: 2px 4px; border-radius: 4px; background: rgba(255,255,255,0.05); border: 1px solid var(--border); color: var(--text); }
.refresh-btn { font-size: 16px; padding: 2px 10px; border-radius: 6px; background: rgba(3,169,244,0.15); border: 1px solid rgba(3,169,244,0.3); color: var(--primary); cursor: pointer; }
.refresh-btn:hover { background: rgba(3,169,244,0.25); }
body.desktop .header-controls { margin: 6px 0 4px; }
.tab-btn {
  flex-shrink: 0;
  padding: 7px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: transparent;
  color: var(--text2);
  cursor: pointer;
  font-size: 11px;
  font-weight: 500;
  transition: all .2s;
  white-space: nowrap;
  -webkit-tap-highlight-color: transparent;
}
.tab-btn:active { background: rgba(255,255,255,0.08); }
.tab-btn.active { background: var(--primary); border-color: var(--primary); color: #0b1515; }

/* ── device cards ────────────────────────────── */
.devices { display: grid; gap: 12px; }

.card {
  position: relative;
  background: var(--card-bg);
  border-radius: 14px;
  padding: 14px;
  border: 1px solid var(--card-border);
  box-shadow: 0 2px 16px rgba(0,0,0,0.3);
  transition: box-shadow .3s;
  max-width: 100%;
  overflow: hidden;
}
.card.on { box-shadow: 0 2px 16px rgba(0,0,0,0.3), 0 0 30px rgba(3,169,244,0.04); }
.card.on::before {
  content: ''; position: absolute; inset: 0;
  border-radius: 14px;
  background: linear-gradient(135deg, rgba(3,169,244,0.04), transparent 60%);
  pointer-events: none;
}

/* ── card header ──────────────────────────── */
.card-header { margin-bottom: 8px; }
.header-row1 {
  display: flex; align-items: center; gap: 6px;
  margin-bottom: 2px;
}
.header-row1 .name-group {
  display: flex; align-items: center; gap: 6px;
  flex: 1; min-width: 0;
}
.header-row1 .name-group .icon-ac { font-size: 18px; color: var(--primary); flex-shrink: 0; }
.header-row1 h2 {
  font-size: 14px; font-weight: 600;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  flex: 1; cursor: pointer;
}
.header-row1 h2:hover { text-decoration: underline dotted var(--text2); }
.header-row1 .conn-badge {
  font-size: 9px; padding: 2px 8px; border-radius: 10px;
  background: rgba(76,175,80,0.12); color: var(--green); font-weight: 600;
  white-space: nowrap; flex-shrink: 0;
}
.header-row1 .conn-badge.off { background: rgba(239,83,80,0.12); color: var(--red); }
.header-row2 {
  display: flex; align-items: center; gap: 8px;
  padding-left: 24px;
}
.header-row2 .mac-label { font-size: 9px; color: var(--text2); }
body.desktop .card-header { display: flex; align-items: center; gap: 4px; }
body.desktop .header-row1 { flex: 1; min-width: 0; margin-bottom: 0; }
body.desktop .header-row1 .name-group { flex-wrap: wrap; }
body.desktop .header-row1 h2 { flex: initial; max-width: 100%; }
body.desktop .header-row2 { padding-left: 0; }

.model-select {
  font-size: 9px; padding: 3px 6px; border-radius: 6px;
  background: rgba(255,255,255,0.05); border: 1px solid var(--border);
  color: var(--text2); cursor: pointer;
  min-width: 160px; width: auto;
}
@media (min-width: 768px) {
  .model-select { min-width: 200px; }
}
.model-select option { background: #1a1d27; color: var(--text); }

/* ── power display ───────────────────────────── */
.power-row {
  display: flex; gap: 8px; margin: 8px 0 10px;
  font-size: 10px; color: var(--text2); justify-content: center;
  max-width: 100%;
}
.power-row .p-item {
  flex: 1; min-width: 0;
  padding: 5px 6px; background: rgba(255,255,255,0.03);
  border-radius: 8px; border: 1px solid var(--border);
  text-align: center;
}
.power-row .p-item .p-val { font-size: 13px; font-weight: 700; color: var(--yellow); }
.power-row .p-item .p-label { font-size: 8px; text-transform: uppercase; letter-spacing: 0.3px; }

/* ── sensors ─────────────────────────────────── */
.sensors {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px;
  margin-bottom: 12px;
  max-width: 100%;
}
.sensor {
  text-align: center;
  padding: 8px 4px;
  background: rgba(255,255,255,0.03);
  border-radius: 10px;
  border: 1px solid var(--border);
}
.sensor .value { font-size: 18px; font-weight: 700; }
.sensor .value.green { color: var(--green); }
.sensor .value.red { color: var(--red); }
.sensor .label { font-size: 9px; color: var(--text2); margin-top: 2px; text-transform: uppercase; letter-spacing: 0.3px; }

/* ── controls ────────────────────────────────── */
.controls { display: grid; gap: 8px; }

.control-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.control-row label {
  width: 100%;
  font-size: 10px;
  color: var(--text2);
  text-transform: uppercase;
  letter-spacing: 0.4px;
  font-weight: 600;
  padding-bottom: 2px;
}

.btn-group { display: flex; gap: 3px; flex-wrap: wrap; max-width: 100%; }

.btn {
  min-height: 36px;
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: transparent;
  color: var(--text2);
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
  transition: all .15s;
  -webkit-tap-highlight-color: transparent;
}
.btn:active { background: rgba(255,255,255,0.08); }
.btn.active {
  background: var(--primary);
  border-color: var(--primary);
  color: #0b1515;
  box-shadow: 0 0 10px var(--primary-glow);
}
.btn.danger.active { background: var(--red); border-color: var(--red); box-shadow: 0 0 10px rgba(239,83,80,0.3); }
.btn.mode-cool.active { background: var(--mode-cool); border-color: var(--mode-cool); }
.btn.mode-heat.active { background: var(--mode-heat); border-color: var(--mode-heat); }
.btn.mode-fan.active { background: var(--mode-fan); border-color: var(--mode-fan); }
.btn.mode-dry.active { background: var(--mode-dry); border-color: var(--mode-dry); }

.temp-control {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  max-width: 100%;
}
.temp-control button {
  width: 40px; height: 40px;
  border-radius: 50%;
  border: 1px solid var(--border);
  background: rgba(255,255,255,0.04);
  color: var(--text);
  font-size: 18px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all .15s;
  -webkit-tap-highlight-color: transparent;
}
.temp-control button:active { background: rgba(255,255,255,0.1); border-color: var(--primary); }
.temp-control .temp-value {
  font-size: 22px;
  font-weight: 700;
  min-width: 48px;
  text-align: center;
}

.switches {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  max-width: 100%;
}
.switch-btn {
  min-height: 32px;
  padding: 6px 10px;
  border-radius: 12px;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--text2);
  font-size: 10px;
  cursor: pointer;
  transition: all .15s;
  -webkit-tap-highlight-color: transparent;
}
.switch-btn.on {
  background: rgba(3,169,244,0.12);
  border-color: var(--primary);
  color: var(--primary);
}
.switch-btn:active { opacity: 0.7; }

/* ── clearer control dashboard ── */
.dashboard-summary {
  display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px;
  margin:12px 0;
}
.summary-tile {
  background:rgba(255,255,255,.035); border:1px solid var(--border);
  border-radius:12px; padding:12px; text-align:center;
}
.summary-tile .summary-value { font-size:22px; font-weight:650; color:var(--text); }
.summary-tile .summary-label { font-size:10px; color:var(--text2); margin-top:3px; }
.control-section { border-top:1px solid var(--border); padding:14px 0 2px; }
.control-section:first-child { border-top:0; }
.section-title { font-size:12px; text-transform:uppercase; letter-spacing:.7px; color:var(--text2); margin-bottom:10px; }
.compact-details { border-top:1px solid var(--border); margin-top:12px; padding-top:10px; }
.compact-details summary { cursor:pointer; color:var(--text2); font-size:12px; }
.preset-quick { display:flex; gap:6px; flex-wrap:wrap; }
.state-line { font-size:11px; color:var(--text2); margin-top:7px; }
@media (max-width: 520px) { .dashboard-summary { grid-template-columns:repeat(2,minmax(0,1fr)); } }

/* ── setup message ───────────────────────────── */
.setup-msg {
  text-align: center;
  padding: 40px 16px;
  color: var(--text2);
}
.setup-msg .icon-ac { font-size: 48px; margin-bottom: 12px; }
.setup-msg h2 { margin-bottom: 6px; color: var(--text); font-size: 16px; }

/* ── wiki tab ────────────────────────────────── */
.wiki { font-size: 12px; }
.wiki h3 { font-size: 14px; color: var(--primary); margin: 16px 0 6px; }
.wiki h4 { font-size: 12px; margin: 10px 0 4px; color: var(--text); }
.wiki table.wt { display:block; width:100%; border-collapse:collapse; font-size:11px; overflow-x:auto; -webkit-overflow-scrolling:touch; }
.wiki table.wt th, .wiki table.wt td { text-align:left; padding:5px 6px; border-bottom:1px solid var(--border); }
.wiki table.wt th { background:rgba(255,255,255,0.05); color:var(--primary); font-weight:500; white-space:nowrap; }
.wiki table.wt td:first-child { font-family:monospace; white-space:nowrap; }
.wiki code { background:rgba(255,255,255,0.08); padding:1px 4px; border-radius:3px; font-size:10px; }

/* ── logs tab ────────────────────────────────── */
.log-toolbar { display:flex; gap:6px; margin-bottom:8px; align-items:center; flex-wrap:wrap; }
.log-toggle { font-size:11px; color:var(--text2); display:flex; align-items:center; gap:4px; cursor:pointer; }
#logCount { font-size:10px; color:var(--text2); margin-left:auto; white-space:nowrap; }
#logContainer,#actionLogContainer { font-size:10px; font-family:monospace; line-height:1.5; max-height:60vh; overflow-y:auto; }
.action-log-entry { display:grid; grid-template-columns:145px 85px 120px minmax(180px,1fr) 70px; gap:9px; padding:7px 8px; border-bottom:1px solid #202b3a; align-items:center; }
.action-log-entry:hover { background:#111a26; }
.action-source { color:#67d7f0; font-weight:700; }
.action-device { color:#9aa9bd; }
.action-result-sent,.action-result-recorded { color:#52d68a; }
.action-result-failed { color:#ff6b6b; }
@media (max-width:720px) { .action-log-entry { grid-template-columns:1fr auto; } .action-device,.action-changes { grid-column:1/-1; } }
.log-entry { padding: 3px 4px; border-bottom: 1px solid var(--border); white-space: pre-wrap; word-break: break-all; }
.log-entry .log-time { color: var(--text2); margin-right: 4px; }
.log-entry .log-debug { color: #666; }
.log-entry .log-info { color: var(--primary); }
.log-entry .log-warning { color: var(--yellow); }
.log-entry .log-error { color: var(--red); }
.log-entry .log-critical { color: var(--red); font-weight: bold; }

/* ── markdown content (readme/changelog) ──────── */
.md-content {
  font-size: 12px; line-height: 1.6; color: var(--text);
}
.md-content h1 { font-size: 18px; font-weight: 600; margin: 16px 0 6px; color: var(--primary); }
.md-content h2 { font-size: 15px; font-weight: 600; margin: 14px 0 5px; color: var(--primary); }
.md-content h3 { font-size: 13px; font-weight: 600; margin: 12px 0 4px; }
.md-content h4 { font-size: 12px; font-weight: 600; margin: 10px 0 4px; color: var(--yellow); }
.md-content p { margin: 5px 0; }
.md-content a { color: var(--primary); text-decoration: none; word-break: break-all; }
.md-content code {
  background: rgba(255,255,255,0.07); padding: 1px 4px; border-radius: 3px; font-size: 11px;
}
.md-content pre {
  background: rgba(0,0,0,0.3); border: 1px solid var(--border); border-radius: 8px;
  padding: 10px; overflow-x: auto; margin: 6px 0;
}
.md-content pre code { background: none; padding: 0; font-size: 11px; }
.md-content table { width: 100%; border-collapse: collapse; margin: 6px 0; font-size: 11px; }
.md-content th, .md-content td {
  text-align: left; padding: 4px 6px; border-bottom: 1px solid var(--border);
}
.md-content th { background: rgba(255,255,255,0.05); color: var(--primary); font-weight: 500; }
.md-content tr:nth-child(even) { background: rgba(255,255,255,0.02); }
.md-content ul, .md-content ol { margin: 5px 0; padding-left: 18px; }
.md-content li { margin: 2px 0; }
.md-content hr { border: none; border-top: 1px solid var(--border); margin: 12px 0; }
.md-content blockquote {
  border-left: 3px solid var(--primary); padding: 4px 10px; margin: 6px 0;
  background: rgba(3,169,244,0.04); color: var(--text2); border-radius: 0 6px 6px 0;
}
.md-content img { max-width: 100%; border-radius: 6px; margin: 6px 0; height: auto; }

/* ── footer ──────────────────────────────────── */
.server-info {
  font-size: 11px;
  color: var(--text2);
  margin-top: 16px;
  text-align: center;
  padding-top: 12px;
  border-top: 1px solid var(--border);
}

/* ── desktop overrides (via JS class for iframe safety) ── */
body.desktop .header { flex-direction: row; align-items: center; gap: 12px; padding: 16px 0; margin-bottom: 20px; }
body.desktop .control-row label { width: auto; min-width: 60px; padding-bottom: 0; }

/* ── tablet / desktop enhancements ───────────── */
@media (min-width: 600px) {
  body { padding: 20px; }
  .header { flex-direction: row; align-items: center; gap: 12px; padding: 16px 0; margin-bottom: 20px; }
  .header h1 { font-size: 20px; }
  .header .icon-ac { font-size: 30px; }
  .tab-nav { gap: 6px; }
  .tab-btn { padding: 8px 16px; font-size: 12px; }
  .card { padding: 20px; }
  .card-header { margin-bottom: 14px; }
  .header-row1 h2 { font-size: 15px; }
  .sensors { gap: 8px; margin-bottom: 16px; }
  .sensor .value { font-size: 22px; }
  .control-row { gap: 8px; }
  .control-row label { width: auto; min-width: 60px; padding-bottom: 0; }
  .btn-group { gap: 4px; }
  .btn { min-height: 32px; padding: 5px 12px; }
  .temp-control button { width: 36px; height: 36px; font-size: 16px; }
  .power-row { gap: 12px; }
  .power-row .p-item { padding: 6px 14px; }
  .power-row .p-item .p-val { font-size: 15px; }
  .setup-msg { padding: 60px 20px; }
  .setup-msg .icon-ac { font-size: 64px; }
  #logContainer,#actionLogContainer { font-size:11px; max-height:70vh; }
  .md-content { font-size: 13px; }
  .md-content h1 { font-size: 20px; }
}
/* ── Gree Control operations interface ───────── */
:root {
  --primary:#22d3ee; --primary-glow:rgba(34,211,238,.18);
  --green:#34d399; --red:#fb7185; --yellow:#fb923c;
  --bg:#090d14; --card-bg:#111722; --card-border:#263142;
  --text:#f1f5f9; --text2:#8290a5; --border:#263142;
}
body { background:var(--bg); color:var(--text); font-size:14px; padding:0; }
.app-shell { min-height:100vh; display:grid; grid-template-columns:220px minmax(0,1fr); align-items:start; }
.header,
body.desktop .header {
  position:sticky; top:0; z-index:20; width:220px; height:100vh; margin:0; padding:18px 14px;
  border:0; border-right:1px solid var(--border); border-radius:0;
  background:#0c111a; display:flex; flex-direction:column; align-items:stretch; gap:0;
}
.header-top { padding:6px 8px 24px; margin:0; gap:10px; }
.header .icon-ac { width:32px; height:32px; padding:0; border-radius:9px; color:#06262d; background:var(--primary); display:grid; place-items:center; line-height:0; }
.header .icon-ac svg { display:block; width:18px; height:18px; margin:auto; }
.header h1 { font-size:16px; font-weight:800; }
.header .status-badge { margin:0; padding:0; background:none !important; color:var(--text2); font-size:9px; font-weight:500; line-height:1.2; }
.tab-nav { order:2; display:grid; gap:4px; overflow:visible; }
.tab-nav::before { content:'NAVIGAZIONE'; padding:4px 12px 5px; color:#57657a; font-size:9px; font-weight:900; letter-spacing:.15em; }
.tab-btn { display:flex; align-items:center; gap:11px; justify-content:flex-start; width:100%; min-height:42px; border:1px solid transparent; border-radius:9px; padding:0 12px; color:#8c99ae; font-size:12px; font-weight:600; }
.nav-icon,.sidebar-action-icon { width:17px; height:17px; flex:0 0 17px; display:grid; place-items:center; }
.mobile-menu-button,.mobile-menu-scrim { display:none; }
.mobile-menu-button { width:42px; height:42px; padding:0; border:1px solid #2b394e; border-radius:9px; background:#151d29; color:#d8e5f5; cursor:pointer; place-items:center; }
.mobile-menu-button svg { width:23px; height:23px; fill:none; stroke:currentColor; stroke-width:2; stroke-linecap:round; }
.nav-icon svg,.sidebar-action-icon svg { width:100%; height:100%; fill:none; stroke:currentColor; stroke-width:1.8; stroke-linecap:round; stroke-linejoin:round; }
.nav-icon svg rect { fill:none; }
.tab-btn.active { color:#d9fbff; background:#152733; border-color:#1d4753; box-shadow:inset 3px 0 0 var(--primary); }
.header-controls,
body.desktop .header-controls { order:3; margin:0; margin-top:auto; padding:14px 8px 0; border-top:1px solid var(--border); display:grid; align-items:stretch; gap:8px; }
.sidebar-connection { display:flex; align-items:center; gap:9px; min-height:38px; padding:0 10px; border-radius:8px; background:#0f1722; }
.connection-dot { width:8px; height:8px; border-radius:50%; background:var(--green); box-shadow:0 0 0 3px rgba(52,211,153,.12); }
.sidebar-connection div { min-width:0; display:grid; line-height:1.2; }
.sidebar-connection strong { color:var(--text); font-size:10px; font-weight:700; }
.sidebar-connection small { color:var(--text2); font-size:9px; }
.interval-label { justify-content:space-between; min-height:36px; padding-left:10px; font-size:10px; }
.interval-label select { min-width:65px; min-height:32px; padding:0 8px; border:1px solid var(--border); border-radius:7px; background:#111722; }
.refresh-btn { display:flex; align-items:center; justify-content:flex-start; gap:10px; width:100%; min-height:40px; padding:0 11px; border:1px solid var(--border); border-radius:9px; background:#111722; color:var(--text); font-size:11px; font-weight:700; }
.refresh-btn:hover { color:#d9fbff; border-color:#315167; background:#15212f; }
.refresh-action { color:#aef6ff; border-color:#215563; background:#11313a; }
button:focus-visible, select:focus-visible, summary:focus-visible { outline:2px solid var(--primary); outline-offset:2px; }
#content { width:100%; max-width:none; min-width:0; margin:0; padding:20px 24px 46px; }
.ops-page-head { display:flex; justify-content:space-between; align-items:center; gap:20px; margin-bottom:18px; }
.ops-page-head h2 { margin:0; font-size:24px; letter-spacing:-.035em; }
.ops-page-head p { margin:4px 0 0; color:var(--text2); font-size:12px; }
.ops-overview { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:10px; margin-bottom:18px; }
.ops-kpi { min-width:0; padding:13px; border:1px solid var(--border); border-radius:11px; background:#111722; }
.ops-kpi span { display:block; color:var(--text2); font-size:9px; letter-spacing:.07em; white-space:nowrap; }
.ops-kpi b { display:block; margin-top:4px; font-size:21px; overflow:hidden; text-overflow:ellipsis; }
.ops-kpi small { display:block; margin-top:2px; color:var(--green); font-size:9px; }
.devices { display:grid; gap:12px; }
.card { padding:0; overflow:hidden; border:1px solid var(--border); border-radius:13px; background:#111722; box-shadow:none; }
.card.on { border-color:#2c3b4f; box-shadow:none; }
.card-header { padding:14px 16px; border-bottom:1px solid var(--border); background:#101620; }
.header-row1 h2 { font-size:14px; }
.header-row1 .conn-badge { color:var(--green); background:#11322a; border-radius:8px; font-size:9px; }
.header-row2 select { max-width:180px; background:#151d29; border-color:var(--border); }
.card-body { padding:0; }
.ops-unit-layout { display:grid; grid-template-columns:240px minmax(410px,1fr) 265px; }
.ops-reading,.ops-controls,.ops-telemetry { min-width:0; padding:16px; }
.ops-reading,.ops-controls { border-right:1px solid var(--border); }
.ops-reading-label,.ops-section-label { color:var(--text2); font-size:10px; letter-spacing:.09em; text-transform:uppercase; }
.ops-room-temp { margin:15px 0 10px; font-size:45px; line-height:1; font-weight:700; letter-spacing:-.06em; }
.ops-reading-grid { display:grid; grid-template-columns:repeat(2,1fr); gap:10px; margin-top:16px; }
.ops-mini { padding:10px; border:1px solid #202b3a; border-radius:8px; background:#0e151f; }
.ops-mini b { display:block; font-size:14px; }
.ops-mini span { color:var(--text2); font-size:10px; }
.ops-power-row { display:flex; justify-content:space-between; align-items:center; min-height:38px; }
.ops-state { color:var(--green); font-size:10px; font-weight:800; }
.ops-power { width:42px; height:42px; display:grid; place-items:center; flex:0 0 42px; padding:0; border:1px solid #256978; border-radius:10px; background:#143942; color:var(--primary); cursor:pointer; }
.ops-power-icon { width:22px; height:22px; display:block; fill:none; stroke:currentColor; stroke-width:2.25; stroke-linecap:round; stroke-linejoin:round; }
.card:not(.on) .ops-power { color:#798597; border-color:var(--border); background:#171d27; }
.ops-target { display:flex; justify-content:space-between; align-items:center; gap:12px; padding:15px 0 12px; }
.temp-control { margin-left:0; }
.temp-control .temp-value { min-width:55px; font-size:24px; }
.temp-control .temp-btn { width:34px; height:34px; border-color:var(--border); border-radius:8px; background:#151d29; }
.ops-modes { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:5px; }
.ops-modes .btn { min-height:47px; padding:5px 2px; border-color:var(--border); border-radius:8px; background:#151d29; color:#8490a1; font-size:10px; }
.ops-modes .btn.active { color:#aef6ff; border-color:#28778a; background:#11353e; box-shadow:none; }
.ops-presets { display:flex; gap:5px; margin-top:10px; }
.ops-presets .btn { flex:1; min-height:32px; border-radius:7px; font-size:10px; }
.ops-presets .btn.active { color:#10151c; border-color:var(--yellow); background:var(--yellow); box-shadow:none; }
.ops-alerts { display:flex; gap:6px; flex-wrap:wrap; margin:10px 0 0; }
.ops-alert { padding:5px 8px; border-radius:7px; border:1px solid rgba(255,193,7,.4); background:rgba(255,193,7,.12); color:#ffd966; font-size:9px; font-weight:800; text-transform:uppercase; }
.ops-alert.manual { border-color:rgba(3,169,244,.45); background:rgba(3,169,244,.12); color:#7dd3fc; }
.ops-chart { margin-top:12px; padding:10px; border:1px solid var(--border); border-radius:10px; background:#0b111a; }
.ops-chart svg { width:100%; height:210px; display:block; overflow:visible; }
.chart-panel.control-chart { margin-top:12px; padding:12px 10px 8px; }
.chart-panel.control-chart:not(.apex-chart-panel) svg { height:230px; }
.control-chart-loading .chart-empty { height:190px; }
.ops-chart-legend { display:flex; gap:14px; flex-wrap:wrap; margin-bottom:8px; font-size:10px; color:#a7b3c5; }
.ops-chart-legend span { display:inline-flex; align-items:center; gap:5px; }
.ops-chart-legend i { width:18px; height:3px; display:inline-block; border-radius:4px; }
.charts-grid { display:grid; grid-template-columns:1fr; gap:18px; max-width:1500px; margin:0 auto; }
.chart-toolbar { display:flex; align-items:center; justify-content:space-between; gap:12px; margin:0 auto 14px; max-width:1500px; padding:10px 12px; border:1px solid var(--border); border-radius:11px; background:#111722; }
.chart-periods,.chart-navigation { display:flex; align-items:center; gap:5px; flex-wrap:wrap; }
.chart-toolbar button { min-height:30px; padding:5px 10px; border:1px solid #2b394e; border-radius:7px; background:#151d29; color:#9cabc0; cursor:pointer; font-size:9px; font-weight:800; }
.chart-toolbar button:hover:not(:disabled),.chart-toolbar button.active { border-color:#2b8294; background:#12343d; color:#bdf7ff; }
.chart-toolbar button:disabled { opacity:.35; cursor:not-allowed; }
.chart-window-label { min-width:190px; color:#aab8cb; text-align:center; font-size:10px; }
.chart-recorder-note { color:#6f8199; font-size:9px; }
.chart-loading { padding:28px; color:#8292a8; text-align:center; font-size:10px; }
.chart-detail-card { padding:20px; border:1px solid #253247; border-radius:16px; background:linear-gradient(145deg,#121a27,#0f1621); box-shadow:0 12px 32px rgba(0,0,0,.2); }
.chart-detail-card h3 { margin:0 0 4px; font-size:17px; }
.chart-detail-card > p { margin:0 0 16px; color:var(--text2); font-size:11px; }
.chart-panels { display:grid; grid-template-columns:minmax(0,3fr) minmax(300px,2fr); gap:14px; }
.chart-panel { min-width:0; padding:14px 14px 10px; border:1px solid #202d40; border-radius:12px; background:#0b111a; }
.chart-panel-header { display:flex; align-items:flex-start; justify-content:space-between; gap:10px; margin-bottom:6px; }
.chart-panel-title { color:#eef6ff; font-size:12px; font-weight:800; letter-spacing:.02em; }
.chart-panel-subtitle { display:block; margin-top:2px; color:#718097; font-size:10px; font-weight:500; }
.ops-chart-plot { position:relative; }
.chart-panel:not(.apex-chart-panel) svg { width:100%; height:clamp(280px,34vw,390px); display:block; touch-action:pan-y; }
.chart-panel:not(.apex-chart-panel).humidity svg { height:clamp(280px,34vw,390px); }
.apex-chart-panel { overflow:hidden; }
.apex-chart-host { width:100%; min-height:230px; }
.apex-chart-panel .apexcharts-canvas { width:100% !important; touch-action:pan-y; }
.apex-chart-panel .apexcharts-svg { width:100% !important; }
.chart-missing-note { margin-top:6px; padding:5px 8px; border-radius:6px; background:rgba(255,167,38,.06); border:1px dashed rgba(255,167,38,.35); color:#c9a35f; font-size:10px; line-height:1.5; }
.apexcharts-canvas,.apexcharts-svg { background:transparent !important; }
.apexcharts-tooltip,.apexcharts-xaxistooltip { border-color:#42536c !important; background:rgba(13,20,31,.97) !important; color:#eef6ff !important; box-shadow:0 8px 30px rgba(0,0,0,.45) !important; }
.apexcharts-tooltip-title { border-bottom-color:#34445a !important; background:#151e2b !important; }
.apexcharts-menu { border-color:#34445a !important; background:#111925 !important; }
.apexcharts-menu-item:hover { background:#18313a !important; }
.apexcharts-toolbar svg { fill:#8290a5 !important; }
.apexcharts-toolbar .apexcharts-selected svg { fill:#22d3ee !important; }
.chart-grid-line { stroke:#223047; stroke-width:1; vector-effect:non-scaling-stroke; }
.chart-axis-line { stroke:#52627a; stroke-width:1; vector-effect:non-scaling-stroke; }
.chart-axis-label { fill:#8290a5; font-size:11px; font-weight:600; }
.chart-series { fill:none; stroke-width:2.4; stroke-linecap:round; stroke-linejoin:round; vector-effect:non-scaling-stroke; }
.chart-series.target { stroke-dasharray:8 6; stroke-width:2; }
.chart-series.outdoor { stroke-dasharray:2 5; }
.chart-area { opacity:.12; }
.chart-point-group { cursor:pointer; outline:none; }
.chart-point-hit { fill:transparent; pointer-events:all; }
.chart-point { pointer-events:none; stroke:#0b111a; stroke-width:2; vector-effect:non-scaling-stroke; transition:r .12s ease,stroke .12s ease; }
.chart-point-group:hover .chart-point,.chart-point-group:focus .chart-point { r:7; stroke:#fff; }
.chart-tooltip { position:absolute; z-index:4; display:none; min-width:150px; padding:9px 11px; border:1px solid #42536c; border-radius:9px; background:rgba(13,20,31,.97); box-shadow:0 8px 30px rgba(0,0,0,.45); color:#eef6ff; font-size:11px; pointer-events:none; transform:translate(-50%,calc(-100% - 12px)); }
.chart-tooltip.visible { display:block; }
.chart-tooltip b { display:block; margin-bottom:3px; font-size:12px; }
.chart-tooltip small { color:#96a5ba; }
.chart-empty { display:grid; place-items:center; height:250px; color:#75849a; font-size:11px; }
.chart-detail-card.expanded { position:fixed; inset:10px; z-index:1100; overflow:auto; background:#0d141f; box-shadow:0 20px 80px #000; }
.chart-detail-card.expanded .chart-panel:not(.apex-chart-panel) svg { height:calc(100vh - 275px); min-height:430px; }
.chart-expand { float:right; min-height:34px; padding:6px 11px; border:1px solid #33435a; border-radius:8px; background:#172131; color:var(--text); cursor:pointer; }
.chart-expand:hover { border-color:#4f6685; background:#1c293b; }
.chart-values { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:7px; margin-top:14px; }
.chart-values div { padding:7px; border-radius:7px; background:#0d131d; text-align:center; font-size:10px; color:var(--text2); }
.chart-values b { display:block; margin-top:2px; color:var(--text); font-size:12px; }
.energy-section { margin-top:22px; padding-top:20px; border-top:1px solid #2a384b; }
.energy-section-head { display:flex; justify-content:space-between; gap:16px; align-items:flex-start; margin-bottom:13px; }
.energy-section-head h4 { margin:2px 0 4px; color:#f1f6ff; font-size:15px; }
.energy-section-head p { margin:0; color:#7f8fa5; font-size:9px; }
.energy-estimate-badge { padding:5px 8px; border:1px solid #725c26; border-radius:7px; color:#f8d878; background:#292410; font-size:8px; font-weight:900; white-space:nowrap; }
.energy-kpis { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:9px; margin-bottom:14px; }
.energy-kpis article { padding:12px; border:1px solid #27364a; border-radius:10px; background:#0c141f; }
.energy-kpis article.saving { border-color:#256447; background:linear-gradient(145deg,rgba(34,197,94,.13),#0c141f); }
.energy-kpis span,.energy-kpis small { display:block; color:#76869c; font-size:8px; }
.energy-kpis b { display:block; margin:5px 0 3px; color:#f8d878; font-size:17px; }
.energy-kpis .saving b { color:#5ee89a; }
.energy-panels { margin-bottom:14px; }
.energy-insights { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
.energy-insights > article { padding:13px; border:1px solid #263549; border-radius:11px; background:#0c131d; }
.energy-insights h5 { margin:0 0 9px; color:#dce8f7; font-size:11px; }
.energy-insights p,.energy-insights > article > small { display:block; margin:7px 0 0; color:#718096; font-size:8px; line-height:1.5; }
.energy-profile-rows,.energy-scenarios { display:grid; gap:5px; }
.energy-profile-rows > div,.energy-scenarios > div { display:flex; justify-content:space-between; align-items:center; gap:12px; padding:7px 8px; border-radius:7px; background:#121c29; color:#aab8ca; font-size:9px; }
.energy-profile-rows b,.energy-scenarios strong { color:#5ee89a; white-space:nowrap; }
.energy-scenarios span b,.energy-scenarios span small { display:block; }
.energy-scenarios span b { color:#dce7f5; font-size:9px; }
.energy-scenarios span small { margin-top:2px; color:#728198; font-size:7px; }
.energy-empty { min-height:110px; display:grid; place-items:center; padding:20px; border:1px dashed #34445b; border-radius:11px; color:#7c8ba0; text-align:center; font-size:10px; line-height:1.55; }
.profiles-layout { display:grid; grid-template-columns:minmax(0,1.45fr) minmax(320px,.75fr); gap:16px; align-items:start; }
.profile-device { margin-bottom:16px; padding:18px; border:1px solid var(--border); border-radius:14px; background:#111722; }
.profile-device-head { display:flex; justify-content:space-between; gap:12px; align-items:center; margin-bottom:13px; }
.profile-device-head h3 { margin:0; font-size:16px; }
.profile-cards { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:9px; }
.profile-card { padding:13px; border:1px solid #263449; border-radius:11px; background:#0d141f; }
.profile-card.active { border-color:#2b8294; box-shadow:inset 0 0 0 1px rgba(34,211,238,.18); }
.profile-card-top { display:flex; justify-content:space-between; gap:8px; align-items:center; }
.profile-card h4 { margin:0; font-size:13px; }
.profile-badge { padding:3px 6px; border-radius:10px; background:#182437; color:#91a1b8; font-size:8px; font-weight:800; }
.profile-card.active .profile-badge { background:#123943; color:#aef6ff; }
.profile-mode-path { margin:10px 0; color:#dce8f7; font-size:11px; font-weight:700; }
.profile-mode-chip { display:inline-flex; align-items:center; gap:4px; margin:2px 2px 2px 0; padding:3px 7px; border:1px solid #33445d; border-radius:6px; color:#a8b7ca; font-size:9px; }
.profile-mode-chip svg { flex-shrink:0; }
.profile-rule { display:flex; justify-content:space-between; gap:8px; padding:5px 0; border-bottom:1px solid #202b3c; color:#8493a8; font-size:10px; }
.profile-rule b { color:#e2eaf5; text-align:right; }
.profile-actions { display:flex; gap:6px; margin-top:10px; }
.profile-actions button { flex:1; }
.profile-card[role="button"] { cursor:pointer; transition:border-color .18s,transform .18s,background .18s; }
.profile-card[role="button"]:hover,.profile-card[role="button"]:focus-visible { border-color:#357f91; background:#101b27; outline:none; transform:translateY(-2px); }
.profile-edit-btn { color:#aef6ff !important; border-color:#2b7180 !important; }
.profile-editor-modal { padding:16px; }
.profile-editor-dialog { width:min(1180px,calc(100vw - 32px)); }
.profile-editor-body { background:#0d131c; }
.profile-editor-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; }
.profile-editor-section { overflow:hidden; border:1px solid #263449; border-radius:13px; background:#111925; }
.profile-editor-section-head { display:flex; gap:11px; align-items:flex-start; padding:14px 16px; border-bottom:1px solid #263449; background:#101722; }
.profile-editor-section-head > span { display:grid; width:28px; height:28px; flex:0 0 28px; place-items:center; border-radius:8px; color:#aef6ff; background:#123943; font-size:9px; font-weight:900; }
.profile-editor-section-head h3 { margin:0; color:#eaf3ff; font-size:13px; }
.profile-editor-section-head p { margin:3px 0 0; color:#8190a5; font-size:9px; line-height:1.45; }
.profile-editor-field { display:grid; grid-template-columns:minmax(0,1.25fr) minmax(145px,.75fr); gap:16px; align-items:center; padding:13px 16px; border-bottom:1px solid #202c3d; }
.profile-editor-field:last-child { border-bottom:0; }
.profile-editor-copy label { display:block; color:#dce7f5; font-size:10px; font-weight:800; }
.profile-editor-copy p { margin:4px 0 0; color:#7f90a6; font-size:9px; line-height:1.5; }
.profile-editor-control { min-width:0; }
.profile-toggle { display:flex; justify-content:flex-end; align-items:center; gap:7px; color:#b8c6d8; font-size:10px; font-weight:700; }
.profile-toggle input,.profile-mode-selector input { accent-color:var(--primary); }
.profile-mode-selector { display:flex; justify-content:flex-end; gap:5px; flex-wrap:wrap; }
.profile-mode-selector label { display:flex; align-items:center; gap:4px; padding:6px 7px; border:1px solid #304158; border-radius:7px; color:#adbbce; font-size:9px; }
.profile-docs { position:sticky; top:16px; padding:18px; border:1px solid var(--border); border-radius:14px; background:#111722; }
.profile-docs h3 { margin:0 0 10px; font-size:15px; }
.profile-docs h4 { margin:16px 0 6px; color:#d9e6f7; font-size:12px; }
.profile-docs p,.profile-docs li { color:#93a2b6; font-size:10px; line-height:1.55; }
.profile-docs ul,.profile-docs ol { padding-left:18px; }
.profile-callout { margin:10px 0; padding:10px; border-left:3px solid var(--primary); border-radius:6px; background:#10202b; color:#b8dce4; font-size:10px; }
@media (max-width:1100px) { .profiles-layout { grid-template-columns:1fr; } .profile-docs { position:static; } .profile-editor-grid { grid-template-columns:1fr; } }
@media (max-width:720px) { .profile-cards { grid-template-columns:1fr; } .profile-editor-field { grid-template-columns:1fr; } .profile-toggle,.profile-mode-selector { justify-content:flex-start; } .chart-toolbar { align-items:stretch; flex-direction:column; } .chart-periods,.chart-navigation { justify-content:center; } .installation-grid { grid-template-columns:1fr; } .installation-field.wide,.installation-note { grid-column:1; } }
.ops-empty { margin-top:10px; color:var(--text2); font-size:10px; }
.ops-telemetry-head { display:flex; justify-content:space-between; gap:10px; margin-bottom:11px; }
.ops-health { color:var(--green); font-size:9px; }
.ops-data-row { display:flex; justify-content:space-between; gap:10px; padding:7px 0; border-bottom:1px solid #222c3b; color:var(--text2); font-size:10px; }
.ops-data-row b { color:var(--text); text-align:right; }
.ops-inline-reset { margin-left:5px; padding:2px 6px; border:1px solid #435269; border-radius:5px; color:#aebdd0; background:#141d29; font-size:8px; cursor:pointer; }
.ops-inline-reset:hover { border-color:#e29b43; color:#ffd08a; }
.ops-details { margin-top:12px; padding-top:10px; border-top:0; }
.ops-details summary { min-height:32px; padding:8px; border:1px solid var(--border); border-radius:7px; text-align:center; cursor:pointer; color:#98a7bc; font-size:9px; }
.ops-details .control-row { display:grid; grid-template-columns:1fr; align-items:start; margin-top:10px; }
.ops-details .control-row label { width:auto; }
.ops-details .btn-group { max-width:none; }
.ops-details .btn { font-size:10px; }
.server-info { margin:0; padding:8px 24px; border-top:1px solid var(--border); color:var(--text2); }
.setup-msg { border:1px solid var(--border); border-radius:13px; background:#111722; }
/* ── configuration dialog ────────────────────── */
.config-modal { display:none; position:fixed; inset:0; z-index:1000; padding:24px; overflow:auto; background:rgba(3,7,13,.82); backdrop-filter:blur(8px); }
.config-dialog { width:min(980px,100%); margin:20px auto; overflow:hidden; border:1px solid #304056; border-radius:16px; background:#0f151f; box-shadow:0 30px 100px rgba(0,0,0,.65); }
.config-header { position:sticky; top:0; z-index:2; display:flex; align-items:center; justify-content:space-between; gap:20px; padding:18px 20px; border-bottom:1px solid var(--border); background:#111925; }
.config-heading { display:flex; align-items:center; gap:12px; }
.config-heading-icon { width:38px; height:38px; flex:0 0 38px; display:grid; place-items:center; border:1px solid #245f6c; border-radius:10px; color:var(--primary); background:#12323b; }
.config-heading-icon svg { width:19px; height:19px; fill:none; stroke:currentColor; stroke-width:1.8; stroke-linecap:round; stroke-linejoin:round; }
.config-header h2 { margin:0; font-size:17px; letter-spacing:-.02em; }
.config-header p { margin:2px 0 0; color:var(--text2); font-size:10px; }
.config-close { width:36px; height:36px; border:1px solid var(--border); border-radius:9px; background:#151d29; color:var(--text2); font-size:19px; cursor:pointer; }
.config-close:hover { color:var(--text); border-color:#45566e; }
.config-body { padding:20px; }
.config-intro { margin:0 0 16px; padding:12px 14px; border:1px solid #203b46; border-radius:10px; color:#9fb4bc; background:#101f27; font-size:11px; }
.config-loading { min-height:160px; display:grid; place-items:center; color:var(--text2); }
.config-common { display:grid; grid-template-columns:230px minmax(0,1fr); align-items:center; gap:14px; padding:15px; border:1px solid var(--border); border-radius:11px; background:#111722; }
.config-common label,.config-field label { color:var(--text2); font-size:10px; font-weight:700; letter-spacing:.035em; text-transform:uppercase; }
.config-select,.config-input { width:100%; min-height:38px; padding:7px 10px; border:1px solid var(--border); border-radius:8px; background:#151d29; color:var(--text); font:inherit; font-size:11px; }
.config-select[multiple] { min-height:118px; padding:5px; }
.config-select option { padding:5px 7px; background:#f8fafc; color:#111827; }
.card-header select option,.interval-label select option { background:#f8fafc; color:#111827; }
.config-device { margin-top:14px; overflow:hidden; border:1px solid var(--border); border-radius:12px; background:#111722; }
.config-device-head { display:flex; align-items:center; justify-content:space-between; gap:15px; padding:13px 15px; border-bottom:1px solid var(--border); background:#121a25; }
.config-device-head h3 { margin:0; font-size:13px; }
.config-device-head code { color:var(--text2); font-size:9px; }
.config-device-body { padding:15px; }
.config-sensor-grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
.config-field { display:grid; gap:6px; min-width:0; }
.installation-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
.installation-device { margin-bottom:16px; padding:14px; border:1px solid #26354a; border-radius:12px; background:#111925; }
.installation-device h3 { margin:0 0 12px; color:#e9f2ff; font-size:14px; }
.installation-field { display:grid; gap:5px; }
.installation-field label { color:var(--text2); font-size:9px; font-weight:700; text-transform:uppercase; }
.installation-field input,.installation-field select,.installation-field textarea { width:100%; box-sizing:border-box; padding:9px; border:1px solid #34465f; border-radius:7px; color:var(--text); background:#0c131d; }
.installation-field textarea { min-height:72px; resize:vertical; }
.installation-field.wide { grid-column:1/-1; }
.installation-note { grid-column:1/-1; margin:0; color:#7f8da2; font-size:9px; }
.config-help { color:var(--text2); font-size:9px; }
.config-section-title { margin:18px 0 9px; color:#b9c8da; font-size:9px; font-weight:900; letter-spacing:.13em; text-transform:uppercase; }
.outdoor-sensor-settings { display:grid; gap:14px; max-width:760px; margin:0 auto; }
.outdoor-sensor-card { display:grid; grid-template-columns:minmax(0,1.35fr) minmax(240px,.65fr); gap:22px; align-items:center; padding:18px; border:1px solid var(--border); border-radius:12px; background:#101823; }
.outdoor-sensor-copy .config-section-title { display:block; margin:0 0 7px; color:var(--primary); }
.outdoor-sensor-copy h3 { margin:0 0 5px; color:#e5eefb; font-size:13px; }
.outdoor-sensor-copy p { margin:0; color:#8494aa; font-size:10px; line-height:1.55; }
.room-sensor-settings { display:grid; gap:14px; }
.room-sensor-device { padding:17px; border:1px solid #2b394c; border-radius:14px; background:linear-gradient(145deg,#111b28,#0d141e); box-shadow:0 12px 28px rgba(0,0,0,.2); }
.room-sensor-device-head { display:flex; justify-content:space-between; gap:12px; align-items:flex-start; margin-bottom:14px; padding-bottom:12px; border-bottom:1px solid var(--border); }
.room-sensor-device h3 { margin:0 0 4px; font-size:14px; letter-spacing:-.01em; }
.room-sensor-device code { color:#718097; font-size:9px; }
.room-sensor-grid { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
.room-sensor-group { min-width:0; overflow:hidden; border:1px solid #29394d; border-radius:12px; background:#0b121c; }
.room-sensor-group.temperature { --sensor-accent:#40c4ff; --sensor-soft:rgba(64,196,255,.12); }
.room-sensor-group.humidity { --sensor-accent:#7c9cff; --sensor-soft:rgba(124,156,255,.12); }
.room-sensor-group-head { display:flex; align-items:center; gap:10px; min-height:64px; padding:11px 12px; border-bottom:1px solid #263448; background:linear-gradient(135deg,var(--sensor-soft),transparent 72%); }
.room-sensor-kind-icon { width:34px; height:34px; flex:0 0 34px; display:grid; place-items:center; border:1px solid color-mix(in srgb,var(--sensor-accent) 45%,transparent); border-radius:9px; color:var(--sensor-accent); background:var(--sensor-soft); font-size:11px; font-weight:800; }
.room-sensor-kind-copy { min-width:0; flex:1; }
.room-sensor-kind-copy h4 { margin:0 0 2px; color:#edf4ff; font-size:12px; }
.room-sensor-kind-copy p { margin:0; color:#7f8da2; font-size:9px; line-height:1.35; }
.room-sensor-tools { display:flex; flex-direction:column; align-items:flex-end; gap:4px; }
.room-sensor-count { color:var(--sensor-accent); font-size:9px; font-weight:700; white-space:nowrap; }
.room-sensor-clear { padding:0; border:0; color:#728197; background:transparent; font:inherit; font-size:8px; cursor:pointer; }
.room-sensor-clear:hover { color:#c9d6e8; }
.room-sensor-list { display:grid; align-content:start; gap:6px; min-height:158px; max-height:238px; padding:9px; overflow:auto; scrollbar-color:#34465f transparent; }
.room-sensor-option { position:relative; display:grid; grid-template-columns:18px minmax(0,1fr) auto; align-items:center; gap:8px; min-height:48px; padding:8px 9px; border:1px solid #243145; border-radius:9px; color:#aeb9c9; background:#111a27; cursor:pointer; transition:border-color .15s,background .15s,transform .15s; }
.room-sensor-option:hover { border-color:#40536d; background:#152132; transform:translateY(-1px); }
.room-sensor-option.is-selected { border-color:color-mix(in srgb,var(--sensor-accent) 62%,#263448); background:var(--sensor-soft); box-shadow:inset 3px 0 0 var(--sensor-accent); }
.room-sensor-option.is-unavailable { opacity:.63; }
.room-sensor-checkbox { width:15px; height:15px; margin:0; accent-color:var(--sensor-accent); cursor:pointer; }
.room-sensor-option-copy { min-width:0; }
.room-sensor-option-copy b,.room-sensor-option-copy small { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.room-sensor-option-copy b { color:#dce6f5; font-size:10px; font-weight:600; }
.room-sensor-option-copy small { margin-top:2px; color:#6f7e93; font-size:8px; }
.room-sensor-value { padding:3px 6px; border-radius:6px; color:var(--sensor-accent); background:var(--sensor-soft); font-size:9px; font-weight:700; white-space:nowrap; }
.room-sensor-empty { display:grid; place-items:center; min-height:138px; padding:18px; color:#6f7e93; text-align:center; font-size:10px; line-height:1.5; }
.room-sensor-group-foot { min-height:43px; padding:9px 11px; border-top:1px solid #202d3e; color:#718097; font-size:8px; line-height:1.45; }
.preset-table { overflow-x:auto; border:1px solid var(--border); border-radius:10px; }
.preset-head,.preset-row { display:grid; grid-template-columns:100px 55px 65px 100px 165px 70px 75px 75px 82px 88px 110px 60px 86px; gap:1px; min-width:1195px; align-items:center; }
.profile-master { display:flex; gap:8px; align-items:center; padding:10px 12px; margin-bottom:12px; border:1px solid rgba(255,193,7,.35); border-radius:8px; background:rgba(255,193,7,.08); font-weight:700; }
.profile-master small { color:var(--text-secondary); font-weight:400; }
.preset-head { color:#718097; background:#0d131c; font-size:8px; font-weight:800; letter-spacing:.05em; text-transform:uppercase; }
.preset-head span { padding:9px 8px; }
.preset-row { border-top:1px solid var(--border); background:#121923; }
.preset-name { padding:9px 10px; font-size:11px; font-weight:800; }
.preset-enable { display:flex; justify-content:center; }
.preset-row .config-input,.preset-row .config-select { min-height:34px; border-radius:0; border-width:0 0 0 1px; background:#151d29; text-align:center; }
.profile-mode-checks { display:flex; justify-content:center; gap:5px; padding:4px; font-size:8px; }
.profile-mode-checks label { display:flex; align-items:center; gap:2px; color:#a6b3c6; }
.profile-mode-checks input { accent-color:var(--primary); }
.config-footer { position:sticky; bottom:0; z-index:2; display:flex; align-items:center; justify-content:space-between; gap:12px; padding:14px 20px; border-top:1px solid var(--border); background:#111925; }
.config-status { color:var(--text2); font-size:10px; }
.config-actions { display:flex; gap:8px; }
.config-btn { min-height:38px; padding:0 15px; border:1px solid var(--border); border-radius:9px; background:#151d29; color:var(--text); font:inherit; font-size:11px; font-weight:800; cursor:pointer; }
.config-btn.primary { color:#aef6ff; border-color:#256575; background:#123943; }
.config-btn:hover { border-color:#486078; }
@media (max-width:1100px) {
  .app-shell { grid-template-columns:76px minmax(0,1fr); }
  .header,
  body.desktop .header { width:76px; padding:16px 10px; }
  .header h1,.nav-label,.sidebar-action-label { display:none; }
  .header-top { justify-content:center; padding-inline:0; }
  .tab-btn { justify-content:center; padding:0; }
  .nav-icon { width:18px; height:18px; flex-basis:18px; }
  .tab-nav::before,.interval-label,.sidebar-connection div { display:none; }
  .sidebar-connection { justify-content:center; padding:0; }
  .refresh-btn { justify-content:center; padding:0; }
  .sidebar-action-icon { width:18px; height:18px; flex-basis:18px; }
  .ops-unit-layout { grid-template-columns:210px minmax(390px,1fr); }
  .ops-telemetry { grid-column:1/-1; border-top:1px solid var(--border); }
  .ops-reading { border-right:1px solid var(--border); }
  .ops-controls { border-right:0; }
  .ops-overview { grid-template-columns:repeat(3,minmax(0,1fr)); }
  .chart-panels { grid-template-columns:1fr; }
}
@media (max-width:720px) {
  /* Touch targets: 40-44px minimo per i controlli principali su mobile. */
  .btn { min-height:40px; }
  .ops-presets .btn { min-height:40px; }
  .temp-control button { width:44px; height:44px; }
  .config-btn { min-height:42px; }
  .interval-label select, .config-select { min-height:40px; }
  .config-modal { padding:0; }
  .config-dialog { min-height:100vh; margin:0; border:0; border-radius:0; }
  .config-body { padding:14px; }
  .config-common,.config-sensor-grid,.room-sensor-grid,.outdoor-sensor-card { grid-template-columns:1fr; }
  .config-header { padding:14px; }
  .config-footer { padding:12px 14px; }
  .config-status { display:none; }
  .config-actions { width:100%; }
  .config-btn { flex:1; }
  .app-shell { display:block; }
  .header,
  body.desktop .header { position:sticky; width:100%; height:60px; margin:0; padding:8px 10px; border-right:0; border-bottom:1px solid var(--border); display:grid; grid-template-columns:auto minmax(0,1fr) auto; align-items:center; gap:8px; overflow:visible; }
  .header-top { justify-content:flex-start; padding:0; grid-column:2; }
  .mobile-menu-button { display:grid; grid-column:1; grid-row:1; }
  .header h1 { display:block; font-size:14px; }
  .header .icon-ac { width:30px; height:30px; }
  .chart-detail-card { padding:13px 8px; border-radius:12px; }
  .chart-panel { padding:11px 4px 8px; }
  .chart-panel:not(.apex-chart-panel) svg,.chart-panel:not(.apex-chart-panel).humidity svg { height:auto; min-height:0; aspect-ratio:460 / 580; }
  .chart-panel:not(.apex-chart-panel).control-chart svg { height:auto; aspect-ratio:460 / 300; }
  .chart-panels { gap:12px; }
  .chart-axis-label { font-size:13px; }
  .chart-series { stroke-width:3; }
  .chart-series.target { stroke-width:2.5; }
  .chart-point { stroke-width:2.5; }
  .chart-values { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .energy-kpis { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .energy-insights { grid-template-columns:1fr; }
  .energy-section-head { flex-direction:column; }
  .mobile-menu-scrim { position:fixed; inset:0; z-index:39; border:0; padding:0; background:rgba(2,7,13,.68); backdrop-filter:blur(2px); }
  body.mobile-menu-open .mobile-menu-scrim { display:block; }
  /* The sticky header creates its own stacking context. Raise that context
     while the drawer is open, otherwise the sibling blur layer also covers
     the hamburger and the drawer even if their local z-index is higher. */
  body.mobile-menu-open .header,body.mobile-menu-open.desktop .header { z-index:41; }
  body.mobile-menu-open .mobile-menu-button { position:relative; z-index:43; border-color:#39788a; background:#123943; color:#bdf7ff; }
  .tab-nav { position:fixed; z-index:42; top:0; bottom:0; left:0; display:flex; width:min(82vw,310px); margin:0; padding:68px 12px 18px; gap:5px; overflow-y:auto; overflow-x:hidden; flex-direction:column; background:#0c111a; border-right:1px solid var(--border); box-shadow:18px 0 50px rgba(0,0,0,.52); transform:translateX(-105%); transition:transform .22s ease; overscroll-behavior:contain; }
  body.mobile-menu-open .tab-nav { transform:translateX(0); }
  .tab-nav::before { display:block; content:'GREE CONTROL'; padding:7px 12px 16px; color:#8fa1b7; font-size:10px; font-weight:900; letter-spacing:.14em; }
  .tab-btn { flex:0 0 auto; width:100%; min-height:48px; justify-content:flex-start; padding:0 14px; font-size:13px; }
  .nav-label { display:inline; font-size:13px; }
  .nav-icon { width:19px; height:19px; flex-basis:19px; }
  .tab-btn.active { box-shadow:inset 3px 0 0 var(--primary); }
  .header-controls,
  body.desktop .header-controls { position:static; grid-column:3; grid-row:1; display:flex; margin:0; padding:0; border:0; }
  .interval-label,.sidebar-connection,.header-controls .refresh-btn:first-of-type { display:none; }
  .refresh-btn { width:36px; min-height:34px; }
  .sidebar-action-icon { display:grid; }
  #content { padding:14px; }
  .ops-page-head h2 { font-size:20px; }
  .ops-overview { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .ops-kpi:last-child { display:none; }
  .ops-unit-layout { grid-template-columns:1fr; }
  .charts-grid { grid-template-columns:1fr; }
  .chart-values { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .ops-reading,.ops-controls { border-right:0; border-bottom:1px solid var(--border); }
  .ops-modes { grid-template-columns:repeat(3,minmax(0,1fr)); }
  .card-header { padding:12px; }
  .header-row2 { flex-wrap:wrap; }
}
@media (max-width:420px) {
  .header h1 { font-size:13px; }
  .nav-label { font-size:13px; }
  .tab-btn { padding-inline:14px; }
  .chart-panel:not(.apex-chart-panel) svg,.chart-panel:not(.apex-chart-panel).humidity svg { height:auto; min-height:0; aspect-ratio:460 / 580; }
  .chart-detail-card.expanded { inset:0; border-radius:0; }
  .chart-detail-card.expanded .chart-panel:not(.apex-chart-panel) svg { height:65vh; min-height:430px; }
  .ops-overview { grid-template-columns:1fr 1fr; }
  .ops-kpi b { font-size:18px; }
  .ops-page-head p { font-size:10px; }
}
</style>
</head>
<body>
<div class="app-shell">

<div class="header">
  <button class="mobile-menu-button" type="button" onclick="toggleMobileMenu()" aria-label="Apri menu di navigazione" aria-controls="primaryNavigation" aria-expanded="false"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16"/></svg></button>
  <div class="header-top">
    <span class="icon-ac"><svg viewBox="0 0 24 24"><path d="M22 11h-4.17l3.24-3.24-1.41-1.42L15 11h-2V9l4.66-4.66-1.42-1.41L13 6.17V2h-2v4.17L7.76 2.93 6.34 4.34 11 9v2H9L4.34 6.34 2.93 7.76 6.17 11H2v2h4.17l-3.24 3.24 1.41 1.42L9 13h2v2l-4.66 4.66 1.42 1.41L11 17.83V22h2v-4.17l3.24 3.24 1.42-1.41L13 15v-2h2l4.66 4.66 1.41-1.42L17.83 13H22z"/></svg></span>
    <h1>Gree Control</h1>
  </div>
  <div class="header-controls">
    <div class="sidebar-connection"><span class="connection-dot"></span><div><strong>Gree Cloud</strong><small id="statusBadge" class="status-badge">Caricamento</small></div></div>
    <label class="interval-label">
      <span>Intervallo dati</span>
      <select id="intervalSelect" onchange="setPollInterval(this.value)" title="Intervallo di polling">
        <option value="5">5 s</option>
        <option value="10">10 s</option>
        <option value="15" selected>15 s</option>
        <option value="30">30 s</option>
        <option value="60">60 s</option>
      </select>
    </label>
    <button class="refresh-btn" onclick="openSensorSettings()" title="Configura sensori ambiente e profili"><span class="sidebar-action-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.83 2.83-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1.03 1.56V21h-4v-.08A1.7 1.7 0 0 0 8.95 19.4a1.7 1.7 0 0 0-1.88.34l-.06.06-2.83-2.83.06-.06A1.7 1.7 0 0 0 4.58 15 1.7 1.7 0 0 0 3 14H3v-4h.08A1.7 1.7 0 0 0 4.6 8.95a1.7 1.7 0 0 0-.34-1.88l-.06-.06 2.83-2.83.06.06A1.7 1.7 0 0 0 8.97 4.6 1.7 1.7 0 0 0 10 3.08V3h4v.08a1.7 1.7 0 0 0 1.05 1.52 1.7 1.7 0 0 0 1.88-.34l.06-.06 2.83 2.83-.06.06A1.7 1.7 0 0 0 19.4 9c.13.61.6 1.08 1.2 1.04H21v4h-.08A1.7 1.7 0 0 0 19.4 15Z"/></svg></span><span class="sidebar-action-label">Configura</span></button>
    <button class="refresh-btn" onclick="openInstallationSettings()" title="Scheda impianto e condotte"><span class="sidebar-action-icon" aria-hidden="true">▦</span><span class="sidebar-action-label">Impianto</span></button>
    <button class="refresh-btn" onclick="openEnergySensorSettings()" title="Associa i sensori di consumo effettivo"><span class="sidebar-action-icon" aria-hidden="true">⚡</span><span class="sidebar-action-label">Consumi</span></button>
    <button class="refresh-btn refresh-action" onclick="refreshNow()" title="Aggiorna ora"><span class="sidebar-action-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M20 6v5h-5"/><path d="M19 11a7 7 0 1 0 1 4"/></svg></span><span class="sidebar-action-label">Aggiorna ora</span></button>
  </div>
  <nav class="tab-nav" id="primaryNavigation" aria-label="Navigazione principale">
    <button class="tab-btn active" data-tab="devices" onclick="switchTab('devices')"><span class="nav-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg></span><span class="nav-label">Controllo</span></button>
    <button class="tab-btn" data-tab="charts" onclick="switchTab('charts')"><span class="nav-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M3 20h18"/><path d="m5 16 4-5 4 3 6-8"/></svg></span><span class="nav-label">Grafici</span></button>
    <button class="tab-btn" data-tab="profiles" onclick="switchTab('profiles')"><span class="nav-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M4 6h16"/><path d="M4 12h16"/><path d="M4 18h16"/><circle cx="8" cy="6" r="2"/><circle cx="16" cy="12" r="2"/><circle cx="10" cy="18" r="2"/></svg></span><span class="nav-label">Profili</span></button>
    <button class="tab-btn" data-tab="wiki" onclick="switchTab('wiki')"><span class="nav-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M4 5.5A3.5 3.5 0 0 1 7.5 2H11v17H7.5A3.5 3.5 0 0 0 4 22Z"/><path d="M20 5.5A3.5 3.5 0 0 0 16.5 2H13v17h3.5A3.5 3.5 0 0 1 20 22Z"/></svg></span><span class="nav-label">Manuale</span></button>
    <button class="tab-btn" data-tab="logs" onclick="switchTab('logs')"><span class="nav-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M4 19V9"/><path d="M10 19V5"/><path d="M16 19v-7"/><path d="M22 19V3"/><path d="M2 19h20"/></svg></span><span class="nav-label">Diagnostica</span></button>
    <button class="tab-btn" data-tab="info" onclick="switchTab('info')"><span class="nav-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 11v6"/><path d="M12 7h.01"/></svg></span><span class="nav-label">Sistema</span></button>
    <button class="tab-btn" data-tab="umatch" onclick="switchTab('umatch')" style="display:none;">U-Match</button>
    <button class="tab-btn" data-tab="readme" onclick="switchTab('readme')" style="display:none;">README</button>
    <button class="tab-btn" data-tab="changelog" onclick="switchTab('changelog')" style="display:none;">Changelog</button>
  </nav>
</div>
<button class="mobile-menu-scrim" type="button" onclick="closeMobileMenu()" aria-label="Chiudi menu di navigazione"></button>

<div id="content">
  <div id="tab-devices">
    <div class="ops-page-head">
      <div><h2>Controllo climatizzazione</h2><p id="opsUpdateText">Caricamento unità e telemetria…</p></div>
    </div>
    <div class="ops-overview" id="opsOverview"></div>
    <div class="setup-msg" id="setupMsg">
      <span class="icon-ac"><svg viewBox="0 0 24 24"><path d="M22 11h-4.17l3.24-3.24-1.41-1.42L15 11h-2V9l4.66-4.66-1.42-1.41L13 6.17V2h-2v4.17L7.76 2.93 6.34 4.34 11 9v2H9L4.34 6.34 2.93 7.76 6.17 11H2v2h4.17l-3.24 3.24 1.41 1.42L9 13h2v2l-4.66 4.66 1.42 1.41L11 17.83V22h2v-4.17l3.24 3.24 1.42-1.41L13 15v-2h2l4.66 4.66 1.41-1.42L17.83 13H22z"/></svg></span>
      <h2>No devices found</h2>
      <p>Configure the Gree AC Cloud integration in<br>
      Settings → Devices &amp; services → Add integration</p>
    </div>
    <div class="devices" id="devices"></div>
  </div>
  <div id="tab-charts" style="display:none;">
    <div class="ops-page-head"><div><h2>Clima ed energia</h2><p>Storico persistente di temperatura, umidità, consumi stimati e misure effettive affiancate.</p></div><div class="config-actions"><button class="config-btn" onclick="openEnergySensorSettings()">Sensori consumi</button></div></div>
    <div id="chartsContent" class="charts-grid"></div>
  </div>
  <div id="tab-profiles" style="display:none;">
    <div class="ops-page-head"><div><span class="ops-eyebrow">AUTOMAZIONE AMBIENTE</span><h2>Profili climatici</h2><p>Clicca su Giorno, Notte o Assente per aprire la configurazione dedicata e spiegata.</p></div><div class="config-actions"><button class="config-btn" onclick="openRoomSensorSettings()">Sensori interni</button><button class="config-btn" onclick="openSensorSettings()">Sensori esterni</button></div></div>
    <div id="profilesContent"></div>
  </div>
  <div id="tab-wiki" style="display:none;">
    <div class="wiki">
      <h2 style="margin:0 0 4px;font-size:18px;font-weight:500;">Parameter Reference</h2>
      <p style="color:var(--text-secondary);font-size:13px;margin-bottom:16px;">XE7A-24/HC wired controller parameters from the official manual. These are accessed <strong>directly on the physical wired controller</strong>, not from HA.</p>

      <h3 style="color:var(--yellow);font-size:14px;"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:5px" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/></svg>How to Access Settings</h3>
      <p style="color:var(--text-secondary);font-size:12px;margin-bottom:8px;">
      Per accedere alle impostazioni sul controller XE7A-24/HC:
      </p>
      <ol style="color:var(--text-secondary);font-size:12px;line-height:1.7;margin:0 0 16px 18px;">
      <li>Premere <strong>FUNCTION</strong> per 5 secondi per entrare in visualizzazione parametri (<strong>C00</strong>)</li>
      <li>Usa <strong>+ / -</strong> per scorrere i codici parametro (C00–C23)</li>
      <li><strong>ENTER</strong> per uscire</li>
      </ol>
      <p style="color:var(--text-secondary);font-size:12px;margin-bottom:8px;">
      Per modificare i parametri (codici <strong>P</strong>):
      </p>
      <ol style="color:var(--text-secondary);font-size:12px;line-height:1.7;margin:0 0 16px 18px;">
      <li>Da <strong>C00</strong>, premi <strong>FUNCTION</strong> di nuovo per 5 secondi → <strong>P00</strong></li>
      <li>Usa <strong>+ / -</strong> per selezionare il parametro</li>
      <li>Premi <strong>MODE</strong> per entrare in modifica (valore lampeggia)</li>
      <li><strong>+ / -</strong> per regolare, <strong>ENTER</strong> per confermare</li>
      <li><strong>ENTER</strong> per tornare indietro e uscire</li>
      </ol>

      <h3>Monitor (View C00–C23)</h3>
      <p style="color:var(--text-secondary);font-size:12px;">Read-only. C00 è la schermata iniziale — usa +/- per navigare agli altri codici.</p>
      <table class="wt"><tr><th>Code</th><th>Nome</th><th>Cosa mostra / Esempio</th><th>Range</th></tr>
      <tr><td>C00</td><td>Schermata iniziale</td><td>N° progetto unità interna attuale. Es: premi FUNCTION 5s → "C00" + numero (es. 1).</td><td>0–4</td></tr>
      <tr><td>C01</td><td>Diagnosi unità guaste</td><td>Premi MODE in C01, usa +/- per selezionare unità — quella selezionata emette bip, mostra errori nel campo temperatura. Es: se un'unità ha errore E1, la trovi C01.</td><td>1–255</td></tr>
      <tr><td>C03</td><td>Unità interne in rete</td><td>Quante unità interne ci sono nella rete di sistema. Es: 4 = quattro unità collegate.</td><td>1–100</td></tr>
      <tr><td>C06</td><td>Modalità prioritaria</td><td>00=normale, 01=prioritario. Es: in caso di sovraccarico, l'unità prioritaria continua, le altre si spengono.</td><td>00–01</td></tr>
      <tr><td>C07</td><td>Temperatura ambiente interna</td><td>Temp rilevata dall'unità interna. Es: 24.5°C.</td><td>—</td></tr>
      <tr><td>C08</td><td>Promemoria pulizia filtro</td><td>Giorni di funzionamento prima dell'avviso filtro. Es: 90 = promemoria dopo 90gg.</td><td>4–416 gg</td></tr>
      <tr><td>C09</td><td>Indirizzo controller</td><td>01=principale, 02=secondario. Es: 2 controller → mostra 01 sul main.</td><td>01, 02</td></tr>
      <tr><td>C11</td><td>Unità controllate</td><td>Quante unità questo controller comanda. Es: 2 unità in un open space.</td><td>1–16</td></tr>
      <tr><td>C12</td><td>Temperatura esterna</td><td>Sensore unità esterna. Es: 35°C d'estate.</td><td>—</td></tr>
      <tr><td>C17</td><td>Umidità interna</td><td>Umidità relativa (InHumi in HA). Es: 55% = comfort, >70% = umido.</td><td>0–100%</td></tr>
      <tr><td>C18</td><td>One-key project</td><td>Mostra su tutti i controller quali unità comandano. Premi MODE in C18, +/- per scorrere.</td><td>1–255</td></tr>
      <tr><td>C20</td><td>Aria fresca outlet</td><td>Temp uscita aria fresca (solo unità aria fresca). Es: 18°C.</td><td>—</td></tr>
      <tr><td>C23</td><td>Versione firmware</td><td>Versione software controller. Es: v3.02.</td><td>text</td></tr>
      </table>

      <h3>Settings (P01–P87)</h3>
      <p style="color:var(--text-secondary);font-size:12px;">
      ⚠ I parametri contrassegnati con <strong>"installazione"</strong> vanno modificati solo all'installazione iniziale.
      Altri parametri (timer, unità temp, step) sono sicuri da cambiare in qualsiasi momento.
      </p>

      <h4>Generali — sicuri da modificare</h4>
      <table class="wt"><tr><th>Code</th><th>Nome</th><th>Cosa fa / Esempio pratico</th><th>Valori</th></tr>
      <tr><td>P16</td><td>Unità temperatura</td><td>Passa da °C a °F. Es: ospiti americani → 01 per Fahrenheit.</td><td>00=°C, 01=°F</td></tr>
      <tr><td>P33</td><td>Tipo timer</td><td>Timer generale (conta alla rovescia) vs orologio (accensione a orario fisso). Es: spegnimento dopo 2h = generale.</td><td>00=generale, 01=orologio</td></tr>
      <tr><td>P34</td><td>Ripeti timer orario</td><td>Il timer orario si ripete ogni giorno? Es: accensione 7:00 tutti i giorni = 01.</td><td>00=una volta, 01=giornaliero</td></tr>
      <tr><td>P82</td><td>Formato ora</td><td>24h o 12h (AM/PM). Es: 01 mostra "3:00 PM" invece di "15:00".</td><td>00=24h, 01=12h</td></tr>
      <tr><td>P87</td><td>Step temperatura</td><td>Step 0.5°C o 1°C con +/-. Es: 01 passa da 24° a 24.5°. Nota: HA usa sempre 0.5°C.</td><td>00=1°C, 01=0.5°C</td></tr>
      </table>

      <h4>Installazione — da configurare all'avvio</h4>
      <table class="wt"><tr><th>Code</th><th>Nome</th><th>Cosa fa / Esempio pratico</th><th>Valori</th></tr>
      <tr><td>P10</td><td>Unità principale</td><td>Imposta questa unità come principale (icona si accende). Es: in un sistema master-slave, imposta 01 su quella principale. Non applicabile a unità parziali.</td><td>00=no change, 01=main</td></tr>
      <tr><td>P11</td><td>Ricevitore IR</td><td>Abilita il telecomando IR. Es: telecomando non funziona? Controlla che P11 sia 01.</td><td>00=off, 01=on</td></tr>
      <tr><td>P13</td><td>Indirizzo controller</td><td>Con 2 controller sullo stesso gruppo: 01=principale, 02=secondario. Il secondario imposta solo il proprio indirizzo.</td><td>01=main, 02=secondary</td></tr>
      <tr><td>P14</td><td>Unità gruppo comandato</td><td>Quante unità interne questo controller comanda. Es: 2 unità in un open space = 02.</td><td>00=off, 01–16</td></tr>
      <tr><td>P30</td><td>Pressione statica (ESP)</td><td>9 livelli (P1-P9) per condotti. Default P5=25Pa (24k) / 37Pa (29k). Range 0-160Pa. P9 massima pressione.<br/><b>Mapping ESP:</b> P1=S05/S03/S02/S01, P5=S09/S07/S06/S05 (default), P9=S13/S11/S10/S09.<br/>Es: condotti lunghi 15m → P7 o P8.</td><td>01–09</td></tr>
      <tr><td>P31</td><td>Soffitto alto</td><td>Soffitto >3m? 01 migliora distribuzione aria. Es: capannone con soffitto 4m → 01.</td><td>00=standard, 01=alto</td></tr>
      </table>

      <h4>HVAC — comportamento climatico</h4>
      <table class="wt"><tr><th>Code</th><th>Nome</th><th>Cosa fa / Esempio pratico</th><th>Valori</th></tr>
      <tr><td>P37</td><td>Auto cool temp</td><td>In Auto, temperatura per raffrescamento. Es: 26°C in estate — sopra questa soglia parte il raffrescamento.</td><td>17–30°C</td></tr>
      <tr><td>P38</td><td>Auto heat temp</td><td>In Auto, temperatura per riscaldamento. Es: 20°C in inverno — sotto questa soglia parte il riscaldamento. Differenza Cool-Heat ≥1°C.</td><td>16–29°C</td></tr>
      <tr><td>P43</td><td>Mod. funzionamento prioritaria</td><td>01=funzionamento prioritario. In caso di potenza elettrica insufficiente, le unità prioritarie continuano, le altre spente.</td><td>00=normale, 01=prioritario</td></tr>
      <tr><td>P46</td><td>Annullamento filtro</td><td>Resetta il tempo accumulato dopo pulizia filtro. Es: hai pulito i filtri → imposta 01 per resettare.</td><td>00=no, 01=annulla</td></tr>
      <tr><td>P49</td><td>Angolo ripresa aria</td><td>Angolo apertura piastra ritorno aria (solo unità con piastra). Es: 02 = 30° per flusso bilanciato.</td><td>01=25°, 02=30°, 03=35°</td></tr>
      <tr><td>P78</td><td>Antivento freddo</td><td>Ritardo ventola in riscaldamento per evitare aria fredda all'avvio. Es: 01=300s (5 min) aspetta che la batteria sia calda.</td><td>00=180s, 01=300s, 02=420s, 03=600s</td></tr>
      </table>

      <h4>Aria fresca (Fresh Air) — solo per unità aria fresca</h4>
      <table class="wt"><tr><th>Code</th><th>Nome</th><th>Cosa fa / Esempio</th><th>Valori</th></tr>
      <tr><td>P50</td><td>Fresh air cool temp</td><td>Temp aria in uscita in modalità raffrescamento. Es: 18°C per aria fresca fredda.</td><td>16–30 °C</td></tr>
      <tr><td>P51</td><td>Fresh air heat temp</td><td>Temp aria in uscita in modalità riscaldamento. Es: 22°C per aria fresca tiepida.</td><td>16–30 °C</td></tr>
      <tr><td>P54</td><td>Controllo comune</td><td>01=accesa/spenta assieme all'unità interna principale. Es: l'aria fresca si spegne quando l'AC si spegne.</td><td>00=senza, 01=con</td></tr>
      </table>

      <h4>Pulizia e deumidifica</h4>
      <table class="wt"><tr><th>Code</th><th>Nome</th><th>Cosa fa / Esempio</th><th>Valori</th></tr>
      <tr><td>P83</td><td>Cool mode ctrl (I-FEEL)</td><td>00=controllo temperatura ambiente, 01=controllo correzione temp+umidità. Es: 01 se hai funzione I-FEEL (sensore controller).</td><td>00=temp, 01=temp+umidità</td></tr>
      <tr><td>P84</td><td>Dry mode ctrl</td><td>00=controllo temperatura, 01=controllo umidità. Es: in cantina umida, 01 per target preciso.</td><td>00=temp, 01=umidità</td></tr>
      <tr><td>P85</td><td>Dry humidity temp</td><td>Setpoint per controllo umidità (solo se P84=01). Es: 16 = 60% umidità target (valore ×2 circa).</td><td>10–30°C</td></tr>
      <tr><td>P86</td><td>Pulizia automatica</td><td>Dopo spegnimento: 01=normale, 02=rapida, 03=accurata. Es: 03 per asciugare bene la batteria.</td><td>01=normale, 02=rapida, 03=accurata</td></tr>
      <tr><td>P76</td><td>Filtro PM2.5</td><td>Abilita filtro PM2.5 se installato. Es: hai modulo filtro? Imposta 01.</td><td>00=no, 01=sì</td></tr>
      </table>

      <h4>Recovery — ripresa dopo blackout</h4>
      <table class="wt"><tr><th>Code</th><th>Nome</th><th>Cosa fa / Esempio pratico</th><th>Valori</th></tr>
      <tr><td>P71</td><td>Funzione ripristino</td><td>Dopo blackout riparte con impostazioni precedenti. Es: consigliato 01 per non tornare e trovare tutto spento.</td><td>00=off, 01=on</td></tr>
      <tr><td>P72</td><td>Limite max ripristino</td><td>Temp max al riavvio. Es: 26°C in estate per risparmiare. Differenza P72-P73 ≥4°C.</td><td>20–30 °C</td></tr>
      <tr><td>P73</td><td>Limite min ripristino</td><td>Temp min al riavvio. Es: 20°C in inverno.</td><td>16–26 °C</td></tr>
      <tr><td>P74</td><td>Ripristino scheda</td><td>Con scheda hotel/gate: reinserendo la scheda riparte come prima. Es: 00 = inserendo scheda non cambia stato.</td><td>00=no, 01=sì</td></tr>
      </table>

      <h3>MQTT Protocol</h3>
      <p style="color:var(--text-secondary);font-size:12px;">Broker: <code>18.185.150.155:1984</code> (TLS), AES-128-ECB per device key.</p>
      <p style="color:var(--text-secondary);font-size:12px;">Topics: <code>request/{parent_mac}</code> → <code>response/{parent_mac}/#</code></p>

      <h3>Entities HA</h3>
      <p style="color:var(--text-secondary);font-size:12px;">Ogni entità HA corrisponde a una funzione del controller XE7A-24/HC. La colonna <strong>ICONA Display</strong> mostra quale simbolo appare sul display fisico quando la funzione è attiva (riferimento Tabella 3.1 del manuale).</p>
      <table class="wt"><tr><th>Platform</th><th>Key</th><th>HA Icona</th><th>ICONA Display</th><th>Descrizione</th></tr>
      <tr><td>climate</td><td>—</td><td><span class="hmi">❄</span></td><td>N.27/31/32 ☀❄/ N.30 Ventola / N.29 Goccia</td><td>Acceso/spento, modo (Auto/Cool/Heat/Fan/Dry), ventola 6 vel, swing, setpoint temperatura</td></tr>
      <tr><td>sensor</td><td>InTem</td><td><span class="hmi">🌡</span></td><td>N.33 — valore temperatura display</td><td>Temperatura ambiente interna: valore protocollo con offset +40, quindi temperatura = InTem − 40 °C; valida quando InTemEn=1</td></tr>
      <tr><td>sensor</td><td>OutTem</td><td><span class="hmi">🌡</span></td><td>N.33 — valore temperatura display</td><td>Temperatura esterna (componente elettronico, non temperatura ambiente esterna reale)</td></tr>
      <tr><td>sensor</td><td>TemSen</td><td><span class="hmi">🌡</span></td><td>—</td><td>Sensore temperatura aggiuntivo/opzionale, separato da InTem; sulle unità osservate il cloud restituisce <code>None</code></td></tr>
      <tr><td>sensor</td><td>InHumi</td><td><span class="hmi">💧</span></td><td>—</td><td>Umidità interna percentuale (0–100%). C17 sul menu service. Non sempre disponibile</td></tr>
      <tr><td>sensor</td><td>SetDeciTem</td><td><span class="hmi">🌡</span></td><td>N.33 — setpoint display</td><td>Setpoint temperatura in decimi di °C (es. 245 = 24.5°C). Solo lettura</td></tr>
      <tr><td>switch</td><td>Health</td><td><span class="hmi">🌿</span></td><td>N.14 🌿 Health function</td><td>Ionizzatore/Health — genera ioni per purificare l'aria. Icona foglia sul display. Confermato funzionante sui tuoi device</td></tr>
      <tr><td>switch</td><td>Quiet</td><td><span class="hmi">🔇</span></td><td>N.21 🔇 Quiet status</td><td>Modalità silenziosa — riduce rumore ventola al minimo. Include Quiet e Auto Quiet. Sempre 0 nella scansione (non supportato dal tuo modello VRF)</td></tr>
      <tr><td>switch</td><td>Tur</td><td><span class="hmi">⚡</span></td><td>— Simbolo Turbo ⚡</td><td>Turbo — massima potenza per raggiungere velocemente la temperatura impostata. Non presente nella tabella LCD base (simbolo specifico). Sempre 0 nella scansione</td></tr>
      <tr><td>switch</td><td>StHt</td><td><span class="hmi">🔥</span></td><td>N.27 ☀ Heating mode</td><td>Strong Heat — riscaldamento intenso con temperatura mandata più alta. Usa lo stesso simbolo della modalità Riscaldamento ☀. Sempre 0 nella scansione</td></tr>
      <tr><td>switch</td><td>Blo</td><td><span class="hmi">🌬</span></td><td>N.16 🌬 X-fan function</td><td>X-Fan — in raffreddamento/deumidificazione asciuga la batteria interna dopo lo spegnimento per limitare batteri e muffe.</td></tr>
      <tr><td>switch</td><td>SvSt</td><td><span class="hmi">💾</span></td><td>N.18 💾 Save status</td><td>Energy Saving — limita la potenza massima per risparmiare energia. Icona Save sul display. Confermato funzionante su device 2 (zona notte)</td></tr>
      <tr><td>switch</td><td>TemRec</td><td><span class="hmi">🔄</span></td><td>—</td><td>Temperature Recovery — alla riaccensione, recupera la temperatura precedente invece di ripartire da 24°C. Funzione cloud, nessuna icona LCD dedicata</td></tr>
      <tr><td>switch</td><td>SlpMod</td><td><span class="hmi">🌙</span></td><td>N.22 ☾ Sleep status</td><td>Sleep — regola gradualmente la temperatura durante la notte per comfort e risparmio. 3 modalità notte disponibili sul manuale. Sempre 0 nella scansione</td></tr>
      <tr><td>switch</td><td>Air</td><td><span class="hmi">🌀</span></td><td>N.19 🌬 Air status</td><td>Air/Fresh Air — ricambio aria opzionale. Non è la direzione del flusso, gestita da SwUpDn/SwingLfRig.</td></tr>
      <tr><td>switch</td><td>Lig</td><td><span class="hmi">💡</span></td><td>—</td><td>Light — retroilluminazione display on/off. Nessuna icona LCD (controlla il retroilluminazione, non un simbolo)</td></tr>
      <tr><td>binary_sensor</td><td>Err</td><td><span class="hmi">⚠</span></td><td>— Mostra codice errore sul display</td><td>Errore attivo — quando ON, sul display appare un codice errore (E1, F3, L0, ecc.). Vedi tabella codici errore sotto</td></tr>
      <tr><td>binary_sensor</td><td>Filter</td><td><span class="hmi">🗓</span></td><td>N.15 🗓 Remind to clean filter</td><td>Promemoria pulizia filtro — si attiva dopo le ore accumulate impostate su C08 (4–416 giorni). P46 per resettare dopo la pulizia</td></tr>
      </table>

      <h3>ICONE Display (Tabella 3.1 Manuale)</h3>
      <p style="color:var(--text-secondary);font-size:12px;">Simboli che appaiono sul display LCD del controller XE7A-24/HC. Riferimento: Tabella 3.1 "LCD display instruction" del manuale ufficiale, pagine 9–12.</p>
      <table class="wt"><tr><th>N.</th><th>Icona</th><th>Nome / Descrizione</th><th>Entità HA correlata</th></tr>
      <tr><td>1</td><td>🚪</td><td>Gate-control function</td><td>—</td></tr>
      <tr><td>2</td><td>🔒</td><td>Child Lock status (tasto FUNCTION 5s blocca)</td><td>—</td></tr>
      <tr><td>3</td><td>🔗</td><td>Slave wired controller (indirizzo 02)</td><td>—</td></tr>
      <tr><td>4</td><td>🏢</td><td>One wired controller controls multiple indoor units</td><td>—</td></tr>
      <tr><td>5</td><td>❄</td><td>Outdoor unit defrosting status</td><td>— (sbrinamento automatico)</td></tr>
      <tr><td>6</td><td>🛡</td><td>Shielding status</td><td>—</td></tr>
      <tr><td>7</td><td>⭐</td><td>Current wired controller connects master indoor unit</td><td>—</td></tr>
      <tr><td>8</td><td>🌬</td><td>Fresh air control function of AHU-KIT</td><td>— (solo unità aria fresca)</td></tr>
      <tr><td>9</td><td>🗳</td><td>System mode priority is voting mode</td><td>—</td></tr>
      <tr><td>10</td><td>📶</td><td>WiFi status (o connesso a G-Cloud)</td><td>—</td></tr>
      <tr><td>11</td><td>⏱</td><td>Timer zone: display system clock and timer status</td><td>—</td></tr>
      <tr><td>12</td><td>🌀</td><td>Current set fan speed</td><td><code>WdSpd</code> (climate entity)</td></tr>
      <tr><td>13</td><td>🚶</td><td>Absence function</td><td>—</td></tr>
      <tr><td>14</td><td>🌿</td><td>Health function, Indoor unit optional function</td><td><code>Health</code> switch</td></tr>
      <tr><td>15</td><td>🗓</td><td>Remind to clean the filter</td><td><code>Filter</code> binary_sensor</td></tr>
      <tr><td>16</td><td>🌬</td><td>X-fan function</td><td><code>Blo</code> switch</td></tr>
      <tr><td>17</td><td>✨</td><td>Auto clean status</td><td>— (<code>CleanEn</code> nel protocollo)</td></tr>
      <tr><td>18</td><td>💾</td><td>Save status of indoor unit</td><td><code>SvSt</code> switch</td></tr>
      <tr><td>19</td><td>🌬</td><td>Air status, Indoor unit optional function</td><td><code>Air</code> switch</td></tr>
      <tr><td>20</td><td>✅</td><td>I-DEMAND function, Indoor unit optional function</td><td>— (<code>IDemand</code> nel protocollo)</td></tr>
      <tr><td>21</td><td>🔇</td><td>Quiet status (including Quiet and Auto Quiet)</td><td><code>Quiet</code> switch</td></tr>
      <tr><td>22</td><td>🌙</td><td>Sleep status</td><td><code>SlpMod</code> switch</td></tr>
      <tr><td>23</td><td>↔</td><td>Left and right swing function</td><td><code>SwingLfRig</code> (climate)</td></tr>
      <tr><td>24</td><td>↕</td><td>Up and down swing function</td><td><code>SwUpDn</code> (climate)</td></tr>
      <tr><td>25</td><td>🔥</td><td>3D Heating mode</td><td>—</td></tr>
      <tr><td>26</td><td>🏠</td><td>Space Heating mode</td><td>—</td></tr>
      <tr><td>27</td><td>☀</td><td>Heating mode</td><td><code>heat</code> (Mod=2, climate)</td></tr>
      <tr><td>28</td><td>🔥</td><td>Floor Heating mode</td><td>—</td></tr>
      <tr><td>29</td><td>💧</td><td>Dry mode</td><td><code>dry</code> (Mod=4, climate)</td></tr>
      <tr><td>30</td><td>🌀</td><td>Fan mode</td><td><code>fan_only</code> (Mod=3, climate)</td></tr>
      <tr><td>31</td><td>🔄</td><td>Auto mode</td><td><code>auto</code> (Mod=0, climate)</td></tr>
      <tr><td>32</td><td>❄</td><td>Cooling mode</td><td><code>cool</code> (Mod=1, climate)</td></tr>
      <tr><td>33</td><td>🌡</td><td>Temperature value display (o FAP per unità aria fresca)</td><td><code>InTem</code>, <code>OutTem</code>, <code>SetTem</code></td></tr>
      </table>
      <p style="color:var(--text2);font-size:11px;margin-top:4px;">Le icone nella colonna sono approssimazioni. Il display LCD effettivo usa una matrice di punti (segment LCD). I numeri (N.) sono riferimenti diretti alla Tabella 3.1 del manuale ufficiale Gree XE7A-24/HC (pagine 9–12).</p>

      <h3>Codici Errore VRF</h3>
      <p style="color:var(--text-secondary);font-size:12px;">Quando il binary_sensor <strong>Err</strong> è ON, il device ha un problema. I codici appaiono sul display del controller wired.</p>

      <h4 style="color:var(--yellow);font-size:12px;">Unità Esterna — Protezioni (E)</h4>
      <table class="wt"><tr><th>Codice</th><th>Significato</th><th>Esempio / Cosa fare</th></tr>
      <tr><td>E0</td><td>Errore unità esterna</td><td>Anomalia generica — spegnere e riaccendere. Se persiste, chiamare assistenza.</td></tr>
      <tr><td>E1</td><td>Protezione alta pressione</td><td>Pressione mandata troppo alta. Es: filtri sporchi o condensa ostruita → pulire filtri e verificare flusso aria.</td></tr>
      <tr><td>E2</td><td>Protezione sottotemperatura scarico</td><td>Gas di scarico compressore troppo freddo. Es: carica refrigerante insufficiente → verificare perdite.</td></tr>
      <tr><td>E3</td><td>Protezione bassa pressione</td><td>Pressione aspirazione troppo bassa. Es: possibile perdita refrigerante → chiamare tecnico.</td></tr>
      <tr><td>E4</td><td>Protezione sovratemperatura scarico</td><td>Gas di scarico compressore troppo caldo (>limite). Es: carica insufficiente o restrizione nel circuito.</td></tr>
      <tr><td>Ed</td><td>Protezione bassa temp modulo comando</td><td>Modulo di comando esterno troppo freddo. Es: verificare ambiente installazione unità esterna.</td></tr>
      </table>

      <h4 style="color:var(--yellow);font-size:12px;">Unità Esterna — Sensori (F)</h4>
      <table class="wt"><tr><th>Codice</th><th>Significato</th><th>Esempio / Cosa fare</th></tr>
      <tr><td>F0</td><td>Scheda principale esterna</td><td>Malfunzionamento PCB esterna. Es: scheda bruciata o corto → sostituire scheda.</td></tr>
      <tr><td>F1</td><td>Sensore pressione alta</td><td>Sensore di pressione lato alta danneggiato. Es: sostituire sensore.</td></tr>
      <tr><td>F2</td><td>Sensore temp. ingresso scambiatore</td><td>Tubo ingresso scambiatore a piastre. Es: sensore scollegato o guasto.</td></tr>
      <tr><td>F3</td><td>Sensore pressione bassa</td><td>Sensore pressione lato bassa. Es: sostituire sensore.</td></tr>
      <tr><td>F4</td><td>Sensore temp. uscita scambiatore</td><td>Tubo uscita scambiatore a piastre. Es: verificare connessione sensore.</td></tr>
      <tr><td>F5</td><td>Sensore temp. scarico compressore 1</td><td>T sensore mandata compressore 1. Es: sensore interrotto → sostituire.</td></tr>
      <tr><td>F6–FA</td><td>Sensore scarico compressore 2–6</td><td>Idem per compressori aggiuntivi (sistemi multi-compressore).</td></tr>
      <tr><td>FC/FL/FE/FF/FJ</td><td>Sensore corrente compressore 2–6</td><td>Sensore di corrente su compressore N. Es: compressore non assorbe → cablaggio o sensore.</td></tr>
      </table>

      <h4 style="color:var(--yellow);font-size:12px;">Unità Esterna — Pannello Compressore (P/H)</h4>
      <table class="wt"><tr><th>Codice</th><th>Significato</th><th>Esempio / Cosa fare</th></tr>
      <tr><td>P0</td><td>Errore pannello comando compressore</td><td>Driver inverter compressore guasto. Es: modulo IPM bruciato → sostituire pannello.</td></tr>
      <tr><td>P1</td><td>Malfunzionamento pannello comando</td><td>Anomalia generica driver compressore. Es: reset e riprovare.</td></tr>
      <tr><td>P2</td><td>Protezione alimentazione</td><td>Tensione alimentazione driver fuori range. Es: verificare alimentazione 380V/220V.</td></tr>
      <tr><td>P3</td><td>Reset modulo pannello</td><td>Reset anomalo del modulo. Es: disturbo elettrico o surriscaldamento.</td></tr>
      <tr><td>H0</td><td>Errore pannello ventola</td><td>Driver motore ventola esterna. Es: ventola non gira → controllare cablaggio.</td></tr>
      <tr><td>H1</td><td>Malfunzionamento pannello ventola</td><td>Anomalia driver ventola. Es: modulo IPM ventola.</td></tr>
      <tr><td>H2</td><td>Protezione alimentaz. ventola</td><td>Tensione driver ventola fuori range.</td></tr>
      </table>

      <h4 style="color:var(--yellow);font-size:12px;">Unità Esterna — Compressore / Sistema (J, b)</h4>
      <table class="wt"><tr><th>Codice</th><th>Significato</th><th>Esempio / Cosa fare</th></tr>
      <tr><td>J1–J6</td><td>Sovracorrente compressore N</td><td>Compressore assorbe troppa corrente. Es: compressore bloccato o refrigerante liquido in aspirazione.</td></tr>
      <tr><td>J7</td><td>Perdita compressione valvola 4 vie</td><td>Valvola di inversione ciclo che perde. Es: sostituire valvola.</td></tr>
      <tr><td>J8</td><td>Sovrapressione sistema</td><td>Pressione troppo alta in qualsiasi condizione. Es: carica refrigerante eccessiva.</td></tr>
      <tr><td>J9</td><td>Sottopressione sistema</td><td>Pressione troppo bassa. Es: perdita refrigerante.</td></tr>
      <tr><td>JL</td><td>Sotto/sovrapressione</td><td>Protezione pressione anomala generale.</td></tr>
      <tr><td>b1</td><td>Sensore temp. ambiente esterna</td><td>Sensore T esterna guasto. Es: mostra -99°C → sostituire sensore.</td></tr>
      <tr><td>b2</td><td>Sensore sbrinamento 1</td><td>Sensore temperatura batteria esterna 1.</td></tr>
      <tr><td>b3</td><td>Sensore sbrinamento 2</td><td>Sensore temperatura batteria esterna 2.</td></tr>
      <tr><td>b4</td><td>Sensore sottoraffreddatore liquido</td><td>T uscita liquido sottoraffreddatore.</td></tr>
      <tr><td>b5</td><td>Sensore sottoraffreddatore gas</td><td>T uscita gas sottoraffreddatore.</td></tr>
      </table>

      <h4 style="color:var(--yellow);font-size:12px;">Unità Interna (L, d, y, o)</h4>
      <table class="wt"><tr><th>Codice</th><th>Significato</th><th>Esempio / Cosa fare</th></tr>
      <tr><td>L0</td><td>Errore unità interna</td><td>Anomalia generica unità interna. Es: resettare e riprovare.</td></tr>
      <tr><td>L1</td><td>Protezione ventola interna</td><td>Ventola interna bloccata o sovracorrente. Es: verificare ventola e cablaggio.</td></tr>
      <tr><td>L2</td><td>Protezione E-heater</td><td>Resistenza elettrica integrativa in protezione. Es: sovratemperatura.</td></tr>
      <tr><td>L4</td><td>Alimentazione comando a filo</td><td>Controller wired non alimentato correttamente. Es: verificare collegamento H1/H2.</td></tr>
      <tr><td>L5</td><td>Protezione antigelo</td><td>Rischio congelamento batteria. Es: temperatura batteria < 0°C → unità si ferma per proteggersi.</td></tr>
      <tr><td>L6</td><td>Conflitto modalità</td><td>Un unità in Cool e l'altra in Heat sulla stessa rete. Es: tutte le unità devono essere nella stessa modalità.</td></tr>
      <tr><td>L7</td><td>Nessuna unità principale</td><td>Manca unità interna principale nella rete. Es: impostare P10=01 su almeno un'unità.</td></tr>
      <tr><td>LA</td><td>Incompatibilità unità interne</td><td>Unità di modelli diversi non compatibili sulla stessa rete.</td></tr>
      <tr><td>LH</td><td>Scarsa qualità aria</td><td>Avvertimento: sensore CO2 o PM2.5 rileva aria insalubre.</td></tr>
      <tr><td>d1</td><td>Scheda elettronica unità interna</td><td>PCB interna guasta. Es: scheda bruciata → sostituire.</td></tr>
      <tr><td>d3</td><td>Sensore temperatura ambiente</td><td>Sensore T interna guasto. Es: mostra 0°C o 99°C → sostituire sensore.</td></tr>
      <tr><td>d4</td><td>Sensore temp. tubo ingresso</td><td>Sensore T batteria ingresso. Es: sensore aperto o corto.</td></tr>
      <tr><td>d5</td><td>Sensore temp. tubo centrale</td><td>Sensore T batteria centrale.</td></tr>
      <tr><td>d6</td><td>Sensore temp. tubo uscita</td><td>Sensore T batteria uscita.</td></tr>
      <tr><td>d7</td><td>Sensore umidità</td><td>Sensore umidità interna guasto.</td></tr>
      <tr><td>dH</td><td>Scheda elettronica controller</td><td>PCB del comando a filo guasta. Es: display danneggiato o touch non risponde → sostituire controller.</td></tr>
      <tr><td>dL</td><td>Sensore temp. aria uscita</td><td>T sensore mandata aria.</td></tr>
      </table>

      <h4 style="color:var(--yellow);font-size:12px;">Comunicazione / Sistema (C, U)</h4>
      <table class="wt"><tr><th>Codice</th><th>Significato</th><th>Esempio / Cosa fare</th></tr>
      <tr><td>C0</td><td>Comunicazione unità int-est / controller</td><td>Bus di comunicazione tra unità interna, esterna o controller interrotto. Es: verificare cablaggio e terminazioni.</td></tr>
      <tr><td>C4</td><td>Nessuna unità interna</td><td>Il sistema non rileva unità interne. Es: verificare indirizzi DIP switch.</td></tr>
      <tr><td>C5</td><td>Conflitto codici progetto</td><td>Due unità interne hanno lo stesso codice progetto. Es: verificare impostazione indirizzi.</td></tr>
      <tr><td>C7</td><td>Comunicazione scambiatore modalità</td><td>Errore di comunicazione con scambiatore di modalità.</td></tr>
      <tr><td>CH</td><td>Capacità nominale troppo alta</td><td>Configurazione capacità superiore al limite. Es: verificare DIP switch capacità.</td></tr>
      <tr><td>CL</td><td>Capacità nominale troppo bassa</td><td>Configurazione capacità inferiore al limite.</td></tr>
      <tr><td>U2</td><td>Codice capacità/cappuccio errato</td><td>Cappuccio ponticello o codice capacità unità esterna errato.</td></tr>
      <tr><td>U4</td><td>Insufficienza refrigerante</td><td>Carica refrigerante troppo bassa. Es: chiamare tecnico per verifica perdite.</td></tr>
      <tr><td>U8</td><td>Malfunzionamento tubo unità interna</td><td>Sensore temperatura tubo anomalo.</td></tr>
      <tr><td>Ud</td><td>Pannello comando collegamento rete</td><td>Errore pannello di comando nella connessione alla rete.</td></tr>
      </table>
      <p style="color:var(--text2);font-size:11px;margin-top:4px;">Nota: i codici sopra sono tratti dal manuale ufficiale XE7A-24/HC (sezioni 6.1.1–6.1.3). Possono variare in base al firmware e alla configurazione VRF.</p>

      <h3>Codici Errore U-Match (Installation Manual)</h3>
      <p style="color:var(--text-secondary);font-size:12px;">Questi sono i codici del manuale di installazione delle unità canalizzabili U-Match (GUD series). Possono sovrapporsi o differire dai codici VRF del controller.</p>

      <h4 style="color:var(--yellow);font-size:12px;">Comunicazione & Sensori (C, d)</h4>
      <table class="wt"><tr><th>Codice</th><th>Significato</th><th>Esempio / Cosa fare</th></tr>
      <tr><td>C0</td><td>Comunicazione controller ↔ unità interna</td><td>Cavo H1/H2 non collegato o danneggiato. Es: display spento → verificare cablaggio 2x0.75mm².</td></tr>
      <tr><td>C1</td><td>Sensore temperatura ambiente interna</td><td>Sonda T interna guasta. Es: mostra -99°C → sostituire sensore.</td></tr>
      <tr><td>C2</td><td>Sensore temperatura evaporatore</td><td>Sonda T batteria interna. Es: sensore aperto o corto.</td></tr>
      <tr><td>C3</td><td>Sensore temperatura condensatore</td><td>Sonda T batteria esterna.</td></tr>
      <tr><td>C6</td><td>Sensore temperatura scarico compressore</td><td>Sonda T mandata compressore.</td></tr>
      <tr><td>C7</td><td>Sensore meso-temperatura condensatore</td><td>Sonda T intermedia batteria esterna.</td></tr>
      <tr><td>CE</td><td>Sensore temperatura comando a filo</td><td>Sensore locale del controller guasto. Es: funzione I-FEEL non disponibile.</td></tr>
      <tr><td>PF</td><td>Sensore temperatura pannello comando</td><td>Sonda T scheda elettronica unità interna.</td></tr>
      <tr><td>CC (dc)</td><td>Sensore temperatura aspirazione compressore</td><td>Sonda T ritorno gas compressore.</td></tr>
      <tr><td>dH</td><td>Scheda elettronica controller</td><td>PCB comando a filo danneggiata → sostituire.</td></tr>
      <tr><td>dJ</td><td>Protezione sequenza/fase</td><td>Inversione o mancanza fase su alimentazione 3ph. Es: invertire due fasi.</td></tr>
      <tr><td>C4</td><td>Cappuccio ponticello esterno</td><td>Jumper capacità unità esterna non inserito o errato.</td></tr>
      <tr><td>CJ</td><td>Cappuccio ponticello interno</td><td>Jumper capacità unità interna errato.</td></tr>
      </table>

      <h4 style="color:var(--yellow);font-size:12px;">Protezioni Unità (E, H)</h4>
      <table class="wt"><tr><th>Codice</th><th>Significato</th><th>Esempio / Cosa fare</th></tr>
      <tr><td>E0</td><td>Errore ventola interna</td><td>Ventola interna bloccata o guasta. Es: motore DC non gira → sostituire.</td></tr>
      <tr><td>E1</td><td>Protezione alta pressione</td><td>Pressione mandata troppo alta. Es: filtri sporchi o condotti ostruiti.</td></tr>
      <tr><td>E2</td><td>Protezione antigelo</td><td>Batteria interna < 0°C. Es: flusso aria insufficiente o filtro sporco.</td></tr>
      <tr><td>E3</td><td>Carenza refrigerante / bassa pressione</td><td>Possibile perdita di gas. Es: chiamare tecnico per verifica.</td></tr>
      <tr><td>E4</td><td>Sovratemperatura scarico compressore</td><td>Gas troppo caldo in mandata. Es: carica insufficiente o restrizione.</td></tr>
      <tr><td>E6</td><td>Comunicazione unità int. ↔ est.</td><td>Cavo 4x1.0mm² tra ID e OD interrotto. Es: verificare cablaggio (max 100m).</td></tr>
      <tr><td>E7</td><td>Conflitto modalità</td><td>Un unità in Cool e l'altra in Heat sulla stessa rete.</td></tr>
      <tr><td>E9</td><td>Protezione riempimento acqua</td><td>Allarme livello acqua — pompa scarico ostruita.</td></tr>
      <tr><td>EE</td><td>Memoria chip lettura/scrittura</td><td>EEPROM scheda danneggiata → sostituire PCB.</td></tr>
      <tr><td>EL</td><td>Emergenza / allarme antincendio</td><td>Segnale da centrale antincendio → unità ferma per sicurezza.</td></tr>
      <tr><td>F3</td><td>Sensore temperatura esterna</td><td>Sonda T ambiente esterno guasta.</td></tr>
      <tr><td>Fo</td><td>Modalità recupero refrigerante</td><td>Modalità service attiva per recupero gas. Non è un errore.</td></tr>
      <tr><td>H1</td><td>Sbrinamento in corso</td><td>Normale operazione di sbrinamento in riscaldamento. Non è un errore.</td></tr>
      <tr><td>H4</td><td>Protezione sovraccarico</td><td>Compressore in overload. Es: attendere raffreddamento.</td></tr>
      <tr><td>H5</td><td>Modulo IPM sovracorrente</td><td>Modulo di potenza compressore in protezione. Es: verificare compressore.</td></tr>
      <tr><td>H7</td><td>Compressore offline</td><td>Comunicazione con driver compressore persa.</td></tr>
      </table>

      <h4 style="color:var(--yellow);font-size:12px;">Driver Compressore (P)</h4>
      <table class="wt"><tr><th>Codice</th><th>Significato</th><th>Esempio / Cosa fare</th></tr>
      <tr><td>P0</td><td>Reset driver</td><td>Reset anomalo del driver compressore. Es: disturbo elettrico.</td></tr>
      <tr><td>P5</td><td>Sovracorrente compressore</td><td>Compressore assorbe troppa corrente. Es: compressore bloccato.</td></tr>
      <tr><td>P6</td><td>Comunicazione master ↔ driver</td><td>Bus di comunicazione tra scheda principale e driver compressore.</td></tr>
      <tr><td>P7</td><td>Sensore temperatura modulo</td><td>Sensore T modulo IPM guasto.</td></tr>
      <tr><td>P8</td><td>Protezione temperatura modulo</td><td>Modulo IPM troppo caldo >limite.</td></tr>
      <tr><td>P9</td><td>Protezione contattore AC</td><td>Contattore compressore non chiude correttamente.</td></tr>
      <tr><td>PA</td><td>Sovracorrente AC esterna</td><td>Corrente assorbita unità esterna troppo alta.</td></tr>
      <tr><td>PH/PL</td><td>Tensione bus alta/bassa</td><td>Tensione DC bus driver fuori range. Es: verificare tensione rete.</td></tr>
      <tr><td>PP</td><td>Tensione AC input errata</td><td>Tensione alimentazione driver non corretta.</td></tr>
      </table>

      <h4 style="color:var(--yellow);font-size:12px;">Ventola Interna DC (q)</h4>
      <table class="wt"><tr><th>Codice</th><th>Significato</th><th>Esempio / Cosa fare</th></tr>
      <tr><td>q0/q1</td><td>Tensione bus ventola bassa/alta</td><td>Alimentazione driver ventola interna fuori range.</td></tr>
      <tr><td>q2</td><td>Sovracorrente ventola AC</td><td>Motore ventola assorbe troppo.</td></tr>
      <tr><td>q3</td><td>IPM ventola</td><td>Modulo IPM driver ventola in protezione.</td></tr>
      <tr><td>q5</td><td>Avvio ventola fallito</td><td>Motore ventola non parte. Es: cuscinetto bloccato.</td></tr>
      <tr><td>q6</td><td>Mancanza fase ventola</td><td>Fase alimentazione motore mancante.</td></tr>
      <tr><td>qE</td><td>Sensore temperatura modulo ventola</td><td>Sonda T driver ventola DC interna guasta.</td></tr>
      <tr><td>qo</td><td>Sensore temperatura scatola elettrica</td><td>Scheda elettronica ventola surriscaldata.</td></tr>
      <tr><td>qC</td><td>Comunicazione master ↔ ventola DC</td><td>Bus comunicazione scheda principale ↔ driver ventola.</td></tr>
      </table>
      <p style="color:var(--text2);font-size:11px;margin-top:4px;">Nota: questi codici sono dal manuale di installazione U-Match ducted (sezione 5.2). I codici visualizzati sul controller XE7A-24/HC possono essere un sottoinsieme di entrambe le tabelle (VRF + U-Match).</p>

      <h3>Specifiche Tecniche</h3>
      <p style="color:var(--text-secondary);font-size:12px;">Dati dalle schede prodotto U-Match 2026 per tutti i modelli della serie GUD.</p>
      <table class="wt"><tr><th>Modello</th><th>BTU</th><th>Cool kW</th><th>Heat kW</th><th>EER/COP</th><th>SEER</th><th>kW nom (c/h)</th><th>Max kW</th><th>ESP Pa</th><th>Flusso H m³/h</th><th>dB(A) H</th><th>Pipe</th><th>R32 kg</th><th>ID mm</th></tr>
      <tr><td>GUD35</td><td>12K</td><td>3.5</td><td>4.0</td><td>3.5/4.0</td><td>6.6</td><td>1.00/1.05</td><td>1.40</td><td>0-100</td><td>—</td><td>—</td><td>1/4-3/8</td><td>0.57</td><td>—</td></tr>
      <tr><td>GUD50</td><td>18K</td><td>5.0</td><td>5.5</td><td>3.5/4.0</td><td>6.6</td><td>1.45/1.50</td><td>2.00</td><td>0-100</td><td>—</td><td>—</td><td>1/4-1/2</td><td>0.85</td><td>—</td></tr>
      <tr><td style="color:var(--yellow)">GUD71</td><td style="color:var(--yellow)">24K</td><td>7.10</td><td>8.00</td><td>3.70/4.00</td><td>6.6</td><td>1.92/2.00</td><td>2.80</td><td>0-160</td><td>1100</td><td>37</td><td>3/8-5/8</td><td>1.50</td><td>260/900/655</td></tr>
      <tr><td style="color:var(--yellow)">GUD85</td><td style="color:var(--yellow)">29K</td><td>8.50</td><td>8.80</td><td>3.40/3.90</td><td>6.4</td><td>2.50/2.26</td><td>3.30</td><td>0-160</td><td>1400</td><td>43</td><td>3/8-5/8</td><td>1.50</td><td>260/900/655</td></tr>
      <tr><td>GUD100</td><td>36K</td><td>10.50</td><td>11.50</td><td>3.50/4.10</td><td>6.4</td><td>3.00/2.80</td><td>4.70</td><td>0-160</td><td>1700</td><td>39</td><td>3/8-5/8</td><td>2.10</td><td>260/1340/655</td></tr>
      <tr><td>GUD140</td><td>46K</td><td>13.40</td><td>15.50</td><td>2.91/3.30</td><td>—</td><td>4.60/4.70</td><td>5.60</td><td>0-160</td><td>2200</td><td>43</td><td>3/8-5/8</td><td>2.80</td><td>300/1400/700</td></tr>
      <tr><td>GUD160</td><td>54K</td><td>16.00</td><td>17.00</td><td>2.96/3.62</td><td>—</td><td>5.40/4.70</td><td>6.80</td><td>0-200</td><td>2600</td><td>44</td><td>3/8-5/8</td><td>3.50</td><td>300/1400/700</td></tr>
      </table>
      <p style="color:var(--text2);font-size:10px;margin-top:4px;">Dati GUD35/GUD50 stimati (serie PS). GUD140/GUD160: modelli 3Ph. I tuoi modelli in giallo. Refrigerante R32 (GWP 675).</p>

      <h3>Caratteristiche Principali</h3>
      <p style="color:var(--text-secondary);font-size:12px;">Funzionalità della serie U-Match GUD:</p>
      <ul style="color:var(--text-secondary);font-size:12px;margin:4px 0 12px 18px;line-height:1.7;">
      <li><b>Doppio sensore temperatura:</b> scegli se usare il sensore dell'unità interna o del comando a filo (I-FEEL)</li>
      <li><b>Pompa scarico integrata:</b> sollevamento fino a 1000 mm — nessuna pompa esterna necessaria</li>
      <li><b>Presa aria fresca:</b> collegabile direttamente all'unità per ricambio d'aria</li>
      <li><b>Batteria a V brevettata:</b> maggiore scambio termico in meno spazio</li>
      <li><b>Ventola centrifuga brevettata:</b> portata maggiore, rumore ridotto</li>
      <li><b>Motore DC:</b> ventola interna a commutazione elettronica, modulante</li>
      <li><b>WiFi opzionale:</b> via controller YAP1F6 (venduto separatamente)</li>
      <li><b>Modbus gateway:</b> ME50-00/EG(M) per integrazione BMS</li>
      <li><b>Controllo centralizzato:</b> CE58-00/EF(CM) per fino a 80 unità</li>
      <li><b>R32:</b> refrigerante ecologico GWP 675, carica ridotta</li>
      <li><b>Valvole di intercettazione:</b> chiudono il refrigerante per manutenzione senza perdite</li>
      <li><b>Sleep modes:</b> 3 modalità notte con regolazione graduale temperatura</li>
      <li><b>I-Demand:</b> risparmio energetico limitando la potenza massima</li>
      <li><b>Sbrinamento intelligente:</b> ottimizzato per ridurre i cicli di sbrinamento</li>
      <li><b>Antivento freddo:</b> ventola ritardata in riscaldamento fino a batteria calda</li>
      <li><b>Deumidifica a bassa temperatura:</b> funzione Dry anche con temperature basse</li>
      </ul>

      <h3>Stima Consumi</h3>
      <p style="color:var(--text-secondary);font-size:12px;">
      La stima si basa sui dati nominali dei modelli sopra. Per calcolare il consumo istantaneo:
      </p>
      <ul style="color:var(--text-secondary);font-size:12px;margin:4px 0 12px 18px;line-height:1.6;">
      <li><b>Off</b> = 0 W</li>
      <li><b>Fan only</b> = 5% della potenza nominale (solo ventola)</li>
      <li><b>Cool/Heat</b> = potenza nominale × fattore ventola × fattore carico termico</li>
      <li><b>Dry</b> = 70% della potenza nominale cool</li>
      <li><b>Turbo</b> = maggiorazione del 20%</li>
      <li><b>Fattore ventola:</b> Auto=90%, Bassa=70%, M-Bassa=80%, Media=90%, M-Alta=100%, Alta=110%</li>
      <li><b>Fattore carico:</b> 50% + (ΔT × 5%). Es: set 24°C, ambiente 27°C → ΔT=3 → 65% carico</li>
      </ul>
    </div>
  </div>
  <div id="tab-umatch" style="display:none;">
    <div class="wiki">
      <h2 style="margin:0 0 4px;font-size:18px;font-weight:500;">U-Match Feature Matrix</h2>
      <p style="color:var(--text-secondary);font-size:13px;margin-bottom:16px;">Funzioni ricavate dai manuali XE7A-24/HC e U-Match 6. I controlli marcati “da verificare” non vengono inviati al dispositivo finché la codifica MQTT non è confermata.</p>

      <h3>Funzioni utente</h3>
      <table class="wt"><tr><th>Funzione</th><th>Vincolo</th><th>Protocollo</th><th>Stato integrazione</th></tr>
      <tr><td>I-Demand / DRED</td><td>Solo Cool: D1 arresta il compressore, D2 limita la domanda al 50%, D3 al 75%. L'attivazione annulla Quiet</td><td><code>DRED</code>, capability <code>DREDEn</code>; <code>Idemand</code> non rappresenta il livello</td><td>Off, D1, D2 e D3 verificati sui comandi XE7A. Le percentuali sono limiti massimi, non misure del consumo</td></tr>
      <tr><td>Absence / antigelo 8 °C</td><td>Solo Heat</td><td><code>GoOut</code></td><td>Da verificare sul dispositivo</td></tr>
      <tr><td>X-Fan</td><td>Cool/Dry; asciugatura evaporatore</td><td><code>Blo</code></td><td>Disponibile come switch</td></tr>
      <tr><td>Auto Clean</td><td>Avvio a unità spenta; ciclo ~30 min</td><td><code>AutoClean</code>, <code>CleanState</code></td><td>Da implementare come button + stato</td></tr>
      <tr><td>Dry 12 °C</td><td>Solo Dry; incompatibile con alcuni limiti energy-save</td><td><code>LowDeHumi</code></td><td>Da verificare</td></tr>
      <tr><td>Target umidità</td><td>45–75%, step 5%; solo unità compatibili</td><td><code>HumiEnable</code>, <code>SetCoolHumi</code></td><td>Da verificare</td></tr>
      <tr><td>Fresh Air</td><td>Accessorio opzionale; livelli 1–10</td><td><code>Air</code>, <code>AirLevel</code></td><td>Switch presente; livello da verificare</td></tr>
      <tr><td>Sleep 1/2/3</td><td>Curve comfort notturno</td><td><code>SwhSlp</code>, <code>SlpMod</code></td><td>Mappatura valori da verificare</td></tr>
      <tr><td>Filtro</td><td>Reminder configurabile e reset accumulo</td><td><code>CleanEn</code>, <code>CleanTime</code>, <code>FClTime</code>, <code>FClRes</code></td><td>Diagnostica e reset da implementare</td></tr>
      </table>

      <h3>Pressione statica esterna — parametro installatore P30</h3>
      <p>Il manuale associa P30 alle curve del ventilatore. Non viene esposto come comando cloud: una taratura errata può compromettere portata, rumore e funzionamento.</p>
      <table class="wt"><tr><th>Modello</th><th>P1</th><th>P2</th><th>P3</th><th>P4</th><th>P5 default</th><th>P6</th><th>P7</th><th>P8</th><th>P9</th></tr>
      <tr><td>GUD35/50</td><td>—</td><td>—</td><td>0 Pa</td><td>15</td><td>25</td><td>50</td><td>80</td><td>—</td><td>—</td></tr>
      <tr><td>GUD71</td><td>0</td><td>10</td><td>15</td><td>20</td><td>25</td><td>50</td><td>75</td><td>100</td><td>160</td></tr>
      <tr><td>GUD85</td><td>0</td><td>10</td><td>15</td><td>20</td><td>37</td><td>50</td><td>75</td><td>100</td><td>160</td></tr>
      <tr><td>GUD100</td><td>0</td><td>10</td><td>15</td><td>25</td><td>37</td><td>50</td><td>75</td><td>100</td><td>160</td></tr>
      <tr><td>GUD140/160</td><td>0</td><td>10</td><td>25</td><td>37</td><td>50</td><td>75</td><td>100</td><td>150</td><td>200</td></tr>
      </table>

      <h3>Parametri installatore documentati</h3>
      <table class="wt"><tr><th>Parametro</th><th>Funzione</th><th>Politica integrazione</th></tr>
      <tr><td>P20</td><td>Sensore ambiente: ripresa/controller/misto</td><td>Sola documentazione</td></tr>
      <tr><td>P22</td><td>Compensazione temperatura in Heat (-15..15)</td><td>Sola documentazione</td></tr>
      <tr><td>P30</td><td>Curva ventilatore / pressione statica</td><td>Sola documentazione</td></tr>
      <tr><td>P37/P38</td><td>Setpoint Auto Cool/Heat</td><td>Sola documentazione</td></tr>
      <tr><td>P46</td><td>Reset tempo filtro</td><td>Da associare a comando MQTT verificato</td></tr>
      <tr><td>P71–P74</td><td>Ripristino e limiti temperatura</td><td>Sola documentazione</td></tr>
      <tr><td>P82–P87</td><td>Sensori, limiti, umidità, Dry e step 0,5 °C</td><td>Sola documentazione</td></tr>
      </table>

      <p style="margin-top:14px;color:var(--text-secondary);">Analisi completa: <code>UMATCH_FEATURE_ANALYSIS.md</code> nel repository.</p>
    </div>
  </div>

  <div id="tab-logs" style="display:none;">
    <div class="ops-page-head"><div><span class="ops-eyebrow">REGISTRO PERSISTENTE</span><h2>Azioni operative</h2><p>Comandi manuali, decisioni dei profili e risultati conservati nello storage Home Assistant.</p></div></div>
    <div class="log-toolbar">
      <button class="btn" onclick="loadActionLog()">↻ Aggiorna registro</button>
      <button class="btn" onclick="copyActionLog()">📋 Copia registro</button>
      <button class="btn" onclick="clearActionLog()">🗑 Azzera registro</button>
      <select id="actionSourceFilter" class="config-select" style="width:auto" onchange="loadActionLog()"><option value="">Tutte le origini</option><option value="panel_manual">Pannello manuale</option><option value="ha_manual">Home Assistant/manuale</option><option value="profile">Profilo Smart</option><option value="startup">Avvio</option><option value="device_external">Comando a muro/app</option><option value="integration">Integrazione</option></select>
      <span id="actionLogCount"></span>
    </div>
    <div id="actionLogContainer"><p style="color:var(--text2);font-size:12px;">Caricamento registro azioni…</p></div>
    <div class="ops-page-head" style="margin-top:24px"><div><span class="ops-eyebrow">DIAGNOSTICA VOLATILE</span><h2>Log tecnici</h2><p>Ultimi messaggi in memoria; vengono persi al riavvio di Home Assistant.</p></div></div>
    <div class="log-toolbar">
      <button class="btn" onclick="copyAllLogs()">📋 Copy all</button>
      <label class="log-toggle">
        <input type="checkbox" id="autoRefreshLogs" checked onchange="onLogAutoRefreshChange()">
        Auto-refresh
      </label>
      <span id="logCount"></span>
    </div>
    <div id="logContainer">
      <p style="color:var(--text2);font-size:12px;">Loading...</p>
    </div>
  </div>
  <div id="tab-readme" style="display:none;">
    <div class="md-content" id="readmeContainer">
      <p style="color:var(--text-secondary);font-size:13px;">Loading...</p>
    </div>
  </div>
  <div id="tab-changelog" style="display:none;">
    <div class="md-content" id="changelogContainer">
      <p style="color:var(--text-secondary);font-size:13px;">Loading...</p>
    </div>
  </div>
  <div id="tab-info" style="display:none;">
    <div id="infoContent"></div>
  </div>
</div>
</div>

<div id="profileEditorModal" class="config-modal profile-editor-modal" role="dialog" aria-modal="true" aria-labelledby="profileEditorTitle" onclick="if(event.target===this)closeProfileEditor()">
  <div class="config-dialog profile-editor-dialog">
    <header class="config-header">
      <div class="config-heading"><span class="config-heading-icon"><svg viewBox="0 0 24 24"><path d="M4 6h16"/><path d="M4 12h16"/><path d="M4 18h16"/><circle cx="8" cy="6" r="2"/><circle cx="16" cy="12" r="2"/><circle cx="10" cy="18" r="2"/></svg></span><div><h2 id="profileEditorTitle">Configurazione profilo</h2><p id="profileEditorSubtitle">Impostazioni climatiche dedicate</p></div></div>
      <button class="config-close" onclick="closeProfileEditor()" aria-label="Chiudi configurazione profilo">×</button>
    </header>
    <div class="config-body profile-editor-body" id="profileEditorBody"><div class="config-loading">Caricamento profilo…</div></div>
    <footer class="config-footer"><span class="config-status" id="profileEditorStatus">Il salvataggio aggiorna solo il profilo selezionato.</span><div class="config-actions"><button class="config-btn" onclick="closeProfileEditor()">Annulla</button><button class="config-btn primary" id="saveProfileEditor" onclick="saveProfileEditor()">Salva profilo</button></div></footer>
  </div>
</div>

<div id="installationSettings" class="config-modal" role="dialog" aria-modal="true" aria-labelledby="installationTitle" onclick="if(event.target===this)closeInstallationSettings()">
  <div class="config-dialog">
    <header class="config-header"><div class="config-heading"><span class="config-heading-icon">▦</span><div><h2 id="installationTitle">Scheda impianto aeraulico</h2><p>Dati descrittivi per contestualizzare portata, rumore e prestazioni</p></div></div><button class="config-close" onclick="closeInstallationSettings()" aria-label="Chiudi scheda impianto">×</button></header>
    <div class="config-body" id="installationSettingsContent"><div class="config-loading">Caricamento configurazione…</div></div>
    <footer class="config-footer"><span class="config-status" id="installationSettingsStatus">Questi dati non modificano P30 e non inviano comandi alla macchina.</span><div class="config-actions"><button class="config-btn" onclick="closeInstallationSettings()">Annulla</button><button class="config-btn primary" id="saveInstallationSettings">Salva scheda</button></div></footer>
  </div>
</div>

<div id="roomSensorSettings" class="config-modal" role="dialog" aria-modal="true" aria-labelledby="roomSensorTitle" onclick="if(event.target===this)closeRoomSensorSettings()">
  <div class="config-dialog">
    <header class="config-header">
      <div class="config-heading"><span class="config-heading-icon"><svg viewBox="0 0 24 24"><path d="M12 3v10"/><circle cx="12" cy="17" r="4"/><path d="M8 7H5v12h3"/><path d="M16 7h3v12h-3"/></svg></span><div><h2 id="roomSensorTitle">Sensori interni</h2><p>Associazione delle sonde ambiente alle singole unità</p></div></div>
      <button class="config-close" onclick="closeRoomSensorSettings()" aria-label="Chiudi sensori interni">×</button>
    </header>
    <div class="config-body"><p class="config-intro">Temperatura e umidità sono gestite in due gruppi indipendenti: seleziona liberamente tutte le sonde da associare alla macchina. La regolazione usa la media dei valori disponibili e ignora automaticamente le entità non disponibili.</p><div id="roomSensorSettingsContent" class="config-loading">Caricamento sensori interni…</div></div>
    <footer class="config-footer"><span class="config-status" id="roomSensorSettingsStatus">Profili e sensori esterni non verranno modificati.</span><div class="config-actions"><button class="config-btn" onclick="closeRoomSensorSettings()">Annulla</button><button class="config-btn primary" id="saveRoomSensorSettings">Salva associazioni</button></div></footer>
  </div>
</div>

<div id="energySensorSettings" class="config-modal" role="dialog" aria-modal="true" aria-labelledby="energySensorTitle" onclick="if(event.target===this)closeEnergySensorSettings()">
  <div class="config-dialog">
    <header class="config-header"><div class="config-heading"><span class="config-heading-icon">⚡</span><div><h2 id="energySensorTitle">Misure elettriche effettive</h2><p>Associa i canali del contatore alle singole unità</p></div></div><button class="config-close" onclick="closeEnergySensorSettings()" aria-label="Chiudi misure elettriche">×</button></header>
    <div class="config-body"><p class="config-intro">La potenza e l’energia misurate vengono mostrate accanto alle stime del modello: non le sostituiscono. Per lo storico della potenza scegli il sensore W del canale; il contatore kWh è facoltativo.</p><div id="energySensorSettingsContent" class="config-loading">Caricamento sensori elettrici…</div></div>
    <footer class="config-footer"><span class="config-status" id="energySensorSettingsStatus">Le associazioni si applicano senza ricaricare MQTT.</span><div class="config-actions"><button class="config-btn" onclick="closeEnergySensorSettings()">Annulla</button><button class="config-btn primary" id="saveEnergySensorSettings">Salva associazioni</button></div></footer>
  </div>
</div>

<div id="sensorSettings" class="config-modal" role="dialog" aria-modal="true" aria-labelledby="configTitle" onclick="if(event.target===this)closeSensorSettings()">
  <div class="config-dialog">
    <header class="config-header">
      <div class="config-heading"><span class="config-heading-icon"><svg viewBox="0 0 24 24"><path d="M12 3v3"/><path d="M5.6 5.6l2.1 2.1"/><path d="M3 12h3"/><path d="M18 12h3"/><circle cx="12" cy="12" r="4"/><path d="M5 21h14"/></svg></span><div><h2 id="configTitle">Sensori esterni</h2><p>Riferimenti meteo comuni a tutte le unità</p></div></div>
      <button class="config-close" onclick="closeSensorSettings()" aria-label="Chiudi configurazione">×</button>
    </header>
    <div class="config-body">
      <p class="config-intro">Seleziona temperatura e umidità esterne usate da tutte le unità. Questi valori alimentano la compensazione del target e i grafici; sensori ambiente e profili si configurano nelle rispettive pagine Home Assistant e Profili.</p>
      <div id="sensorSettingsContent" class="config-loading">Caricamento configurazione…</div>
    </div>
    <footer class="config-footer">
      <span class="config-status" id="sensorSettingsGlobalStatus">La selezione è comune a tutte le unità della stessa integrazione.</span>
      <div class="config-actions"><button class="config-btn" onclick="closeSensorSettings()">Annulla</button><button class="config-btn primary" id="saveAllSensorSettings">Salva sensori esterni</button></div>
    </footer>
  </div>
</div>

<div class="server-info" id="serverInfo"></div>
<div style="font-size:11px;color:var(--yellow);text-align:center;margin-top:6px;">
  ⚠ L'integrazione disconnette l'app Gree+ (una sessione per account)
</div>

<script>
__APEXCHARTS_JS__
</script>
<script>
const HA_BASE = window.location.origin;
const PANEL_DATA_URL = HA_BASE + '/api/gree_ac_cloud/panel/data';
const PANEL_CMD_URL = HA_BASE + '/api/gree_ac_cloud/panel/command';
const PANEL_ACTION_LOG_URL = HA_BASE + '/api/gree_ac_cloud/panel/action-log';
const PANEL_NAMES_URL = HA_BASE + '/api/gree_ac_cloud/panel/names';
const PANEL_SETTINGS_URL = HA_BASE + '/api/gree_ac_cloud/panel/settings';
const PANEL_REFRESH_URL = HA_BASE + '/api/gree_ac_cloud/panel/refresh';
const PANEL_DEVICES_INFO_URL = HA_BASE + '/api/gree_ac_cloud/panel/devices-info';
const PANEL_ROOM_SENSORS_URL = HA_BASE + '/api/gree_ac_cloud/panel/room-sensors';
const PANEL_ENERGY_SENSORS_URL = HA_BASE + '/api/gree_ac_cloud/panel/energy-sensors';
const PANEL_INSTALLATION_URL = HA_BASE + '/api/gree_ac_cloud/panel/installation';
const PANEL_HISTORY_URL = HA_BASE + '/api/gree_ac_cloud/panel/history';
const PANEL_PROFILE_URL = HA_BASE + '/api/gree_ac_cloud/panel/profile';

const __README_CONTENT__ = __README_JSON__;
const __CHANGELOG_CONTENT__ = __CHANGELOG_JSON__;
const __DEVICE_NAMES__ = __DEVICE_NAMES_JSON__;

let _rejectedAccessToken = null;
function usableAccessToken(token) {
  return token && token !== _rejectedAccessToken ? token : null;
}
function getAccessToken() {
  // Prefer the live Home Assistant auth object. A token cached in localStorage
  // can remain present after it has expired, especially in long-lived desktop
  // tabs and Android WebView partitions.
  for (const frame of [window.parent, window.top]) {
    try {
      const app = frame.document.querySelector('home-assistant');
      const auth = app && app.hass && app.hass.auth;
      const token = usableAccessToken(auth && (auth.accessToken || (auth.data && auth.data.access_token)));
      if (token) return token;
    } catch (e) {}
  }

  // Storage remains a fallback when the Companion app isolates the iframe
  // from the root frontend object.
  const stores = [];
  try { stores.push(window.localStorage); } catch (e) {}
  try { if (window.parent !== window) stores.push(window.parent.localStorage); } catch (e) {}
  try { if (window.top !== window.parent) stores.push(window.top.localStorage); } catch (e) {}
  for (const store of stores) {
    try {
      const raw = store.getItem('hassTokens');
      const parsed = raw ? JSON.parse(raw) : null;
      const token = usableAccessToken(parsed && parsed.access_token);
      if (token) return token;
    } catch (e) {}
  }
  return null;
}

function authHeaders(extra = {}, token = getAccessToken()) {
  const headers = Object.assign({}, extra);
  if (token) headers.Authorization = 'Bearer ' + token;
  return headers;
}

function liveHomeAssistantAuth() {
  for (const frame of [window.parent, window.top]) {
    try {
      const app = frame.document.querySelector('home-assistant');
      const auth = app && app.hass && app.hass.auth;
      if (auth) return auth;
    } catch (e) {}
  }
  return null;
}

const authDelay = milliseconds => new Promise(resolve => setTimeout(resolve,milliseconds));
async function waitForAccessToken(forceRefresh = false, timeout = 8000) {
  const deadline = Date.now() + timeout;
  let refreshAttempted = false;
  while (Date.now() < deadline) {
    const auth = liveHomeAssistantAuth();
    if (forceRefresh && auth && !refreshAttempted && typeof auth.refreshAccessToken === 'function') {
      refreshAttempted = true;
      try { await auth.refreshAccessToken(); } catch (error) { console.warn('HA token refresh pending:',error); }
    }
    const token = getAccessToken();
    if (token) return token;
    await authDelay(250);
  }
  return null;
}

let _panelAuthFailureShown = false;
let _panelAuthRetryTimer = null;
let _panelAuthRecoveryRunning = false;
function showPanelAuthFailure() {
  if (_panelAuthFailureShown) return;
  _panelAuthFailureShown = true;
  const badge = document.getElementById('statusBadge');
  if (badge) {
    badge.textContent = 'RINNOVO SESSIONE…';
    badge.classList.add('off');
  }
  // Keep the last valid dashboard visible while HA renews its access token.
  // Destroying the cards made a short-lived mobile WebView race look like a
  // permanent logout and forced the user to reload the whole panel.
}
async function recoverPanelAuth() {
  if (_panelAuthRecoveryRunning) return;
  _panelAuthRecoveryRunning = true;
  try {
    const token = await waitForAccessToken(true,10000);
    if (!token) throw new Error('token not available yet');
    _panelAuthFailureShown = false;
    await loadModels();
    await loadNames();
    await loadData();
  } catch (error) {
    schedulePanelAuthRetry();
  } finally {
    _panelAuthRecoveryRunning = false;
  }
}
function schedulePanelAuthRetry(delay = 3000) {
  if (_panelAuthRetryTimer) return;
  _panelAuthRetryTimer = setTimeout(() => {
    _panelAuthRetryTimer = null;
    recoverPanelAuth();
  },delay);
}

async function apiFetch(url, opts = {}) {
  const protectedPanelUrl = url.startsWith(HA_BASE + '/api/gree_ac_cloud/');
  let token = getAccessToken();
  if (!token && protectedPanelUrl) token = await waitForAccessToken(false);
  const request = {...opts,headers:authHeaders(opts.headers || {},token),credentials:'same-origin'};
  if (!request.headers.Authorization && protectedPanelUrl) {
    showPanelAuthFailure();
    schedulePanelAuthRetry();
    const error = new Error('Autenticazione Home Assistant temporaneamente non disponibile');
    error.status = 401;
    throw error;
  }
  let resp = await fetch(url,request);
  if (resp.status === 401 && protectedPanelUrl) {
    const authorization = request.headers.Authorization || '';
    _rejectedAccessToken = authorization.startsWith('Bearer ') ? authorization.slice(7) : null;
    showPanelAuthFailure();
    token = await waitForAccessToken(true,10000);
    if (token) {
      request.headers = authHeaders(opts.headers || {},token);
      resp = await fetch(url,request);
    }
  }
  if (!resp.ok) {
    if (resp.status === 401) schedulePanelAuthRetry();
    const error = new Error(resp.status === 401 ? 'Autenticazione Home Assistant temporaneamente non disponibile' : (resp.statusText || `HTTP ${resp.status}`));
    error.status = resp.status;
    throw error;
  }
  _panelAuthFailureShown = false;
  _rejectedAccessToken = null;
  if (_panelAuthRetryTimer) { clearTimeout(_panelAuthRetryTimer); _panelAuthRetryTimer = null; }
  return resp.json();
}

window.addEventListener('focus',() => { if (_panelAuthFailureShown) recoverPanelAuth(); });
document.addEventListener('visibilitychange',() => { if (!document.hidden && _panelAuthFailureShown) recoverPanelAuth(); });

function sensorOptions(sensors, selected) {
  const chosen = new Set(selected || []);
  return sensors.map(s => `<option value="${escHtml(s.entity_id)}" ${chosen.has(s.entity_id) ? 'selected' : ''}>${escHtml(s.name)} — ${escHtml(s.state)} ${escHtml(s.unit || '')}</option>`).join('');
}

function roomSensorOptions(sensors, selected, mac, kind) {
  const chosen = new Set(selected || []);
  if (!sensors.length) return '<div class="room-sensor-empty">Nessun sensore compatibile disponibile in Home Assistant.</div>';
  return sensors.map(sensor => {
    const entityId = escHtml(sensor.entity_id);
    const checked = chosen.has(sensor.entity_id);
    const unavailable = ['unknown','unavailable'].includes(String(sensor.state).toLowerCase());
    const value = unavailable ? 'Non disponibile' : `${escHtml(sensor.state)}${sensor.unit ? ` ${escHtml(sensor.unit)}` : ''}`;
    return `<label class="room-sensor-option${checked ? ' is-selected' : ''}${unavailable ? ' is-unavailable' : ''}"><input class="room-sensor-checkbox" type="checkbox" name="room-${kind}-${mac}" value="${entityId}" ${checked ? 'checked' : ''}><span class="room-sensor-option-copy"><b>${escHtml(sensor.name)}</b><small>${entityId}</small></span><span class="room-sensor-value">${value}</span></label>`;
  }).join('');
}

function updateRoomSensorCount(group) {
  const count = group.querySelectorAll('.room-sensor-checkbox:checked').length;
  const badge = group.querySelector('.room-sensor-count');
  if (badge) badge.textContent = count === 1 ? '1 selezionato' : `${count} selezionati`;
}

function bindRoomSensorLists() {
  document.querySelectorAll('.room-sensor-group').forEach(group => {
    group.querySelectorAll('.room-sensor-checkbox').forEach(checkbox => {
      checkbox.addEventListener('change', () => {
        checkbox.closest('.room-sensor-option').classList.toggle('is-selected', checkbox.checked);
        updateRoomSensorCount(group);
      });
    });
    const clear = group.querySelector('.room-sensor-clear');
    if (clear) clear.addEventListener('click', () => {
      group.querySelectorAll('.room-sensor-checkbox').forEach(checkbox => {
        checkbox.checked = false;
        checkbox.closest('.room-sensor-option').classList.remove('is-selected');
      });
      updateRoomSensorCount(group);
    });
    updateRoomSensorCount(group);
  });
}

function roomSensorGroup(device, sensors, selected, kind) {
  const isTemperature = kind === 'temp';
  const mac = escHtml(device.mac);
  const title = isTemperature ? 'Temperatura ambiente' : 'Umidità ambiente';
  const subtitle = isTemperature ? 'Sonde usate dalla regolazione termica' : 'Sonde usate per la gestione Dry';
  const icon = isTemperature ? '°C' : '%';
  const help = isTemperature ? 'I valori disponibili vengono mediati per temperatura corrente, profili Smart e storico.' : 'I valori disponibili vengono mediati e confrontati con la soglia di umidità del profilo.';
  return `<section class="room-sensor-group ${isTemperature ? 'temperature' : 'humidity'}" id="room-${kind}-${mac}"><header class="room-sensor-group-head"><span class="room-sensor-kind-icon">${icon}</span><div class="room-sensor-kind-copy"><h4>${title}</h4><p>${subtitle}</p></div><div class="room-sensor-tools"><span class="room-sensor-count">0 selezionati</span><button class="room-sensor-clear" type="button">Deseleziona tutti</button></div></header><div class="room-sensor-list">${roomSensorOptions(sensors,selected,mac,kind)}</div><footer class="room-sensor-group-foot">${help}</footer></section>`;
}

function installationNumberField(mac, key, label, value, unit, step='1') {
  return `<div class="installation-field"><label>${label}</label><input type="number" min="0" step="${step}" data-installation-mac="${mac}" data-installation-key="${key}" value="${value ?? ''}" placeholder="Non indicato"><small>${unit}</small></div>`;
}
function installationSelectField(mac, key, label, value, options) {
  return `<div class="installation-field"><label>${label}</label><select data-installation-mac="${mac}" data-installation-key="${key}"><option value="">Non indicato</option>${options.map(([v,l]) => `<option value="${v}" ${value === v ? 'selected' : ''}>${l}</option>`).join('')}</select></div>`;
}
async function openInstallationSettings() {
  const modal = document.getElementById('installationSettings');
  const content = document.getElementById('installationSettingsContent');
  modal.style.display = 'block';
  content.innerHTML = '<div class="config-loading">Caricamento schede impianto…</div>';
  try {
    const [devices, installations] = await Promise.all([apiFetch(PANEL_DATA_URL), apiFetch(PANEL_INSTALLATION_URL)]);
    content.innerHTML = devices.map(device => {
      const mac = escHtml(device.mac); const cfg = installations[device.mac] || {};
      return `<section class="installation-device" data-installation-device="${mac}"><h3>${escHtml(__DEVICE_NAMES__[device.mac] || device.name)} · <code>${mac}</code></h3><div class="installation-grid">
        ${installationNumberField(mac,'static_pressure_pa','Pressione statica impostata',cfg.static_pressure_pa,'Pa','1')}
        ${installationNumberField(mac,'static_pressure_level','Livello P30',cfg.static_pressure_level,'P1–P9','1')}
        ${installationNumberField(mac,'main_duct_length_m','Condotta principale',cfg.main_duct_length_m,'metri','0.1')}
        ${installationNumberField(mac,'total_duct_length_m','Sviluppo totale condotte',cfg.total_duct_length_m,'metri','0.1')}
        ${installationNumberField(mac,'served_rooms','Locali serviti',cfg.served_rooms,'numero','1')}
        ${installationNumberField(mac,'supply_outlets','Bocchette di mandata',cfg.supply_outlets,'numero','1')}
        ${installationNumberField(mac,'return_grilles','Griglie di ripresa',cfg.return_grilles,'numero','1')}
        ${installationNumberField(mac,'duct_diameter_mm','Diametro equivalente',cfg.duct_diameter_mm,'mm','1')}
        ${installationNumberField(mac,'duct_section_cm2','Sezione condotta',cfg.duct_section_cm2,'cm²','1')}
        ${installationSelectField(mac,'duct_type','Tipo condotte',cfg.duct_type,[['rigid','Rigide'],['flexible','Flessibili'],['mixed','Miste'],['other','Altro']])}
        ${installationSelectField(mac,'supply_outlet_type','Terminali di mandata',cfg.supply_outlet_type,[['grille','Griglie'],['diffuser','Diffusori'],['slot','Feritoie lineari'],['mixed','Misti'],['other','Altro']])}
        ${installationSelectField(mac,'return_grille_type','Tipo ripresa',cfg.return_grille_type,[['grille','Griglia'],['filter_grille','Griglia portafiltro'],['mixed','Mista'],['other','Altro']])}
        ${installationSelectField(mac,'filter_type','Filtrazione',cfg.filter_type,[['none','Nessuna'],['standard','Standard'],['high_efficiency','Alta efficienza'],['other','Altro']])}
        <div class="installation-field wide"><label>Note installazione</label><textarea data-installation-mac="${mac}" data-installation-key="notes" maxlength="1000">${escHtml(cfg.notes || '')}</textarea></div>
        <p class="installation-note">Dato descrittivo: non è una misura in tempo reale e non modifica automaticamente la pressione statica della macchina.</p>
      </div></section>`;
    }).join('');
    document.getElementById('saveInstallationSettings').onclick = () => saveInstallationSettings(devices);
  } catch (error) { content.innerHTML = `<div class="config-loading">Errore: ${escHtml(error.message)}</div>`; }
}
function closeInstallationSettings() { document.getElementById('installationSettings').style.display = 'none'; }
async function saveInstallationSettings(devices) {
  const button = document.getElementById('saveInstallationSettings'); const status = document.getElementById('installationSettingsStatus'); button.disabled = true;
  try {
    for (const device of devices) {
      const body = {mac:device.mac};
      document.querySelectorAll(`[data-installation-mac="${device.mac}"]`).forEach(input => { if (input.value !== '') body[input.dataset.installationKey] = input.value; });
      await apiFetch(PANEL_INSTALLATION_URL,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    }
    status.textContent = 'Schede salvate senza ricaricare l’integrazione.'; setTimeout(() => { closeInstallationSettings(); loadData(); }, 700);
  } catch (error) { status.textContent = `Errore: ${error.message}`; } finally { button.disabled = false; }
}

async function openEnergySensorSettings() {
  const modal = document.getElementById('energySensorSettings');
  const content = document.getElementById('energySensorSettingsContent');
  modal.style.display = 'block';
  content.className = 'config-loading';
  content.textContent = 'Caricamento sensori elettrici…';
  try {
    const data = await apiFetch(PANEL_ENERGY_SENSORS_URL);
    const powers = data.sensors.filter(sensor => sensor.device_class === 'power');
    const energies = data.sensors.filter(sensor => sensor.device_class === 'energy');
    content.className = 'room-sensor-settings';
    content.innerHTML = data.devices.map(device => {
      const mac = escHtml(device.mac);
      const name = escHtml(__DEVICE_NAMES__[device.mac] || device.name || device.mac);
      return `<section class="room-sensor-device" data-entry-id="${escHtml(device.entry_id)}" data-mac="${mac}"><div class="room-sensor-device-head"><div><h3>${name}</h3><code>${mac}</code></div><span class="config-device-status" id="energy-status-${mac}">Stima mantenuta</span></div><div class="room-sensor-grid"><section class="outdoor-sensor-card"><div class="outdoor-sensor-copy"><span class="config-section-title">POTENZA EFFETTIVA</span><h3>Canale del contatore</h3><p>Sensore istantaneo in W. Verrà tracciato insieme alla potenza stimata.</p></div><select class="config-select actual-power-sensor"><option value="">Nessun sensore di potenza</option>${sensorOptions(powers,[device.actual_power_sensor])}</select></section><section class="outdoor-sensor-card"><div class="outdoor-sensor-copy"><span class="config-section-title">ENERGIA EFFETTIVA</span><h3>Contatore cumulativo</h3><p>Sensore in kWh facoltativo. Resta separato dall’energia stimata.</p></div><select class="config-select actual-energy-sensor"><option value="">Nessun sensore energia</option>${sensorOptions(energies,[device.actual_energy_sensor])}</select></section></div></section>`;
    }).join('');
    document.getElementById('saveEnergySensorSettings').onclick = () => saveEnergySensorAssociations(data.devices);
  } catch (error) {
    content.className = 'config-loading';
    content.textContent = `Impossibile caricare i sensori elettrici: ${error.message}`;
  }
}
function closeEnergySensorSettings() { document.getElementById('energySensorSettings').style.display = 'none'; }
async function saveEnergySensorAssociations(devices) {
  const button = document.getElementById('saveEnergySensorSettings');
  const status = document.getElementById('energySensorSettingsStatus');
  button.disabled = true;
  try {
    for (const device of devices) {
      const card = document.querySelector(`#energySensorSettingsContent [data-mac="${device.mac}"]`);
      await apiFetch(PANEL_ENERGY_SENSORS_URL,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({entry_id:device.entry_id,mac:device.mac,actual_power_sensor:card.querySelector('.actual-power-sensor').value,actual_energy_sensor:card.querySelector('.actual-energy-sensor').value})});
    }
    status.textContent = 'Associazioni salvate. Stime e misure reali resteranno entrambe visibili.';
    setTimeout(() => { closeEnergySensorSettings(); clearPersistentHistory(); loadData(); },800);
  } catch (error) { status.textContent = `Errore: ${error.message}`; } finally { button.disabled = false; }
}

async function openRoomSensorSettings() {
  const modal = document.getElementById('roomSensorSettings');
  const content = document.getElementById('roomSensorSettingsContent');
  modal.style.display = 'block';
  content.className = 'config-loading';
  content.textContent = 'Caricamento sensori interni…';
  try {
    const data = await apiFetch(PANEL_ROOM_SENSORS_URL);
    const temperatures = data.sensors.filter(sensor => sensor.device_class === 'temperature');
    const humidities = data.sensors.filter(sensor => sensor.device_class === 'humidity');
    content.className = 'room-sensor-settings';
    content.innerHTML = data.devices.map(device => {
      const mac = escHtml(device.mac);
      const name = escHtml(__DEVICE_NAMES__[device.mac] || device.name || device.mac);
      return `<section class="room-sensor-device" data-entry-id="${escHtml(device.entry_id)}" data-mac="${mac}"><div class="room-sensor-device-head"><div><h3>${name}</h3><code>${mac}</code></div><span class="config-device-status" id="room-status-${mac}">Pronto</span></div><div class="room-sensor-grid">${roomSensorGroup(device,temperatures,device.temperature_sensors,'temp')}${roomSensorGroup(device,humidities,device.humidity_sensors,'hum')}</div></section>`;
    }).join('');
    bindRoomSensorLists();
    document.getElementById('saveRoomSensorSettings').onclick = () => saveRoomSensorAssociations(data.devices);
  } catch (error) {
    content.className = 'config-loading';
    content.textContent = `Impossibile caricare i sensori interni: ${error.message}`;
  }
}
function closeRoomSensorSettings() {
  document.getElementById('roomSensorSettings').style.display = 'none';
}
async function saveRoomSensorAssociations(devices) {
  const button = document.getElementById('saveRoomSensorSettings');
  const status = document.getElementById('roomSensorSettingsStatus');
  const selected = id => [...document.querySelectorAll(`#${id} .room-sensor-checkbox:checked`)].map(checkbox => checkbox.value);
  button.disabled = true;
  status.textContent = 'Salvataggio associazioni…';
  try {
    for (const device of devices) {
      const mac = device.mac;
      await apiFetch(PANEL_ROOM_SENSORS_URL,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({entry_id:device.entry_id,mac,temperature_sensors:selected(`room-temp-${mac}`),humidity_sensors:selected(`room-hum-${mac}`),outdoor_temperature_sensor:device.outdoor_temperature_sensor || '',outdoor_humidity_sensor:device.outdoor_humidity_sensor || ''})});
      const deviceStatus = document.getElementById(`room-status-${mac}`);
      if (deviceStatus) deviceStatus.textContent = 'Salvato';
    }
    status.textContent = 'Associazioni salvate. Ricarica integrazione in corso…';
    setTimeout(() => { closeRoomSensorSettings(); loadData(); },1600);
  } catch (error) {
    status.textContent = `Errore: ${error.message}`;
  } finally {
    button.disabled = false;
  }
}

async function openSensorSettings() {
  const modal = document.getElementById('sensorSettings');
  const content = document.getElementById('sensorSettingsContent');
  modal.style.display = 'block';
  content.className = 'config-loading';
  content.textContent = 'Caricamento sensori esterni…';
  try {
    const data = await apiFetch(PANEL_ROOM_SENSORS_URL);
    const temperatures = data.sensors.filter(sensor => sensor.device_class === 'temperature');
    const humidities = data.sensors.filter(sensor => sensor.device_class === 'humidity');
    const outdoor = data.devices.find(device => device.outdoor_temperature_sensor)?.outdoor_temperature_sensor || '';
    const outdoorHumidity = data.devices.find(device => device.outdoor_humidity_sensor)?.outdoor_humidity_sensor || '';
    content.className = '';
    content.innerHTML = `<div class="outdoor-sensor-settings"><section class="outdoor-sensor-card"><div class="outdoor-sensor-copy"><span class="config-section-title">TEMPERATURA ESTERNA</span><h3>Riferimento termico esterno</h3><p>Usato dalla compensazione dei profili e dallo storico. Se il dato è più vecchio di tre ore viene ignorato dalla regolazione Smart.</p></div><select class="config-select" id="outdoorSensor"><option value="">Nessun sensore esterno</option>${sensorOptions(temperatures,[outdoor])}</select></section><section class="outdoor-sensor-card"><div class="outdoor-sensor-copy"><span class="config-section-title">UMIDITÀ ESTERNA</span><h3>Riferimento igrometrico esterno</h3><p>Registrato nei grafici per leggere le condizioni meteo. Non sostituisce il sensore di umidità interno usato per decidere Dry.</p></div><select class="config-select" id="outdoorHumiditySensor"><option value="">Nessun sensore esterno</option>${sensorOptions(humidities,[outdoorHumidity])}</select></section><div class="profile-callout"><b>Ambiente interno</b><br>I sensori interni restano associati dalle opzioni dell’integrazione. Giorno, Notte e Assente si modificano esclusivamente nella pagina Profili, così questo modale non può più sovrascriverli.</div></div>`;
    document.getElementById('saveAllSensorSettings').onclick = () => saveOutdoorSensors(data.devices);
  } catch (error) {
    content.className = 'config-loading';
    content.textContent = `Impossibile caricare i sensori esterni: ${error.message}`;
  }
}

function closeSensorSettings() {
  document.getElementById('sensorSettings').style.display = 'none';
}

async function saveOutdoorSensors(devices) {
  const button = document.getElementById('saveAllSensorSettings');
  const globalStatus = document.getElementById('sensorSettingsGlobalStatus');
  button.disabled = true;
  globalStatus.textContent = 'Salvataggio sensori esterni…';
  const outdoor = document.getElementById('outdoorSensor').value;
  const outdoorHumidity = document.getElementById('outdoorHumiditySensor').value;
  try {
    for (const device of devices) {
      await apiFetch(PANEL_ROOM_SENSORS_URL,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({entry_id:device.entry_id,mac:device.mac,temperature_sensors:device.temperature_sensors || [],humidity_sensors:device.humidity_sensors || [],outdoor_temperature_sensor:outdoor,outdoor_humidity_sensor:outdoorHumidity})});
    }
    globalStatus.textContent = 'Sensori esterni salvati. Ricarica integrazione in corso…';
    setTimeout(() => { closeSensorSettings(); loadData(); }, 1600);
  } catch (error) {
    globalStatus.textContent = `Errore: ${error.message}`;
  } finally {
    button.disabled = false;
  }
}

async function sendCommand(mac, options, values) {
  try {
    const result = await apiFetch(PANEL_CMD_URL, {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ mac, options, values }),
    });
    return result.ok;
  } catch (e) {
    console.error('Command failed:', e);
    return false;
  }
}

function formatRuntime(seconds) {
  const value = Math.max(0, Number(seconds) || 0);
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  return `${hours.toLocaleString('it-IT')} h ${String(minutes).padStart(2,'0')} min`;
}
async function resetRuntime(mac) {
  if (!window.confirm('Azzerare le ore totali di accensione di questa unità? Il consumo energetico non verrà azzerato.')) return;
  try {
    await apiFetch(PANEL_CMD_URL,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mac,reset_runtime:true})});
    await loadData();
  } catch (error) { alert('Azzeramento ore non riuscito: ' + error.message); }
}

function parseProbeTemp(val) {
  if (val == null || val === undefined) return null;
  const raw = Number(val);
  return Number.isFinite(raw) && raw !== 0 ? raw - 40 : null;
}

const MODELS = {
  'GUD35': { cool: 1.03, heat: 1.00, max: 1.30, btus: '12K', name: 'GUD35 (12K BTU/3.5kW)' },
  'GUD50': { cool: 1.51, heat: 1.42, max: 1.90, btus: '18K', name: 'GUD50 (18K BTU/5.0kW)' },
  'GUD71': { cool: 1.92, heat: 2.00, max: 2.80, btus: '24K', name: 'GUD71 (24K BTU/7.1kW)' },
  'GUD85': { cool: 2.50, heat: 2.25, max: 3.30, btus: '29K', name: 'GUD85 (29K BTU/8.5kW)' },
  'GUD100': { cool: 3.00, heat: 2.80, max: 4.70, btus: '36K', name: 'GUD100 (36K BTU/10.5kW)' },
  'GUD140': { cool: 4.60, heat: 4.70, max: 5.60, btus: '46K', name: 'GUD140 (46K BTU/13.4kW)' },
  'GUD160': { cool: 5.40, heat: 4.70, max: 6.80, btus: '54K', name: 'GUD160 (55K BTU/16.0kW)' },
};

let _serverModels = {};
async function loadModels() {
  try {
    _serverModels = await apiFetch(HA_BASE + '/api/gree_ac_cloud/panel/models');
  } catch (e) {
    console.warn('Failed to load server models, using localStorage:', e);
    _serverModels = {};
  }
}
async function loadNames() {
  try {
    Object.assign(
      __DEVICE_NAMES__,
      await apiFetch(HA_BASE + '/api/gree_ac_cloud/panel/names')
    );
  } catch (e) {
    console.warn('Failed to load device names:', e);
  }
}
function getModel(mac) { const k = _serverModels[mac] || localStorage.getItem('model_' + mac) || ''; return MODELS[k] || null; }
function getModelKey(mac) { return _serverModels[mac] || localStorage.getItem('model_' + mac) || ''; }
async function setModel(mac, val) {
  _serverModels[mac] = val;
  localStorage.setItem('model_' + mac, val);
  try {
    await apiFetch(HA_BASE + '/api/gree_ac_cloud/panel/models', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({mac, model: val}) });
  } catch (e) {
    console.warn('setModel server-side failed:', e);
  }
  loadData();
}

async function renameDevice(mac) {
  const current = __DEVICE_NAMES__[mac] || '';
  const name = prompt('Nome personalizzato per ' + mac, current);
  if (name === null) return;
  try {
    await apiFetch(HA_BASE + '/api/gree_ac_cloud/panel/names', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({mac, name})
    });
    __DEVICE_NAMES__[mac] = name;
  } catch (e) {
    console.warn('renameDevice failed:', e);
  }
  loadData();
}

async function setPollInterval(val) {
  try {
    await apiFetch(PANEL_SETTINGS_URL, {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({update_interval: parseInt(val)})
    });
  } catch (e) {
    console.warn('setPollInterval failed:', e);
  }
}

async function refreshNow() {
  try {
    await apiFetch(PANEL_REFRESH_URL, { method: 'POST' });
  } catch (e) {
    console.warn('refreshNow failed:', e);
  }
  loadData();
}

function estimatePower(s, model) {
  if (!s || !s.Pow || !model) return 0;
  const mode = Number(s.Mod);
  if (mode === 3) return Math.round(model.cool * 0.05 * 100) / 100;
  let dred = Number(s.DRED || 0);
  const iDemandActive = Number(s.Idemand || 0) === 1;
  if (dred === 0 && iDemandActive) dred = 1;
  if (dred === 1) return Math.round(model.cool * 0.05 * 100) / 100;
  const base = (mode === 2) ? model.heat : model.cool;
  let duty = mode === 4 ? 0.55 : 0.70;
  if (dred === 2) duty = Math.min(duty, 0.50);
  if (dred === 3) duty = Math.min(duty, 0.75);
  if (s.Quiet) duty *= 0.85;
  if (s.Tur) duty = Math.min(1.0, duty * 1.20);
  return Math.round(Math.min(base * duty, model.max) * 100) / 100;
}

// Track kWh per device (in memory)
const _kwhTracker = {};

function renderDevice(d) {
  const s = d.state || {};
  const pow = s.Pow;
  const mod = s.Mod;
  const tem = s.SetDeciTem != null ? (s.SetDeciTem / 10).toFixed(1) : (s.SetTem || '--');
  const inTem = s.InTem;
  const outTem = s.OutTem;
  const measuredAir = s.InTem;
  const nativeRoomTemp = parseProbeTemp(measuredAir);
  const externalRoomTemp = s.RoomTemperature;
  const roomTemp = externalRoomTemp != null
    ? Number(externalRoomTemp)
    : nativeRoomTemp;
  const probeIn = parseProbeTemp(inTem);
  const probeOut = parseProbeTemp(outTem);
  const nativeHumidity = Number(s.InHumiEn) === 1 && Number(s.InHumi) > 0
    ? Number(s.InHumi) : null;
  const inHumi = s.RoomHumidity != null ? Number(s.RoomHumidity) : nativeHumidity;
  const fan = s.WdSpd;
  const swingV = s.SwUpDn;
  const swingH = s.SwingLfRig;
  const connected = d.connected;
  const iDemandActive = s.IdemandActive === true || Number(s.Idemand || 0) === 1;
  const effectiveDred = s.DREDEffective != null
    ? Number(s.DREDEffective)
    : (Number(s.DRED || 0) === 0 && iDemandActive ? 1 : Number(s.DRED || 0));
  const startupDred = s.StartupDRED == null ? 'none' : String(s.StartupDRED);
  const safeMac = escHtml(String(d.mac || ''));
  const modelKey = getModelKey(d.mac);
  const model = MODELS[modelKey] || null;
  const estPower = s.estimated_power_w != null
    ? Number(s.estimated_power_w) / 1000 : estimatePower(s, model);
  
  // Track kWh: accumulate when card is rendered (every ~10s)
  if (pow && modelKey && estPower > 0) {
    if (!_kwhTracker[d.mac]) _kwhTracker[d.mac] = { lastRender: Date.now(), kwh: 0 };
    const t = _kwhTracker[d.mac];
    const elapsed = (Date.now() - t.lastRender) / 3600000;
    if (elapsed > 0.001) t.kwh += estPower * elapsed;
    t.lastRender = Date.now();
  } else if (!pow) {
    if (_kwhTracker[d.mac]) _kwhTracker[d.mac].kwh = 0;
  }
  const totalKwh = s.estimated_energy_kwh != null
    ? Number(s.estimated_energy_kwh).toFixed(2)
    : ((_kwhTracker[d.mac] && _kwhTracker[d.mac].kwh)
      ? _kwhTracker[d.mac].kwh.toFixed(2) : '0.00');

  const modeCls = ['auto','cool','heat','fan','dry'];
  const modeLabels = ['Auto','Cool','Heat','Fan','Dry'];
  const modeTips = [
    'Auto: regola automaticamente fresco/caldo in base alla temperatura ambiente',
    'Cool: raffrescamento — abbassa la temperatura',
    'Heat: riscaldamento — alza la temperatura',
    'Fan: solo ventilazione — senza raffrescare o scaldare',
    'Dry: deumidifica — riduce l\'umidità mantenendo fresco'
  ];
  const fanTips = [
    'Auto: velocità regolata automaticamente dal device',
    'Bassa: ventilazione minima, silenzioso',
    'Media-Bassa: leggermente più potente',
    'Media: ventilazione media, bilanciato',
    'Media-Alta: ventilazione sostenuta',
    'Alta: massima velocità ordinaria',
    'Turbo: massima portata, WdSpd=6 e Tur=1'
  ];
  const modeName = modeLabels[Number(mod)] || 'Sconosciuta';
  const activePreset = s.ActivePreset || null;
  const enabledPresets = Object.entries(s.Presets || {}).filter(([,p]) => p && p.enabled);

  const switchTips = {
    Health: 'Health: ionizzatore / purificazione aria',
    Quiet: 'Quiet: modalità silenziosa, riduce rumore ventola',
    Tur: 'Turbo: massima potenza velocemente',
    StHt: 'Strong Heat: riscaldamento intenso per ambienti grandi',
    Blo: 'X-Fan: asciuga la batteria dopo Cool/Dry per limitare muffe',
    Air: 'Fresh Air: ricambio aria opzionale, se supportato dall’unità',
    SvSt: 'Energy Save: risparmio energetico',
    SlpMod: 'Sleep: regola temperatura gradualmente durante la notte',
    Lig: 'Light: retroilluminazione display controller',
  };

  const deviceName = escHtml(__DEVICE_NAMES__[d.mac] || d.name || 'Condizionatore');

  let curSwing = 'off';
  if (swingV && swingH) curSwing = 'both';
  else if (swingV) curSwing = 'v';
  else if (swingH) curSwing = 'h';

  return `
<div class="card${pow ? ' on' : ''}" data-mac="${escHtml(d.mac)}" style="position:relative">
  <div class="card-header">
    <div class="header-row1">
      <div class="name-group">
        <span class="icon-ac"><svg viewBox="0 0 24 24"><path d="M22 11h-4.17l3.24-3.24-1.41-1.42L15 11h-2V9l4.66-4.66-1.42-1.41L13 6.17V2h-2v4.17L7.76 2.93 6.34 4.34 11 9v2H9L4.34 6.34 2.93 7.76 6.17 11H2v2h4.17l-3.24 3.24 1.41 1.42L9 13h2v2l-4.66 4.66 1.42 1.41L11 17.83V22h2v-4.17l3.24 3.24 1.42-1.41L13 15v-2h2l4.66 4.66 1.41-1.42L17.83 13H22z"/></svg></span>
        <h2 class="device-name" ondblclick="renameDevice('${safeMac}')" title="Doppio click per rinominare">${deviceName}</h2>
      </div>
      <span class="conn-badge${!connected ? ' off' : ''}">${connected ? '● online' : '○ offline'}</span>
    </div>
    <div class="header-row2">
      <span class="mac-label">${escHtml(d.mac)}</span>
      <select class="model-select" onchange="setModel('${safeMac}', this.value)" title="Seleziona modello per stima consumi">
        <option value="">— modello —</option>
        ${Object.entries(MODELS).map(([k,v]) => `<option value="${k}" ${modelKey === k ? 'selected' : ''}>${v.name}</option>`).join('')}
      </select>
    </div>
  </div>

  <div class="dashboard-summary">
    <div class="summary-tile" title="${externalRoomTemp != null ? `Media di ${s.RoomTemperatureSensors?.length || 0} sensori HA selezionati` : 'Fallback sensore unità'}">
      <div class="summary-value">${roomTemp != null ? roomTemp.toFixed(1) : '--'}°</div>
      <div class="summary-label">Temperatura ambiente${externalRoomTemp != null ? ' · media HA' : ''}</div>
    </div>
    <div class="summary-tile" title="${s.RoomHumidity != null ? 'Media dei sensori umidità HA selezionati' : 'Sensore nativo Gree'}">
      <div class="summary-value">${inHumi != null ? Number(inHumi).toFixed(1) + '%' : '--'}</div>
      <div class="summary-label">Umidità ambiente${s.RoomHumidity != null ? ' · media HA' : ''}</div>
    </div>
    <div class="summary-tile">
      <div class="summary-value">${tem}°</div><div class="summary-label">Temperatura obiettivo</div>
    </div>
    <div class="summary-tile">
      <div class="summary-value">${pow ? modeName : 'Spento'}</div><div class="summary-label">Stato e modalità</div>
    </div>
    <div class="summary-tile">
      <div class="summary-value">${s.OutdoorTemperature != null ? Number(s.OutdoorTemperature).toFixed(1) + '°' : '--'}</div><div class="summary-label">Temperatura esterna HA</div>
    </div>
    <div class="summary-tile">
      <div class="summary-value">${activePreset ? ({day:'Giorno',night:'Notte',away:'Assente'}[activePreset] || activePreset) : 'Manuale'}</div><div class="summary-label">Profilo attivo</div>
    </div>
  </div>

  ${modelKey ? `<div class="power-row">
    <div class="p-item"><div class="p-val">${estPower.toFixed(2)} kW</div><div class="p-label">Stima, non misurata</div></div>
    <div class="p-item"><div class="p-val">${totalKwh} kWh</div><div class="p-label">Stima integrata HA</div></div>
    <div class="p-item"><div class="p-val">${(estPower * 730).toFixed(0)} kWh</div><div class="p-label">Mese stimato</div></div>
  </div>` : ''}

  <div class="controls">
    <section class="control-section"><div class="section-title">Accensione e modalità</div>
    <div class="control-row">
      <label>Accensione</label>
      <div class="btn-group">
        <button class="btn ${!pow ? 'danger active' : ''}" onclick="setPower('${safeMac}',0)" title="Spegne il condizionatore">Off</button>
        <button class="btn ${pow ? 'active' : ''}" onclick="setPower('${safeMac}',1)" title="Accende il condizionatore">On</button>
      </div>
    </div>

    <div class="control-row">
      <label>Modalità</label>
      <div class="btn-group">
        ${[0,1,2,3,4].map(i => `<button class="btn mode-${modeCls[i]} ${mod === i && pow ? 'active' : ''}" onclick="setMode('${safeMac}',${i})" title="${modeTips[i]}">${modeLabels[i]}</button>`).join('')}
      </div>
    </div>

    </section>

    ${enabledPresets.length ? `<section class="control-section"><div class="section-title">Profili ambiente</div><div class="preset-quick">
      ${enabledPresets.map(([name]) => `<button class="btn ${activePreset === name ? 'active' : ''}" onclick="setPreset('${safeMac}','${name}')">${{day:'Giorno',night:'Notte',away:'Assente'}[name] || name}</button>`).join('')}
    </div><div class="state-line">I profili applicano target, soglie e I-Demand configurati con <svg viewBox="0 0 24 24" width="10" height="10" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/></svg>.</div></section>` : `<section class="control-section"><div class="section-title">Profili ambiente</div><div class="state-line">Nessun profilo abilitato. Configurali dal pulsante <svg viewBox="0 0 24 24" width="10" height="10" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/></svg> in alto.</div></section>`}

    <section class="control-section"><div class="section-title">Sonde native Gree</div>
    <div class="control-row">
      <label>Temperatura interna</label>
      <div class="btn-group"><button class="btn ${Number(s.InTemEn) === 1 ? 'active' : ''}" onclick="toggleNativeSensor('${safeMac}','InTemEn')">${Number(s.InTemEn) === 1 ? 'Abilitata' : 'Disabilitata'}</button></div>
      <span class="state-line">${nativeRoomTemp != null ? nativeRoomTemp.toFixed(1) + ' °C (InTem ' + escHtml(String(measuredAir)) + ' − 40)' : 'Nessun valore valido'}</span>
    </div>
    <div class="control-row">
      <label>Umidità interna</label>
      <div class="btn-group"><button class="btn ${Number(s.InHumiEn) === 1 ? 'active' : ''}" onclick="toggleNativeSensor('${safeMac}','InHumiEn')">${Number(s.InHumiEn) === 1 ? 'Abilitata' : 'Disabilitata'}</button></div>
      <span class="state-line">${nativeHumidity != null ? nativeHumidity.toFixed(1) + '%' : 'Nessun valore valido'}</span>
    </div>
    </section>

    <section class="control-section"><div class="section-title">Comfort</div>
    <div class="control-row">
      <label>Temperatura</label>
      <div class="temp-control">
        <button onclick="setTemp('${safeMac}',-0.5)" title="Abbassa la temperatura di 0.5°C">−</button>
        <span class="temp-value">${tem}°</span>
        <button onclick="setTemp('${safeMac}',0.5)" title="Alza la temperatura di 0.5°C">+</button>
      </div>
    </div>

    <div class="control-row">
      <label>Fan</label>
      <div class="btn-group">
        ${[0,1,2,3,4,5].map(v => `<button class="btn ${fan === v && pow ? 'active' : ''}" onclick="setFan('${safeMac}',${v})" title="${fanTips[v]}">${['Auto','Bassa','M-Bassa','Media','M-Alta','Alta'][v]}</button>`).join('')}
      </div>
    </div>

    <div class="control-row">
      <label>Swing</label>
      <div class="btn-group">
        <button class="btn ${curSwing === 'off' && pow ? 'active' : ''}" onclick="setSwing('${safeMac}','off')" title="Swing disattivato">Off</button>
        <button class="btn ${curSwing === 'v' && pow ? 'active' : ''}" onclick="setSwing('${safeMac}','v')" title="Swing verticale: palette su/giù">V</button>
        <button class="btn ${curSwing === 'h' && pow ? 'active' : ''}" onclick="setSwing('${safeMac}','h')" title="Swing orizzontale: palette destra/sinistra">H</button>
        <button class="btn ${curSwing === 'both' && pow ? 'active' : ''}" onclick="setSwing('${safeMac}','both')" title="Swing verticale + orizzontale">Both</button>
      </div>
    </div>

    <div class="control-row">
      <label>Extra</label>
      <div class="switches">
        ${Object.entries({
          Health:'Health', Quiet:'Quiet', Tur:'Turbo', StHt:'S.Heat',
          Blo:'X-Fan', SvSt:'E.Save', SlpMod:'Sleep', Air:'Fresh Air', Lig:'Light'
        }).filter(([k]) => Object.prototype.hasOwnProperty.call(s, k))
          .map(([k,l]) => `<button class="switch-btn ${(d.state||{})[k] ? 'on' : ''}" onclick="toggleSwitch('${safeMac}','${k}')" title="${switchTips[k] || k}">${l}</button>`).join('')}
      </div>
    </div>

    </section>

    ${s.DREDEn === 1 && s.DRED !== undefined ? `<section class="control-section"><div class="section-title">Limite potenza I-Demand</div><div class="control-row">
      <label>Adesso</label>
      <div class="btn-group">
        ${[['Off',0],['D1',1],['D2',2],['D3',3]].map(([label,value]) =>
          `<button class="btn ${effectiveDred === value ? 'active' : ''}" onclick="setDred('${safeMac}',${value})" title="${value === 0 ? 'Nessun limite I-Demand: piena capacità disponibile' : value === 1 ? 'D1: compressore disabilitato; non raffresca attivamente' : value === 2 ? 'D2: domanda elettrica limitata a non oltre il 50%' : 'D3: domanda elettrica limitata a non oltre il 75%'}">${label}</button>`
        ).join('')}
        ${iDemandActive ? '<span class="switch-btn on" title="Flag I-Demand separato riportato dal dispositivo">I-Demand attivo</span>' : ''}
      </div>
      <span style="color:${effectiveDred > 0 ? 'var(--green)' : 'var(--text-secondary)'};font-size:11px;font-weight:${effectiveDred > 0 ? '600' : '400'};">Stato effettivo: ${effectiveDred > 0 ? `D${effectiveDred} attivo${iDemandActive ? ' (I-Demand)' : ''}` : 'Off · piena capacità'} · D1 ferma il compressore e non viene mai scelto da Smart durante una richiesta Cool · D2: max 50% · D3: max 75%.</span>
      <label style="margin-top:8px;">Alla prossima accensione</label>
      <div class="btn-group">
        ${[['Nessuna','none'],['Off','0'],['D1','1'],['D2','2'],['D3','3']].map(([label,value]) =>
          `<button class="btn ${startupDred === value ? 'active' : ''}" onclick="setStartupDred('${safeMac}','${value}')" title="${value === 'none' ? 'Non modifica I-Demand all’accensione' : `Applica ${label} a ogni accensione in Cool, anche dal comando a muro`}">${label}</button>`
        ).join('')}
      </div>
      <span style="color:var(--text-secondary);font-size:11px;">Preferenza persistente: si applica dopo l’accensione in Cool da HA o dal monitor a muro.</span>
    </div></section>` : ''}

    <details class="compact-details"><summary>Dettagli tecnici e sonde diagnostiche</summary>
      <div class="state-line">Sonde IDU/ODU (raw − 40): ${probeIn != null ? probeIn.toFixed(1) : '--'}° / ${probeOut != null ? probeOut.toFixed(1) : '--'}° · Sensori HA temperatura: ${s.RoomTemperatureSensors?.length || 0} · umidità: ${s.RoomHumiditySensors?.length || 0}</div>
    ${['Errcode','ErrType','RefLeak','MSysStatus','CleanState','CleanTime','FClTime','CleanDataFlag']
      .some(k => s[k] !== undefined && s[k] !== null) ? `<div class="control-row">
      <label>Stati</label>
      <div class="switches">
        ${[
          ['Errcode','Errore'], ['ErrType','Tipo'], ['RefLeak','Refrigerante'],
          ['MSysStatus','Sistema'], ['CleanState','Auto Clean'],
          ['CleanTime','Tempo filtro'], ['FClTime','Intervallo filtro'],
          ['CleanDataFlag','Avviso filtro']
        ].filter(([k]) => s[k] !== undefined && s[k] !== null)
          .map(([k,l]) => `<span class="switch-btn" title="${k}">${l}: ${escHtml(Array.isArray(s[k]) ? s[k].join(', ') : String(s[k]))}</span>`).join('')}
      </div>
    </div>` : ''}
    </details>
  </div>
</div>`;
}

__PANEL_HISTORY_JS__
function renderOperationsDevice(d) {
  const s = d.state || {};
  const pow = Number(s.Pow || 0) === 1;
  const mod = Number(s.Mod || 0);
  const target = s.SetDeciTem != null ? (Number(s.SetDeciTem) / 10).toFixed(1) : (s.SetTem || '--');
  const measuredAir = s.InTem;
  const roomTemp = s.RoomTemperature != null
    ? Number(s.RoomTemperature)
    : parseProbeTemp(measuredAir);
  const humidity = s.RoomHumidity != null
    ? Number(s.RoomHumidity)
    : (Number(s.InHumiEn) === 1 && Number(s.InHumi) > 0 ? Number(s.InHumi) : null);
  const probeIn = parseProbeTemp(s.InTem);
  const probeOut = parseProbeTemp(s.OutTem);
  const safeMac = escHtml(String(d.mac || ''));
  const modelKey = getModelKey(d.mac);
  const model = MODELS[modelKey] || null;
  const estPower = s.estimated_power_w != null
    ? Number(s.estimated_power_w) / 1000
    : estimatePower(s, model);
  const energy = s.estimated_energy_kwh != null
    ? Number(s.estimated_energy_kwh).toFixed(2)
    : ((_kwhTracker[d.mac] && _kwhTracker[d.mac].kwh)
      ? _kwhTracker[d.mac].kwh.toFixed(2) : '0.00');
  const modeNames = ['Automatico', 'Raffrescamento', 'Riscaldamento', 'Ventilazione', 'Deumidificazione'];
  const modeShort = ['AUTO', 'FREDDO', 'CALDO', 'VENTOLA', 'DRY'];
  const modeIcons = [
    '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 3v6h-6"/></svg>',
    '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12h20M12 2v20"/><path d="m20 16-4-4 4-4"/><path d="M4 8l4 4-4 4"/><path d="m16 4-4 4-4-4"/><path d="m8 20 4-4 4 4"/></svg>',
    '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/></svg>',
    '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12.8 19.6A2 2 0 1 0 14 16H2"/><path d="M17.5 8a2.5 2.5 0 1 1 2 4H2"/><path d="M9.8 4.4A2 2 0 1 1 11 8H2"/></svg>',
    '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 22a7 7 0 0 0 7-7c0-2-1-3.9-3-5.5s-3.5-4-4-6.5c-.5 2.5-2 4.9-4 6.5C6 11.1 5 13 5 15a7 7 0 0 0 7 7z"/></svg>',
  ];
  const modeClasses = ['auto', 'cool', 'heat', 'fan', 'dry'];
  const modeName = modeNames[mod] || 'Sconosciuta';
  const activePreset = s.ActivePreset || null;
  const enabledPresets = Object.entries(s.Presets || {}).filter(([, preset]) => preset && preset.enabled);
  const iDemandActive = s.IdemandActive === true || Number(s.Idemand || 0) === 1;
  const effectiveDred = s.DREDEffective != null
    ? Number(s.DREDEffective)
    : (Number(s.DRED || 0) === 0 && iDemandActive ? 1 : Number(s.DRED || 0));
  const startupDred = s.StartupDRED == null ? 'none' : String(s.StartupDRED);
  const errorCode = Number(s.Errcode || 0);
  const deviceName = __DEVICE_NAMES__[d.mac] || d.name || 'Gree AC';
  const presetLabels = { day: 'GIORNO', night: 'NOTTE', away: 'ASSENTE', manual: 'MANUALE' };
  const override = s.smart_manual_power_override;
  const profileEnabled = s.profile_control_enabled !== false;
  const alerts = [
    !profileEnabled || activePreset === 'manual' ? '<span class="ops-alert manual">Controllo manuale</span>' : '',
    override === false ? '<span class="ops-alert">Override manuale: spento</span>' : '',
    override === true ? '<span class="ops-alert">Override manuale: acceso</span>' : '',
    s.smart_dred_level ? `<span class="ops-alert ${s.smart_dred_verified === false ? '' : 'manual'}">I-Demand Smart: ${escHtml(s.smart_dred_level)} · applicato ${escHtml(s.smart_dred_applied || '?')}${s.smart_dred_verified === false ? ' ⚠' : ' ✓'}</span>` : '',
  ].filter(Boolean).join('');
  const fanLabels = ['Auto', 'Bassa', 'Medio-bassa', 'Media', 'Medio-alta', 'Alta', 'Turbo'];

  if (pow && modelKey && estPower > 0) {
    if (!_kwhTracker[d.mac]) _kwhTracker[d.mac] = { lastRender: Date.now(), kwh: 0 };
    const tracker = _kwhTracker[d.mac];
    const elapsed = (Date.now() - tracker.lastRender) / 3600000;
    if (elapsed > 0.001) tracker.kwh += estPower * elapsed;
    tracker.lastRender = Date.now();
  } else if (!pow && _kwhTracker[d.mac]) {
    _kwhTracker[d.mac].kwh = 0;
  }

  return `<article class="card${pow ? ' on' : ''}" data-mac="${safeMac}">
    <header class="card-header">
      <div class="header-row1">
        <div class="name-group">
          <span class="icon-ac"><svg viewBox="0 0 24 24"><path d="M4 5h16v3H4zm2 5h12v2H6zm2 4h8v2H8z"/></svg></span>
          <h2 onclick="renameDevice('${safeMac}')" title="Clicca per rinominare">${escHtml(deviceName)}</h2>
          <span class="conn-badge ${d.connected ? '' : 'off'}">${d.connected ? 'ONLINE' : 'OFFLINE'}</span>
        </div>
      </div>
      <div class="header-row2">
        <span class="mac-label">${safeMac}</span>
        <select onchange="setModel('${safeMac}',this.value)" title="Modello per la stima energetica">
          <option value="">Modello non impostato</option>
          ${Object.entries(MODELS).map(([key, value]) => `<option value="${key}" ${modelKey === key ? 'selected' : ''}>${value.name}</option>`).join('')}
        </select>
      </div>
    </header>
    <div class="card-body ops-unit-layout">
      <section class="ops-reading">
        <div class="ops-reading-label">Temperatura ambiente</div>
        <div class="ops-room-temp">${roomTemp != null && Number.isFinite(roomTemp) ? roomTemp.toFixed(1) : '--'}°</div>
        <div class="ops-state">${pow ? '● ACCESO' : '○ SPENTO'} · ${escHtml(modeName.toUpperCase())}</div>
        <div class="ops-reading-grid">
          <div class="ops-mini"><b>${humidity != null && Number.isFinite(humidity) ? humidity.toFixed(1) + '%' : '--'}</b><span>UMIDITÀ</span></div>
          <div class="ops-mini"><b>${s.OutdoorTemperature != null ? Number(s.OutdoorTemperature).toFixed(1) + '°' : '--'}</b><span>ESTERNO HA</span></div>
          <div class="ops-mini"><b>${s.RoomTemperatureSensors?.length || 0}</b><span>SONDE TEMP. HA</span></div>
          <div class="ops-mini"><b>${s.RoomHumiditySensors?.length || 0}</b><span>SONDE UMIDITÀ HA</span></div>
        </div>
      </section>
      <section class="ops-controls">
        <div class="ops-power-row">
          <div><div class="ops-section-label">Stato unità</div><span class="ops-state">${pow ? 'DISPOSITIVO ATTIVO' : 'DISPOSITIVO SPENTO'}</span></div>
          <button class="ops-power" onclick="setPower('${safeMac}',${pow ? 0 : 1})" aria-label="${pow ? 'Spegni' : 'Accendi'} ${escHtml(deviceName)}" title="${pow ? 'Spegni' : 'Accendi'}"><svg class="ops-power-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2.5v9"/><path d="M7.1 5.6a8 8 0 1 0 9.8 0"/></svg></button>
        </div>
        <div class="ops-target">
          <div><div class="ops-section-label">Temperatura obiettivo</div><span class="state-line">Intervallo 16–30 °C</span></div>
          <div class="temp-control">
            <button class="temp-btn" onclick="setTemp('${safeMac}',-0.5)" aria-label="Riduci temperatura">−</button>
            <span class="temp-value">${target}</span><span class="temp-unit">°C</span>
            <button class="temp-btn" onclick="setTemp('${safeMac}',0.5)" aria-label="Aumenta temperatura">+</button>
          </div>
        </div>
        <div class="ops-section-label" style="margin-bottom:7px">Modalità</div>
        <div class="ops-modes">
          ${[0,1,2,3,4].map(value => `<button class="btn mode-${modeClasses[value]} ${mod === value && pow ? 'active' : ''}" onclick="setMode('${safeMac}',${value})" title="${modeNames[value]}"><span style="display:block;line-height:0;margin-bottom:3px">${modeIcons[value]}</span>${modeShort[value]}</button>`).join('')}
        </div>
        <div class="ops-section-label" style="margin-top:12px">Ventilazione e potenza</div>
        <div class="btn-group">
          ${[0,1,2,3,4,5].map(value => `<button class="btn ${Number(s.WdSpd) === value ? 'active' : ''}" onclick="setFan('${safeMac}',${value})">${fanLabels[value]}</button>`).join('')}
          ${s.Tur !== undefined ? `<button class="btn ${Number(s.Tur) === 1 || Number(s.WdSpd) === 6 ? 'active' : ''}" onclick="setTurbo('${safeMac}',${Number(s.Tur) === 1 || Number(s.WdSpd) === 6 ? 0 : 1})"><svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:4px" aria-hidden="true"><path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/><path d="m12 15-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/><path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0"/><path d="M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"/></svg>TURBO</button>` : ''}
        </div>
        ${s.smart_manual_fan_override ? `<div class="state-line">Override manuale ventola: ${escHtml(s.smart_manual_fan_override)} · resta attivo finché non selezioni un altro profilo</div>` : ''}
        <div class="ops-section-label" style="margin-top:12px">Profili ambiente</div>
        <div class="ops-presets"><button class="btn ${activePreset === 'manual' || !profileEnabled ? 'active' : ''}" onclick="setPreset('${safeMac}','manual')">MANUALE</button>${enabledPresets.map(([name]) => `<button class="btn ${activePreset === name ? 'active' : ''}" onclick="setPreset('${safeMac}','${escHtml(name)}')">${presetLabels[name] || escHtml(name).toUpperCase()}</button>`).join('')}</div>
        <div class="ops-alerts">${alerts}</div>
        <div id="control-chart-${safeMac}">${renderEnvironmentChart(d.mac, s)}</div>
      </section>
      <section class="ops-telemetry">
        <div class="ops-telemetry-head"><span class="ops-section-label">Telemetria</span><span class="ops-health">${errorCode === 0 ? '● NESSUN ERRORE' : '● ERRORE ' + errorCode}</span></div>
        <div class="ops-data-row"><span>Potenza effettiva / stimata</span><b>${s.ActualPowerW != null ? (Number(s.ActualPowerW) / 1000).toFixed(2) + ' kW' : '--'} / ${modelKey || s.estimated_power_w != null ? estPower.toFixed(2) + ' kW' : '--'}</b></div>
        <div class="ops-data-row"><span>Energia effettiva / stimata</span><b>${s.ActualEnergyKWh != null ? Number(s.ActualEnergyKWh).toFixed(2) + ' kWh' : '--'} / ${modelKey || s.estimated_energy_kwh != null ? energy + ' kWh' : '--'}</b></div>
        <div class="ops-data-row"><span>Ore accensione totali</span><b>${formatRuntime(s.total_runtime_seconds)} <button class="ops-inline-reset" onclick="resetRuntime('${safeMac}')" title="Azzera solo il contatore delle ore">Azzera</button></b></div>
        <div class="ops-data-row"><span>Dall’ultima accensione</span><b>${pow ? formatRuntime(s.current_run_seconds) : '--'}</b></div>
        <div class="ops-data-row"><span>Ventilatore</span><b>${fanLabels[Number(s.WdSpd)] || s.WdSpd || '--'}</b></div>
        <div class="ops-data-row"><span>Profilo</span><b>${activePreset ? (presetLabels[activePreset] || escHtml(activePreset).toUpperCase()) : 'MANUALE'}</b></div>
        <div class="ops-data-row"><span>Decisione Smart</span><b>${escHtml(s.smart_last_action || '--')}</b></div>
        <div class="ops-data-row"><span>I-Demand</span><b>${s.DREDEn === 1 ? (effectiveDred === 0 ? 'OFF' : (effectiveDred === 1 ? 'D1 · compressore escluso' : effectiveDred === 2 ? 'D2 · limite 50%' : 'D3 · limite 75%')) : 'N/D'}</b></div>
        <div class="ops-data-row"><span>Sonde IDU / ODU (raw − 40)</span><b>${probeIn != null ? probeIn.toFixed(1) + '°' : '--'} / ${probeOut != null ? probeOut.toFixed(1) + '°' : '--'}</b></div>
        <div class="ops-data-row"><span>Impianto aeraulico</span><b>${s.Installation?.static_pressure_pa != null ? s.Installation.static_pressure_pa + ' Pa' : '--'} · ${s.Installation?.served_rooms != null ? s.Installation.served_rooms + ' locali' : '--'} · ${s.Installation?.supply_outlets != null ? s.Installation.supply_outlets + ' mandate' : '--'}</b></div>
        <details class="ops-details"><summary>APRI CONTROLLI AVANZATI ↓</summary>
          <div class="control-row"><label>Ventilatore</label><div class="btn-group">${[0,1,2,3,4,5].map(value => `<button class="btn ${Number(s.WdSpd) === value ? 'active' : ''}" onclick="setFan('${safeMac}',${value})">${fanLabels[value]}</button>`).join('')}</div></div>
          <div class="control-row"><label>Oscillazione</label><div class="btn-group"><button class="btn ${!s.SwUpDn && !s.SwingLfRig ? 'active' : ''}" onclick="setSwing('${safeMac}','off')">Off</button><button class="btn ${s.SwUpDn && !s.SwingLfRig ? 'active' : ''}" onclick="setSwing('${safeMac}','v')">Verticale</button><button class="btn ${!s.SwUpDn && s.SwingLfRig ? 'active' : ''}" onclick="setSwing('${safeMac}','h')">Orizzontale</button><button class="btn ${s.SwUpDn && s.SwingLfRig ? 'active' : ''}" onclick="setSwing('${safeMac}','both')">Entrambi</button></div></div>
          <div class="control-row"><label>Funzioni</label><div class="btn-group">${[['Quiet','Silenzioso'],['Health','Purifica'],['Blo','X-Fan'],['SvSt','Eco'],['StHt','Strong Heat'],['StCold','Strong Cool'],['HtSp','Heat Support'],['Air','Aria'],['FreshAir','Aria fresca'],['AutoClean','Auto Clean'],['XFA','XFA']].filter(([key]) => s[key] !== undefined).map(([key,label]) => `<button class="btn ${s[key] ? 'active' : ''}" onclick="toggleSwitch('${safeMac}','${key}')">${label}</button>`).join('')}${s.Tur !== undefined ? `<button class="btn ${Number(s.Tur) === 1 || Number(s.WdSpd) === 6 ? 'active' : ''}" onclick="setTurbo('${safeMac}',${Number(s.Tur) === 1 || Number(s.WdSpd) === 6 ? 0 : 1})">Turbo</button>` : ''}</div></div>
          ${s.DREDEn === 1 && s.DRED !== undefined ? `<div class="control-row"><label>I-Demand attuale</label><div class="btn-group">${[[0,'Off'],[1,'100%'],[2,'50%'],[3,'75%']].map(([value,label]) => `<button class="btn ${effectiveDred === value ? 'active' : ''}" onclick="setDred('${safeMac}',${value})">${label}</button>`).join('')}</div></div><div class="control-row"><label>All’avvio in Cool</label><div class="btn-group">${[['none','Nessuno'],['1','100%'],['2','50%'],['3','75%']].map(([value,label]) => `<button class="btn ${startupDred === value ? 'active' : ''}" onclick="setStartupDred('${safeMac}','${value}')">${label}</button>`).join('')}</div></div>` : ''}
          <div class="state-line">Codice errore: ${errorCode}${s.ErrType !== undefined ? ' · tipo ' + escHtml(String(s.ErrType)) : ''} · perdita refrigerante: ${Number(s.RefLeak || 0) ? 'ATTENZIONE' : 'OK'} · stato sistema: ${escHtml(String(s.MSysStatus ?? '--'))}</div>
          <div class="state-line">InTem: ${escHtml(String(s.InTem ?? '--'))} · OutTem: ${escHtml(String(s.OutTem ?? '--'))} · pulizia: ${escHtml(String(s.CleanState ?? '--'))} · filtro: ${escHtml(String(s.FClTime ?? '--'))}</div>
        </details>
      </section>
    </div>
  </article>`;
}

__PANEL_PROFILES_JS__
function renderOperationsOverview(data) {
  const states = data.map(device => device.state || {});
  const valid = values => values.filter(value => Number.isFinite(value));
  const average = values => values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
  const roomTemps = valid(states.map(state => state.RoomTemperature != null ? Number(state.RoomTemperature) : (parseProbeTemp(state.InTem) ?? NaN)));
  const humidity = valid(states.map(state => state.RoomHumidity != null ? Number(state.RoomHumidity) : (state.InHumi != null ? Number(state.InHumi) : NaN)));
  const outdoor = valid(states.map(state => state.OutdoorTemperature != null ? Number(state.OutdoorTemperature) : NaN));
  const power = states.reduce((sum, state, index) => {
    const device = data[index];
    const model = getModel(device.mac);
    return sum + (state.estimated_power_w != null ? Number(state.estimated_power_w) / 1000 : estimatePower(state, model));
  }, 0);
  const connected = data.filter(device => device.connected).length;
  const fmt = (value, suffix) => value == null ? '--' : value.toFixed(1) + suffix;
  return `<div class="ops-kpi"><span>UNITÀ ONLINE</span><b>${connected} / ${data.length}</b><small>${connected === data.length ? '● Tutte operative' : '● Verifica connessione'}</small></div>
    <div class="ops-kpi"><span>TEMP. MEDIA</span><b>${fmt(average(roomTemps),'°')}</b><small>Media sensori ambiente</small></div>
    <div class="ops-kpi"><span>UMIDITÀ MEDIA</span><b>${fmt(average(humidity),'%')}</b><small>Media sensori HA</small></div>
    <div class="ops-kpi"><span>POTENZA STIMATA</span><b>${power > 0 ? power.toFixed(2) + ' kW' : '--'}</b><small>${states.filter(state => Number(state.Pow || 0) === 1).length} unità attive</small></div>
    <div class="ops-kpi"><span>ESTERNO</span><b>${fmt(average(outdoor),'°')}</b><small>Sensore Home Assistant</small></div>`;
}

async function loadData() {
  try {
    const data = await apiFetch(PANEL_DATA_URL);
    const container = document.getElementById('devices');
    const setupMsg = document.getElementById('setupMsg');
    const badge = document.getElementById('statusBadge');
    _panelAuthFailureShown = false;
    if (_panelAuthRetryTimer) {
      clearTimeout(_panelAuthRetryTimer);
      _panelAuthRetryTimer = null;
    }

    if (!data || data.length === 0) {
      setupMsg.style.display = 'block';
      container.innerHTML = '';
      badge.textContent = 'no devices';
      badge.style.background = 'var(--yellow)';
      document.getElementById('opsOverview').innerHTML = '';
      document.getElementById('opsUpdateText').textContent = 'Nessuna unità configurata';
      return;
    }

    setupMsg.style.display = 'none';
    const allConnected = data.every(d => d.connected);
    badge.textContent = allConnected ? `${data.length} device${data.length > 1 ? 's' : ''} online` : `${data.filter(d => d.connected).length}/${data.length} online`;
    badge.style.background = allConnected ? 'var(--green)' : 'var(--yellow)';
    const connectionDot = document.querySelector('.connection-dot');
    if (connectionDot) connectionDot.style.background = allConnected ? 'var(--green)' : 'var(--yellow)';

    document.getElementById('opsOverview').innerHTML = renderOperationsOverview(data);
    document.getElementById('opsUpdateText').textContent = `${data.length} unità · ${data.filter(d => d.connected).length} online · aggiornato ${new Date().toLocaleTimeString('it-IT')}`;
    container.innerHTML = data.map(d => renderOperationsDevice(d)).join('');
    window._lastPanelData = data;
    const activeTab = document.querySelector('.tab-btn.active')?.dataset.tab;
    if (activeTab === 'charts') renderChartsPage(data);
    else if (activeTab === 'devices') renderControlCharts(data);
    renderProfilesPage(data);

    const info = document.getElementById('serverInfo');
    info.textContent = 'Gree AC Cloud v__VERSION__ | ' + (data[0]?.cloud_host || 'eugrih.gree.com') + ' | ' + (data[0]?.server || 'Europe');
  } catch (e) {
    console.error('Load failed:', e);
    const setupMsg = document.getElementById('setupMsg');
    const title = setupMsg && setupMsg.querySelector('h2');
    const detail = setupMsg && setupMsg.querySelector('p');
    if (setupMsg) setupMsg.style.display = 'block';
    if (title) title.textContent = e.status === 401 ? 'Sessione non disponibile' : 'Impossibile caricare i dispositivi';
    if (detail) detail.textContent = e.status === 401
      ? 'Riapri il pannello da Home Assistant oppure aggiorna la sessione dell’app.'
      : 'Verifica la connessione a Home Assistant e riprova.';
    document.getElementById('statusBadge').textContent = e.status === 401 ? 'auth error' : 'error';
    document.getElementById('statusBadge').style.background = 'var(--red)';
    const connectionDot = document.querySelector('.connection-dot');
    if (connectionDot) connectionDot.style.background = 'var(--red)';
  }
}

async function setPreset(mac, preset) {
  const button = document.querySelector(`[data-mac="${mac}"] .ops-presets button[onclick*="'${preset}'"]`);
  if (button) { button.disabled = true; button.textContent = 'APPLICO…'; }
  try {
    const devices = await apiFetch(PANEL_DATA_URL);
    const device = devices.find(d => d.mac === mac);
    const entityId = device && device.state && device.state.ClimateEntityId;
    if (!entityId) throw new Error('Entità climate non trovata');
    await apiFetch(HA_BASE + '/api/services/climate/set_preset_mode', {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ entity_id: entityId, preset_mode: preset }),
    });
    await loadData();
  } catch (e) {
    console.error('Preset failed:', e);
  } finally {
    if (button) button.disabled = false;
  }
}

async function setPower(mac, val) {
  try {
    const devices = await apiFetch(PANEL_DATA_URL);
    const device = devices.find(d => d.mac === mac);
    const entityId = device && device.state && device.state.ClimateEntityId;
    if (entityId) {
      await apiFetch(HA_BASE + `/api/services/climate/${val ? 'turn_on' : 'turn_off'}`, {
        method: 'POST',
        headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ entity_id: entityId }),
      });
    } else {
      await sendCommand(mac, ['Pow'], [val]);
    }
    setTimeout(loadData, 1000);
  } catch (e) {
    console.error('Power command failed:', e);
  }
}

async function setMode(mac, val) {
  await sendCommand(mac, ['Pow', 'Mod'], [1, val]);
  setTimeout(loadData, 1000);
}

async function setTemp(mac, delta) {
  const card = document.querySelector(`[data-mac="${mac}"]`);
  if (!card) return;
  const el = card.querySelector('.temp-value');
  const cur = parseFloat(el.textContent) || 26;
  const newTemp = Math.round(Math.max(16,Math.min(30,cur+delta))*2)/2;
  try {
    const devices = await apiFetch(PANEL_DATA_URL);
    const device = devices.find(d => d.mac === mac);
    const entityId = device?.state?.ClimateEntityId;
    if (!entityId) throw new Error('Entità climate non disponibile');
    await apiFetch(HA_BASE + '/api/services/climate/set_temperature', {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ entity_id: entityId, temperature: newTemp }),
    });
    if (el) el.textContent = `${newTemp.toFixed(1)}°`;
  } catch (error) {
    console.error('Temperature command failed:',error);
    alert('Temperatura non applicata: '+error.message);
  }
  setTimeout(loadData,1000);
}

async function setFan(mac, val) {
  const fanModes = ['Auto','Bassa','Media-Bassa','Media','Media-Alta','Alta','Turbo'];
  if (!Number.isInteger(val) || val < 0 || val >= fanModes.length) return;
  try {
    const devices = await apiFetch(PANEL_DATA_URL);
    const device = devices.find(item => item.mac === mac);
    const entityId = device && device.state && device.state.ClimateEntityId;
    if (!entityId) throw new Error('Entità climate non trovata');
    await apiFetch(HA_BASE + '/api/services/climate/set_fan_mode', {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ entity_id: entityId, fan_mode: fanModes[val] }),
    });
  } catch (error) {
    console.error('Fan command failed:', error);
    alert('Velocità ventola non applicata: ' + error.message);
  }
  setTimeout(loadData, 1000);
}

async function setTurbo(mac, enabled) {
  try {
    const devices = await apiFetch(PANEL_DATA_URL);
    const device = devices.find(item => item.mac === mac);
    const state = device && device.state;
    const entityId = state && state.ClimateEntityId;
    if (!state || !entityId) throw new Error('Entità climate non trovata');
    if (enabled) {
      await sendCommand(mac, ['Tur','WdSpd','Quiet','DRED'], [1,6,0,0]);
    } else {
      await sendCommand(mac, ['Tur','WdSpd'], [0,5]);
    }
  } catch (error) {
    console.error('Turbo command failed:', error);
    alert('Turbo non applicato: ' + error.message);
  }
  setTimeout(loadData, 1500);
}

async function setDred(mac, val) {
  if (![0,1,2,3].includes(val)) return;
  const label = val === 0 ? 'Off' : `D${val}`;
  if (!window.confirm(`${label} modifica il limite I-Demand e, se attivo, disattiva Quiet. Continuare?`)) return;
  await sendCommand(mac, ['DRED'], [val]);
  setTimeout(loadData, 1500);
}

async function setStartupDred(mac, val) {
  try {
    await apiFetch(PANEL_CMD_URL, {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({
        mac,
        startup_dred: val === 'none' ? null : Number(val),
      }),
    });
  } catch (e) {
    console.error('Startup DRED setting failed:', e);
  }
  setTimeout(loadData, 300);
}

async function setSwing(mac, mode) {
  const v = (mode === 'v' || mode === 'both') ? 1 : 0;
  const h = (mode === 'h' || mode === 'both') ? 1 : 0;
  await sendCommand(mac, ['SwUpDn', 'SwingLfRig'], [v, h]);
  setTimeout(loadData, 1000);
}

async function toggleNativeSensor(mac, key) {
  const data = await apiFetch(PANEL_DATA_URL);
  const dev = data.find(d => d.mac === mac);
  if (!dev) return;
  const nextValue = Number(dev.state && dev.state[key]) === 1 ? 0 : 1;
  const ok = await sendCommand(mac, [key], [nextValue]);
  if (!ok) alert(`Il dispositivo ha rifiutato il comando ${key}=${nextValue}.`);
  setTimeout(loadData, 1800);
}

async function toggleSwitch(mac, key) {
  const data = await apiFetch(PANEL_DATA_URL);
  const dev = data.find(d => d.mac === mac);
  if (!dev) return;
  const curVal = dev.state && dev.state[key] ? 1 : 0;
  await sendCommand(mac, [key], [curVal ? 0 : 1]);
  setTimeout(loadData, 1000);
}

// ── Markdown renderer ────────────────────────────────

function mdToHtml(md) {
  let h = md.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  h = h.replace(/```(\w*)\n?([\s\S]*?)```/g, '<pre><code>$2</code></pre>');
  h = h.replace(/^#{5}\s+(.+)$/gm, '<h5>$1</h5>');
  h = h.replace(/^#{4}\s+(.+)$/gm, '<h4>$1</h4>');
  h = h.replace(/^#{3}\s+(.+)$/gm, '<h3>$1</h3>');
  h = h.replace(/^#{2}\s+(.+)$/gm, '<h2>$1</h2>');
  h = h.replace(/^#\s+(.+)$/gm, '<h1>$1</h1>');
  h = h.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
  h = h.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  h = h.replace(/\*(.+?)\*/g, '<em>$1</em>');
  h = h.replace(/`([^`]+)`/g, '<code>$1</code>');
  h = h.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  h = h.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, '<img src="$2" alt="$1" style="max-width:100%;height:auto;">');
  h = h.replace(/^-{3,}$/gm, '<hr>');
  h = h.replace(/^\|(.+)\|$/gm, function(m) {
    if (m.includes('---')) return '';
    const cells = m.split('|').slice(1,-1).map(c => c.trim());
    return '<tr><td>' + cells.join('</td><td>') + '</td></tr>';
  });
  h = h.replace(/(<tr>.*<\/tr>\n?)+/g, function(m) {
    const rows = m.trim().split('\n').filter(r => r.trim());
    const isHeader = rows.length >= 2 && /^<tr>/.test(rows[1]);
    const tag = isHeader ? 'thead' : 'tbody';
    return '<table><' + tag + '>' + rows.join('') + '</' + tag + '></table>';
  });
  h = h.replace(/^> (.+)$/gm, '<blockquote><p>$1</p></blockquote>');
  h = h.replace(/<\/blockquote>\s*<blockquote>/g, '\n');
  h = h.replace(/^\s*[-*]\s+(.+)$/gm, '<li>$1</li>');
  h = h.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>');
  h = h.replace(/^\s*\d+\.\s+(.+)$/gm, '<li>$1</li>');
  h = h.replace(/^(?!<[a-z]|<\/[a-z]|$)(.+)$/gm, function(m) {
    m = m.trim();
    return m ? '<p>' + m + '</p>' : '';
  });
  h = h.replace(/\n{2,}/g, '\n');
  return '<div class="md-content">' + h + '</div>';
}

function loadReadme() {
  const el = document.getElementById('readmeContainer');
  if (!el) return;
  el.innerHTML = mdToHtml(__README_CONTENT__);
}

function loadChangelog() {
  const el = document.getElementById('changelogContainer');
  if (!el) return;
  el.innerHTML = mdToHtml(__CHANGELOG_CONTENT__);
}

let _logAutoRefreshTimer = null;
let _lastLogCount = 0;
let _lastActionLog = [];

function actionLogText(entries) {
  return entries.map(entry => `[${new Date(entry.timestamp).toLocaleString('it-IT')}] ${entry.mac} ${entry.source} ${entry.action} ${JSON.stringify(entry.changes || {})} ${entry.result}`).join('\n');
}
async function loadActionLog() {
  const container = document.getElementById('actionLogContainer');
  if (!container) return;
  try {
    const source = document.getElementById('actionSourceFilter')?.value || '';
    const data = await apiFetch(PANEL_ACTION_LOG_URL + '?limit=1000' + (source ? '&source=' + encodeURIComponent(source) : ''));
    _lastActionLog = data;
    container.innerHTML = data.length ? data.map(entry => {
      const name = __DEVICE_NAMES__[entry.mac] || entry.mac;
      return `<div class="action-log-entry"><span>${escHtml(new Date(entry.timestamp).toLocaleString('it-IT'))}</span><span class="action-source">${escHtml(entry.source)}</span><span class="action-device">${escHtml(name)}</span><span class="action-changes"><b>${escHtml(entry.action)}</b> · ${escHtml(JSON.stringify(entry.changes || {}))}</span><span class="action-result-${escHtml(entry.result)}">${escHtml(entry.result)}</span></div>`;
    }).join('') : '<p style="color:var(--text2)">Nessuna azione registrata.</p>';
    const count = document.getElementById('actionLogCount');
    if (count) count.textContent = data.length + ' azioni';
    container.scrollTop = container.scrollHeight;
  } catch (error) { container.innerHTML = `<p style="color:var(--red)">Registro non disponibile: ${escHtml(error.message)}</p>`; }
}
async function copyActionLog() {
  if (!_lastActionLog.length) await loadActionLog();
  const text = actionLogText(_lastActionLog);
  try { await navigator.clipboard.writeText(text); } catch (error) { const ta=document.createElement('textarea');ta.value=text;document.body.appendChild(ta);ta.select();document.execCommand('copy');ta.remove(); }
}
async function clearActionLog() {
  if (!window.confirm('Azzerare definitivamente tutto il registro persistente delle azioni?')) return;
  await apiFetch(PANEL_ACTION_LOG_URL,{method:'DELETE'});
  await loadActionLog();
}

async function loadLogs() {
  const container = document.getElementById('logContainer');
  if (!container) return;
  try {
    const data = await apiFetch(HA_BASE + '/api/gree_ac_cloud/panel/log');
    const wasAtBottom = container.scrollTop + container.clientHeight >= container.scrollHeight - 4;
    container.innerHTML = data.map(e =>
      `<div class="log-entry"><span class="log-time">${e.t}</span><span class="log-${e.l.toLowerCase()}">${e.l} ${e.m}</span></div>`
    ).join('');
    const countEl = document.getElementById('logCount');
    if (countEl) countEl.textContent = data.length + ' entries';
    _lastLogCount = data.length;
    if (wasAtBottom) container.scrollTop = container.scrollHeight;
  } catch (e) {
    container.innerHTML = '<p style="color:var(--red)">Failed to load logs.</p>';
  }
}

async function copyAllLogs() {
  try {
    const data = await apiFetch(HA_BASE + '/api/gree_ac_cloud/panel/log');
    const text = data.map(e => `[${e.t}] ${e.l} ${e.m}`).join('\n');
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed'; ta.style.left = '-9999px';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    }
    const btn = document.querySelector('button[onclick="copyAllLogs()"]');
    if (btn) {
      const orig = btn.textContent;
      btn.textContent = '✅ Copied!';
      setTimeout(() => btn.textContent = orig, 2000);
    }
  } catch (e) {
    alert('Failed to copy logs: ' + e.message);
  }
}

function onLogAutoRefreshChange() {
  if (_logAutoRefreshTimer) {
    clearInterval(_logAutoRefreshTimer);
    _logAutoRefreshTimer = null;
  }
  if (document.getElementById('autoRefreshLogs').checked) {
    _logAutoRefreshTimer = setInterval(loadLogs, 2000);
  }
}

function toggleMobileMenu(force) {
  const open = force == null ? !document.body.classList.contains('mobile-menu-open') : Boolean(force);
  document.body.classList.toggle('mobile-menu-open', open);
  document.querySelector('.mobile-menu-button')?.setAttribute('aria-expanded', String(open));
}
function closeMobileMenu() {
  toggleMobileMenu(false);
}
function switchTab(tab) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  closeMobileMenu();
  const tabs = ['devices','charts','profiles','wiki','umatch','logs','readme','changelog','info'];
  tabs.forEach(t => {
    const el = document.getElementById('tab-' + t);
    if (el) el.style.display = t === tab ? 'block' : 'none';
  });
  const badge = document.getElementById('statusBadge');
  badge.style.display = ['devices','charts','profiles'].includes(tab) ? 'inline' : 'none';
  if (tab === 'devices') destroyApexCharts('detail');
  else if (tab === 'charts') destroyApexCharts('control');
  else destroyApexCharts();
  if (tab === 'charts' && window._lastPanelData) renderChartsPage(window._lastPanelData);
  if (tab === 'profiles' && window._lastPanelData) renderProfilesPage(window._lastPanelData);
  if (tab === 'logs') {
    loadActionLog();
    loadLogs();
    onLogAutoRefreshChange();
  } else {
    if (_logAutoRefreshTimer) {
      clearInterval(_logAutoRefreshTimer);
      _logAutoRefreshTimer = null;
    }
  }
  if (tab === 'readme') loadReadme();
  if (tab === 'changelog') loadChangelog();
  if (tab === 'info') loadInfo();
}

// ── Info tab (device keys, nerd details) ──

async function loadInfo() {
  const el = document.getElementById('infoContent');
  el.innerHTML = '<p style="color:var(--text-secondary);">Loading device info...</p>';
  try {
    const data = await apiFetch(PANEL_DEVICES_INFO_URL);
    if (!data.length) {
      el.innerHTML = '<p style="color:var(--text-secondary);">No devices found. Configure the integration first.</p>';
      return;
    }
    let html = '';
    for (const install of data) {
      html += '<div class="wiki" style="margin-bottom:20px;">';
      html += `<h3 style="margin-bottom:8px;">🔒 MQTT Connection</h3>`;
      html += `<table class="wt"><tr><th>Parameter</th><th>Value</th></tr>`;
      html += `<tr><td>UID</td><td><code>${escHtml(install.uid)}</code></td></tr>`;
      html += `<tr><td>Server Region</td><td>${escHtml(install.server_region)}</td></tr>`;
      html += `<tr><td>Cloud API</td><td><code>${escHtml(install.cloud_host)}</code></td></tr>`;
      html += `<tr><td>MQTT Broker</td><td><code>${escHtml(install.mqtt_host)}:${escHtml(install.mqtt_port)}</code></td></tr>`;
      html += `</table><br>`;

      html += `<h3 style="margin-bottom:8px;">📡 Devices (${install.devices.length})</h3>`;
      html += `<table class="wt"><tr><th>Name</th><th>MAC</th><th>Parent MAC</th><th>Key</th><th>Firmware</th><th>Props</th><th>Connected</th></tr>`;
      for (const d of install.devices) {
        const connCls = d.connected ? 'green' : 'red';
        const connTxt = d.connected ? '✓' : '✗';
        html += `<tr>
          <td>${escHtml(d.name)}</td>
          <td><code>${escHtml(d.mac)}</code></td>
          <td><code>${escHtml(d.parent_mac)}</code></td>
          <td><code style="font-size:9px;word-break:break-all;">${escHtml(d.key)}</code></td>
          <td style="font-size:10px;">${escHtml(d.hid || '-')}</td>
          <td>${d.properties_count}</td>
          <td style="color:var(--${connCls});font-weight:700;">${connTxt}</td>
        </tr>`;
      }
      html += `</table><br>`;

      html += `<h3 style="margin-bottom:8px;">🧭 MQTT Topics</h3>`;
      html += `<table class="wt"><tr><th>Device</th><th>Publish (request)</th><th>Subscribe (status)</th><th>Subscribe (response)</th></tr>`;
      for (const d of install.devices) {
        html += `<tr>
          <td>${escHtml(d.name)}</td>
          <td><code>${escHtml(d.mqtt_topic_request)}</code></td>
          <td><code>${escHtml(d.mqtt_topic_status)}</code></td>
          <td><code>${escHtml(d.mqtt_topic_response)}</code></td>
        </tr>`;
      }
      html += `</table>`;
      html += '</div>';
    }
    html += '<div style="margin-top:12px;">';
    html += '<button class="btn" onclick="reDiscoverDevices()" title="Re-authenticate: fetch fresh device keys from Gree Cloud API and update running integration">🔑 Re-authenticate &amp; Update Keys</button>';
    html += '<span id="rediscoverStatus" style="margin-left:8px;font-size:11px;color:var(--text2);"></span>';
    html += '<div id="keyChanges" style="margin-top:8px;"></div>';
    html += '</div>';
    el.innerHTML = html;
  } catch (e) {
    el.innerHTML = '<p style="color:var(--red);">Error loading device info: ' + escHtml(e.message) + '</p>';
  }
}

async function reDiscoverDevices() {
  const btn = document.querySelector('button[onclick="reDiscoverDevices()"]');
  const status = document.getElementById('rediscoverStatus');
  const changes = document.getElementById('keyChanges');
  if (btn) btn.disabled = true;
  if (status) status.textContent = 'Re-authenticating with Gree Cloud...';
  if (changes) changes.innerHTML = '';
  try {
    const resp = await apiFetch(PANEL_DEVICES_INFO_URL, {method:'POST'});
    if (resp.error) {
      if (status) status.textContent = '❌ ' + resp.error;
      return;
    }
    const kc = resp.key_changes || [];
    if (kc.length) {
      let tbl = '<table class="wt" style="margin-top:4px;"><tr><th>MAC</th><th>Name</th><th>Old Key</th><th>→ New Key</th></tr>';
      for (const c of kc) {
        tbl += `<tr><td><code>${escHtml(c.mac)}</code></td><td>${escHtml(c.name)}</td><td><code style="font-size:9px;color:var(--red);">${escHtml(c.old_key)}</code></td><td><code style="font-size:9px;color:var(--green);">${escHtml(c.new_key)}</code></td></tr>`;
      }
      tbl += '</table>';
      if (changes) changes.innerHTML = '<div style="font-size:11px;color:var(--yellow);font-weight:600;margin-bottom:2px;">🔑 Keys updated</div>' + tbl;
    } else {
      if (changes) changes.innerHTML = '<span style="font-size:11px;color:var(--green);">✓ Keys unchanged</span>';
    }
    if (status) status.textContent = `✅ Found ${resp.devices.length} devices. Cloud: ${resp.server_region}`;
    // Reload content to show current keys
    loadInfo();
  } catch (e) {
    if (status) status.textContent = '❌ ' + e.message;
  } finally {
    if (btn) btn.disabled = false;
  }
}

function escHtml(s) {
  if (s == null) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── viewport detection (works in iframe context) ──
function updateViewportClass() {
  document.body.classList.toggle('desktop', window.innerWidth >= 600);
}
updateViewportClass();
window.addEventListener('resize', () => {
  updateViewportClass();
  if (window.innerWidth > 720) closeMobileMenu();
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') closeMobileMenu();
});

loadModels();
loadNames().then(loadData);
setInterval(() => { if (!_panelAuthFailureShown) loadData(); }, 10000);

(async function initSettings() {
  try {
    const s = await apiFetch(PANEL_SETTINGS_URL);
    const sel = document.getElementById('intervalSelect');
    if (sel && s.update_interval) sel.value = String(s.update_interval);
  } catch (e) {}
})();
</script>
</body>
</html>"""
