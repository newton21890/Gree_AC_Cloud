#!/usr/bin/env python3
"""
Gree Cloud MQTT Bridge.
Connects to Gree Cloud MQTT broker, polls device state, exposes REST API.
Designed to run alongside Home Assistant.

Usage:
  python3 gree_cloud_bridge.py [--port 8765]
"""

import json, hashlib, datetime, base64, time, ssl, os, threading, argparse
from http.server import HTTPServer, BaseHTTPRequestHandler
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
import paho.mqtt.client as mqtt

# === Configuration ===
APP_ID = "4920681951525131286"
APP_HASH = "0fa513124aa97781d1f3f40d61ca1a89"
AES_KEY = b"#G$&^jgfujy6ujxt"
CLOUD_IP = "3.71.159.59"
MQTT_HOST = "18.185.150.155"
MQTT_PORT = 1984

CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'cloud_bridge_config.json')

# === Crypto ===
def md5(s): return hashlib.md5(s.encode()).hexdigest()
def aes_cipher(key): return AES.new(key.encode() if isinstance(key, str) else key, AES.MODE_ECB)
def aes_encrypt(cipher, data): return base64.b64encode(cipher.encrypt(pad(data.encode(), AES.block_size))).decode()
def aes_decrypt(cipher, data): return unpad(cipher.decrypt(base64.b64decode(data)), AES.block_size).decode()

# === Cloud API ===
class GreeCloudAPI:
    def __init__(self):
        self.uid = None
        self.token = None
        self.devices = []
        self._cipher = AES.new(AES_KEY, AES.MODE_ECB)
    
    def _enc(self, data): return base64.b64encode(self._cipher.encrypt(pad(data.encode(), AES.block_size)))
    def _dec(self, data): return unpad(self._cipher.decrypt(base64.b64decode(data)), AES.block_size).decode()
    
    def _post(self, endpoint, payload, props):
        d = datetime.datetime.now(datetime.timezone.utc)
        t = d.strftime("%Y-%m-%d %H:%M:%S")
        r = int(d.timestamp())
        vc = md5(f"{APP_ID}_{APP_HASH}_{t}_{r}")
        datVc = md5(f"{APP_HASH}_{'_'.join(str(payload[p]) for p in props)}")
        body = json.dumps({"api": {"appId": APP_ID, "r": r, "t": t, "vc": vc}, "datVc": datVc, **payload})
        
        import requests
        res = requests.post(f"https://{CLOUD_IP}{endpoint}", self._enc(body),
            headers={"Host": "eugrih.gree.com", "Content-Type": "application/x-www-form-urlencoded",
                     "Gaen1": "5ac2bdf935bcca70", "Charset": "utf-8"},
            verify=False, timeout=10)
        return json.loads(self._dec(res.json()["enRes"]))
    
    def login(self, username, password):
        d = datetime.datetime.now(datetime.timezone.utc)
        t = d.strftime("%Y-%m-%d %H:%M:%S")
        h = md5(md5(password) + password)
        psw = md5(h + t)
        data = self._post("/App/UserLoginV2", {"user": username, "psw": psw, "t": t}, ["user", "psw", "t"])
        self.uid = data["uid"]
        self.token = data["token"]
        return self.uid, self.token
    
    def get_devices(self):
        homes = self._post("/App/GetHomes", {"token": self.token, "uid": self.uid}, ["token", "uid"])
        home_id = homes["home"][0]["id"]
        devs = self._post("/App/GetDevsInRoomsOfHomeV2", 
            {"token": self.token, "uid": self.uid, "homeId": home_id},
            ["token", "uid", "homeId"])
        self.devices = []
        for room in devs["rooms"]:
            for dev in room["devs"]:
                self.devices.append(dev)
        return self.devices

