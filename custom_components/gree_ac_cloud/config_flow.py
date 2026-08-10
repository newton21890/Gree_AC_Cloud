import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_DEVICE,
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
    CONF_PRESET_HOLD_ACTION,
    CONF_PRESET_HUMIDITY,
    CONF_PRESET_MAX_TEMP,
    CONF_PRESET_MIN_TEMP,
    CONF_PRESET_MODE,
    CONF_PRESET_QUIET,
    CONF_PRESET_SMART,
    CONF_PRESET_TARGET,
    CONF_PRESETS,
    CONF_SERVER,
    CONF_TEMPERATURE_SENSOR,
    CONF_TEMPERATURE_SENSORS,
    DOMAIN,
    GREE_CLOUD_SERVERS,
    PRESET_AWAY,
    PRESET_DAY,
    PRESET_DRED_OPTIONS,
    PRESET_FAN_OPTIONS,
    PRESET_HOLD_OFF,
    PRESET_HOLD_OPTIONS,
    PRESET_NIGHT,
    SMART_MODES,
)
from .gree_api import api_login

_LOGGER = logging.getLogger(__name__)


class GreeACCloudConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = 2

    @staticmethod
    def async_get_options_flow(config_entry):
        return GreeACCloudOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors = {}

        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_USERNAME].strip().lower())
            self._abort_if_unique_id_configured()

            server = GREE_CLOUD_SERVERS.get(user_input[CONF_SERVER], "eugrih.gree.com")
            try:
                uid, _token = await self.hass.async_add_executor_job(
                    api_login,
                    server,
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                )
                _LOGGER.info("Gree Cloud login OK: uid=%s", uid)
                _LOGGER.warning(
                    "Gree AC Cloud connected. Note: this will log out "
                    "the official Gree+ App (one session per account)."
                )
                return self.async_create_entry(
                    title=f"Gree AC Cloud ({user_input[CONF_USERNAME]})",
                    data=user_input,
                )
            except ValueError as err:
                errors["base"] = "invalid_auth"
                _LOGGER.warning("Auth failed: %s", err)
            except Exception as err:
                errors["base"] = "cannot_connect"
                _LOGGER.error("Connection failed: %s", err)

        data_schema = vol.Schema(
            {
                vol.Required(CONF_SERVER, default="Europe"): vol.In(list(GREE_CLOUD_SERVERS)),
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )


class GreeACCloudOptionsFlow(config_entries.OptionsFlow):
    """Configure external room sensors and climate presets per device."""

    def __init__(self):
        self._device_mac: str | None = None
        self._outdoor_sensor: str | None = None
        self._outdoor_humidity_sensor: str | None = None

    def _coordinators(self):
        runtime = getattr(self.config_entry, "runtime_data", None) or {}
        return runtime.get("coordinators", [])

    async def async_step_init(self, user_input=None):
        coordinators = self._coordinators()
        if not coordinators:
            return self.async_abort(reason="devices_not_ready")
        if user_input is not None:
            self._outdoor_sensor = user_input.get(CONF_OUTDOOR_TEMPERATURE_SENSOR)
            self._outdoor_humidity_sensor = user_input.get(CONF_OUTDOOR_HUMIDITY_SENSOR)
            return await self.async_step_device()
        current_outdoor = self.config_entry.options.get(CONF_OUTDOOR_TEMPERATURE_SENSOR)
        current_outdoor_humidity = self.config_entry.options.get(CONF_OUTDOOR_HUMIDITY_SENSOR)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_OUTDOOR_TEMPERATURE_SENSOR,
                        description={"suggested_value": current_outdoor},
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
                    ),
                    vol.Optional(
                        CONF_OUTDOOR_HUMIDITY_SENSOR,
                        description={"suggested_value": current_outdoor_humidity},
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor", device_class="humidity")
                    ),
                }
            ),
        )

    async def async_step_device(self, user_input=None):
        coordinators = self._coordinators()
        choices = {coordinator.device.mac: coordinator.device.name for coordinator in coordinators}
        if user_input is not None:
            self._device_mac = user_input[CONF_DEVICE]
            return await self.async_step_sensors()
        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(value=mac, label=name)
                                for mac, name in choices.items()
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    def _device_options(self) -> dict:
        return dict(self.config_entry.options.get(CONF_DEVICES, {}).get(self._device_mac, {}))

    async def async_step_sensors(self, user_input=None):
        current = self._device_options()
        if user_input is not None:
            current[CONF_TEMPERATURE_SENSORS] = user_input.get(CONF_TEMPERATURE_SENSORS, [])
            current[CONF_HUMIDITY_SENSORS] = user_input.get(CONF_HUMIDITY_SENSORS, [])
            current.pop(CONF_TEMPERATURE_SENSOR, None)
            current.pop(CONF_HUMIDITY_SENSOR, None)
            self._working = current
            return await self.async_step_day()
        return self.async_show_form(
            step_id="sensors",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_TEMPERATURE_SENSORS,
                        description={
                            "suggested_value": current.get(CONF_TEMPERATURE_SENSORS)
                            or (
                                [current[CONF_TEMPERATURE_SENSOR]]
                                if current.get(CONF_TEMPERATURE_SENSOR)
                                else []
                            )
                        },
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain="sensor", device_class="temperature", multiple=True
                        )
                    ),
                    vol.Optional(
                        CONF_HUMIDITY_SENSORS,
                        description={
                            "suggested_value": current.get(CONF_HUMIDITY_SENSORS)
                            or (
                                [current[CONF_HUMIDITY_SENSOR]]
                                if current.get(CONF_HUMIDITY_SENSOR)
                                else []
                            )
                        },
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain="sensor", device_class="humidity", multiple=True
                        )
                    ),
                }
            ),
        )

    def _preset_schema(self, preset: str) -> vol.Schema:
        defaults = self._working.get(CONF_PRESETS, {}).get(preset, {})
        return vol.Schema(
            {
                vol.Required(
                    CONF_PRESET_ENABLED, default=defaults.get(CONF_PRESET_ENABLED, False)
                ): bool,
                vol.Required(
                    CONF_PRESET_SMART,
                    default=defaults.get(CONF_PRESET_SMART, True),
                ): bool,
                vol.Required(
                    CONF_PRESET_MODE,
                    default=defaults.get(CONF_PRESET_MODE, "auto"),
                ): vol.In(SMART_MODES),
                vol.Optional(
                    CONF_PRESET_ALLOWED_MODES,
                    description={
                        "suggested_value": defaults.get(
                            CONF_PRESET_ALLOWED_MODES,
                            ["cool", "heat", "dry"]
                            if defaults.get(CONF_PRESET_MODE, "auto") == "auto"
                            else [defaults.get(CONF_PRESET_MODE)],
                        )
                    },
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value="cool", label="Cool"),
                            selector.SelectOptionDict(value="heat", label="Heat"),
                            selector.SelectOptionDict(value="dry", label="Dry"),
                        ],
                        multiple=True,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_PRESET_TARGET,
                    default=defaults.get(CONF_PRESET_TARGET, 26.0),
                ): vol.All(vol.Coerce(float), vol.Range(min=16, max=30)),
                vol.Required(
                    CONF_PRESET_DEADBAND,
                    default=defaults.get(CONF_PRESET_DEADBAND, 0.5),
                ): vol.All(vol.Coerce(float), vol.Range(min=0.2, max=2.0)),
                vol.Required(
                    CONF_PRESET_ADAPTIVE,
                    default=defaults.get(CONF_PRESET_ADAPTIVE, True),
                ): bool,
                vol.Required(
                    CONF_PRESET_FAN,
                    default=defaults.get(CONF_PRESET_FAN, "Smart"),
                ): vol.In(PRESET_FAN_OPTIONS),
                vol.Required(
                    CONF_PRESET_QUIET,
                    default=defaults.get(CONF_PRESET_QUIET, preset == PRESET_NIGHT),
                ): bool,
                vol.Optional(
                    CONF_PRESET_AUTO_OFF,
                    description={"suggested_value": defaults.get(CONF_PRESET_AUTO_OFF)},
                ): vol.All(vol.Coerce(float), vol.Range(min=16, max=35)),
                vol.Optional(
                    CONF_PRESET_HUMIDITY,
                    description={"suggested_value": defaults.get(CONF_PRESET_HUMIDITY)},
                ): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
                vol.Optional(
                    CONF_PRESET_MIN_TEMP,
                    description={"suggested_value": defaults.get(CONF_PRESET_MIN_TEMP)},
                ): vol.All(vol.Coerce(float), vol.Range(min=10, max=35)),
                vol.Optional(
                    CONF_PRESET_MAX_TEMP,
                    description={"suggested_value": defaults.get(CONF_PRESET_MAX_TEMP)},
                ): vol.All(vol.Coerce(float), vol.Range(min=10, max=35)),
                vol.Required(
                    CONF_PRESET_DRED,
                    default=defaults.get(CONF_PRESET_DRED, "No action"),
                ): vol.In(PRESET_DRED_OPTIONS),
                vol.Required(
                    CONF_PRESET_HOLD_ACTION,
                    default=defaults.get(CONF_PRESET_HOLD_ACTION, PRESET_HOLD_OFF),
                ): vol.In(PRESET_HOLD_OPTIONS),
            }
        )

    async def _preset_step(self, preset: str, next_step: str | None, user_input):
        if user_input is not None:
            presets = self._working.setdefault(CONF_PRESETS, {})
            presets[preset] = dict(user_input)
            if next_step:
                return await getattr(self, f"async_step_{next_step}")()
            devices = dict(self.config_entry.options.get(CONF_DEVICES, {}))
            devices[self._device_mac] = self._working
            return self.async_create_entry(
                title="",
                data={
                    CONF_DEVICES: devices,
                    CONF_OUTDOOR_TEMPERATURE_SENSOR: self._outdoor_sensor,
                    CONF_OUTDOOR_HUMIDITY_SENSOR: self._outdoor_humidity_sensor,
                },
            )
        return self.async_show_form(step_id=preset, data_schema=self._preset_schema(preset))

    async def async_step_day(self, user_input=None):
        return await self._preset_step(PRESET_DAY, PRESET_NIGHT, user_input)

    async def async_step_night(self, user_input=None):
        return await self._preset_step(PRESET_NIGHT, PRESET_AWAY, user_input)

    async def async_step_away(self, user_input=None):
        return await self._preset_step(PRESET_AWAY, None, user_input)
