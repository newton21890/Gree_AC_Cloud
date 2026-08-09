DOMAIN = "gree_ac_cloud"

CONF_SERVER = "server"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_DEVICE = "device"
CONF_TEMPERATURE_SENSOR = "temperature_sensor"
CONF_HUMIDITY_SENSOR = "humidity_sensor"
CONF_PRESET_ENABLED = "enabled"
CONF_PRESET_TARGET = "target_temperature"
CONF_PRESET_AUTO_OFF = "auto_off_temperature"
CONF_PRESET_HUMIDITY = "humidity_threshold"
CONF_PRESET_MIN_TEMP = "min_temperature"
CONF_PRESET_MAX_TEMP = "max_temperature"
CONF_PRESET_DRED = "dred"
CONF_DEVICES = "devices"
CONF_PRESETS = "presets"

PRESET_DAY = "day"
PRESET_NIGHT = "night"
PRESET_AWAY = "away"
PRESET_NAMES = (PRESET_DAY, PRESET_NIGHT, PRESET_AWAY)

UPDATE_INTERVAL = 15
STALE_AFTER_SECONDS = UPDATE_INTERVAL * 4  # 60s — mark device unavailable after this many seconds without fresh MQTT data

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
    "Europe": "mqtt-eu.gree.com",
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
    "InTem", "OutTem", "TemSen", "InHumi", "SetDeciTem",
    "Err", "Errcode", "ErrType", "RefLeak", "MSysStatus",
    "Filter", "CleanEn", "CleanTime", "CleanDataFlag", "CleanState", "FClTime",
    "Idemand", "DRED", "DREDEn", "WaterSen",
]

COMMAND_OPTIONS = frozenset(
    {
        "Pow", "Mod", "SetTem", "SetDeciTem", "WdSpd", "Air", "Blo",
        "Health", "SwhSlp", "Lig", "SwUpDn", "SwingLfRig", "Quiet", "Tur",
        "StHt", "TemUn", "TemRec", "SvSt", "SlpMod", "DRED",
    }
)

DRED_OPTIONS = {
    0: "Off",
    1: "D1",
    2: "D2",
    3: "D3",
}
DRED_OPTIONS_REV = {value: key for key, value in DRED_OPTIONS.items()}
STARTUP_DRED_NO_ACTION = "No action"
STARTUP_DRED_OPTIONS = [STARTUP_DRED_NO_ACTION, *DRED_OPTIONS_REV]

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
    "InTem": {
        "name": "Indoor Unit Air Sensor (Raw/Unverified)",
        "icon": "mdi:thermometer-alert",
        "diagnostic": True,
    },
    "OutTem": {
        "name": "Outdoor Unit Sensor (Raw/Unverified)",
        "icon": "mdi:thermometer-alert",
        "diagnostic": True,
    },
    "TemSen": {"name": "Indoor Air Temperature", "icon": "mdi:home-thermometer"},
    "InHumi": {"name": "Indoor Humidity", "icon": "mdi:water-percent"},
    "SetDeciTem": {"name": "Target Temperature (Decimal)", "icon": "mdi:thermometer"},
    "Errcode": {"name": "Error Code", "icon": "mdi:alert-circle-outline", "diagnostic": True},
    "ErrType": {"name": "Error Type", "icon": "mdi:alert-outline", "diagnostic": True},
    "MSysStatus": {"name": "System Status", "icon": "mdi:state-machine", "diagnostic": True},
    "CleanState": {"name": "Auto Clean Status", "icon": "mdi:auto-fix", "diagnostic": True},
    "CleanTime": {"name": "Filter Runtime", "icon": "mdi:timer-outline", "diagnostic": True},
    "FClTime": {"name": "Filter Cleaning Interval", "icon": "mdi:calendar-clock", "diagnostic": True},
}

DEVICE_SWITCHES = {
    "Health": {"name": "Health/Ionizer", "icon": "mdi:leaf"},
    "Quiet": {"name": "Quiet Mode", "icon": "mdi:volume-off"},
    "Tur": {"name": "Turbo Mode", "icon": "mdi:rocket-launch"},
    "StHt": {"name": "Strong Heat", "icon": "mdi:fire"},
    "Blo": {"name": "X-Fan / Coil Dry", "icon": "mdi:fan-clock"},
    "SvSt": {"name": "Energy Saving", "icon": "mdi:solar-power"},
    "TemRec": {"name": "Temperature Recovery", "icon": "mdi:thermostat-auto"},
    "SlpMod": {"name": "Sleep Mode", "icon": "mdi:sleep"},
    "Air": {"name": "Fresh Air", "icon": "mdi:air-filter"},
    "Lig": {"name": "Light", "icon": "mdi:lightbulb"},
}

ENERGY_MODELS = {
    "GUD35": {"cool": 1.03, "heat": 1.00, "max": 1.30, "name": "GUD35 (12K)"},
    "GUD50": {"cool": 1.51, "heat": 1.42, "max": 1.90, "name": "GUD50 (18K)"},
    "GUD71": {"cool": 1.92, "heat": 2.00, "max": 2.80, "name": "GUD71 (24K)"},
    "GUD85": {"cool": 2.50, "heat": 2.25, "max": 3.30, "name": "GUD85 (29K)"},
    "GUD100": {"cool": 3.00, "heat": 2.80, "max": 4.70, "name": "GUD100 (36K)"},
    "GUD140": {"cool": 4.60, "heat": 4.70, "max": 5.60, "name": "GUD140 (46K)"},
    "GUD160": {"cool": 5.40, "heat": 4.70, "max": 6.80, "name": "GUD160 (55K)"},
}

DEVICE_BINARY_SENSORS = {
    "Err": {
        "name": "Error Status",
        "device_class": "problem",
        "sources": ("Err", "Errcode", "ErrType"),
    },
    "Filter": {
        "name": "Filter Status",
        "device_class": "cleaning",
        "sources": ("Filter", "CleanDataFlag"),
    },
    "RefLeak": {
        "name": "Refrigerant Warning",
        "device_class": "problem",
        "sources": ("RefLeak",),
        "diagnostic": True,
    },
}

STORAGE_VERSION = 1
STORAGE_KEY_MODELS = f"{DOMAIN}.models"
STORAGE_KEY_SETTINGS = f"{DOMAIN}.settings"
