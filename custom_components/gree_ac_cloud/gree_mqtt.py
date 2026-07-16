"""Gree AC Cloud MQTT - standalone MQTT protocol module.

Handles:
  - MQTT connection to Gree cloud broker (TLS, auth)
  - Device status polling
  - Command sending
  - Response decryption

Test: python3 -m custom_components.gree_ac_cloud.gree_mqtt
"""

from __future__ import annotations

import json
import logging
import socket

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
        self._running = False
        self._user_params: dict[str, set[str]] = {d.mac: set() for d in devices}
        self._req_counter = 0
        self._req_lock = threading.Lock()

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
            self._client.connect(self.host, self.port, keepalive=10)
            self._client.loop_start()
        except Exception as exc:
            _LOGGER.error("MQTT connect failed: %s", exc)
            return False

        return self._ready.wait(timeout)

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
                pmac = dev.parent_mac
                self._client.subscribe(f"response/{pmac}/#", qos=1)
                self._client.subscribe(f"status/{pmac}/#", qos=1)
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
            _LOGGER.debug("MQTT RECV non-JSON on %s", msg.topic)
            return

        pack = payload.get("pack")
        if not pack:
            _LOGGER.debug("MQTT RECV no pack on %s keys=%s", msg.topic, list(payload.keys()))
            return

        # Topic format: response/{parent_mac}/...
        topic_parts = msg.topic.split("/")
        topic_pmac = topic_parts[1] if len(topic_parts) > 1 else ""

        # Prefer matching by topic → parent_mac → 12-char parent device
        mac = None
        dev = None
        for m, d in self.devices.items():
            if d.parent_mac == topic_pmac and len(m) == 12:
                mac, dev = m, d
                break

        if mac is None:
            # Fallback: try all devices by decryption
            for m, d in self.devices.items():
                data = d.decrypt_pack(pack)
                if data is not None:
                    mac, dev, _data = m, d, data
                    break
            if mac is None:
                _LOGGER.debug("MQTT: no device decrypted (topic=%s)", msg.topic)
                return
        else:
            _data = dev.decrypt_pack(pack)
            if _data is None:
                _LOGGER.debug("MQTT: %s decrypt failed (topic=%s)", mac, msg.topic)
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
            # Re-enable params the user had set ON before power cycle
            for key in list(self._user_params.get(mac, set())):
                if not dev.properties.get(key):
                    dev.properties[key] = 1
                    needs_reenable.append(key)

        self._response_events[mac].set()
        if self._on_data:
            self._on_data(mac, dict(dev.properties))
        _LOGGER.debug("MQTT: %s ⇐ %s (topic=%s)",
                      mac, dict(sorted(_data.items())), msg.topic)

        # Re-publish commands for re-enabled params
        if needs_reenable:
            _LOGGER.info("Re-enabling %s on %s after power-on", needs_reenable, mac)
            dev = self.devices.get(mac)
            if dev:
                pack = dev.build_command_pack(needs_reenable, [1] * len(needs_reenable))
                msg = json.dumps(
                    {"t": "pack", "i": 0, "uid": self.uid, "cid": "ha_ac_cloud",
                     "tcid": mac, "pack": pack},
                    separators=(",", ":"),
                )
                self._client.publish(f"request/{dev.parent_mac}", msg)


    # ── public API ─────────────────────────────────────

    def poll_device(self, mac: str, cols: list[str] | None = None) -> bool:
        """Send a status request for a specific device.

        Returns True if the request was published.
        """
        dev = self.devices.get(mac)
        if not dev or not self._client or not self._client.is_connected():
            return False

        with self._req_lock:
            self._req_counter += 1
            req_i = self._req_counter

        pack = dev.build_status_request(cols or POLL_COLS)
        msg = json.dumps(
            {
                "t": "pack",
                "i": req_i,
                "uid": self.uid,
                "cid": "ha_ac_cloud",
                "tcid": mac,
                "pack": pack,
            },
            separators=(",", ":"),
        )
        result = self._client.publish(f"request/{dev.parent_mac}", msg)
        ok = result.rc == 0 if hasattr(result, 'rc') else False
        _LOGGER.debug(
            "Poll %s published (i=%d rc=%s) to request/%s",
            mac, req_i, result.rc if hasattr(result, 'rc') else '?', dev.parent_mac,
        )
        return ok

    def poll_all(self, cols: list[str] | None = None):
        """Send status requests for all devices."""
        for mac in self.devices:
            self.poll_device(mac, cols)

    def send_command(
        self, mac: str, options: list[str], values: list[Any]
    ) -> bool:
        """Send a command to a device.

        Returns True if the command was published.
        """
        dev = self.devices.get(mac)
        if not dev or not self._client or not self._client.is_connected():
            return False

        pack = dev.build_command_pack(options, values)
        msg = json.dumps(
            {
                "t": "pack",
                "i": 0,
                "uid": self.uid,
                "cid": "ha_ac_cloud",
                "tcid": mac,
                "pack": pack,
            },
            separators=(",", ":"),
        )
        self._client.publish(f"request/{dev.parent_mac}", msg)
        _LOGGER.info(
            "send_command: %s options=%s values=%s",
            mac, options, values,
        )
        # Track user intent for extra params (re-enable after power cycle)
        up = self._user_params.setdefault(mac, set())
        for opt, val in zip(options, values):
            if opt in EXTRA_KEYS:
                if val == 1:
                    up.add(opt)
                else:
                    up.discard(opt)
        return True

    def wait_for_response(self, mac: str, timeout: float = 5) -> bool:
        """Block until a response is received for a device."""
        ev = self._response_events.get(mac)
        if ev is None:
            return False
        return ev.wait(timeout)


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

    host = "3.71.159.59"
    uid, token, devices = discover_devices(host, cfg["username"], cfg["password"])
    print(f"\nDevices ({len(devices)}):")
    for d in devices:
        print(f"  {d.mac:16s} {d.name} (parent={d.parent_mac})")

    received = threading.Event()
    results = {}

    def on_data(mac, data):
        results[mac] = data
        print(f"\n  ← Data for {mac}: Pow={data.get('Pow')} T={data.get('InTem')}°C")
        received.set()

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

    for d in devices:
        print(f"  Polling {d.mac}...")
        client.poll_device(d.mac)
        if client.wait_for_response(d.mac, timeout=5):
            print(f"    OK: {results.get(d.mac, {})}")
        else:
            print(f"    No response")

    client.stop()
    print("\nDone.")


if __name__ == "__main__":
    _test()
