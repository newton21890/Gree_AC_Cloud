from homeassistant.components.climate import (
    FAN_AUTO,
    SWING_BOTH,
    SWING_HORIZONTAL,
    SWING_OFF,
    SWING_VERTICAL,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_HALVES, UnitOfTemperature
from homeassistant.core import callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_DEVICES,
    CONF_HUMIDITY_SENSOR,
    CONF_PRESET_AUTO_OFF,
    CONF_PRESET_DRED,
    CONF_PRESET_ENABLED,
    CONF_PRESET_HUMIDITY,
    CONF_PRESET_MAX_TEMP,
    CONF_PRESET_MIN_TEMP,
    CONF_PRESET_TARGET,
    CONF_PRESETS,
    CONF_TEMPERATURE_SENSOR,
    DRED_OPTIONS_REV,
    FAN_MAP,
    FAN_MAP_REV,
    HVAC_MAP,
    HVAC_MAP_REV,
    MAX_TEMP_C,
    MIN_TEMP_C,
    PRESET_NAMES,
)
from .entity import GreeDeviceEntity


async def async_setup_entry(hass, entry, async_add_entities: AddEntitiesCallback):
    coordinators = entry.runtime_data["coordinators"]
    async_add_entities(
        GreeACClimateEntity(coord) for coord in coordinators
    )


