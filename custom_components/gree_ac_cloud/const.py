DOMAIN = "gree_ac_cloud"

CONF_SERVER = "server"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"

UPDATE_INTERVAL = 15

TARGET_TEMPERATURE_STEP = 1
MIN_TEMP_C = 16
MAX_TEMP_C = 30

GREE_CLOUD_SERVERS = {
    "Europe": "eugrih.gree.com",
    "North America": "nagrih.gree.com",
    "China Mainland": "grih.gree.com",
    "Australia": "augrih.gree.com",
    "East South Asia": "hkgrih.gree.com",
    "India": "ingrih.gree.com",
    "Latin America": "lagrih.gree.com",
    "Middle East": "megrih.gree.com",
    "Russia": "rugrih.gree.com",
    "South America": "sagrih.gree.com",
}

GREE_MQTT_HOSTS = {
    "Europe": "18.185.150.155",  # mqtt-eu.gree.com
    "North America": "mqtt-us.gree.com",
    "China Mainland": "mqtt-cn.gree.com",
    "Australia": "mqtt-au.gree.com",
    "East South Asia": "mqtt-as.gree.com",
    "India": "mqtt-in.gree.com",
    "Latin America": "mqtt-la.gree.com",
    "Middle East": "mqtt-me.gree.com",
    "Russia": "mqtt-ru.gree.com",
    "South America": "mqtt-sa.gree.com",
}

GREE_MQTT_PORTS = {
    "Europe": 1984,
}

POLL_COLS = [
    "Pow", "Mod", "SetTem", "WdSpd", "Air", "Blo", "Health",
    "SwhSlp", "Lig", "SwUpDn", "SwingLfRig", "Quiet", "Tur",
    "StHt", "TemUn", "HeatCoolType", "TemRec", "SvSt", "SlpMod",
    "InTem", "OutTem", "InHumi", "SetDeciTem",
    "Err", "Filter", "WaterSen",
]

FAN_MAP = {
    0: "Auto", 1: "Bassa", 2: "Media-Bassa", 3: "Media",
    4: "Media-Alta", 5: "Alta",
}

FAN_MAP_REV = {v: k for k, v in FAN_MAP.items()}

HVAC_MAP = {
    0: "auto", 1: "cool", 2: "heat", 3: "fan_only", 4: "dry",
}
HVAC_MAP_REV = {v: k for k, v in HVAC_MAP.items()}

DEVICE_SENSORS = {
    "InTem": {"name": "Indoor Temperature", "icon": "mdi:thermometer"},
    "OutTem": {"name": "Outdoor Temperature", "icon": "mdi:thermometer"},
    "InHumi": {"name": "Indoor Humidity", "icon": "mdi:water-percent"},
    "SetDeciTem": {"name": "Target Temperature (Decimal)", "icon": "mdi:thermometer"},
}

DEVICE_SWITCHES = {
    "Health": {"name": "Health/Ionizer", "icon": "mdi:leaf"},
    "Quiet": {"name": "Quiet Mode", "icon": "mdi:volume-off"},
    "Tur": {"name": "Turbo Mode", "icon": "mdi:rocket-launch"},
    "StHt": {"name": "Strong Heat", "icon": "mdi:fire"},
    "Blo": {"name": "Blow", "icon": "mdi:air-filter"},
    "SvSt": {"name": "Energy Saving", "icon": "mdi:solar-power"},
    "TemRec": {"name": "Temperature Recovery", "icon": "mdi:thermostat-auto"},
    "SlpMod": {"name": "Sleep Mode", "icon": "mdi:sleep"},
    "Air": {"name": "Air Direction", "icon": "mdi:air-conditioner"},
    "Lig": {"name": "Light", "icon": "mdi:lightbulb"},
}

ENERGY_MODELS = {
    "GUD35": {"cool": 1.00, "heat": 1.05, "max": 1.40, "name": "GUD35 (12K)"},
    "GUD50": {"cool": 1.45, "heat": 1.50, "max": 2.00, "name": "GUD50 (18K)"},
    "GUD71": {"cool": 1.92, "heat": 2.00, "max": 2.80, "name": "GUD71 (24K)"},
    "GUD85": {"cool": 2.50, "heat": 2.26, "max": 3.30, "name": "GUD85 (29K)"},
    "GUD100": {"cool": 3.00, "heat": 2.80, "max": 4.70, "name": "GUD100 (36K)"},
    "GUD140": {"cool": 4.60, "heat": 4.70, "max": 5.60, "name": "GUD140 (46K)"},
    "GUD160": {"cool": 5.40, "heat": 4.70, "max": 6.80, "name": "GUD160 (55K)"},
}

DEVICE_BINARY_SENSORS = {
    "Err": {"name": "Error Status", "device_class": "problem"},
    "Filter": {"name": "Filter Status", "device_class": "cleaning"},
}

STORAGE_VERSION = 1
STORAGE_KEY_MODELS = f"{DOMAIN}.models"
STORAGE_KEY_SETTINGS = f"{DOMAIN}.settings"
