from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEVICE_SWITCHES
from .entity import GreeDeviceEntity


async def async_setup_entry(hass, entry, async_add_entities: AddEntitiesCallback):
    coordinators = entry.runtime_data["coordinators"]
    entities = []
    for coord in coordinators:
        for key, cfg in DEVICE_SWITCHES.items():
            entities.append(GreeSwitch(coord, key, cfg))
    async_add_entities(entities)


class GreeSwitch(GreeDeviceEntity, SwitchEntity):
    def __init__(self, coordinator, key, cfg):
        super().__init__(coordinator, coordinator.device, key_suffix=key)
        self._key = key
        self._attr_name = cfg["name"]
        self._attr_icon = cfg.get("icon")
        self._attr_entity_registry_enabled_default = key in ("Quiet", "Tur", "Health")

    @property
    def available(self) -> bool:
        """Expose controls only when the device reports the capability."""
        return super().available and self._key in self.coordinator.data

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.get(self._key, 0))

    async def async_turn_on(self, **kwargs):
        mqtt = self.coordinator._mqtt
        if await mqtt.send_command(self.coordinator.device.mac, [self._key], [1]):
            self.coordinator.device.properties[self._key] = 1
            self.coordinator.async_set_updated_data(
                dict(self.coordinator.device.properties)
            )

    async def async_turn_off(self, **kwargs):
        mqtt = self.coordinator._mqtt
        if await mqtt.send_command(self.coordinator.device.mac, [self._key], [0]):
            self.coordinator.device.properties[self._key] = 0
            self.coordinator.async_set_updated_data(
                dict(self.coordinator.device.properties)
            )
