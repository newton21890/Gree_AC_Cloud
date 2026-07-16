import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN, ENERGY_MODELS, STORAGE_VERSION, UPDATE_INTERVAL
from .gree_api import GreeDevice, discover_devices

_LOGGER = logging.getLogger(__name__)


async def async_discover_and_connect(
    hass: HomeAssistant,
    cloud_host: str,
    mqtt_host: str,
    mqtt_port: int,
    username: str,
    password: str,
    on_data_callback,
) -> tuple[int, str, list[GreeDevice], GreeMQTTClient]:

    uid, token, devices = await hass.async_add_executor_job(
        discover_devices, cloud_host, username, password
    )

    parent_devices = [d for d in devices if len(d.mac) == 12]

    _LOGGER.info(
        "Discovered %d devices (%d parent units)",
        len(devices),
        len(parent_devices),
    )

    from .gree_mqtt import GreeMQTTClient

    mqtt = GreeMQTTClient(
        host=mqtt_host,
        port=mqtt_port,
        uid=uid,
        token=token,
        devices=devices,
        on_data=on_data_callback,
    )

    ok = await hass.async_add_executor_job(mqtt.start, 15)
    if not ok:
        raise ConnectionError("Failed to connect to MQTT broker")

    return uid, token, parent_devices, mqtt


class GreeDeviceCoordinator(DataUpdateCoordinator):

    def __init__(
        self,
        hass: HomeAssistant,
        entry,
        mqtt,
        device: GreeDevice,
    ):
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}-{device.mac}",
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )
        self.device = device
        self._mqtt = mqtt
        self._error_count = 0
        self._total_energy_kwh: float = 0.0
        self._last_energy_time: float = time.time()
        self._energy_save_counter = 0
        self._energy_store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.energy.{device.mac}")

    async def async_init(self):
        data = await self._energy_store.async_load()
        if data:
            self._total_energy_kwh = data.get("total_kwh", 0.0)
            self._last_energy_time = data.get("last_time", time.time())

    async def async_save_energy(self):
        await self._energy_store.async_save({
            "total_kwh": self._total_energy_kwh,
            "last_time": self._last_energy_time,
        })

    @property
    def _model_key(self) -> str:
        return self.hass.data.get(DOMAIN, {}).get("models", {}).get(self.device.mac, "")

    @property
    def _model_specs(self) -> dict | None:
        return ENERGY_MODELS.get(self._model_key)

    def _estimate_power_w(self, state: dict) -> float:
        model = self._model_specs
        if not model or not state.get("Pow"):
            return 0.0

        mode = state.get("Mod")
        if mode == 3:
            return round(model["cool"] * 0.05 * 1000)

        base = model["heat"] if mode == 2 else model["cool"]
        dry_factor = 0.7 if mode == 4 else 1.0

        fan_map = {0: 0.9, 1: 0.7, 2: 0.8, 3: 0.9, 4: 1.0, 5: 1.1}
        fan_factor = fan_map.get(state.get("WdSpd"), 0.9)
        turbo_factor = 1.2 if state.get("Tur") else 1.0

        set_tem_raw = state.get("SetDeciTem")
        set_tem = set_tem_raw / 10 if set_tem_raw else (state.get("SetTem", 24) or 24)
        in_tem_raw = state.get("InTem")
        in_tem = in_tem_raw / 2 if in_tem_raw and in_tem_raw > 50 else (in_tem_raw or set_tem)
        delta = abs(set_tem - in_tem)
        load_factor = min(1.0, 0.5 + delta * 0.05)

        power_kw = base * fan_factor * turbo_factor * load_factor * dry_factor
        return round(min(power_kw, model["max"]) * 1000)

    def _build_data(self) -> dict[str, Any]:
        data = dict(self.device.properties)
        data["estimated_power_w"] = self._estimate_power_w(data)
        now = time.time()
        elapsed_h = (now - self._last_energy_time) / 3600.0
        self._last_energy_time = now
        if data.get("Pow") and elapsed_h > 0 and elapsed_h < 1:
            self._total_energy_kwh += data["estimated_power_w"] * elapsed_h / 1000.0
        data["estimated_energy_kwh"] = round(self._total_energy_kwh, 3)
        return data

    async def _async_update_data(self) -> dict[str, Any]:
        if not self._mqtt.connected:
            _LOGGER.warning("%s: MQTT disconnected, skipping poll", self.device.name)
            return self._build_data()

        data = await self.hass.async_add_executor_job(
            self._mqtt.refresh_device, self.device.mac, 5
        )

        if data is not None:
            self._error_count = 0
            result = self._build_data()
            self._energy_save_counter += 1
            if self._energy_save_counter >= 5:
                self._energy_save_counter = 0
                await self.async_save_energy()
            _LOGGER.debug(
                "%s: refresh OK (Pow=%s)",
                self.device.name, data.get("Pow"),
            )
            return result

        self._error_count += 1
        _LOGGER.debug(
            "%s: refresh TIMEOUT (attempt %d, Pow=%s)",
            self.device.name, self._error_count,
            self.device.properties.get("Pow"),
        )
        if self._error_count >= 3:
            _LOGGER.warning(
                "%s: no response after %d attempts",
                self.device.name,
                self._error_count,
            )
        return self._build_data()
