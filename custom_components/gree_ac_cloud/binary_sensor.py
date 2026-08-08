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
        self._sources = cfg.get("sources", (key,))
        self._attr_entity_registry_enabled_default = cfg.get("diagnostic", False)
        if cfg.get("diagnostic"):
            from homeassistant.helpers.entity import EntityCategory
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @staticmethod
    def _active(raw) -> bool:
        if raw is None:
            return False
        if isinstance(raw, list):
            return any(GreeBinarySensor._active(item) for item in raw)
        if isinstance(raw, str):
            return raw.strip().lower() not in ("", "0", "00", "none", "normal", "off")
        return bool(raw)

    @property
    def available(self) -> bool:
        return super().available and any(key in self.coordinator.data for key in self._sources)

    @property
    def is_on(self) -> bool:
        return any(self._active(self.coordinator.data.get(key)) for key in self._sources)

    @property
    def extra_state_attributes(self):
        return {
            key: self.coordinator.data.get(key)
            for key in self._sources
            if self.coordinator.data.get(key) is not None
        }
