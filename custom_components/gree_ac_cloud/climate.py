from homeassistant.components.climate import (
    FAN_AUTO, FAN_HIGH, FAN_LOW, FAN_MEDIUM,
    SWING_BOTH, SWING_HORIZONTAL, SWING_OFF, SWING_VERTICAL,
    ClimateEntity, ClimateEntityFeature, HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_HALVES, UnitOfTemperature
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import FAN_MAP, FAN_MAP_REV, HVAC_MAP, HVAC_MAP_REV, MIN_TEMP_C, MAX_TEMP_C
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
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.TURN_ON
    )
    _attr_name = None

    def __init__(self, coordinator):
        super().__init__(coordinator, coordinator.device, key_suffix="")
        self._attr_unique_id = f"climate_{coordinator.device.mac}"

    # ── temperature ───────────────────────────────────

    @property
    def current_temperature(self) -> float | None:
        raw = self.coordinator.data.get("InTem")
        if raw is None:
            return None
        if raw > 50:
            return raw / 2
        return float(raw)

    @property
    def target_temperature(self) -> float | None:
        d = self.coordinator.data.get("SetDeciTem")
        if d is not None:
            return d / 10
        raw = self.coordinator.data.get("SetTem")
        return float(raw) if raw is not None else None

    async def _sync_data(self):
        await self.coordinator.async_set_updated_data(dict(self._device.properties))

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
        await self.hass.async_add_executor_job(
            mqtt.send_command, self._device.mac, options, values,
        )

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
            await self.hass.async_add_executor_job(
                mqtt.send_command, self._device.mac, ["Pow"], [0]
            )
            self._device.properties["Pow"] = 0
        else:
            gree_mod = HVAC_MAP_REV.get(hvac_mode, 0)
            await self.hass.async_add_executor_job(
                mqtt.send_command, self._device.mac, ["Pow", "Mod"], [1, gree_mod]
            )
            self._device.properties["Pow"] = 1
            self._device.properties["Mod"] = gree_mod
        await self._sync_data()

    async def async_turn_on(self):
        mqtt = self.coordinator._mqtt
        await self.hass.async_add_executor_job(
            mqtt.send_command, self._device.mac, ["Pow"], [1]
        )
        self._device.properties["Pow"] = 1
        await self._sync_data()

    async def async_turn_off(self):
        mqtt = self.coordinator._mqtt
        await self.hass.async_add_executor_job(
            mqtt.send_command, self._device.mac, ["Pow"], [0]
        )
        self._device.properties["Pow"] = 0
        await self._sync_data()

    # ── fan ───────────────────────────────────────────

    @property
    def fan_mode(self) -> str:
        speed = self.coordinator.data.get("WdSpd")
        return FAN_MAP.get(speed, FAN_AUTO)

    async def async_set_fan_mode(self, fan_mode: str):
        speed = FAN_MAP_REV.get(fan_mode, 0)
        mqtt = self.coordinator._mqtt
        await self.hass.async_add_executor_job(
            mqtt.send_command, self._device.mac, ["WdSpd"], [speed]
        )
        self._device.properties["WdSpd"] = speed
        await self._sync_data()

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
        await self.hass.async_add_executor_job(
            mqtt.send_command, self._device.mac, ["SwUpDn", "SwingLfRig"], [v, h]
        )
        self._device.properties["SwUpDn"] = v
        self._device.properties["SwingLfRig"] = h
        await self._sync_data()
