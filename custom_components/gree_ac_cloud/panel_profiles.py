"""Dedicated profile configuration API for the Gree AC Cloud panel."""

from __future__ import annotations

import re

from aiohttp import web
from homeassistant.components.http import HomeAssistantView

from .const import (
    CONF_DEVICES,
    CONF_PRESET_ADAPTIVE,
    CONF_PRESET_ALLOWED_MODES,
    CONF_PRESET_AUTO_OFF,
    CONF_PRESET_DEADBAND,
    CONF_PRESET_DRED,
    CONF_PRESET_ENABLED,
    CONF_PRESET_FAN,
    CONF_PRESET_HOLD_ACTION,
    CONF_PRESET_HUMIDITY,
    CONF_PRESET_MAX_TEMP,
    CONF_PRESET_MIN_TEMP,
    CONF_PRESET_MODE,
    CONF_PRESET_QUIET,
    CONF_PRESET_SMART,
    CONF_PRESET_TARGET,
    CONF_PRESET_WORK_CURVE,
    CONF_PRESETS,
    DOMAIN,
    PRESET_DRED_ALIASES,
    PRESET_DRED_OPTIONS,
    PRESET_FAN_ALIASES,
    PRESET_FAN_OPTIONS,
    PRESET_HOLD_OFF,
    PRESET_HOLD_OPTIONS,
    PRESET_NAMES,
    PRESET_WORK_CURVE_BALANCED,
    PRESET_WORK_CURVE_OPTIONS,
    SMART_MODES,
)

PANEL_PROFILE_URL = "/api/gree_ac_cloud/panel/profile"
_ALLOWED_AUTOMATIC_MODES = {"cool", "heat", "dry"}
_OPTIONAL_TEMPERATURE_KEYS = (
    CONF_PRESET_AUTO_OFF,
    CONF_PRESET_HUMIDITY,
    CONF_PRESET_MIN_TEMP,
    CONF_PRESET_MAX_TEMP,
)


def _valid_mac(value: str) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9A-Fa-f]{12,14}", value) is not None


def _is_admin(request: web.Request) -> bool:
    user = request.get("hass_user")
    return bool(user and user.is_admin)


