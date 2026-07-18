"""Gree AC Cloud MQTT — async MQTT client using aiomqtt.

Test: python3 -m custom_components.gree_ac_cloud.gree_mqtt
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

from .gree_api import GreeDevice

POLL_COLS = [
    "Pow", "Mod", "SetTem", "WdSpd", "Air", "Blo", "Health",
    "SwhSlp", "Lig", "SwUpDn", "SwingLfRig", "Quiet", "Tur",
    "StHt", "TemUn", "HeatCoolType", "TemRec", "SvSt", "SlpMod",
    "InTem", "OutTem", "InHumi", "SetDeciTem",
    "Err", "Filter", "WaterSen",
]

_LOGGER = logging.getLogger(__name__)

EXTRA_KEYS = ["Health", "Quiet", "Tur", "StHt", "Blo", "SvSt", "SlpMod", "Lig", "Air", "SwingLfRig", "SwUpDn"]


class GreeMQTTClient:
    """Async MQTT client for Gree cloud devices using aiomqtt."""

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
        self.devices = {d.mac: d for d in devices}
        self._on_data = on_data
        self._client = None
        self._listener_task: asyncio.Task | None = None
        self._running = False
        self._user_params: dict[str, set[str]] = {d.mac: set() for d in devices}

    # ── lifecycle ──────────────────────────────────────

    def _create_client(self):
        import ssl

        import aiomqtt

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        return aiomqtt.Client(
            hostname=self.host,
            port=self.port,
            username=str(self.uid),
            password=self.token,
            identifier=f"gree_ac_{int(__import__('time').time())}",
            protocol=aiomqtt.ProtocolVersion.V311,
            keepalive=60,
            tls_context=ctx,
        )

    async def start(self) -> bool:
        self._running = True
        self._client = self._create_client()
        try:
            await self._client.__aenter__()
        except Exception as exc:
            _LOGGER.error("MQTT connect failed: %s", exc)
            return False

        _LOGGER.info("Connected to %s:%s", self.host, self.port)

        for dev in self.devices.values():
            pmac = dev.parent_mac
            await self._client.subscribe(f"status/{pmac}/#", qos=1)
            await self._client.subscribe(f"response/{pmac}/#", qos=1)
        _LOGGER.info("Subscribed to %d devices", len(self.devices))

        self._listener_task = asyncio.create_task(self._listener())
        return True

    async def stop(self):
        self._running = False
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except (asyncio.CancelledError, StopAsyncIteration):
                pass
            self._listener_task = None
        if self._client:
            try:
                await self._client.__aexit__(None, None, None)
            except Exception:
                pass
            self._client = None

    @property
    def connected(self) -> bool:
        return self._client is not None

    # ── message processing ─────────────────────────────

    async def _listener(self):
        while self._running:
            try:
                async for msg in self._client.messages:
                    self._process_message(msg)
            except (asyncio.CancelledError, GeneratorExit, RuntimeError):
                break
            except Exception as exc:
                if self._running:
                    _LOGGER.warning("MQTT listener error: %s", exc)
                    await asyncio.sleep(1)

    def _process_message(self, msg):
        import base64
        from Crypto.Util.Padding import unpad

        topic = str(msg.topic)
        _LOGGER.debug("MQTT RECV topic=%s payload=%.120s", topic, msg.payload)

        try:
            payload = json.loads(msg.payload)
        except json.JSONDecodeError:
            return

        pack = payload.get("pack")
        if not pack:
            return

        topic_parts = topic.split("/")
        topic_pmac = topic_parts[1] if len(topic_parts) > 1 else ""

        mac = None
        dev = None
        for m, d in self.devices.items():
            if d.parent_mac == topic_pmac and len(m) == 12:
                mac, dev = m, d
                break

        if mac is None:
            for m, d in self.devices.items():
                try:
                    raw = unpad(d.cipher.decrypt(base64.b64decode(pack)), 16).decode()
                    result = json.loads(raw)
                    if "cols" in result and "dat" in result:
                        mac, dev, _data = m, d, dict(zip(result["cols"], result["dat"]))
                        break
                except (ValueError, KeyError, json.JSONDecodeError):
                    continue
            if mac is None:
                return
        else:
            _data = dev.decrypt_pack(pack)
            if _data is None:
                return

        old_pow = dev.properties.get("Pow")
        new_pow = _data.get("Pow")
        power_on = (old_pow == 0 and new_pow == 1)

        dev.properties.update(_data)

        needs_reenable: list[str] = []
        if power_on:
            for key in EXTRA_KEYS:
                if key not in _data:
                    dev.properties[key] = 0
            for key in list(self._user_params.get(mac, set())):
                if not dev.properties.get(key):
                    dev.properties[key] = 1
                    needs_reenable.append(key)

        if self._on_data:
            self._on_data(mac, dict(dev.properties))

        _LOGGER.debug("MQTT: %s ⇐ %s (topic=%s)", mac, dict(sorted(_data.items())), topic)

        if needs_reenable:
            _LOGGER.info("Re-enabling %s on %s after power-on", needs_reenable, mac)
            dev = self.devices.get(mac)
            if dev:
                pack = dev.build_command_pack(needs_reenable, [1] * len(needs_reenable))
                asyncio.ensure_future(self._publish_json(
                    f"request/{dev.parent_mac}",
                    {"t": "pack", "i": 0, "uid": self.uid, "cid": "ha_ac_cloud",
                     "tcid": mac, "pack": pack},
                    qos=1,
                ))

    # ── publish helper ─────────────────────────────────

    async def _publish_json(self, topic: str, obj: dict, qos: int = 0) -> bool:
        if not self._client:
            return False
        payload = json.dumps(obj, separators=(",", ":"))
        try:
            await self._client.publish(topic, payload, qos=qos)
            return True
        except Exception as exc:
            _LOGGER.warning("Publish failed: %s", exc)
            return False

    # ── public API ─────────────────────────────────────

    async def refresh_device(self, mac: str, cols: list[str] | None = None) -> dict[str, Any] | None:
        """Send a poll request (fire-and-forget) and return current device properties."""
        dev = self.devices.get(mac)
        if not dev:
            return None
        pack = dev.build_status_request(cols or POLL_COLS)
        await self._publish_json(
            f"request/{dev.parent_mac}",
            {"t": "pack", "i": 0, "uid": self.uid, "cid": "ha_ac_cloud",
             "tcid": mac, "pack": pack},
            qos=1,
        )
        return dict(dev.properties) if dev.properties else None

    async def send_command(
        self, mac: str, options: list[str], values: list[Any]
    ) -> bool:
        """Send a command to a device (fire-and-forget)."""
        dev = self.devices.get(mac)
        if not dev:
            return False
        pack = dev.build_command_pack(options, values)
        ok = await self._publish_json(
            f"request/{dev.parent_mac}",
            {"t": "pack", "i": 0, "uid": self.uid, "cid": "ha_ac_cloud",
             "tcid": mac, "pack": pack},
            qos=1,
        )
        _LOGGER.info("send_command: %s options=%s values=%s", mac, options, values)

        up = self._user_params.setdefault(mac, set())
        for opt, val in zip(options, values):
            if opt in EXTRA_KEYS:
                if val == 1:
                    up.add(opt)
                else:
                    up.discard(opt)
        return ok


# ── test / standalone usage ───────────────────────────

def _test():
    import os

    from .gree_api import discover_devices

    logging.basicConfig(level=logging.DEBUG)
    logging.getLogger("aiomqtt").setLevel(logging.WARNING)

    project_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..")
    )
    creds = os.path.join(project_root, ".bridge_credentials.json")
    if not os.path.exists(creds):
        creds = os.path.expanduser("~/.bridge_credentials.json")

    with open(creds) as f:
        cfg = json.load(f)

    host = "eugrih.gree.com"
    uid, token, devices = discover_devices(host, cfg["username"], cfg["password"])
    print(f"\nDevices ({len(devices)}):")
    for d in devices:
        print(f"  {d.mac:16s} {d.name} (parent={d.parent_mac}) key={d.key[:4]}...")

    results = {}

    def on_data(mac, data):
        results[mac] = data
        print(f"\n  ← Data for {mac}: Pow={data.get('Pow')} T={data.get('InTem')}°C")

    client = GreeMQTTClient(
        host="18.185.150.155",
        port=1984,
        uid=uid,
        token=token,
        devices=devices,
        on_data=on_data,
    )

    async def run():
        ok = await client.start()
        print(f"\nMQTT connect: {'OK' if ok else 'FAIL'}")
        if not ok:
            return

        parents = [d for d in devices if len(d.mac) == 12]
        for _ in range(4):
            await asyncio.sleep(2)
            for d in parents:
                mac = d.mac
                print(f"  Refreshing {mac}...", end=" ")
                data = await client.refresh_device(mac)
                if data:
                    print(f"OK Pow={data.get('Pow')} T={data.get('InTem')}°C")
                else:
                    print("NO DATA")

        await client.stop()
        print("\nDone.")

    asyncio.run(run())


if __name__ == "__main__":
    _test()
