import time
from datetime import timedelta

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
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DEVICES,
    CONF_HUMIDITY_SENSOR,
    CONF_HUMIDITY_SENSORS,
    CONF_OUTDOOR_HUMIDITY_SENSOR,
    CONF_OUTDOOR_TEMPERATURE_SENSOR,
    CONF_PRESET_ADAPTIVE,
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
    DRED_OPTIONS_REV,
    FAN_MAP,
    FAN_MAP_REV,
    HVAC_MAP,
    HVAC_MAP_REV,
    MAX_TEMP_C,
    MIN_TEMP_C,
    PRESET_DRED_SMART,
    PRESET_FAN_ALIASES,
    PRESET_FAN_SMART,
    PRESET_MANUAL,
    PRESET_NAMES,
    SMART_COMMAND_COOLDOWN_SECONDS,
    SMART_MODE_AUTO,
    SMART_MODE_COOL,
    SMART_MODE_DRY,
    SMART_MODE_HEAT,
)
from .entity import GreeDeviceEntity


async def async_setup_entry(hass, entry, async_add_entities: AddEntitiesCallback):
    coordinators = entry.runtime_data["coordinators"]
    async_add_entities(GreeACClimateEntity(coord) for coord in coordinators)


class GreeACClimateEntity(GreeDeviceEntity, ClimateEntity, RestoreEntity):
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
        self._smart_last_action = "inactive"
        self._smart_effective_target: float | None = None
        self._smart_last_command_at = 0.0
        self._smart_holding = False
        self._smart_manual_power: bool | None = None
        self._smart_fan_speed: str | None = None
        self._smart_dred_level: str | None = None
        self._last_observed_power = bool(coordinator.data.get("Pow"))

    @property
    def _room_options(self) -> dict:
        entry = self.coordinator.config_entry
        return entry.options.get(CONF_DEVICES, {}).get(self._device.mac, {})

    @property
    def _external_temperature_entities(self) -> list[str]:
        entities = self._room_options.get(CONF_TEMPERATURE_SENSORS)
        if entities is not None:
            return list(entities)
        legacy = self._room_options.get(CONF_TEMPERATURE_SENSOR)
        return [legacy] if legacy else []

    @property
    def _external_humidity_entities(self) -> list[str]:
        entities = self._room_options.get(CONF_HUMIDITY_SENSORS)
        if entities is not None:
            return list(entities)
        legacy = self._room_options.get(CONF_HUMIDITY_SENSOR)
        return [legacy] if legacy else []

    @property
    def _outdoor_temperature_entity(self) -> str | None:
        return self.coordinator.config_entry.options.get(CONF_OUTDOOR_TEMPERATURE_SENSOR)

    @property
    def _outdoor_humidity_entity(self) -> str | None:
        return self.coordinator.config_entry.options.get(CONF_OUTDOOR_HUMIDITY_SENSOR)

    def _average_entities(self, entity_ids: list[str]) -> float | None:
        values = [
            value
            for entity_id in entity_ids
            if (value := self._numeric_state(self.hass.states.get(entity_id))) is not None
        ]
        return round(sum(values) / len(values), 2) if values else None

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
        entities = list(
            dict.fromkeys(
                [
                    *self._external_temperature_entities,
                    *self._external_humidity_entities,
                    self._outdoor_temperature_entity,
                    self._outdoor_humidity_entity,
                ]
            )
        )
        entities = [entity_id for entity_id in entities if entity_id]
        if entities:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, entities, self._async_external_sensor_changed
                )
            )
        previous = await self.async_get_last_state()
        restored_preset = previous.attributes.get("preset_mode") if previous else None
        if not self._profile_control_enabled:
            restored_preset = PRESET_MANUAL
        elif restored_preset not in self.preset_modes:
            # Safety-first migration: never start regulating (and possibly
            # power on) until the user explicitly selects an automatic profile.
            restored_preset = PRESET_MANUAL
        if restored_preset in self.preset_modes:
            self._preset_mode = restored_preset
        # An off unit restored with an automatic profile is treated as an
        # explicit manual-off choice. This prevents startup from powering it on.
        if self._preset_mode != PRESET_MANUAL and not self.coordinator.data.get("Pow"):
            self._smart_manual_power = False
            self._smart_last_action = "manual_off"
        self.async_on_remove(
            async_track_time_interval(self.hass, self._async_smart_interval, timedelta(minutes=2))
        )
        if self._preset_mode:
            self.hass.async_create_task(self._async_evaluate_smart_profile(force=True))

    @callback
    def _handle_coordinator_update(self) -> None:
        """Recognize power changes made from a wall controller or another client."""
        power = bool(self.coordinator.data.get("Pow"))
        if power != self._last_observed_power:
            self._last_observed_power = power
            if not self._preset_action_lock and self._smart_profile_enabled:
                self._smart_manual_power = power
                self._smart_last_action = "manual_on" if power else "manual_off"
        super()._handle_coordinator_update()

    @callback
    def _async_external_sensor_changed(self, _event) -> None:
        self.async_write_ha_state()
        if self._preset_mode:
            self.hass.async_create_task(self._async_evaluate_smart_profile())

    @callback
    def _async_smart_interval(self, _now) -> None:
        if self._preset_mode:
            self.hass.async_create_task(self._async_evaluate_smart_profile())

    # ── temperature ───────────────────────────────────

    @property
    def current_temperature(self) -> float | None:
        """Use the configured HA room sensor, then documented TemSen."""
        value = self._average_entities(self._external_temperature_entities)
        if value is not None:
            return value
        raw = self.coordinator.data.get("TemSen")
        if raw is None or not isinstance(raw, (int, float)):
            return None
        return float(raw - 40)

    @property
    def current_humidity(self) -> float | None:
        return self._average_entities(self._external_humidity_entities)

    @property
    def extra_state_attributes(self):
        return {
            "temperature_sensors": self._external_temperature_entities,
            "humidity_sensors": self._external_humidity_entities,
            "temperature_average_sources": len(self._external_temperature_entities),
            "humidity_average_sources": len(self._external_humidity_entities),
            "outdoor_temperature_sensor": self._outdoor_temperature_entity,
            "outdoor_humidity_sensor": self._outdoor_humidity_entity,
            "outdoor_humidity": self._numeric_state(
                self.hass.states.get(self._outdoor_humidity_entity)
            )
            if self._outdoor_humidity_entity
            else None,
            "outdoor_temperature": self._numeric_state(
                self.hass.states.get(self._outdoor_temperature_entity)
            )
            if self._outdoor_temperature_entity
            else None,
            "preset_rules": self._room_options.get(CONF_PRESETS, {}),
            "smart_profile_active": self._smart_profile_enabled if self._preset_mode else False,
            "smart_effective_target": self._smart_effective_target,
            "smart_last_action": self._smart_last_action,
            "smart_fan_speed": self._smart_fan_speed,
            "smart_dred_level": self._smart_dred_level,
            "smart_manual_power_override": self._smart_manual_power,
            "profile_control_enabled": self._profile_control_enabled,
        }

    @property
    def _profile_control_enabled(self) -> bool:
        return bool(self._room_options.get(CONF_PROFILE_CONTROL_ENABLED, True))

    @property
    def preset_modes(self) -> list[str]:
        presets = self._room_options.get(CONF_PRESETS, {})
        modes = [PRESET_MANUAL]
        modes.extend(
            preset for preset in PRESET_NAMES if presets.get(preset, {}).get(CONF_PRESET_ENABLED)
        )
        return modes

    @property
    def preset_mode(self) -> str | None:
        return self._preset_mode

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode not in self.preset_modes:
            raise ValueError(f"Unsupported or disabled preset: {preset_mode}")
        self._preset_mode = preset_mode
        self._smart_manual_power = None
        self._smart_holding = False
        if preset_mode == PRESET_MANUAL:
            self._smart_effective_target = None
            self._smart_fan_speed = None
            self._smart_dred_level = None
            self._smart_last_action = "manual_profile"
        else:
            await self._async_apply_preset()
        self.async_write_ha_state()

    def _active_preset(self) -> dict:
        if not self._preset_mode or self._preset_mode == PRESET_MANUAL:
            return {}
        return self._room_options.get(CONF_PRESETS, {}).get(self._preset_mode, {})

    @property
    def _smart_profile_enabled(self) -> bool:
        return self._profile_control_enabled and bool(
            self._active_preset().get(CONF_PRESET_SMART, True)
        )

    @staticmethod
    def _normalize_preset_fan(fan: str | None) -> str:
        return PRESET_FAN_ALIASES.get(fan, fan or PRESET_FAN_SMART)

    @staticmethod
    def _smart_fan_for_demand(error: float, deadband: float) -> str:
        """Choose a quiet but responsive fan speed from room demand."""
        demand = max(0.0, error - deadband)
        if demand >= 3.0:
            return "Alta"
        if demand >= 2.0:
            return "Media-Alta"
        if demand >= 1.2:
            return "Media"
        if demand >= 0.6:
            return "Media-Bassa"
        return "Bassa"

    async def _async_apply_smart_fan(
        self,
        preset: dict,
        desired_mode: HVACMode,
        current: float,
        target: float,
        deadband: float,
    ) -> None:
        configured = self._normalize_preset_fan(preset.get(CONF_PRESET_FAN))
        if configured == PRESET_FAN_SMART:
            if desired_mode == HVACMode.COOL:
                fan = self._smart_fan_for_demand(current - target, deadband)
            elif desired_mode == HVACMode.HEAT:
                fan = self._smart_fan_for_demand(target - current, deadband)
            else:
                fan = "Bassa"
        else:
            fan = configured
        self._smart_fan_speed = fan
        if fan in FAN_MAP_REV and self.fan_mode != fan:
            await self.async_set_fan_mode(fan)

    async def _async_apply_smart_dred(
        self,
        preset: dict,
        desired_mode: HVACMode | None,
        current: float,
        target: float,
        deadband: float,
    ) -> None:
        """Choose I-Demand from thermal demand while preserving comfort."""
        configured = preset.get(CONF_PRESET_DRED, "No action")
        if configured == PRESET_DRED_SMART and desired_mode is None:
            self._smart_dred_level = "D3"
            if (
                self.coordinator.data.get("DREDEn") == 1
                and self.coordinator.data.get("Mod") == 1
                and int(self.coordinator.data.get("DRED", 0)) != DRED_OPTIONS_REV["D3"]
            ):
                await self._send(["DRED"], [DRED_OPTIONS_REV["D3"]])
            return
        if desired_mode != HVACMode.COOL or configured == "No action":
            self._smart_dred_level = None
            return
        if configured == PRESET_DRED_SMART:
            demand = current - target - deadband
            humidity = self.current_humidity
            humidity_limit = preset.get(CONF_PRESET_HUMIDITY)
            humidity_pressure = (
                humidity is not None
                and humidity_limit is not None
                and humidity > float(humidity_limit)
            )
            if demand >= 2.5 or humidity_pressure:
                dred = "Off"
            elif demand >= 1.5:
                dred = "D1"
            elif demand >= 0.8:
                dred = "D2"
            else:
                dred = "D3"
        else:
            dred = configured
        self._smart_dred_level = dred
        if (
            dred in DRED_OPTIONS_REV
            and self.coordinator.data.get("DREDEn") == 1
            and self.coordinator.data.get("Mod") == 1
        ):
            wanted = DRED_OPTIONS_REV[dred]
            if int(self.coordinator.data.get("DRED", 0)) != wanted:
                await self._send(["DRED"], [wanted])

    def _effective_smart_target(self, preset: dict) -> float:
        target = float(preset.get(CONF_PRESET_TARGET, 26))
        if not preset.get(CONF_PRESET_ADAPTIVE, True):
            return target
        outdoor_state = (
            self.hass.states.get(self._outdoor_temperature_entity)
            if self._outdoor_temperature_entity
            else None
        )
        outdoor = self._numeric_state(outdoor_state)
        if (
            outdoor_state is not None
            and (dt_util.utcnow() - outdoor_state.last_updated).total_seconds() > 10800
        ):
            outdoor = None
        mode = preset.get(CONF_PRESET_MODE, SMART_MODE_AUTO)
        if outdoor is not None:
            if mode in (SMART_MODE_AUTO, SMART_MODE_COOL) and outdoor > 30:
                target += min(2.0, (outdoor - 30) * 0.15)
            elif mode in (SMART_MODE_AUTO, SMART_MODE_HEAT) and outdoor < 8:
                target -= min(1.5, (8 - outdoor) * 0.10)
        return round(max(MIN_TEMP_C, min(MAX_TEMP_C, target)) * 2) / 2

    async def _async_apply_preset(self) -> None:
        preset = self._active_preset()
        if not preset or not self._profile_control_enabled:
            return
        self._smart_effective_target = self._effective_smart_target(preset)
        fan = self._normalize_preset_fan(preset.get(CONF_PRESET_FAN))
        if fan in FAN_MAP_REV:
            await self.async_set_fan_mode(fan)
        quiet = preset.get(CONF_PRESET_QUIET)
        if quiet is not None and "Quiet" in self.coordinator.data:
            await self._send(["Quiet"], [1 if quiet else 0])
        dred = preset.get(CONF_PRESET_DRED, "No action")
        if dred in DRED_OPTIONS_REV and self._device.properties.get("Mod") == 1:
            await self._send(["DRED"], [DRED_OPTIONS_REV[dred]])
            self._smart_dred_level = dred
        await self._async_evaluate_smart_profile(force=True)

    async def _async_evaluate_smart_profile(self, force: bool = False) -> None:
        """Regulate room climate with hysteresis and external sensor feedback."""
        if self._preset_action_lock:
            return
        preset = self._active_preset()
        if not preset or not self._profile_control_enabled:
            self._smart_last_action = "profile_disabled"
            return
        current = self.current_temperature
        humidity = self.current_humidity
        if current is None:
            self._smart_last_action = "waiting_room_temperature"
            self.async_write_ha_state()
            return
        target = self._effective_smart_target(preset)
        self._smart_effective_target = target
        deadband = max(0.2, min(2.0, float(preset.get(CONF_PRESET_DEADBAND, 0.5))))
        selected_mode = preset.get(CONF_PRESET_MODE, SMART_MODE_AUTO)
        minimum = preset.get(CONF_PRESET_MIN_TEMP)
        maximum = preset.get(CONF_PRESET_MAX_TEMP)
        humidity_limit = preset.get(CONF_PRESET_HUMIDITY)
        desired_mode: HVACMode | None = None

        if minimum is not None and current < float(minimum):
            desired_mode = HVACMode.HEAT
        elif maximum is not None and current > float(maximum):
            desired_mode = HVACMode.COOL
        elif selected_mode == SMART_MODE_COOL:
            desired_mode = HVACMode.COOL if current > target + deadband else None
        elif selected_mode == SMART_MODE_HEAT:
            desired_mode = HVACMode.HEAT if current < target - deadband else None
        elif selected_mode == SMART_MODE_DRY:
            desired_mode = (
                HVACMode.DRY
                if humidity_limit is not None
                and humidity is not None
                and humidity > float(humidity_limit)
                else None
            )
        else:
            if (
                humidity_limit is not None
                and humidity is not None
                and humidity > float(humidity_limit)
                and current >= target - deadband
            ):
                desired_mode = HVACMode.DRY
            elif current > target + deadband:
                desired_mode = HVACMode.COOL
            elif current < target - deadband:
                desired_mode = HVACMode.HEAT

        if not self._smart_profile_enabled:
            self._smart_fan_speed = None
            self._smart_dred_level = None
            # Smart disabled means monitoring/configuration only: never power
            # the machine on just to apply a target.
            if self.coordinator.data.get("Pow") and self.target_temperature != target:
                await self.async_set_temperature(**{ATTR_TEMPERATURE: target})
            self._smart_last_action = "fixed_target"
            self.async_write_ha_state()
            return

        now = time.monotonic()
        can_command = force or now - self._smart_last_command_at >= SMART_COMMAND_COOLDOWN_SECONDS
        is_on = bool(self.coordinator.data.get("Pow"))

        # Explicit user power commands always win. Keep the selected profile and
        # continue monitoring, but do not countermand that choice until the room
        # crosses the opposite hysteresis boundary or the profile is reselected.
        if self._smart_manual_power is False:
            self._smart_last_action = "manual_off"
            self.async_write_ha_state()
            return
        elif self._smart_manual_power is True:
            self._smart_last_action = "manual_on"
            self.async_write_ha_state()
            return

        await self._async_apply_smart_dred(preset, desired_mode, current, target, deadband)

        if desired_mode is None:
            self._smart_fan_speed = None
            self._smart_last_action = "comfort_hold"
            if is_on and can_command:
                self._preset_action_lock = True
                try:
                    await self.async_turn_off()
                    self._smart_last_command_at = now
                    self._smart_last_action = "comfort_reached_off"
                    self._smart_holding = True
                finally:
                    self._preset_action_lock = False
        else:
            await self._async_apply_smart_fan(preset, desired_mode, current, target, deadband)
            self._smart_last_action = f"request_{desired_mode.value}"
            if can_command and (not is_on or self.hvac_mode != desired_mode):
                self._preset_action_lock = True
                try:
                    await self.async_set_hvac_mode(desired_mode)
                    self._smart_last_command_at = now
                    self._smart_holding = False
                finally:
                    self._preset_action_lock = False
            if can_command and self.target_temperature != target:
                await self.async_set_temperature(**{ATTR_TEMPERATURE: target})
        self.async_write_ha_state()

    @property
    def target_temperature(self) -> float | None:
        d = self.coordinator.data.get("SetDeciTem")
        if d is not None:
            return d / 10
        raw = self.coordinator.data.get("SetTem")
        return float(raw) if raw is not None else None

    def _sync_data(self):
        self._last_observed_power = bool(self._device.properties.get("Pow"))
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
            if not self._preset_action_lock and self._smart_profile_enabled:
                self._smart_manual_power = hvac_mode != HVACMode.OFF
                self._smart_last_action = "manual_on" if self._smart_manual_power else "manual_off"
                self.async_write_ha_state()

    async def async_turn_on(self):
        mqtt = self.coordinator._mqtt
        if await mqtt.send_command(self._device.mac, ["Pow"], [1]):
            self._device.properties["Pow"] = 1
            self._sync_data()
            await self.coordinator.async_apply_startup_settings()
            if not self._preset_action_lock and self._smart_profile_enabled:
                self._smart_manual_power = True
                self._smart_last_action = "manual_on"
                self.async_write_ha_state()

    async def async_turn_off(self):
        mqtt = self.coordinator._mqtt
        if await mqtt.send_command(self._device.mac, ["Pow"], [0]):
            self._device.properties["Pow"] = 0
            self._sync_data()
            if not self._preset_action_lock and self._smart_profile_enabled:
                self._smart_manual_power = False
                self._smart_last_action = "manual_off"
                self.async_write_ha_state()

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
        if await mqtt.send_command(self._device.mac, ["SwUpDn", "SwingLfRig"], [v, h]):
            self._device.properties["SwUpDn"] = v
            self._device.properties["SwingLfRig"] = h
            self._sync_data()