def _number(value, minimum: float, maximum: float, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as err:
        raise ValueError(f"invalid {label}") from err
    if number < minimum or number > maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
    return number


def _clean_profile(profile: dict, current: dict) -> dict:
    """Validate one profile while retaining compatible optional values."""
    if not isinstance(profile, dict):
        raise ValueError("invalid profile")
    cleaned = dict(current)
    mode = profile.get(CONF_PRESET_MODE, current.get(CONF_PRESET_MODE, "auto"))
    if mode not in SMART_MODES:
        raise ValueError("invalid climate strategy")
    configured_modes = profile.get(CONF_PRESET_ALLOWED_MODES)
    if mode == "auto":
        if not isinstance(configured_modes, list):
            raise ValueError("allowed modes must be a list")
        allowed_modes = [item for item in configured_modes if item in _ALLOWED_AUTOMATIC_MODES]
        if not allowed_modes:
            raise ValueError("select at least one automatic mode")
    else:
        allowed_modes = [mode]

    fan = PRESET_FAN_ALIASES.get(profile.get(CONF_PRESET_FAN)) or profile.get(
        CONF_PRESET_FAN, current.get(CONF_PRESET_FAN, "Smart")
    )
    if fan not in PRESET_FAN_OPTIONS:
        raise ValueError("invalid fan speed")
    dred = PRESET_DRED_ALIASES.get(profile.get(CONF_PRESET_DRED)) or profile.get(
        CONF_PRESET_DRED, current.get(CONF_PRESET_DRED, "No action")
    )
    if dred not in PRESET_DRED_OPTIONS:
        raise ValueError("invalid I-Demand setting")
    hold_action = profile.get(
        CONF_PRESET_HOLD_ACTION, current.get(CONF_PRESET_HOLD_ACTION, PRESET_HOLD_OFF)
    )
    if hold_action not in PRESET_HOLD_OPTIONS:
        raise ValueError("invalid comfort hold action")
    work_curve = profile.get(
        CONF_PRESET_WORK_CURVE,
        current.get(CONF_PRESET_WORK_CURVE, PRESET_WORK_CURVE_BALANCED),
    )
    if work_curve not in PRESET_WORK_CURVE_OPTIONS:
        raise ValueError("invalid work curve")

    cleaned.update(
        {
            CONF_PRESET_ENABLED: bool(profile.get(CONF_PRESET_ENABLED, False)),
            CONF_PRESET_SMART: bool(profile.get(CONF_PRESET_SMART, True)),
            CONF_PRESET_MODE: mode,
            CONF_PRESET_ALLOWED_MODES: allowed_modes,
            CONF_PRESET_TARGET: _number(profile.get(CONF_PRESET_TARGET, 26), 16, 30, "target"),
            CONF_PRESET_DEADBAND: _number(
                profile.get(CONF_PRESET_DEADBAND, 0.5), 0.2, 2, "deadband"
            ),
            CONF_PRESET_ADAPTIVE: bool(profile.get(CONF_PRESET_ADAPTIVE, True)),
            CONF_PRESET_FAN: fan,
            CONF_PRESET_QUIET: bool(profile.get(CONF_PRESET_QUIET, False)),
            CONF_PRESET_DRED: dred,
            CONF_PRESET_HOLD_ACTION: hold_action,
            CONF_PRESET_WORK_CURVE: work_curve,
        }
    )
    limits = {
        CONF_PRESET_AUTO_OFF: (16, 30, "automatic switch-off temperature"),
        CONF_PRESET_HUMIDITY: (20, 90, "humidity threshold"),
        CONF_PRESET_MIN_TEMP: (10, 30, "minimum temperature"),
        CONF_PRESET_MAX_TEMP: (16, 35, "maximum temperature"),
    }
    for key in _OPTIONAL_TEMPERATURE_KEYS:
        if key not in profile:
            continue
        value = profile.get(key)
        if value in (None, ""):
            cleaned.pop(key, None)
        else:
            minimum, maximum, label = limits[key]
            cleaned[key] = _number(value, minimum, maximum, label)
    minimum = cleaned.get(CONF_PRESET_MIN_TEMP)
    maximum = cleaned.get(CONF_PRESET_MAX_TEMP)
    if minimum is not None and maximum is not None and minimum >= maximum:
        raise ValueError("minimum temperature must be lower than maximum temperature")
    return cleaned


class GreePanelProfileView(HomeAssistantView):
    """Update a single profile without touching sensors or sibling profiles."""

    url = PANEL_PROFILE_URL
    name = "api:gree_ac_cloud:panel_profile"
    requires_auth = True

    async def patch(self, request: web.Request) -> web.Response:
        if not _is_admin(request):
            return self.json({"error": "admin required"}, status_code=403)
        hass = request.app["hass"]
        try:
            body = await request.json()
        except Exception:
            return self.json({"error": "invalid JSON"}, status_code=400)
        entry_id = body.get("entry_id")
        mac = body.get("mac")
        profile_name = body.get("profile_name")
        if not entry_id or not _valid_mac(mac) or profile_name not in PRESET_NAMES:
            return self.json({"error": "invalid profile selection"}, status_code=400)
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            return self.json({"error": "entry not found"}, status_code=404)
        devices = dict(entry.options.get(CONF_DEVICES, {}))
        if mac not in devices:
            return self.json({"error": "device not found"}, status_code=404)
        room = dict(devices[mac])
        presets = dict(room.get(CONF_PRESETS, {}))
        try:
            cleaned = _clean_profile(body.get("profile"), presets.get(profile_name, {}))
        except ValueError as err:
            return self.json({"error": str(err)}, status_code=400)
        presets[profile_name] = cleaned
        room[CONF_PRESETS] = presets
        devices[mac] = room
        options = dict(entry.options)
        options[CONF_DEVICES] = devices
        hass.config_entries.async_update_entry(entry, options=options)
        await hass.config_entries.async_reload(entry.entry_id)
        return self.json({"ok": True, "profile": cleaned})
