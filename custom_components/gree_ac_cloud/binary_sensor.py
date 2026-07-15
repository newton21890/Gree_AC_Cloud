from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEVICE_BINARY_SENSORS
from .entity import GreeDeviceEntity


async def async_setup_entry(hass, entry, async_add_entities: AddEntitiesCallback):
    coordinators = entry.runtime_data["coordinators"]
    entities = []
    for coord in coordinators:
        for key, cfg in DEVICE_BINARY_SENSORS.items():
            entities.append(GreeBinarySensor(coord, key, cfg))
    async_add_entities(entities)


class GreeBinarySensor(GreeDeviceEntity, BinarySensorEntity):
    def __init__(self, coordinator, key, cfg):
        super().__init__(coordinator, coordinator.device, key_suffix=key)
        self._key = key
        self._attr_name = cfg["name"]
        self._attr_device_class = cfg.get("device_class")

    @property
    def is_on(self) -> bool:
        raw = self.coordinator.data.get(self._key, 0)
        if isinstance(raw, list):
            return len(raw) > 0
        return bool(raw)
