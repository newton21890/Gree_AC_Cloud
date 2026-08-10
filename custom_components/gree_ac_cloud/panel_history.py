"""Recorder-backed history API for the Gree AC Cloud panel."""

from __future__ import annotations

import logging
from datetime import timedelta
from functools import partial

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DEVICES,
    CONF_HUMIDITY_SENSOR,
    CONF_HUMIDITY_SENSORS,
    CONF_OUTDOOR_HUMIDITY_SENSOR,
    CONF_OUTDOOR_TEMPERATURE_SENSOR,
    CONF_TEMPERATURE_SENSOR,
    CONF_TEMPERATURE_SENSORS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PANEL_HISTORY_URL = "/api/gree_ac_cloud/panel/history"
HISTORY_PERIODS = {
    "6h": timedelta(hours=6),
    "24h": timedelta(hours=24),
    "3d": timedelta(days=3),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}
HISTORY_MAX_POINTS = 720


def _valid_mac(value: str) -> bool:
    return (
        isinstance(value, str)
        and 12 <= len(value) <= 14
        and all(character in "0123456789abcdefABCDEF" for character in value)
    )


def _history_sensor_ids(hass, entry, device) -> dict[str, list[str]]:
    """Return Recorder-backed entities used to build the panel history."""
    room = entry.options.get(CONF_DEVICES, {}).get(device.mac, {})
    temperature_ids = room.get(CONF_TEMPERATURE_SENSORS) or (
        [room[CONF_TEMPERATURE_SENSOR]] if room.get(CONF_TEMPERATURE_SENSOR) else []
    )
    humidity_ids = room.get(CONF_HUMIDITY_SENSORS) or (
        [room[CONF_HUMIDITY_SENSOR]] if room.get(CONF_HUMIDITY_SENSOR) else []
    )
    registry = er.async_get(hass)

    def platform_entity(unique_id: str, domain: str) -> str | None:
        entity_id = registry.async_get_entity_id(domain, DOMAIN, unique_id)
        registry_entry = registry.async_get(entity_id) if entity_id else None
        return entity_id if registry_entry and registry_entry.disabled_by is None else None

    climate_entity = platform_entity(f"climate_{device.mac}", "climate")
    if climate_entity is None:  # Compatibility with early integration versions.
        climate_entity = platform_entity(device.mac, "climate")
    fallback_temperature = platform_entity(f"{device.mac}_TemSen", "sensor")
    fallback_humidity = platform_entity(f"{device.mac}_InHumi", "sensor")
    estimated_power = platform_entity(f"{device.mac}_power", "sensor")
    baseline_power = platform_entity(f"{device.mac}_baseline_power", "sensor")
    saving_power = platform_entity(f"{device.mac}_saving_power", "sensor")
    estimated_energy = platform_entity(f"{device.mac}_energy", "sensor")
    outdoor_temperature = entry.options.get(CONF_OUTDOOR_TEMPERATURE_SENSOR)
    outdoor_humidity = entry.options.get(CONF_OUTDOOR_HUMIDITY_SENSOR)
    return {
        "room": list(temperature_ids)
        or ([climate_entity] if climate_entity else [])
        or ([fallback_temperature] if fallback_temperature else []),
        "humidity": list(humidity_ids) or ([fallback_humidity] if fallback_humidity else []),
        "outdoor": [outdoor_temperature] if outdoor_temperature else [],
        "outdoorHumidity": [outdoor_humidity] if outdoor_humidity else [],
        "target": [climate_entity] if climate_entity else [],
        "power": [estimated_power] if estimated_power else [],
        "baselinePower": [baseline_power] if baseline_power else [],
        "savingPower": [saving_power] if saving_power else [],
        "energy": [estimated_energy] if estimated_energy else [],
        "mode": [climate_entity] if climate_entity else [],
        "preset": [climate_entity] if climate_entity else [],
        "dred": [climate_entity] if climate_entity else [],
        "profileActive": [climate_entity] if climate_entity else [],
        "smartAction": [climate_entity] if climate_entity else [],
    }


def _downsample(points: list[dict], maximum: int = HISTORY_MAX_POINTS) -> list[dict]:
    """Keep payloads bounded while retaining first and last samples."""
    if len(points) <= maximum:
        return points
    step = (len(points) - 1) / (maximum - 1)
    indexes = {round(index * step) for index in range(maximum)}
    return [point for index, point in enumerate(points) if index in indexes]


