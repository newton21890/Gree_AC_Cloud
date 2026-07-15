"""Gree AC Cloud API - standalone protocol module.

Handles:
  - Cloud API authentication (UserLoginV2)
  - Home/device discovery (GetHomes, GetDevsInRoomsOfHomeV2)
  - Device data models
  - Encryption/decryption primitives

Test: python3 -m custom_components.gree_ac_cloud.gree_api
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

_LOGGER = logging.getLogger(__name__)

APP_ID = "4920681951525131286"
APP_HASH = "0fa513124aa97781d1f3f40d61ca1a89"
AES_KEY = b"#G$&^jgfujy6ujxt"


def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


def _aes_encrypt(data: str, key: bytes = AES_KEY) -> str:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
    c = AES.new(key, AES.MODE_ECB)
    return base64.b64encode(c.encrypt(pad(data.encode(), 16))).decode()


def _aes_decrypt(data_b64: str, key: bytes = AES_KEY) -> str:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad
    c = AES.new(key, AES.MODE_ECB)
    return unpad(c.decrypt(base64.b64decode(data_b64)), 16).decode()


def _encrypt_payload(payload: dict, key: bytes = AES_KEY) -> str:
    return _aes_encrypt(json.dumps(payload, separators=(",", ":")), key)


def _build_api_request(endpoint: str, body: dict, props: list[str]) -> bytes:
    now = datetime.now(timezone.utc)
    t = now.strftime("%Y-%m-%d %H:%M:%S")
    r = int(now.timestamp())

    vc = _md5(f"{APP_ID}_{APP_HASH}_{t}_{r}")
    prop_vals = [str(body[p]) for p in props]
    datVc = _md5(f"{APP_HASH}_{'_'.join(prop_vals)}")

    full = {
        "api": {"appId": APP_ID, "r": r, "t": t, "vc": vc},
        "datVc": datVc,
        **body,
    }
    return _encrypt_payload(full).encode()


def _parse_api_response(data: bytes) -> dict[str, Any]:
    raw = json.loads(data)
    decrypted = _aes_decrypt(raw["enRes"])
    return json.loads(decrypted)


def api_login(
    host: str,
    username: str,
    password: str,
    user_agent: str = "Gree+2.8.0",
) -> tuple[int, str]:
    """Authenticate with Gree cloud API.

    Returns (uid, token).

    Raises ValueError on invalid credentials.
    """
    import requests

    now = datetime.now(timezone.utc)
    t = now.strftime("%Y-%m-%d %H:%M:%S")

    h = _md5(_md5(password) + password)
    psw = _md5(h + t)

    body = {"user": username, "psw": psw, "t": t}
    props = ["user", "psw", "t"]

    payload = _build_api_request("/App/UserLoginV2", body, props)

    resp = requests.post(
        f"https://{host}/App/UserLoginV2",
        payload,
        headers={
            "Host": host,
            "Content-Type": "application/x-www-form-urlencoded",
            "Gaen1": "5ac2bdf935bcca70",
            "Charset": "utf-8",
            "User-Agent": user_agent,
        },
        verify=False,
        timeout=15,
    )
    resp.raise_for_status()
    data = _parse_api_response(resp.content)

    if "uid" not in data:
        raise ValueError(data.get("msg", "Login failed"))

    return int(data["uid"]), data["token"]


def api_get_homes(
    host: str, uid: int, token: str
) -> list[dict[str, Any]]:
    import requests

    body = {"token": token, "uid": uid}
    props = ["token", "uid"]
    payload = _build_api_request("/App/GetHomes", body, props)

    resp = requests.post(
        f"https://{host}/App/GetHomes",
        payload,
        headers={
            "Host": host,
            "Content-Type": "application/x-www-form-urlencoded",
            "Gaen1": "5ac2bdf935bcca70",
            "Charset": "utf-8",
        },
        verify=False,
        timeout=15,
    )
    resp.raise_for_status()
    data = _parse_api_response(resp.content)
    return data.get("home", [])


def api_get_devices(
    host: str, uid: int, token: str, home_id: int
) -> list[dict[str, Any]]:
    import requests

    body = {"token": token, "uid": uid, "homeId": home_id}
    props = ["token", "uid", "homeId"]
    payload = _build_api_request("/App/GetDevsInRoomsOfHomeV2", body, props)

    resp = requests.post(
        f"https://{host}/App/GetDevsInRoomsOfHomeV2",
        payload,
        headers={
            "Host": host,
            "Content-Type": "application/x-www-form-urlencoded",
            "Gaen1": "5ac2bdf935bcca70",
            "Charset": "utf-8",
        },
        verify=False,
        timeout=15,
    )
    resp.raise_for_status()
    data = _parse_api_response(resp.content)
    devices = []
    for room in data.get("rooms", []):
        for dev in room.get("devs", []):
            devices.append(dev)
    return devices


@dataclass
class GreeDevice:
    mac: str
    name: str
    key: str
    hid: str | None = None
    parent_mac: str = field(init=False)
    mqtt_port: int = 1984
    _properties: dict[str, Any] = field(default_factory=dict, repr=False)
    _cipher: Any = field(default=None, repr=False)

    def __post_init__(self):
        # 14-char MACs are sub-unit aliases: strip last 2 chars for parent MAC
        # 12-char MACs are parent devices: use full MAC
        if len(self.mac) == 14:
            self.parent_mac = self.mac[:-2]
        else:
            self.parent_mac = self.mac

    @property
    def cipher(self):
        if self._cipher is None:
            from Crypto.Cipher import AES
            self._cipher = AES.new(self.key.encode(), AES.MODE_ECB)
        return self._cipher

    @property
    def properties(self) -> dict[str, Any]:
        return self._properties

    @properties.setter
    def properties(self, value: dict[str, Any]):
        self._properties = value

    def build_status_request(self, cols: list[str]) -> str:
        from Crypto.Util.Padding import pad
        req = json.dumps({"t": "status", "cols": cols}, separators=(",", ":"))
        return base64.b64encode(
            self.cipher.encrypt(pad(req.encode(), 16))
        ).decode()

    def build_command_pack(self, options: list[str], values: list[Any]) -> str:
        from Crypto.Util.Padding import pad
        req = json.dumps({"t": "cmd", "opt": options, "p": values}, separators=(",", ":"))
        return base64.b64encode(
            self.cipher.encrypt(pad(req.encode(), 16))
        ).decode()

    def decrypt_pack(self, pack_b64: str) -> dict[str, Any] | None:
        from Crypto.Util.Padding import unpad
        try:
            raw = unpad(
                self.cipher.decrypt(base64.b64decode(pack_b64)),
                16,
            ).decode()
            result = json.loads(raw)
            if "cols" in result and "dat" in result:
                return dict(zip(result["cols"], result["dat"]))
            return result
        except (ValueError, KeyError, json.JSONDecodeError):
            return None


def discover_devices(
    host: str, username: str, password: str
) -> tuple[int, str, list[GreeDevice]]:
    """Full discovery flow: login -> get homes -> get devices.

    Returns (uid, token, list[GreeDevice]).
    """
    uid, token = api_login(host, username, password)
    homes = api_get_homes(host, uid, token)
    if not homes:
        raise ValueError("No homes found for this account")

    home_id = homes[0]["id"]
    raw_devices = api_get_devices(host, uid, token, home_id)
    if not raw_devices:
        raise ValueError("No devices found in home")

    devices = [
        GreeDevice(
            mac=d["mac"],
            name=d.get("name", f"Gree Device {d['mac']}"),
            key=d.get("key", ""),
            hid=d.get("hid"),
        )
        for d in raw_devices
    ]
    return uid, token, devices


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    import os, sys
    creds = os.path.expanduser("~/.bridge_credentials.json")
    if not os.path.exists(creds):
        print(f"Credentials file not found: {creds}")
        sys.exit(1)

    with open(creds) as f:
        cfg = json.load(f)

    host = "3.71.159.59"
    uid, token, devices = discover_devices(
        host, cfg["username"], cfg["password"]
    )
    print(f"\nLogged in: uid={uid}, token={token[:16]}...")
    print(f"Found {len(devices)} device(s):")
    for d in devices:
        print(f"  [{d.mac}] {d.name}")
        print(f"    parent: {d.parent_mac}, key: {d.key[:4]}...")
