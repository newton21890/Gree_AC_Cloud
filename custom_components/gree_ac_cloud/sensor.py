from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower, UnitOfTemperature
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEVICE_SENSORS
from .entity import GreeDeviceEntity


async def async_setup_entry(hass, entry, async_add_entities: AddEntitiesCallback):
    coordinators = entry.runtime_data["coordinators"]
    entities = []
    for coord in coordinators:
        for key, cfg in DEVICE_SENSORS.items():
            entities.append(GreeSensor(coord, key, cfg))
        entities.append(GreePowerSensor(coord))
        entities.append(GreeEnergySensor(coord))
    async_add_entities(entities)


SENSOR_CLASSES = {
    "InTem": SensorDeviceClass.TEMPERATURE,
    "OutTem": SensorDeviceClass.TEMPERATURE,
    "InHumi": SensorDeviceClass.HUMIDITY,
    "SetDeciTem": None,
}

SENSOR_UNITS = {
    "InTem": UnitOfTemperature.CELSIUS,
    "OutTem": UnitOfTemperature.CELSIUS,
    "InHumi": PERCENTAGE,
    "SetDeciTem": None,
}

SENSOR_STATE_CLASS = {
    "InTem": SensorStateClass.MEASUREMENT,
    "OutTem": SensorStateClass.MEASUREMENT,
    "InHumi": SensorStateClass.MEASUREMENT,
    "SetDeciTem": SensorStateClass.MEASUREMENT,
}


class GreeSensor(GreeDeviceEntity, SensorEntity):
    def __init__(self, coordinator, key, cfg):
        super().__init__(coordinator, coordinator.device, key_suffix=key)
        self._key = key
        self._attr_name = cfg["name"]
        self._attr_icon = cfg.get("icon")
        self._attr_device_class = SENSOR_CLASSES.get(key)
        self._attr_native_unit_of_measurement = SENSOR_UNITS.get(key)
        self._attr_state_class = SENSOR_STATE_CLASS.get(key)
        self._attr_entity_registry_enabled_default = key in ("InTem", "OutTem", "InHumi")

        config_entry_id = coordinator.config_entry.entry_id if hasattr(coordinator, "config_entry") else None
        if key in ("SetDeciTem",):
            self._attr_entity_registry_visible_default = False

    @property
    def native_value(self):
        raw = self.coordinator.data.get(self._key)
        if raw is None:
            return None
        if self._key in ("InTem", "OutTem"):
            return raw / 2 if raw > 50 else raw
        if self._key == "InHumi":
            return raw
        return raw


class GreePowerSensor(GreeDeviceEntity, SensorEntity):

    def __init__(self, coordinator):
        super().__init__(coordinator, coordinator.device, key_suffix="power")
        self._attr_name = f"{coordinator.device.name} Power"
        self._attr_device_class = SensorDeviceClass.POWER
        self._attr_native_unit_of_measurement = UnitOfPower.WATT
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_entity_registry_enabled_default = True
        self._attr_icon = "mdi:lightning-bolt"

    @property
    def native_value(self):
        return self.coordinator.data.get("estimated_power_w")


class GreeEnergySensor(GreeDeviceEntity, SensorEntity):

    def __init__(self, coordinator):
        super().__init__(coordinator, coordinator.device, key_suffix="energy")
        self._attr_name = f"{coordinator.device.name} Energy"
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_entity_registry_enabled_default = True
        self._attr_icon = "mdi:lightning-bolt-outline"

    @property
    def native_value(self):
        return self.coordinator.data.get("estimated_energy_kwh")
