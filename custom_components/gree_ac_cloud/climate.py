import logging
import time
from collections import deque
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
    CONF_PRESET_ALLOWED_MODES,
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
    PRESET_DRED_ALIASES,
    PRESET_DRED_SMART,
    PRESET_FAN_ALIASES,
    PRESET_FAN_SMART,
    PRESET_HOLD_D1,
    PRESET_HOLD_FAN,
    PRESET_HOLD_OFF,
    PRESET_MANUAL,
    PRESET_NAMES,
    PRESET_WORK_CURVE_BALANCED,
    PRESET_WORK_CURVE_GENTLE,
    PRESET_WORK_CURVE_RAPID,
    SMART_COMMAND_COOLDOWN_SECONDS,
    SMART_MODE_AUTO,
    SMART_MODE_COOL,
    SMART_MODE_DRY,
    SMART_MODE_HEAT,
)
from .entity import GreeDeviceEntity

_LOGGER = logging.getLogger(__name__)


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
        self._smart_manual_override_explicit = False
        self._smart_fan_speed: str | None = None
        self._smart_manual_fan: str | None = None
        self._smart_dred_level: str | None = None
        self._smart_samples: deque[tuple[float, float]] = deque(maxlen=180)
        self._smart_temperature_trend: float | None = None
        self._smart_temperature_trend_samples = 0
        self._smart_unmet_since = 0.0
        self._smart_unmet_minutes = 0.0
        self._smart_stall_boost = 0.0
        self._last_observed_power = bool(coordinator.data.get("Pow"))
        self._ignore_power_echo: bool | None = None
        self._ignore_power_echo_until = 0.0

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
        if previous and previous.attributes.get("smart_manual_override_explicit") is True:
            restored_override = previous.attributes.get("smart_manual_power_override")
            if isinstance(restored_override, bool):
                self._smart_manual_power = restored_override
                self._smart_manual_override_explicit = True
                self._smart_last_action = "manual_on" if restored_override else "manual_off"
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
            expected_echo = (
                self._ignore_power_echo is power
                and time.monotonic() <= self._ignore_power_echo_until
            )
            if expected_echo:
                self._ignore_power_echo = None
            elif not self._preset_action_lock and self._smart_profile_enabled:
                command_age = self.coordinator._mqtt.command_age(self._device.mac)
                if command_age is not None and command_age <= 45:
                    _LOGGER.info(
                        "Ignoring delayed power echo for %s: Pow=%s, last integration command %.1fs ago",
                        self._device.mac,
                        int(power),
                        command_age,
                    )
                else:
                    self._smart_manual_power = power
                    self._smart_manual_override_explicit = True
                    self._smart_last_action = "manual_on" if power else "manual_off"
                    _LOGGER.info(
                        "External power change for %s classified as %s; last integration command age=%s",
                        self._device.mac,
                        self._smart_last_action,
                        f"{command_age:.1f}s" if command_age is not None else "none",
                    )
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
        """Use configured HA room sensors, then enabled native InTem."""
        value = self._average_entities(self._external_temperature_entities)
        if value is not None:
            return value
        raw = self.coordinator.data.get("InTem")
        if raw is None or not isinstance(raw, (int, float)) or raw == 0:
            return None
        return float(raw - 40)

    @property
    def current_humidity(self) -> float | None:
        value = self._average_entities(self._external_humidity_entities)
        if value is not None:
            return value
        raw = self.coordinator.data.get("InHumi")
        if (
            self.coordinator.data.get("InHumiEn") != 1
            or not isinstance(raw, (int, float))
            or not 0 < raw <= 100
        ):
            return None
        return float(raw)

    @property
    def extra_state_attributes(self):
        return {
            "temperature_sensors": self._external_temperature_entities,
            "humidity_sensors": self._external_humidity_entities,
            "temperature_average_sources": len(self._external_temperature_entities),
            "humidity_average_sources": len(self._external_humidity_entities),
            "gree_indoor_temperature_sensor_enabled": self.coordinator.data.get("InTemEn") == 1,
            "gree_indoor_temperature_sensor_enable_raw": self.coordinator.data.get("InTemEn"),
            "gree_indoor_humidity_sensor_enabled": self.coordinator.data.get("InHumiEn") == 1,
            "gree_indoor_humidity_sensor_enable_raw": self.coordinator.data.get("InHumiEn"),
            "gree_indoor_probe_raw": self.coordinator.data.get("InTem"),
            "gree_outdoor_probe_raw": self.coordinator.data.get("OutTem"),
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
            "smart_manual_fan_override": self._smart_manual_fan,
            "smart_work_curve": self._profile_work_curve(self._active_preset())
            if self._preset_mode and self._preset_mode != PRESET_MANUAL
            else None,
            "smart_dred_level": self._smart_dred_level,
            "smart_dred_applied": self._effective_dred_label,
            "smart_dred_verified": (
                self._smart_dred_level is None
                or self._smart_dred_level == self._effective_dred_label
            ),
            "smart_temperature_trend_c_per_hour": self._smart_temperature_trend,
            "smart_temperature_trend_samples": self._smart_temperature_trend_samples,
            "smart_unmet_minutes": round(self._smart_unmet_minutes, 1),
            "smart_stall_boost": self._smart_stall_boost,
            "smart_manual_power_override": self._smart_manual_power,
            "smart_manual_override_explicit": self._smart_manual_override_explicit,
            "profile_control_enabled": self._profile_control_enabled,
        }

    @property
    def _effective_dred_label(self) -> str | None:
        try:
            raw = int(self.coordinator.data.get("DRED", 0))
            if raw == 0 and int(self.coordinator.data.get("Idemand", 0)) == 1:
                raw = 1
        except (TypeError, ValueError):
            return None
        return {value: label for label, value in DRED_OPTIONS_REV.items()}.get(raw)

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
        self._smart_manual_override_explicit = False
        self._smart_manual_fan = None
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
    def _profile_work_curve(preset: dict) -> str:
        """Return a supported approach curve, including legacy profiles."""
        curve = preset.get(CONF_PRESET_WORK_CURVE, PRESET_WORK_CURVE_BALANCED)
        return (
            curve
            if curve
            in (
                PRESET_WORK_CURVE_GENTLE,
                PRESET_WORK_CURVE_BALANCED,
                PRESET_WORK_CURVE_RAPID,
            )
            else PRESET_WORK_CURVE_BALANCED
        )

    @staticmethod
    def _smart_fan_for_demand(
        error: float, deadband: float, curve: str = PRESET_WORK_CURVE_BALANCED
    ) -> str:
        """Map absolute thermal demand to airflow according to the curve.

        Deadband controls only when an idle unit restarts. Subtracting it from
        an active demand made the controller settle above target with low fan.
        """
        demand = max(0.0, error)
        thresholds = {
            PRESET_WORK_CURVE_GENTLE: (3.5, 2.6, 1.8, 0.9),
            PRESET_WORK_CURVE_BALANCED: (2.6, 1.7, 1.0, 0.4),
            PRESET_WORK_CURVE_RAPID: (1.5, 0.9, 0.5, 0.15),
        }.get(curve, (2.6, 1.7, 1.0, 0.4))
        if demand >= thresholds[0]:
            return "Alta"
        if demand >= thresholds[1]:
            return "Media-Alta"
        if demand >= thresholds[2]:
            return "Media"
        if demand >= thresholds[3]:
            return "Media-Bassa"
        return "Bassa"

    def _smart_stall_demand_boost(
        self,
        desired_mode: HVACMode | None,
        current: float,
        target: float,
        trend: float | None,
    ) -> float:
        """Escalate capacity when an active demand stops approaching target."""
        now = time.monotonic()
        error = (
            current - target
            if desired_mode == HVACMode.COOL
            else target - current
            if desired_mode == HVACMode.HEAT
            else 0.0
        )
        moving_toward_target = (
            desired_mode == HVACMode.COOL and trend is not None and trend <= -0.12
        ) or (desired_mode == HVACMode.HEAT and trend is not None and trend >= 0.12)
        if desired_mode not in (HVACMode.COOL, HVACMode.HEAT) or error <= 0.15:
            self._smart_unmet_since = 0.0
            self._smart_unmet_minutes = 0.0
            self._smart_stall_boost = 0.0
            return 0.0
        if self._smart_unmet_since <= 0 or moving_toward_target:
            self._smart_unmet_since = now
        self._smart_unmet_minutes = max(0.0, (now - self._smart_unmet_since) / 60)
        curve = self._profile_work_curve(self._active_preset())
        first_stage, second_stage = {
            PRESET_WORK_CURVE_GENTLE: (90, 180),
            PRESET_WORK_CURVE_BALANCED: (45, 90),
            PRESET_WORK_CURVE_RAPID: (20, 45),
        }[curve]
        self._smart_stall_boost = (
            1.2
            if self._smart_unmet_minutes >= second_stage
            else 0.6
            if self._smart_unmet_minutes >= first_stage
            else 0.0
        )
        return self._smart_stall_boost

    def _record_smart_temperature(self, current: float) -> float | None:
        """Track a short rolling trend used to damp profile decisions.

        Recorder remains the persistent source for UI analysis. The control
        loop keeps its own live window so a Recorder query can never block the
        two-minute profile evaluation.
        """
        now = time.monotonic()
        if not self._smart_samples or now - self._smart_samples[-1][0] >= 45:
            self._smart_samples.append((now, current))
        while self._smart_samples and now - self._smart_samples[0][0] > 7200:
            self._smart_samples.popleft()
        recent = [sample for sample in self._smart_samples if now - sample[0] <= 3600]
        self._smart_temperature_trend_samples = len(recent)
        if len(recent) < 3 or recent[-1][0] - recent[0][0] < 600:
            self._smart_temperature_trend = None
            return None
        elapsed_hours = (recent[-1][0] - recent[0][0]) / 3600
        trend = (recent[-1][1] - recent[0][1]) / elapsed_hours
        self._smart_temperature_trend = round(max(-6.0, min(6.0, trend)), 2)
        return self._smart_temperature_trend

    @staticmethod
    def _trend_adjusted_deadband(
        deadband: float,
        selected_mode: str,
        active_mode: HVACMode,
        current: float,
        target: float,
        trend: float | None,
    ) -> float:
        """Add a small restart guard while temperature is moving correctly."""
        if trend is None or active_mode != HVACMode.OFF:
            return deadband
        cooling_toward_target = (
            selected_mode in (SMART_MODE_AUTO, SMART_MODE_COOL)
            and current > target
            and trend <= -0.2
        )
        heating_toward_target = (
            selected_mode in (SMART_MODE_AUTO, SMART_MODE_HEAT)
            and current < target
            and trend >= 0.2
        )
        return (
            min(2.0, deadband + 0.2) if cooling_toward_target or heating_toward_target else deadband
        )

    @staticmethod
    def _temperature_hysteresis_mode(
        selected_mode: str,
        current: float,
        target: float,
        deadband: float,
        active_mode: HVACMode,
    ) -> HVACMode | None:
        """Return demand using target as stop point and deadband as restart gap.

        Cooling stops when the room reaches the target and restarts only above
        target + deadband. Heating is the exact inverse. This avoids treating
        the deadband as a symmetric offset that stops cooling before the target.
        """
        if selected_mode == SMART_MODE_COOL:
            if active_mode == HVACMode.COOL and current > target:
                return HVACMode.COOL
            return HVACMode.COOL if current > target + deadband else None
        if selected_mode == SMART_MODE_HEAT:
            if active_mode == HVACMode.HEAT and current < target:
                return HVACMode.HEAT
            return HVACMode.HEAT if current < target - deadband else None
        if selected_mode == SMART_MODE_AUTO:
            if active_mode == HVACMode.COOL and current > target:
                return HVACMode.COOL
            if active_mode == HVACMode.HEAT and current < target:
                return HVACMode.HEAT
            if current > target + deadband:
                return HVACMode.COOL
            if current < target - deadband:
                return HVACMode.HEAT
        return None

    def _smart_fan_for_profile(
        self,
        preset: dict,
        desired_mode: HVACMode,
        current: float,
        target: float,
        deadband: float,
        demand_boost: float = 0.0,
    ) -> str:
        if self._smart_manual_fan in FAN_MAP_REV:
            return self._smart_manual_fan
        configured = self._normalize_preset_fan(preset.get(CONF_PRESET_FAN))
        if configured != PRESET_FAN_SMART:
            return configured
        curve = self._profile_work_curve(preset)
        if desired_mode == HVACMode.COOL:
            return self._smart_fan_for_demand(current - target + demand_boost, deadband, curve)
        if desired_mode == HVACMode.HEAT:
            return self._smart_fan_for_demand(target - current + demand_boost, deadband, curve)
        return "Bassa"

    def _smart_dred_for_profile(
        self,
        preset: dict,
        desired_mode: HVACMode | None,
        current: float,
        target: float,
        deadband: float,
        demand_boost: float = 0.0,
    ) -> str | None:
        """Choose I-Demand from thermal demand while preserving comfort."""
        configured = PRESET_DRED_ALIASES.get(
            preset.get(CONF_PRESET_DRED), preset.get(CONF_PRESET_DRED, "No action")
        )
        if desired_mode != HVACMode.COOL or configured == "No action":
            return None
        if configured != PRESET_DRED_SMART:
            return configured
        # D1 disables the compressor, so Smart must never request it while
        # cooling is needed. High thermal demand runs without I-Demand;
        # D3/D2 are introduced only as the room approaches its target.
        demand = current - target + demand_boost
        humidity = self.current_humidity
        humidity_limit = preset.get(CONF_PRESET_HUMIDITY)
        humidity_pressure = (
            humidity is not None and humidity_limit is not None and humidity > float(humidity_limit)
        )
        curve = self._profile_work_curve(preset)
        full_power_at, reduced_at = {
            PRESET_WORK_CURVE_GENTLE: (2.2, 1.3),
            PRESET_WORK_CURVE_BALANCED: (1.5, 0.8),
            PRESET_WORK_CURVE_RAPID: (0.5, 0.2),
        }[curve]
        if demand >= full_power_at or humidity_pressure:
            return "Off"
        if demand >= reduced_at:
            return "D3"
        return "D2"

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
        configured_modes = preset.get(CONF_PRESET_ALLOWED_MODES)
        allowed_modes = (
            set(configured_modes)
            if mode == SMART_MODE_AUTO and configured_modes is not None
            else (
                {SMART_MODE_COOL, SMART_MODE_HEAT, SMART_MODE_DRY}
                if mode == SMART_MODE_AUTO
                else {mode}
            )
        )
        if outdoor is not None:
            if SMART_MODE_COOL in allowed_modes and outdoor > 30:
                target += min(2.0, (outdoor - 30) * 0.15)
            elif SMART_MODE_HEAT in allowed_modes and outdoor < 8:
                target -= min(1.5, (8 - outdoor) * 0.10)
        return round(max(MIN_TEMP_C, min(MAX_TEMP_C, target)) * 2) / 2

    async def _async_apply_preset(self) -> None:
        preset = self._active_preset()
        if not preset or not self._profile_control_enabled:
            return
        self._smart_effective_target = self._effective_smart_target(preset)
        # Evaluation batches mode, target, fan, Quiet and I-Demand into one
        # cloud command, making profile changes substantially faster.
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
        trend = self._record_smart_temperature(current)
        decision_deadband = self._trend_adjusted_deadband(
            deadband, selected_mode, self.hvac_mode, current, target, trend
        )
        minimum = preset.get(CONF_PRESET_MIN_TEMP)
        maximum = preset.get(CONF_PRESET_MAX_TEMP)
        humidity_limit = preset.get(CONF_PRESET_HUMIDITY)
        is_on = bool(self.coordinator.data.get("Pow"))
        active_mode = self.hvac_mode if is_on else HVACMode.OFF
        desired_mode: HVACMode | None = None

        configured_modes = preset.get(CONF_PRESET_ALLOWED_MODES)
        allowed_modes = (
            set(configured_modes)
            if selected_mode == SMART_MODE_AUTO and configured_modes is not None
            else (
                {SMART_MODE_COOL, SMART_MODE_HEAT, SMART_MODE_DRY}
                if selected_mode == SMART_MODE_AUTO
                else {selected_mode}
            )
        )
        if minimum is not None and current < float(minimum) and SMART_MODE_HEAT in allowed_modes:
            desired_mode = HVACMode.HEAT
        elif maximum is not None and current > float(maximum) and SMART_MODE_COOL in allowed_modes:
            desired_mode = HVACMode.COOL
        elif selected_mode == SMART_MODE_DRY:
            desired_mode = (
                HVACMode.DRY
                if SMART_MODE_DRY in allowed_modes
                and humidity_limit is not None
                and humidity is not None
                and humidity > float(humidity_limit)
                else None
            )
        elif (
            selected_mode == SMART_MODE_AUTO
            and SMART_MODE_DRY in allowed_modes
            and humidity_limit is not None
            and humidity is not None
            and humidity > float(humidity_limit)
            and current >= target - deadband
        ):
            desired_mode = HVACMode.DRY
        else:
            desired_mode = self._temperature_hysteresis_mode(
                selected_mode, current, target, decision_deadband, active_mode
            )
        mode_names = {
            HVACMode.COOL: SMART_MODE_COOL,
            HVACMode.HEAT: SMART_MODE_HEAT,
            HVACMode.DRY: SMART_MODE_DRY,
        }
        if desired_mode is not None and mode_names.get(desired_mode) not in allowed_modes:
            desired_mode = None

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

        # Explicit user power commands always win. Keep the selected profile and
        # continue monitoring, but do not countermand that choice until the room
        # crosses the opposite hysteresis boundary or the profile is reselected.
        if self._smart_manual_power is False:
            self._smart_fan_speed = None
            self._smart_dred_level = None
            self._smart_last_action = "manual_off"
            self.async_write_ha_state()
            return
        elif self._smart_manual_power is True:
            # Automatic commands are suspended, but diagnostics must describe
            # the observed controller state rather than a stale Smart request.
            self._smart_fan_speed = FAN_MAP.get(self.coordinator.data.get("WdSpd"))
            self._smart_dred_level = self._effective_dred_label
            self._smart_last_action = "manual_on"
            self.async_write_ha_state()
            return

        demand_boost = self._smart_stall_demand_boost(desired_mode, current, target, trend)
        dred = self._smart_dred_for_profile(
            preset, desired_mode, current, target, deadband, demand_boost
        )
        self._smart_dred_level = dred

        if desired_mode is None:
            hold_action = preset.get(CONF_PRESET_HOLD_ACTION, PRESET_HOLD_OFF)
            fan = self._smart_fan_for_profile(preset, HVACMode.FAN_ONLY, current, target, deadband)
            self._smart_fan_speed = fan if hold_action != PRESET_HOLD_OFF else None
            self._smart_last_action = f"comfort_hold_{hold_action}"
            if hold_action == PRESET_HOLD_OFF:
                if is_on and can_command:
                    self._preset_action_lock = True
                    try:
                        await self.async_turn_off()
                        self._smart_last_command_at = now
                        self._smart_last_action = "comfort_reached_off"
                        self._smart_holding = True
                    finally:
                        self._preset_action_lock = False
            elif can_command:
                # Two explicit circulation strategies are supported at comfort:
                # FAN_ONLY is semantically clear and compressor-independent;
                # D1 keeps Cool selected but requests compressor inhibition on
                # controllers where this is the preferred ventilation path.
                hold_mode = HVACMode.FAN_ONLY if hold_action == PRESET_HOLD_FAN else HVACMode.COOL
                options = ["Pow", "Mod"]
                values = [1, HVAC_MAP_REV.get(hold_mode, 3)]
                if fan in FAN_MAP_REV:
                    options.append("WdSpd")
                    values.append(FAN_MAP_REV[fan])
                if hold_action == PRESET_HOLD_D1 and self.coordinator.data.get("DREDEn") == 1:
                    options.append("DRED")
                    values.append(DRED_OPTIONS_REV["D1"])
                elif hold_action == PRESET_HOLD_FAN and "DRED" in self.coordinator.data:
                    # FAN_ONLY does not need I-Demand; clear any old D1/D2/D3
                    # so the next compressor request starts from a known state.
                    options.append("DRED")
                    values.append(DRED_OPTIONS_REV["Off"])
                self._preset_action_lock = True
                self._expect_power_echo(True)
                try:
                    if await self.coordinator._mqtt.send_command(self._device.mac, options, values):
                        for option, value in zip(options, values):
                            self._device.properties[option] = value
                        self._sync_data()
                        self._smart_last_command_at = now
                        self._smart_last_action = (
                            "comfort_circulation_fan"
                            if hold_action == PRESET_HOLD_FAN
                            else "comfort_circulation_d1"
                        )
                        self._smart_holding = True
                finally:
                    self._preset_action_lock = False
        else:
            fan = self._smart_fan_for_profile(
                preset, desired_mode, current, target, deadband, demand_boost
            )
            self._smart_fan_speed = fan
            self._smart_last_action = f"request_{desired_mode.value}"
            if can_command:
                options = ["Pow", "Mod", "SetDeciTem"]
                values = [1, HVAC_MAP_REV.get(desired_mode, 0), round(target * 2) * 5]
                if fan in FAN_MAP_REV:
                    options.append("WdSpd")
                    values.append(FAN_MAP_REV[fan])
                quiet = preset.get(CONF_PRESET_QUIET)
                if quiet is not None and "Quiet" in self.coordinator.data:
                    options.append("Quiet")
                    values.append(1 if quiet else 0)
                if (
                    dred in DRED_OPTIONS_REV
                    and self.coordinator.data.get("DREDEn") == 1
                    and desired_mode == HVACMode.COOL
                ):
                    options.append("DRED")
                    values.append(DRED_OPTIONS_REV[dred])
                elif (
                    desired_mode == HVACMode.COOL
                    and self.coordinator.data.get("DREDEn") == 1
                    and self._smart_holding
                    and int(self.coordinator.data.get("DRED", 0)) == 1
                ):
                    # Leaving a D1 comfort hold must always restore compressor
                    # availability, even when cooling I-Demand is "No action".
                    options.append("DRED")
                    values.append(DRED_OPTIONS_REV["Off"])
                self._preset_action_lock = True
                self._expect_power_echo(True)
                try:
                    if await self.coordinator._mqtt.send_command(self._device.mac, options, values):
                        for option, value in zip(options, values):
                            self._device.properties[option] = value
                        self._sync_data()
                        self._smart_last_command_at = now
                        self._smart_holding = False
                        if desired_mode == HVACMode.COOL:
                            await self.coordinator.async_apply_startup_settings()
                finally:
                    self._preset_action_lock = False
        self.async_write_ha_state()

    @property
    def target_temperature(self) -> float | None:
        d = self.coordinator.data.get("SetDeciTem")
        if d is not None:
            return d / 10
        raw = self.coordinator.data.get("SetTem")
        return float(raw) if raw is not None else None

    def _expect_power_echo(self, power: bool) -> None:
        self._ignore_power_echo = power
        self._ignore_power_echo_until = time.monotonic() + 30

    def _sync_data(self):
        self._last_observed_power = bool(self._device.properties.get("Pow"))
        self.coordinator.async_set_updated_data(dict(self._device.properties))

    async def async_set_temperature(self, **kwargs):
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        mqtt = self.coordinator._mqtt
        temp = round(float(temp) * 2) / 2
        deci = round(temp * 10)
        profile_options = None
        profile_active = self._preset_mode and self._preset_mode != PRESET_MANUAL
        if profile_active:
            devices = dict(self.coordinator.config_entry.options.get(CONF_DEVICES, {}))
            room = dict(devices.get(self._device.mac, {}))
            presets = dict(room.get(CONF_PRESETS, {}))
            preset = dict(presets.get(self._preset_mode, {}))
            preset[CONF_PRESET_TARGET] = temp
            presets[self._preset_mode] = preset
            room[CONF_PRESETS] = presets
            devices[self._device.mac] = room
            profile_options = {
                **self.coordinator.config_entry.options,
                CONF_DEVICES: devices,
            }
            self._smart_effective_target = self._effective_smart_target(preset)
            temp = self._smart_effective_target
            deci = round(temp * 10)
        if profile_active or self._device.properties.get("Pow"):
            # A profile decides power from room demand. Changing its target must
            # never turn an intentionally idle unit on merely to update the setpoint.
            options, values = ["SetDeciTem"], [deci]
        else:
            options, values = ["Pow", "SetDeciTem"], [1, deci]
            self._expect_power_echo(True)
        if await mqtt.send_command(self._device.mac, options, values):
            for option, value in zip(options, values):
                self._device.properties[option] = value
            self._sync_data()
            if profile_options is not None:
                _LOGGER.info(
                    "Profile target updated from climate control: %s preset=%s configured=%.1f effective=%.1f",
                    self._device.mac,
                    self._preset_mode,
                    preset[CONF_PRESET_TARGET],
                    temp,
                )
                # Persist only after the hardware command succeeds. The existing
                # options listener reloads the entry and resumes this profile.
                self.hass.config_entries.async_update_entry(
                    self.coordinator.config_entry, options=profile_options
                )
            elif "Pow" in options:
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
        self._expect_power_echo(hvac_mode != HVACMode.OFF)
        if await mqtt.send_command(self._device.mac, options, values):
            for option, value in zip(options, values):
                self._device.properties[option] = value
            self._sync_data()
            if hvac_mode == HVACMode.COOL:
                await self.coordinator.async_apply_startup_settings()
            if not self._preset_action_lock and self._smart_profile_enabled:
                self._smart_manual_power = hvac_mode != HVACMode.OFF
                self._smart_manual_override_explicit = True
                self._smart_last_action = "manual_on" if self._smart_manual_power else "manual_off"
                self.async_write_ha_state()

    async def async_turn_on(self):
        mqtt = self.coordinator._mqtt
        self._expect_power_echo(True)
        if await mqtt.send_command(self._device.mac, ["Pow"], [1]):
            self._device.properties["Pow"] = 1
            self._sync_data()
            await self.coordinator.async_apply_startup_settings()
            if not self._preset_action_lock and self._smart_profile_enabled:
                self._smart_manual_power = True
                self._smart_manual_override_explicit = True
                self._smart_last_action = "manual_on"
                self.async_write_ha_state()

    async def async_turn_off(self):
        mqtt = self.coordinator._mqtt
        self._expect_power_echo(False)
        if await mqtt.send_command(self._device.mac, ["Pow"], [0]):
            self._device.properties["Pow"] = 0
            self._sync_data()
            if not self._preset_action_lock and self._smart_profile_enabled:
                self._smart_manual_power = False
                self._smart_manual_override_explicit = True
                self._smart_last_action = "manual_off"
                self.async_write_ha_state()

    # ── fan ───────────────────────────────────────────

    @property
    def fan_mode(self) -> str:
        speed = self.coordinator.data.get("WdSpd")
        return FAN_MAP.get(speed, FAN_AUTO)

    async def async_set_fan_mode(self, fan_mode: str):
        if fan_mode not in FAN_MAP_REV:
            raise ValueError(f"Unsupported fan mode: {fan_mode}")
        speed = FAN_MAP_REV[fan_mode]
        mqtt = self.coordinator._mqtt
        if await mqtt.send_command(self._device.mac, ["WdSpd"], [speed]):
            self._device.properties["WdSpd"] = speed
            self._smart_manual_fan = fan_mode if self._smart_profile_enabled else None
            self._smart_fan_speed = fan_mode if self._smart_profile_enabled else None
            self._sync_data()
            self.async_write_ha_state()

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
