from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


class GreeDeviceEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, device: DeviceInfo, key_suffix: str = ""):
        super().__init__(coordinator)
        self._device = coordinator.device
        self._attr_unique_id = f"{self._device.mac}_{key_suffix}" if key_suffix else self._device.mac
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._device.mac)},
            name=self._device.name,
            manufacturer="Gree",
            model="VRF AC",
            sw_version=self._device.hid,
            serial_number=self._device.mac,
        )
