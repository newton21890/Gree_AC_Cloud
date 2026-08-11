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
        entities.append(GreeBaselinePowerSensor(coord))
        entities.append(GreeSavingPowerSensor(coord))
        entities.append(GreeEnergySensor(coord))
    async_add_entities(entities)


SENSOR_CLASSES = {
    "InTem": SensorDeviceClass.TEMPERATURE,
    "OutTem": SensorDeviceClass.TEMPERATURE,
    "TemSen": SensorDeviceClass.TEMPERATURE,
    "InHumi": SensorDeviceClass.HUMIDITY,
    "SetDeciTem": None,
}

SENSOR_UNITS = {
    "InTem": UnitOfTemperature.CELSIUS,
    "OutTem": UnitOfTemperature.CELSIUS,
    "TemSen": UnitOfTemperature.CELSIUS,
    "InHumi": PERCENTAGE,
    "SetDeciTem": None,
}

SENSOR_STATE_CLASS = {
    "InTem": SensorStateClass.MEASUREMENT,
    "OutTem": SensorStateClass.MEASUREMENT,
    "TemSen": SensorStateClass.MEASUREMENT,
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
        # A column being returned by the cloud is not the same as the physical
        # sensor being enabled. U-Match units commonly return TemSen=None and
        # InHumi=0 when those optional probes are not available.
        self._attr_entity_registry_enabled_default = cfg.get(
            "enabled_default", cfg.get("diagnostic", False)
        )

        if cfg.get("diagnostic"):
            from homeassistant.helpers.entity import EntityCategory

            self._attr_entity_category = EntityCategory.DIAGNOSTIC

        if key == "SetDeciTem":
            self._attr_entity_registry_visible_default = False

    @property
    def available(self) -> bool:
        if not super().available or self._key not in self.coordinator.data:
            return False
        raw = self.coordinator.data.get(self._key)
        if self._key == "InTem":
            enabled = self.coordinator.data.get("InTemEn")
            return enabled == 1 and isinstance(raw, (int, float)) and raw != 0
        if self._key == "TemSen":
            return isinstance(raw, (int, float)) and raw != 0
        if self._key == "InHumi":
            enabled = self.coordinator.data.get("InHumiEn")
            return enabled == 1 and isinstance(raw, (int, float)) and 0 < raw <= 100
        return raw is not None

    @property
    def native_value(self):
        raw = self.coordinator.data.get(self._key)
        if raw is None:
            return None
        if self._key == "InTem" and isinstance(raw, (int, float)):
            # The indoor-air protocol value uses a +40 offset.
            return raw - 40 if raw != 0 else None
        if self._key == "OutTem" and isinstance(raw, (int, float)):
            # The supplied manuals do not identify this physical probe.
            return raw / 2 if raw > 50 else raw
        if self._key == "TemSen" and isinstance(raw, (int, float)):
            # Gree measured-air temperatures use a +40 protocol offset; zero
            # is the protocol sentinel for an unsupported/disabled sensor.
            return raw - 40 if raw != 0 else None
        if self._key == "InHumi" and isinstance(raw, (int, float)):
            return raw if 0 < raw <= 100 else None
        if isinstance(raw, list):
            return ", ".join(str(item) for item in raw) if raw else "0"
        if isinstance(raw, dict):
            return str(raw)
        return raw

    @property
    def extra_state_attributes(self):
        if self._key == "InTem":
            return {
                "protocol_property": "InTem",
                "raw_value": self.coordinator.data.get("InTem"),
                "enable_property": "InTemEn",
                "enable_value": self.coordinator.data.get("InTemEn"),
                "source": "indoor-unit air sensor",
                "encoding": "raw value minus 40 °C",
            }
        if self._key == "TemSen":
            return {
                "protocol_property": "TemSen",
                "source": "additional temperature sensor",
                "encoding": "raw value minus 40 °C",
            }
        if self._key == "InHumi":
            return {
                "protocol_property": "InHumi",
                "enable_property": "InHumiEn",
                "enable_value": self.coordinator.data.get("InHumiEn"),
            }
        if self._key == "OutTem":
            return {
                "protocol_property": self._key,
                "raw_value": self.coordinator.data.get(self._key),
                "source": "physical probe not identified by the supplied manuals",
                "warning": "Do not interpret this as room or outdoor ambient temperature.",
            }
        return None


class GreePowerSensor(GreeDeviceEntity, SensorEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator, coordinator.device, key_suffix="power")
        self._attr_name = "Estimated Power"
        self._attr_device_class = SensorDeviceClass.POWER
        self._attr_native_unit_of_measurement = UnitOfPower.WATT
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_entity_registry_enabled_default = True
        self._attr_icon = "mdi:lightning-bolt"

    @property
    def native_value(self):
        return self.coordinator.data.get("estimated_power_w")

    @property
    def extra_state_attributes(self):
        return {
            "estimated": True,
            "model": self.coordinator._model_key or None,
            "method": "nominal input adjusted by HVAC mode and verified DRED limit",
            "not_a_meter": True,
        }


class GreeBaselinePowerSensor(GreeDeviceEntity, SensorEntity):
    """Counterfactual power estimate without DRED and Quiet."""

    def __init__(self, coordinator):
        super().__init__(coordinator, coordinator.device, key_suffix="baseline_power")
        self._attr_name = "Estimated Baseline Power"
        self._attr_device_class = SensorDeviceClass.POWER
        self._attr_native_unit_of_measurement = UnitOfPower.WATT
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_entity_registry_enabled_default = True
        self._attr_icon = "mdi:chart-line"

    @property
    def native_value(self):
        return self.coordinator.data.get("estimated_baseline_power_w")

    @property
    def extra_state_attributes(self):
        return {
            "estimated": True,
            "model": self.coordinator._model_key or None,
            "method": "same HVAC mode without DRED or Quiet",
            "counterfactual": True,
            "not_a_meter": True,
        }


class GreeSavingPowerSensor(GreeDeviceEntity, SensorEntity):
    """Instantaneous estimated saving relative to the baseline."""

    def __init__(self, coordinator):
        super().__init__(coordinator, coordinator.device, key_suffix="saving_power")
        self._attr_name = "Estimated Saving Power"
        self._attr_device_class = SensorDeviceClass.POWER
        self._attr_native_unit_of_measurement = UnitOfPower.WATT
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_entity_registry_enabled_default = True
        self._attr_icon = "mdi:leaf"

    @property
    def native_value(self):
        return self.coordinator.data.get("estimated_saving_power_w")

    @property
    def extra_state_attributes(self):
        return {
            "estimated": True,
            "model": self.coordinator._model_key or None,
            "method": "estimated baseline power minus estimated actual power",
            "counterfactual": True,
            "not_a_meter": True,
        }


class GreeEnergySensor(GreeDeviceEntity, SensorEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator, coordinator.device, key_suffix="energy")
        self._attr_name = "Estimated Energy"
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_entity_registry_enabled_default = True
        self._attr_icon = "mdi:lightning-bolt-outline"

    @property
    def native_value(self):
        return self.coordinator.data.get("estimated_energy_kwh")

    @property
    def extra_state_attributes(self):
        return {
            "estimated": True,
            "model": self.coordinator._model_key or None,
            "method": "time integral of estimated power; not suitable for billing",
            "not_a_meter": True,
        }