def _merge_histories(histories, sensor_ids, period) -> list[dict]:
    """Merge asynchronous entities into averaged, time-ordered chart points."""
    entity_series: dict[str, list[str]] = {}
    for key, entity_ids in sensor_ids.items():
        for entity_id in entity_ids:
            entity_series.setdefault(entity_id, []).append(key)
    categorical_keys = {"mode", "preset", "dred", "profileActive", "smartAction"}
    events: list[tuple[float, str, str, float | str | bool | None]] = []
    for entity_id, states in histories.items():
        keys = entity_series.get(entity_id, [])
        for state in states:
            for key in keys:
                if key == "target":
                    smart_target = state.attributes.get("smart_effective_target")
                    raw_value = (
                        smart_target
                        if smart_target is not None
                        else state.attributes.get("temperature")
                    )
                elif key == "room" and entity_id.startswith("climate."):
                    raw_value = state.attributes.get("current_temperature")
                elif key == "preset":
                    raw_value = state.attributes.get("preset_mode")
                elif key == "dred":
                    raw_value = state.attributes.get("smart_dred_applied")
                elif key == "profileActive":
                    raw_value = state.attributes.get("smart_profile_active")
                elif key == "smartAction":
                    raw_value = state.attributes.get("smart_last_action")
                else:
                    raw_value = state.state
                if key in categorical_keys:
                    value = (
                        raw_value if raw_value not in (None, "", "unknown", "unavailable") else None
                    )
                else:
                    try:
                        value = float(raw_value)
                    except (TypeError, ValueError):
                        value = None
                events.append((state.last_updated.timestamp() * 1000, key, entity_id, value))

    events.sort(key=lambda item: item[0])
    current: dict[str, dict[str, float | str | bool]] = {key: {} for key in sensor_ids}
    points: list[dict] = []
    last_bucket = None
    bucket_seconds = max(60, int(period.total_seconds() / HISTORY_MAX_POINTS))
    for timestamp, key, entity_id, value in events:
        if value is None:
            current[key].pop(entity_id, None)
        else:
            current[key][entity_id] = value
        bucket = int(timestamp / (bucket_seconds * 1000))
        if points and bucket == last_bucket:
            point = points[-1]
        else:
            point = {"t": int(timestamp)}
            points.append(point)
            last_bucket = bucket
        for series_key, values in current.items():
            if not values:
                continue
            if series_key in categorical_keys:
                point[series_key] = next(reversed(values.values()))
            else:
                numeric_values = [float(item) for item in values.values()]
                point[series_key] = round(sum(numeric_values) / len(numeric_values), 3)
    return _downsample(points)


class GreePanelHistoryView(HomeAssistantView):
    """Return persistent environmental history from Home Assistant Recorder."""

    url = PANEL_HISTORY_URL
    name = "api:gree_ac_cloud:panel_history"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        mac = request.query.get("mac", "")
        period_name = request.query.get("period", "24h")
        end_timestamp = request.query.get("end")
        if not _valid_mac(mac) or period_name not in HISTORY_PERIODS:
            return self.json({"error": "invalid request"}, status_code=400)
        try:
            end = (
                dt_util.utc_from_timestamp(int(end_timestamp) / 1000)
                if end_timestamp
                else dt_util.utcnow()
            )
        except (TypeError, ValueError, OSError):
            return self.json({"error": "invalid end timestamp"}, status_code=400)
        period = HISTORY_PERIODS[period_name]
        start = end - period

        selected_entry = None
        selected_device = None
        for entry in hass.config_entries.async_entries(DOMAIN):
            runtime = getattr(entry, "runtime_data", None) or {}
            for coordinator in runtime.get("coordinators", []):
                if coordinator.device.mac.lower() == mac.lower():
                    selected_entry, selected_device = entry, coordinator.device
                    break
            if selected_device:
                break
        if not selected_entry or not selected_device:
            return self.json({"error": "device not found"}, status_code=404)

        sensor_ids = _history_sensor_ids(hass, selected_entry, selected_device)
        entity_ids = {entity_id for ids in sensor_ids.values() for entity_id in ids}
        if not entity_ids:
            return self.json(
                {
                    "points": [],
                    "period": period_name,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "entities": sensor_ids,
                }
            )
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.history import get_significant_states

            histories = await get_instance(hass).async_add_executor_job(
                partial(
                    get_significant_states,
                    hass,
                    start,
                    end,
                    entity_ids,
                    include_start_time_state=True,
                    significant_changes_only=False,
                    minimal_response=False,
                    no_attributes=False,
                )
            )
        except Exception as err:  # Recorder can be disabled or still starting.
            _LOGGER.warning("Unable to read Recorder history for %s: %s", mac, err)
            return self.json({"error": "recorder unavailable", "detail": str(err)}, status_code=503)

        return self.json(
            {
                "points": _merge_histories(histories, sensor_ids, period),
                "period": period_name,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "entities": sensor_ids,
            }
        )
