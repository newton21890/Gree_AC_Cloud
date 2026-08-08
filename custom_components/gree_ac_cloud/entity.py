from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


class GreeDeviceEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    @property
    def available(self) -> bool:
        """Report availability from the coordinator and real MQTT freshness."""
        mqtt = self.coordinator._mqtt
        last_seen = mqtt.seconds_since_last_seen(self._device.mac)
        interval = self.coordinator.update_interval
        stale_after = max(60, interval.total_seconds() * 4 if interval else 60)
        return (
            super().available
            and mqtt.connected
            and last_seen is not None
            and last_seen <= stale_after
        )

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