class GreeACClimateEntity(GreeDeviceEntity, ClimateEntity):
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.5
    _attr_precision = PRECISION_HALVES
    _attr_min_temp = MIN_TEMP_C
    _attr_max_temp = MAX_TEMP_C
    _attr_hvac_modes = [HVACMode(v) for v in HVAC_MAP_REV] + [HVACMode.OFF]
    _attr_fan_modes = list(FAN_MAP_REV)
    _attr_swing_modes = [SWING_OFF, SWING_VERTICAL, SWING_HORIZONTAL, SWING_BOTH]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.SWING_MODE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.TURN_ON
    )
    _attr_name = None

    def __init__(self, coordinator):
        super().__init__(coordinator, coordinator.device, key_suffix="")
        self._attr_unique_id = f"climate_{coordinator.device.mac}"
        self._preset_mode: str | None = None
        self._preset_action_lock = False

    @property
    def _room_options(self) -> dict:
        entry = self.coordinator.config_entry
        return entry.options.get(CONF_DEVICES, {}).get(self._device.mac, {})

    @property
    def _external_temperature_entity(self) -> str | None:
        return self._room_options.get(CONF_TEMPERATURE_SENSOR)

    @property
    def _external_humidity_entity(self) -> str | None:
        return self._room_options.get(CONF_HUMIDITY_SENSOR)

    @staticmethod
    def _numeric_state(state) -> float | None:
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        entities = [
            entity_id
            for entity_id in (
                self._external_temperature_entity,
                self._external_humidity_entity,
            )
            if entity_id
        ]
        if entities:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, entities, self._async_external_sensor_changed
                )
            )

    @callback
    def _async_external_sensor_changed(self, event) -> None:
        self.async_write_ha_state()
        if self._preset_mode:
            self.hass.async_create_task(self._async_evaluate_preset())

    # ── temperature ───────────────────────────────────

    @property
    def current_temperature(self) -> float | None:
        """Use the configured HA room sensor, then documented TemSen."""
        external = self._external_temperature_entity
        if external:
            value = self._numeric_state(self.hass.states.get(external))
            if value is not None:
                return value
        raw = self.coordinator.data.get("TemSen")
        if raw is None or not isinstance(raw, (int, float)):
            return None
        return float(raw - 40)

    @property
    def current_humidity(self) -> float | None:
        external = self._external_humidity_entity
        if not external:
            return None
        return self._numeric_state(self.hass.states.get(external))

    @property
    def extra_state_attributes(self):
        return {
            "temperature_sensor": self._external_temperature_entity,
            "humidity_sensor": self._external_humidity_entity,
            "preset_rules": self._room_options.get(CONF_PRESETS, {}),
        }

    @property
    def preset_modes(self) -> list[str]:
        presets = self._room_options.get(CONF_PRESETS, {})
        return [
            preset
            for preset in PRESET_NAMES
            if presets.get(preset, {}).get(CONF_PRESET_ENABLED)
        ]

    @property
    def preset_mode(self) -> str | None:
        return self._preset_mode

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode not in self.preset_modes:
            raise ValueError(f"Unsupported or disabled preset: {preset_mode}")
        self._preset_mode = preset_mode
        await self._async_apply_preset()
        self.async_write_ha_state()

    def _active_preset(self) -> dict:
        if not self._preset_mode:
            return {}
        return self._room_options.get(CONF_PRESETS, {}).get(self._preset_mode, {})

    async def _async_apply_preset(self) -> None:
        preset = self._active_preset()
        if not preset:
            return
        target = preset.get(CONF_PRESET_TARGET)
        if target is not None:
            await self.async_set_temperature(**{ATTR_TEMPERATURE: target})
        dred = preset.get(CONF_PRESET_DRED, "No action")
        if dred in DRED_OPTIONS_REV and self._device.properties.get("Mod") == 1:
            value = DRED_OPTIONS_REV[dred]
            if await self.coordinator._mqtt.send_command(
                self._device.mac, ["DRED"], [value]
            ):
                self._device.properties["DRED"] = value
                if value:
                    self._device.properties["Quiet"] = 0
                self._sync_data()
        await self._async_evaluate_preset()

    async def _async_evaluate_preset(self) -> None:
        """Apply optional room-sensor stop limits for the active preset."""
        if self._preset_action_lock:
            return
        preset = self._active_preset()
        if not preset:
            return
        temperature = self.current_temperature
        humidity = self.current_humidity
        auto_off = preset.get(CONF_PRESET_AUTO_OFF)
        min_temp = preset.get(CONF_PRESET_MIN_TEMP)
        max_temp = preset.get(CONF_PRESET_MAX_TEMP)
        humidity_limit = preset.get(CONF_PRESET_HUMIDITY)

        should_stop = (
            temperature is not None
            and (
                (auto_off is not None and temperature <= auto_off)
                or (min_temp is not None and temperature < min_temp)
                or (max_temp is not None and temperature > max_temp)
            )
        )
        if humidity_limit is not None and humidity is not None:
            should_stop = should_stop or humidity <= humidity_limit

        if should_stop and self._device.properties.get("Pow"):
            self._preset_action_lock = True
            try:
                await self.async_turn_off()
            finally:
                self._preset_action_lock = False

    @property
    def target_temperature(self) -> float | None:
        d = self.coordinator.data.get("SetDeciTem")
        if d is not None:
            return d / 10
        raw = self.coordinator.data.get("SetTem")
        return float(raw) if raw is not None else None

    def _sync_data(self):
        self.coordinator.async_set_updated_data(dict(self._device.properties))

    async def async_set_temperature(self, **kwargs):
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        mqtt = self.coordinator._mqtt
        deci = round(temp * 2) * 5
        if self._device.properties.get("Pow"):
            options, values = ["SetDeciTem"], [deci]
        else:
            options, values = ["Pow", "SetDeciTem"], [1, deci]
        if await mqtt.send_command(self._device.mac, options, values):
            for option, value in zip(options, values):
                self._device.properties[option] = value
            self._sync_data()
            if "Pow" in options:
                await self.coordinator.async_apply_startup_settings()

    # ── hvac mode ─────────────────────────────────────

    @property
    def hvac_mode(self) -> HVACMode:
        if not self.coordinator.data.get("Pow"):
            return HVACMode.OFF
        mod = self.coordinator.data.get("Mod")
        raw = HVAC_MAP.get(mod, "auto")
        return HVACMode(raw)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode):
        mqtt = self.coordinator._mqtt
        if hvac_mode == HVACMode.OFF:
            options, values = ["Pow"], [0]
        else:
            options = ["Pow", "Mod"]
            values = [1, HVAC_MAP_REV.get(hvac_mode, 0)]
        if await mqtt.send_command(self._device.mac, options, values):
            for option, value in zip(options, values):
                self._device.properties[option] = value
            self._sync_data()
            if hvac_mode == HVACMode.COOL:
                await self.coordinator.async_apply_startup_settings()

    async def async_turn_on(self):
        mqtt = self.coordinator._mqtt
        if await mqtt.send_command(self._device.mac, ["Pow"], [1]):
            self._device.properties["Pow"] = 1
            self._sync_data()
            await self.coordinator.async_apply_startup_settings()

    async def async_turn_off(self):
        mqtt = self.coordinator._mqtt
        if await mqtt.send_command(self._device.mac, ["Pow"], [0]):
            self._device.properties["Pow"] = 0
            self._sync_data()

    # ── fan ───────────────────────────────────────────

    @property
    def fan_mode(self) -> str:
        speed = self.coordinator.data.get("WdSpd")
        return FAN_MAP.get(speed, FAN_AUTO)

    async def async_set_fan_mode(self, fan_mode: str):
        speed = FAN_MAP_REV.get(fan_mode, 0)
        mqtt = self.coordinator._mqtt
        if await mqtt.send_command(self._device.mac, ["WdSpd"], [speed]):
            self._device.properties["WdSpd"] = speed
            self._sync_data()

    # ── swing ─────────────────────────────────────────

    @property
    def swing_mode(self) -> str:
        v = self.coordinator.data.get("SwUpDn", 0)
        h = self.coordinator.data.get("SwingLfRig", 0)
        if v and h:
            return SWING_BOTH
        if v:
            return SWING_VERTICAL
        if h:
            return SWING_HORIZONTAL
        return SWING_OFF

    async def async_set_swing_mode(self, swing_mode: str):
        v = 1 if swing_mode in (SWING_VERTICAL, SWING_BOTH) else 0
        h = 1 if swing_mode in (SWING_HORIZONTAL, SWING_BOTH) else 0
        mqtt = self.coordinator._mqtt
        if await mqtt.send_command(
            self._device.mac, ["SwUpDn", "SwingLfRig"], [v, h]
        ):
            self._device.properties["SwUpDn"] = v
            self._device.properties["SwingLfRig"] = h
            self._sync_data()
