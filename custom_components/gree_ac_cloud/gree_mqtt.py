"""Asynchronous MQTT client for Gree cloud devices."""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
from typing import Any, Callable

from .const import POLL_COLS
from .gree_api import GreeDevice

_LOGGER = logging.getLogger(__name__)

EXTRA_KEYS = [
    "Health",
    "Quiet",
    "Tur",
    "StHt",
    "Blo",
    "SvSt",
    "SlpMod",
    "Lig",
    "Air",
    "SwingLfRig",
    "SwUpDn",
]


class GreeMQTTClient:
    """Maintain an MQTT connection and process Gree device messages."""

    def __init__(
        self,
        host: str,
        port: int,
        uid: int,
        token: str,
        devices: list[GreeDevice],
        on_data: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        self.host = host
        self.port = port
        self.uid = uid
        self.token = token
        self.devices = {device.mac: device for device in devices}
        self._on_data = on_data
        self._client = None
        self._tls_context: ssl.SSLContext | None = None
        self._listener_task: asyncio.Task | None = None
        self._running = False
        self._connected = False
        self._connected_event = asyncio.Event()
        self._user_params: dict[str, set[str]] = {device.mac: set() for device in devices}
        self._last_seen: dict[str, float] = {}
        self._last_command: dict[str, float] = {}
        self.action_log = None

    def _create_client(self):
        import aiomqtt

        if self._tls_context is None:
            raise RuntimeError("MQTT TLS context has not been initialized")
        return aiomqtt.Client(
            hostname=self.host,
            port=self.port,
            username=str(self.uid),
            password=self.token,
            identifier=f"gree_ac_{int(time.time())}",
            protocol=aiomqtt.ProtocolVersion.V311,
            keepalive=60,
            tls_context=self._tls_context,
        )

    async def start(self) -> bool:
        """Start the connection manager and wait for its first connection."""
        if self._listener_task and not self._listener_task.done():
            return self.connected

        self._running = True
        self._connected_event.clear()
        if self._tls_context is None:
            # Loading the operating-system CA bundle performs blocking file I/O.
            self._tls_context = await asyncio.to_thread(ssl.create_default_context)
        loop = asyncio.get_running_loop()
        self._listener_task = loop.create_task(self._connection_loop(), name="gree_ac_cloud_mqtt")
        try:
            await asyncio.wait_for(self._connected_event.wait(), timeout=15)
        except asyncio.TimeoutError:
            _LOGGER.error("MQTT connection to %s:%s timed out", self.host, self.port)
            await self.stop()
            return False
        return True

    async def stop(self):
        """Stop reconnect attempts and close the active client."""
        self._running = False
        self._connected = False
        self._connected_event.clear()
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            self._listener_task = None
        self._client = None

    @property
    def connected(self) -> bool:
        return self._connected

    async def _connection_loop(self):
        delay = 1
        while self._running:
            try:
                client = self._create_client()
                self._client = client
                async with client:
                    for parent_mac in {d.parent_mac for d in self.devices.values()}:
                        await client.subscribe(f"status/{parent_mac}/#", qos=1)
                        await client.subscribe(f"response/{parent_mac}/#", qos=1)

                    self._connected = True
                    self._connected_event.set()
                    delay = 1
                    _LOGGER.info(
                        "Connected to %s:%s; subscribed to %d parent devices",
                        self.host,
                        self.port,
                        len({d.parent_mac for d in self.devices.values()}),
                    )

                    async for message in client.messages:
                        self._process_message(message)

                if self._running:
                    raise ConnectionError("MQTT connection closed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._running:
                    _LOGGER.warning("MQTT connection lost (%s); reconnecting in %ds", exc, delay)
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 60)
            finally:
                self._connected = False
                self._client = None

    def _process_message(self, msg):
        import base64

        from Crypto.Util.Padding import unpad

        topic = str(msg.topic)
        _LOGGER.debug("MQTT RECV topic=%s payload=%.120s", topic, msg.payload)

        try:
            payload = json.loads(msg.payload)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
            return

        pack = payload.get("pack")
        if not pack:
            return

        topic_parts = topic.split("/")
        topic_parent_mac = topic_parts[1] if len(topic_parts) > 1 else ""
        mac = None
        device = None
        data = None

        # Parent devices are the entities exposed by this integration.
        for candidate_mac, candidate in self.devices.items():
            if candidate.parent_mac == topic_parent_mac and len(candidate_mac) == 12:
                decrypted = candidate.decrypt_pack(pack)
                if decrypted is not None:
                    mac, device, data = candidate_mac, candidate, decrypted
                    break

        # Some brokers do not include a useful parent MAC in the topic.
        if device is None:
            for candidate_mac, candidate in self.devices.items():
                try:
                    raw = unpad(candidate.cipher.decrypt(base64.b64decode(pack)), 16).decode()
                    result = json.loads(raw)
                    if "cols" in result and "dat" in result:
                        result = dict(zip(result["cols"], result["dat"]))
                    mac, device, data = candidate_mac, candidate, result
                    break
                except (
                    ValueError,
                    KeyError,
                    TypeError,
                    json.JSONDecodeError,
                    UnicodeDecodeError,
                ):
                    continue

        if device is None or mac is None or data is None:
            return

        old_power = device.properties.get("Pow")
        new_power = data.get("Pow")
        power_on = old_power == 0 and new_power == 1
        device.properties.update(data)
        self._last_seen[mac] = time.monotonic()

        needs_reenable: list[str] = []
        if power_on:
            for key in EXTRA_KEYS:
                if key not in data:
                    device.properties[key] = 0
            for key in self._user_params.get(mac, set()):
                if not device.properties.get(key):
                    device.properties[key] = 1
                    needs_reenable.append(key)

        if self._on_data:
            self._on_data(mac, dict(device.properties))

        _LOGGER.debug("MQTT: %s ⇐ %s", mac, dict(sorted(data.items())))
        if needs_reenable:
            loop = asyncio.get_running_loop()
            loop.create_task(self.send_command(mac, needs_reenable, [1] * len(needs_reenable)))

    async def _publish_json(self, topic: str, obj: dict, qos: int = 0) -> bool:
        client = self._client
        if not self._connected or client is None:
            return False
        try:
            await client.publish(topic, json.dumps(obj, separators=(",", ":")), qos=qos)
            return True
        except Exception as exc:
            _LOGGER.warning("Publish failed: %s", exc)
            return False

    async def refresh_device(
        self, mac: str, cols: list[str] | None = None
    ) -> dict[str, Any] | None:
        device = self.devices.get(mac)
        if not device:
            return None
        await self._publish_json(
            f"request/{device.parent_mac}",
            {
                "t": "pack",
                "i": 0,
                "uid": self.uid,
                "cid": "ha_ac_cloud",
                "tcid": mac,
                "pack": device.build_status_request(cols or POLL_COLS),
            },
            qos=1,
        )
        return dict(device.properties) if device.properties else None

    def seconds_since_last_seen(self, mac: str) -> float | None:
        seen = self._last_seen.get(mac)
        return time.monotonic() - seen if seen is not None else None

    def command_age(self, mac: str) -> float | None:
        """Return seconds since this integration last commanded the device."""
        sent = self._last_command.get(mac)
        return time.monotonic() - sent if sent is not None else None

    async def send_command(
        self,
        mac: str,
        options: list[str],
        values: list[Any],
        *,
        source: str = "integration",
        action: str = "device_command",
    ) -> bool:
        device = self.devices.get(mac)
        if not device or not options or len(options) != len(values):
            return False
        ok = await self._publish_json(
            f"request/{device.parent_mac}",
            {
                "t": "pack",
                "i": 0,
                "uid": self.uid,
                "cid": "ha_ac_cloud",
                "tcid": mac,
                "pack": device.build_command_pack(options, values),
            },
            qos=1,
        )
        _LOGGER.info("send_command: %s options=%s values=%s", mac, options, values)
        if ok:
            self._last_command[mac] = time.monotonic()
        if self.action_log is not None:
            changes = dict(zip(options, values))
            await self.action_log.async_record(
                mac,
                source,
                action,
                changes,
                result="sent" if ok else "failed",
            )

        user_params = self._user_params.setdefault(mac, set())
        for option, value in zip(options, values):
            if option in EXTRA_KEYS:
                if value == 1:
                    user_params.add(option)
                else:
                    user_params.discard(option)
        return ok
