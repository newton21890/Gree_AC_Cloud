from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.const import Platform
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["climate", "sensor", "switch", "binary_sensor"]


async def async_setup_entry(hass: HomeAssistant, entry):
    from homeassistant.const import EVENT_HOMEASSISTANT_STOP
    from homeassistant.exceptions import ConfigEntryNotReady
    from homeassistant.helpers.storage import Store

    from .const import (
        CONF_PASSWORD,
        CONF_SERVER,
        CONF_USERNAME,
        DOMAIN,
        GREE_CLOUD_SERVERS,
        GREE_MQTT_HOSTS,
        GREE_MQTT_PORTS,
        STORAGE_KEY_MODELS,
        STORAGE_VERSION,
    )
    STORAGE_KEY_NAMES = f"{DOMAIN}.names"
    from .coordinator import async_discover_and_connect, GreeDeviceCoordinator
    from .panel import async_register_panel, async_unregister_panel

    _LOGGER.info("Setting up %s", DOMAIN)
    server = entry.data[CONF_SERVER]
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]

    cloud_host = GREE_CLOUD_SERVERS.get(server, "eugrih.gree.com")
    mqtt_host = GREE_MQTT_HOSTS.get(server, "mqtt-eu.gree.com")
    mqtt_port = GREE_MQTT_PORTS.get(server, 1984)

    coordinators = []
    data_forwarder = {"cb": None}

    def on_device_data(mac, data):
        if data_forwarder["cb"]:
            data_forwarder["cb"](mac, data)

    last_error = None
    for attempt in range(1, 4):
        try:
            uid, token, devices, mqtt = await async_discover_and_connect(
                hass,
                cloud_host,
                mqtt_host,
                mqtt_port,
                username,
                password,
                on_device_data,
            )
            last_error = None
            break
        except Exception as err:
            last_error = err
            if attempt < 3:
                _LOGGER.warning(
                    "Setup attempt %d/3 failed: %s — retrying in 5s",
                    attempt, err,
                )
                await asyncio.sleep(5)

    if last_error:
        raise ConfigEntryNotReady(
            f"Connection failed after 3 attempts: {last_error}"
        ) from last_error

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault("models", {})
    hass.data[DOMAIN].setdefault("device_names", {})

    models_store = Store(hass, STORAGE_VERSION, STORAGE_KEY_MODELS)
    saved_models = await models_store.async_load()
    if saved_models:
        hass.data[DOMAIN]["models"].update(saved_models)
        _LOGGER.info("Restored %d device model mappings", len(saved_models))

    names_store = Store(hass, STORAGE_VERSION, STORAGE_KEY_NAMES)
    saved_names = await names_store.async_load()
    if saved_names:
        hass.data[DOMAIN]["device_names"].update(saved_names)
        _LOGGER.info("Restored %d device names", len(saved_names))

    coordinators = [
        GreeDeviceCoordinator(hass, entry, mqtt, dev)
        for dev in devices
    ]

    for coord in coordinators:
        await coord.async_init()
        await coord.async_config_entry_first_refresh()

    def _forward(mac, data):
        if "Pow" not in data:
            return
        for coord in coordinators:
            if coord.device.mac == mac:
                asyncio.run_coroutine_threadsafe(
                    coord.async_set_updated_data(coord._build_data()),
                    hass.loop,
                )
                break

    data_forwarder["cb"] = _forward

    entry.runtime_data = {
        "mqtt": mqtt,
        "coordinators": coordinators,
        "uid": uid,
    }

    async def _persist_all(event=None):
        store = Store(hass, STORAGE_VERSION, STORAGE_KEY_MODELS)
        await store.async_save(hass.data[DOMAIN].get("models", {}))
        ns = Store(hass, STORAGE_VERSION, STORAGE_KEY_NAMES)
        await ns.async_save(hass.data[DOMAIN].get("device_names", {}))
        for coord in coordinators:
            await coord.async_save_energy()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _persist_all)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await async_register_panel(hass)

    _LOGGER.info(
        "Gree AC Cloud setup complete: %d devices", len(devices)
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry):
    from .panel import async_unregister_panel

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        mqtt = entry.runtime_data.get("mqtt")
        if mqtt:
            await hass.async_add_executor_job(mqtt.stop)
        await async_unregister_panel(hass)
    return unload_ok
