from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, ENERGY_MODELS, STALE_AFTER_SECONDS, STORAGE_VERSION, UPDATE_INTERVAL
from .gree_api import GreeDevice, discover_devices

if TYPE_CHECKING:
    from .gree_mqtt import GreeMQTTClient

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
    if not parent_devices:
        raise ValueError("No supported parent devices found for this account")

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

    try:
        ok = await mqtt.start()
    except Exception:
        await mqtt.stop()
        raise
    if not ok:
        await mqtt.stop()
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
        self._total_energy_kwh: float = 0.0
        self._last_energy_time: float = time.monotonic()
        self._energy_save_counter = 0
        self._energy_store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.energy.{device.mac}")

    async def async_init(self):
        data = await self._energy_store.async_load()
        if data:
            self._total_energy_kwh = data.get("total_kwh", 0.0)
        self._last_energy_time = time.monotonic()

    async def async_save_energy(self):
        await self._energy_store.async_save({
            "total_kwh": self._total_energy_kwh,
        })

    @property
    def _model_key(self) -> str:
        return self.hass.data.get(DOMAIN, {}).get("models", {}).get(self.device.mac, "")

    @property
    def _model_specs(self) -> dict | None:
        return ENERGY_MODELS.get(self._model_key)

    def _estimate_power_w(self, state: dict) -> float:
        """Estimate electrical input without pretending raw probes are ambient.

        The model values are nominal electrical inputs, not measured power. The
        supplied documentation does not identify the physical `InTem` probe, so
        temperature delta must not be used as a compressor-load measurement.
        DRED limits follow the standard demand-response ceilings; D1 means no
        compressor, D2 <= 50%, D3 <= 75%. Off uses a conservative 70% duty-cycle
        estimate. Fan-only is approximated at 5% of nominal cooling input.
        """
        model = self._model_specs
        if not model or not state.get("Pow"):
            return 0.0

        mode = state.get("Mod")
        if mode == 3:
            return round(model["cool"] * 0.05 * 1000)

        dred = state.get("DRED", 0)
        try:
            dred = int(dred)
        except (TypeError, ValueError):
            dred = 0
        # Firmware variants report D1 either as DRED=1 or as the separate
        # Idemand=1 flag while leaving DRED=0. Both forms were observed live.
        try:
            idemand_active = int(state.get("Idemand", 0)) == 1
        except (TypeError, ValueError):
            idemand_active = False
        if dred == 0 and idemand_active:
            dred = 1
        if dred == 1:
            return round(model["cool"] * 0.05 * 1000)

        base = model["heat"] if mode == 2 else model["cool"]
        duty_factor = 0.70
        if mode == 4:
            duty_factor = 0.55
        if dred == 2:
            duty_factor = min(duty_factor, 0.50)
        elif dred == 3:
            duty_factor = min(duty_factor, 0.75)

        if state.get("Quiet"):
            duty_factor *= 0.85
        if state.get("Tur"):
            duty_factor = min(1.0, duty_factor * 1.20)

        power_kw = base * duty_factor
        return round(min(power_kw, model["max"]) * 1000)

    def _build_data(self) -> dict[str, Any]:
        data = dict(self.device.properties)
        data["estimated_power_w"] = self._estimate_power_w(data)
        now = time.monotonic()
        elapsed_h = (now - self._last_energy_time) / 3600.0
        self._last_energy_time = now
        if data.get("Pow") and elapsed_h > 0 and elapsed_h < 1:
            self._total_energy_kwh += data["estimated_power_w"] * elapsed_h / 1000.0
        data["estimated_energy_kwh"] = round(self._total_energy_kwh, 3)
        return data

    async def _async_update_data(self) -> dict[str, Any]:
        if not self._mqtt.connected:
            raise UpdateFailed(f"{self.device.name}: MQTT disconnected")

        await self._mqtt.refresh_device(self.device.mac)

        elapsed = self._mqtt.seconds_since_last_seen(self.device.mac)
        interval = self.update_interval or timedelta(seconds=UPDATE_INTERVAL)
        stale_after = max(STALE_AFTER_SECONDS, interval.total_seconds() * 4)
        if elapsed is None or elapsed > stale_after:
            age = f"{elapsed:.0f}s" if elapsed is not None else "never"
            _LOGGER.warning(
                "%s: no fresh MQTT data (last seen: %s); keeping last known state",
                self.device.name,
                age,
            )
        else:
            _LOGGER.debug(
                "%s: refresh (Pow=%s, last_seen=%.1fs ago)",
                self.device.name, self.device.properties.get("Pow"), elapsed,
            )

        result = self._build_data()
        self._energy_save_counter += 1
        if self._energy_save_counter >= 5:
            self._energy_save_counter = 0
            await self.async_save_energy()
        return result
