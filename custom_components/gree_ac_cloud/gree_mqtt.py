"""Gree AC Cloud MQTT - standalone MQTT protocol module.

Handles:
  - MQTT connection to Gree cloud broker (TLS, auth)
  - Device status polling via unsubscribe+resubscribe
  - Command sending
  - Response decryption

Test: python3 -m custom_components.gree_ac_cloud.gree_mqtt
"""

from __future__ import annotations

import json
import logging
import socket
import sys
import threading
import time
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
    """Manages MQTT connection to Gree cloud broker.

    Runs in its own thread with paho's loop_start().
    Provides callbacks for data updates.
    """

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
        self._client: Any = None
        self._ready = threading.Event()
        self._response_events: dict[str, threading.Event] = {
            d.mac: threading.Event() for d in devices
        }
        self._data_seq: dict[str, int] = {d.mac: 0 for d in devices}
        self._running = False
        self._keepalive_thread: threading.Thread | None = None
        self._user_params: dict[str, set[str]] = {d.mac: set() for d in devices}

    # ── lifecycle ──────────────────────────────────────

    def start(self, timeout: float = 15) -> bool:
        import ssl
        import paho.mqtt.client as mqtt

        self._running = True
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"gree_ac_{int(time.time())}",
            protocol=mqtt.MQTTv311,
        )
        self._client.tls_set(cert_reqs=ssl.CERT_NONE)
        self._client.tls_insecure_set(True)
        self._client.username_pw_set(str(self.uid), self.token)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect


        try:
            self._client.reconnect_delay_set(min_delay=1, max_delay=30)
            _LOGGER.info("Connecting to %s:%s", self.host, self.port)
            self._client.connect(self.host, self.port, keepalive=60)
            self._client.loop_start()
        except Exception as exc:
            _LOGGER.error("MQTT connect failed: %s", exc)
            return False

        ok = self._ready.wait(timeout)
        if ok:
            self._keepalive_thread = threading.Thread(
                target=self._keepalive_loop, daemon=True
            )
            self._keepalive_thread.start()
        return ok

    def stop(self):
        self._running = False
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.is_connected()

    # ── paho callbacks ─────────────────────────────────

    def _on_connect(self, _c, _u, _flags, rc, _reason=None):
        rc_ok = (rc == 0) or (hasattr(rc, "value") and rc.value == 0)
        if rc_ok:
            for mac, dev in self.devices.items():
                self._client.subscribe(f"status/{dev.parent_mac}/#", qos=1)
            _LOGGER.info(
                "MQTT connected, subscribed to %d devices", len(self.devices)
            )
            self._set_tcp_keepalive()
            self._ready.set()
        else:
            _LOGGER.error("MQTT connect failed: rc=%s", rc)

    def _on_disconnect(self, _c, _u, _rc, _reason=None, _properties=None):
        _LOGGER.warning("MQTT disconnected (rc=%s) — auto-reconnect enabled", _rc)
        self._ready.clear()

    def _set_tcp_keepalive(self):
        try:
            sock = self._client._sock
            if sock is None:
                return
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            if hasattr(socket, "TCP_KEEPALIVE"):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPALIVE, 10)
            elif hasattr(socket, "TCP_KEEPIDLE"):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
            _LOGGER.debug("TCP keepalive set (idle=10s, probe=5s)")
        except Exception:
            _LOGGER.warning("Could not set TCP keepalive", exc_info=True)

    def _on_message(self, _c, _u, msg):
        _LOGGER.debug("MQTT RECV topic=%s payload=%.120s", msg.topic, msg.payload)
        try:
            payload = json.loads(msg.payload)
        except json.JSONDecodeError:
            return

        pack = payload.get("pack")
        if not pack:
            return

        topic_parts = msg.topic.split("/")
        topic_pmac = topic_parts[1] if len(topic_parts) > 1 else ""

        mac = None
        dev = None
        for m, d in self.devices.items():
            if d.parent_mac == topic_pmac and len(m) == 12:
                mac, dev = m, d
                break

        if mac is None:
            for m, d in self.devices.items():
                data = d.decrypt_pack(pack)
                if data is not None:
                    mac, dev, _data = m, d, data
                    break
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
        self._data_seq[mac] += 1

        needs_reenable: list[str] = []
        if power_on:
            for key in EXTRA_KEYS:
                if key not in _data:
                    dev.properties[key] = 0
            for key in list(self._user_params.get(mac, set())):
                if not dev.properties.get(key):
                    dev.properties[key] = 1
                    needs_reenable.append(key)

        self._response_events[mac].set()
        if self._on_data:
            self._on_data(mac, dict(dev.properties))
        _LOGGER.debug("MQTT: %s ⇐ %s (topic=%s)",
                      mac, dict(sorted(_data.items())), msg.topic)

        if needs_reenable:
            _LOGGER.info("Re-enabling %s on %s after power-on", needs_reenable, mac)
            dev = self.devices.get(mac)
            if dev:
                pack = dev.build_command_pack(needs_reenable, [1] * len(needs_reenable))
                self._publish_json(
                    f"request/{dev.parent_mac}",
                    {"t": "pack", "i": 0, "uid": self.uid, "cid": "ha_ac_cloud",
                     "tcid": mac, "pack": pack},
                )

    def _keepalive_loop(self):
        while self._running:
            time.sleep(25)
            if not self._client or not self._client.is_connected():
                continue
            try:
                self._publish_json(
                    f"kA/{self.uid}",
                    {"t": "ka", "ts": time.time()},
                )
            except Exception:
                pass

    # ── public API ─────────────────────────────────────

    def _publish_json(self, topic: str, obj: dict) -> bool:
        payload = json.dumps(obj, separators=(",", ":"))
        result = self._client.publish(topic, payload)
        return result.rc == 0 if hasattr(result, 'rc') else False

    def refresh_device(self, mac: str, timeout: float = 5) -> dict[str, Any] | None:
        """Trigger a status push by resubscribing.

        Unsubscribe + resubscribe forces the broker to re-deliver the
        current device state on the status/ topic. Returns the device
        properties dict, or None on timeout.
        """
        dev = self.devices.get(mac)
        if not dev or not self._client or not self._client.is_connected():
            return None

        event = self._response_events.get(mac)
        if not event:
            return None

        seq_before = self._data_seq.get(mac, 0)
        event.clear()

        pmac = dev.parent_mac
        self._client.unsubscribe(f"status/{pmac}/#")
        self._client.subscribe(f"status/{pmac}/#", qos=1)

        if not event.wait(timeout):
            _LOGGER.debug("%s: refresh timeout (seq=%d)", mac, seq_before)
            return None

        if self._data_seq.get(mac, 0) <= seq_before:
            return None

        return dict(dev.properties)

    def poll_device(self, mac: str, cols: list[str] | None = None) -> bool:
        """Legacy: send a status request. Prefer refresh_device()."""
        dev = self.devices.get(mac)
        if not dev or not self._client or not self._client.is_connected():
            return False

        pack = dev.build_status_request(cols or POLL_COLS)
        return self._publish_json(
            f"request/{dev.parent_mac}",
            {"t": "pack", "i": 0, "uid": self.uid, "cid": "ha_ac_cloud",
             "tcid": mac, "pack": pack},
        )

    def poll_all(self, cols: list[str] | None = None):
        for mac in self.devices:
            self.poll_device(mac, cols)

    def send_command(
        self, mac: str, options: list[str], values: list[Any]
    ) -> bool:
        """Send a command to a device."""
        dev = self.devices.get(mac)
        if not dev or not self._client or not self._client.is_connected():
            return False

        pack = dev.build_command_pack(options, values)
        ok = self._publish_json(
            f"request/{dev.parent_mac}",
            {"t": "pack", "i": 0, "uid": self.uid, "cid": "ha_ac_cloud",
             "tcid": mac, "pack": pack},
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
    logging.getLogger().handlers[0].setLevel(logging.DEBUG)

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
        print(f"  {d.mac:16s} {d.name} (parent={d.parent_mac})")

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

    ok = client.start(timeout=15)
    print(f"\nMQTT connect: {'OK' if ok else 'FAIL'}")

    if not ok:
        print("Exiting: MQTT connection failed")
        return

    parents = [d for d in devices if len(d.mac) == 12]
    total = 0
    ok_count = 0
    for _ in range(4):
        time.sleep(2)
        for d in parents:
            mac = d.mac
            total += 1
            print(f"  Refreshing {mac}...", end=" ")
            data = client.refresh_device(mac, timeout=5)
            if data is not None:
                ok_count += 1
                print(f"OK Pow={data.get('Pow')} T={data.get('InTem')}°C")
            else:
                print("TIMEOUT")
    print(f"\n  Result: {ok_count}/{total} OK")

    client.stop()
    print("\nDone.")


if __name__ == "__main__":
    _test()
