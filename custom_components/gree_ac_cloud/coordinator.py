from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    DRED_OPTIONS,
    ENERGY_MODELS,
    STALE_AFTER_SECONDS,
    STORAGE_VERSION,
    UPDATE_INTERVAL,
)
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
        self._total_runtime_seconds: float = 0.0
        self._current_run_seconds: float = 0.0
        self._last_runtime_time: float = time.monotonic()
        self._runtime_powered = bool(device.properties.get("Pow"))
        self._last_energy_time: float = time.monotonic()
        self._energy_save_counter = 0
        self._energy_store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.energy.{device.mac}")
        self._startup_store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.startup.{device.mac}")
        self._startup_settings: dict[str, Any] = {"dred": None}
        self._last_power = device.properties.get("Pow")
        self._startup_apply_lock = False

    async def async_init(self):
        data = await self._energy_store.async_load()
        if data:
            self._total_energy_kwh = data.get("total_kwh", 0.0)
            self._total_runtime_seconds = max(0.0, float(data.get("total_runtime_seconds", 0.0)))
            if self.device.properties.get("Pow"):
                self._current_run_seconds = max(0.0, float(data.get("current_run_seconds", 0.0)))
        startup_data = await self._startup_store.async_load()
        if startup_data:
            dred = startup_data.get("dred")
            if dred in (0, 1, 2, 3):
                self._startup_settings["dred"] = dred
        self._last_power = self.device.properties.get("Pow")
        self._runtime_powered = bool(self._last_power)
        if not self._runtime_powered:
            self._current_run_seconds = 0.0
        self._last_runtime_time = time.monotonic()
        self._last_energy_time = time.monotonic()

    @property
    def startup_dred(self) -> int | None:
        """Return the configured DRED level to apply at the next power-on."""
        return self._startup_settings.get("dred")

    async def async_set_startup_dred(self, value: int | None) -> None:
        """Persist the DRED action for future power-on transitions."""
        if value not in (None, 0, 1, 2, 3):
            raise ValueError(f"Unsupported startup DRED value: {value}")
        self._startup_settings["dred"] = value
        await self._startup_store.async_save(dict(self._startup_settings))
        self.async_update_listeners()

    async def async_apply_startup_settings(self) -> bool:
        """Apply configured settings after either HA or wall-controller start."""
        dred = self.startup_dred
        data = self.device.properties
        if (
            dred is None
            or not data.get("Pow")
            or data.get("Mod") != 1
            or data.get("DREDEn") != 1
            or self._startup_apply_lock
        ):
            return False

        self._startup_apply_lock = True
        # Mark locally initiated starts as already observed, so the subsequent
        # MQTT echo does not apply the same startup preference a second time.
        self._last_power = data.get("Pow")
        try:
            ok = await self._mqtt.send_command(self.device.mac, ["DRED"], [dred])
            if ok:
                data["DRED"] = dred
                if dred:
                    data["Quiet"] = 0
                self.async_set_updated_data(self._build_data())
                label = DRED_OPTIONS[dred]
                _LOGGER.info(
                    "%s: applied startup I-Demand setting %s",
                    self.device.name,
                    label,
                )
            return ok
        finally:
            self._startup_apply_lock = False

    def async_process_device_update(self) -> None:
        """Publish fresh data and detect starts originating outside HA."""
        current_power = self.device.properties.get("Pow")
        power_on = self._last_power == 0 and current_power == 1
        if power_on:
            self._current_run_seconds = 0.0
        self._last_power = current_power
        self.async_set_updated_data(self._build_data())
        if power_on and self.startup_dred is not None:
            self.hass.async_create_task(self.async_apply_startup_settings())

    async def async_save_energy(self):
        await self._energy_store.async_save(
            {
                "total_kwh": self._total_energy_kwh,
                "total_runtime_seconds": self._total_runtime_seconds,
                "current_run_seconds": self._current_run_seconds,
            }
        )

    async def async_reset_runtime(self) -> None:
        """Reset the persistent total runtime without affecting energy data."""
        self._total_runtime_seconds = 0.0
        self._last_runtime_time = time.monotonic()
        await self.async_save_energy()
        self.async_set_updated_data(self._build_data())

    @property
    def _model_key(self) -> str:
        return self.hass.data.get(DOMAIN, {}).get("models", {}).get(self.device.mac, "")

    @property
    def _model_specs(self) -> dict | None:
        return ENERGY_MODELS.get(self._model_key)

    def _estimate_baseline_power_w(self, state: dict) -> float:
        """Estimate the same operating mode without efficiency settings.

        This counterfactual baseline removes DRED and Quiet while preserving
        power state and HVAC mode. It is useful for comparing strategies, but
        remains a model-based estimate rather than a billing-grade meter.
        """
        model = self._model_specs
        if not model or not state.get("Pow"):
            return 0.0
        mode = state.get("Mod")
        if mode == 3:
            return round(model["cool"] * 0.05 * 1000)
        base = model["heat"] if mode == 4 else model["cool"]
        duty_factor = 0.55 if mode == 2 else 0.70
        if state.get("Tur"):
            duty_factor = min(1.0, duty_factor * 1.20)
        return round(min(base * duty_factor, model["max"]) * 1000)

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

        base = model["heat"] if mode == 4 else model["cool"]
        duty_factor = 0.70
        if mode == 2:
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
        data["estimated_baseline_power_w"] = self._estimate_baseline_power_w(data)
        data["estimated_saving_power_w"] = max(
            0.0, data["estimated_baseline_power_w"] - data["estimated_power_w"]
        )
        now = time.monotonic()
        elapsed_h = (now - self._last_energy_time) / 3600.0
        self._last_energy_time = now
        runtime_elapsed = now - self._last_runtime_time
        self._last_runtime_time = now
        # Attribute elapsed time to the state observed during the preceding
        # interval. This avoids adding off-time at startup and preserves the
        # final interval when an MQTT update reports a power-off transition.
        if self._runtime_powered and 0 < runtime_elapsed < 3600:
            self._total_runtime_seconds += runtime_elapsed
            self._current_run_seconds += runtime_elapsed
        self._runtime_powered = bool(data.get("Pow"))
        if not self._runtime_powered:
            self._current_run_seconds = 0.0
        if data.get("Pow") and elapsed_h > 0 and elapsed_h < 1:
            self._total_energy_kwh += data["estimated_power_w"] * elapsed_h / 1000.0
        data["estimated_energy_kwh"] = round(self._total_energy_kwh, 3)
        data["total_runtime_seconds"] = round(self._total_runtime_seconds)
        data["current_run_seconds"] = round(self._current_run_seconds)
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
                self.device.name,
                self.device.properties.get("Pow"),
                elapsed,
            )

        result = self._build_data()
        self._energy_save_counter += 1
        if self._energy_save_counter >= 5:
            self._energy_save_counter = 0
            await self.async_save_energy()
        return result