# === MQTT Bridge ===
class GreeMQTTBridge:
    def __init__(self, api: GreeCloudAPI):
        self.api = api
        self.device_states = {}
        self._client = None
        self._running = False
    
    def _make_pack(self, key, data):
        c = aes_cipher(key)
        return aes_encrypt(c, json.dumps(data))
    
    def _decrypt_pack(self, pack_b64, key):
        try:
            c = aes_cipher(key)
            return json.loads(aes_decrypt(c, pack_b64))
        except:
            return None
    
    def start(self):
        self._running = True
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"ha_bridge_{int(time.time())}", protocol=mqtt.MQTTv311)
        self._client.tls_set(cert_reqs=ssl.CERT_NONE)
        self._client.tls_insecure_set(True)
        self._client.username_pw_set(str(self.api.uid), self.api.token)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.connect(MQTT_HOST, MQTT_PORT, 60)
        self._client.loop_start()
        
        poller = threading.Thread(target=self._poll_loop, daemon=True)
        poller.start()
    
    def _on_connect(self, c, u, flags, rc, reason=None):
        rc_ok = (rc == 0) or (hasattr(rc, 'value') and rc.value == 0)
        print(f"MQTT connect: {rc}")
        if rc_ok:
            for dev in self.api.devices:
                pmac = dev["mac"][:-2]
                c.subscribe(f"response/{pmac}/#", 1)
                c.subscribe(f"status/{pmac}/#", 1)
            print(f"MQTT subscribed to {len(self.api.devices)*2} topics")
    
    def _on_message(self, c, u, msg):
        try:
            p = json.loads(msg.payload)
            print(f"MQTT msg: {msg.topic} ({len(msg.payload)}B)")
            if "pack" in p:
                for dev in self.api.devices:
                    result = self._decrypt_pack(p["pack"], dev["key"])
                    if result:
                        # Convert cols+dat format to dict
                        if "cols" in result and "dat" in result:
                            data = dict(zip(result["cols"], result["dat"]))
                        else:
                            data = result
                        self.device_states[dev["mac"]] = {
                            "name": dev["name"],
                            "mac": dev["mac"],
                            "key": dev["key"],
                            "data": data,
                            "updated": time.time()
                        }
                        print(f"  Data for {dev['name']}: Pow={data.get('Pow')} SetTem={data.get('SetTem')} InTem={data.get('InTem')} OutTem={data.get('OutTem')}")
                        break
        except Exception as e:
            print(f"MQTT msg error: {e}")
    
    def _poll_loop(self):
        while self._running:
            time.sleep(5)
            if self._client and self._client.is_connected():
                for dev in self.api.devices:
                    req = json.dumps({"t": "status", "cols": [
                        "Pow", "Mod", "SetTem", "WdSpd", "Air", "Blo", "Health",
                        "SwhSlp", "Lig", "SwUpDn", "SwingLfRig", "Quiet", "Tur",
                        "StHt", "TemUn", "HeatCoolType", "TemRec", "SvSt", "SlpMod",
                        "InTem", "OutTem", "InHumi", "SetDeciTem"
                    ]})
                    pack = self._make_pack(dev["key"], req)
                    msg = json.dumps({"t": "pack", "i": 0, "uid": self.api.uid,
                        "cid": "ha_bridge", "tcid": dev["mac"], "pack": pack})
                    self._client.publish(f"request/{dev['mac'][:-2]}", msg)
    
    def get_state(self, mac=None):
        if mac:
            return self.device_states.get(mac)
        return self.device_states
    
    def send_command(self, mac, command):
        dev = None
        for d in self.api.devices:
            if d["mac"] == mac:
                dev = d
                break
        if not dev:
            return False
        
        if "SetTem" in command:
            command["SetDeciTem"] = command["SetTem"] * 10
        
        req = json.dumps({"t": "cmd", "opt": list(command.keys()), "p": list(command.values())})
        pack = self._make_pack(dev["key"], req)
        msg = json.dumps({"t": "pack", "i": 0, "uid": self.api.uid,
            "cid": "ha_bridge", "tcid": mac, "pack": pack})
        self._client.publish(f"request/{dev['mac'][:-2]}", msg)
        return True
    
    def stop(self):
        self._running = False
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()

# === HTTP API ===
class BridgeHandler(BaseHTTPRequestHandler):
    bridge = None
    
    def _json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
    
    def do_GET(self):
        if self.path == '/devices':
            self._json(list(BridgeHandler.bridge.get_state().values()))
        elif self.path.startswith('/devices/'):
            mac = self.path.split('/')[-1]
            state = BridgeHandler.bridge.get_state(mac)
            if state:
                self._json(state)
            else:
                self._json({"error": "device not found"}, 404)
        else:
            self._json({"devices": "/devices", "commands": "POST /devices/<mac>/command"})
    
    def do_POST(self):
        if '/command' in self.path:
            mac = self.path.split('/')[-2]
            length = int(self.headers['Content-Length'])
            body = json.loads(self.rfile.read(length))
            ok = BridgeHandler.bridge.send_command(mac, body)
            self._json({"ok": ok})
        else:
            self._json({"error": "use /devices/<mac>/command"}, 404)
    
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
    
    def log_message(self, format, *args):
        pass

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {}

def save_config(username, password):
    with open(CONFIG_FILE, 'w') as f:
        json.dump({"username": username, "password": password}, f)
    os.chmod(CONFIG_FILE, 0o600)

def main():
    parser = argparse.ArgumentParser(description='Gree Cloud MQTT Bridge')
    parser.add_argument('--port', type=int, default=8765, help='HTTP API port')
    parser.add_argument('--username', help='Gree cloud username (email)')
    parser.add_argument('--password', help='Gree cloud password')
    parser.add_argument('--save', action='store_true', help='Save credentials to config file')
    args = parser.parse_args()
    
    config = load_config()
    username = args.username or config.get("username") or input("Gree username (email): ")
    password = args.password or config.get("password") or input("Gree password: ")
    
    if args.save:
        save_config(username, password)
        print(f"Credentials saved to {CONFIG_FILE}")
    
    # Login to cloud API
    print("Logging into Gree Cloud API...")
    api = GreeCloudAPI()
    uid, token = api.login(username, password)
    devices = api.get_devices()
    print(f"Logged in: uid={uid}")
    print(f"Found {len(devices)} devices:")
    for d in devices:
        print(f"  {d['name']} - MAC: {d['mac']}")
    
    # Start MQTT bridge
    print(f"\nConnecting to MQTT broker {MQTT_HOST}:{MQTT_PORT}...")
    bridge = GreeMQTTBridge(api)
    bridge.start()
    print("MQTT connected. Polling devices every 5s...")
    
    # Start HTTP API
    BridgeHandler.bridge = bridge
    server = HTTPServer(('0.0.0.0', args.port), BridgeHandler)
    print(f"HTTP API on http://0.0.0.0:{args.port}")
    print("Press Ctrl+C to stop")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        bridge.stop()
        server.server_close()

if __name__ == '__main__':
    main()
