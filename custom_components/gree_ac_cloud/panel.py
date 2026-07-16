"""Gree AC Cloud panel.

Registers a sidebar panel in HA with a custom web interface
for monitoring and controlling Gree cloud VRF devices.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from datetime import datetime

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, STORAGE_KEY_MODELS, STORAGE_VERSION

_LOGGER = logging.getLogger(__name__)

PANEL_URL = "/api/gree_ac_cloud/panel"
PANEL_DATA_URL = "/api/gree_ac_cloud/panel/data"
PANEL_CMD_URL = "/api/gree_ac_cloud/panel/command"
PANEL_LOG_URL = "/api/gree_ac_cloud/panel/log"
PANEL_README_URL = "/api/gree_ac_cloud/panel/readme"
PANEL_CHANGELOG_URL = "/api/gree_ac_cloud/panel/changelog"

# ── In-memory log capture ─────────────────────────────

class _GreeLogHandler(logging.Handler):
    def __init__(self, maxlen: int = 200):
        super().__init__()
        self.logs: deque[dict] = deque(maxlen=maxlen)
        self.setLevel(logging.DEBUG)

    def emit(self, record: logging.LogRecord):
        self.logs.append({
            "t": datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
            "l": record.levelname,
            "m": record.getMessage(),
        })

_log_handler = _GreeLogHandler()
_logger_root = logging.getLogger("custom_components.gree_ac_cloud")
_logger_root.setLevel(logging.DEBUG)
_logger_root.addHandler(_log_handler)

# ── Cached file content ──────────────────────────────
import os as _os

_README_CACHE = "# README\n(file not found)"
_CHANGELOG_CACHE = "# Changelog\n(file not found)"
_changelog_path = _os.path.join(_os.path.dirname(__file__), "CHANGELOG.md")
_readme_path = _os.path.join(_os.path.dirname(__file__), "README.md")
for _cache_var, _path in [("_README_CACHE", _readme_path), ("_CHANGELOG_CACHE", _changelog_path)]:
    try:
        with open(_path, encoding="utf-8") as _f:
            _c = _f.read()
        if _cache_var == "_README_CACHE":
            _README_CACHE = _c
        else:
            _CHANGELOG_CACHE = _c
    except Exception:
        pass


async def async_register_panel(hass: HomeAssistant):
    """Register the sidebar panel and API views."""
    from homeassistant.components import frontend

    hass.http.register_view(GreePanelView)
    hass.http.register_view(GreePanelDataView)
    hass.http.register_view(GreePanelCommandView)
    hass.http.register_view(GreePanelLogView)
    hass.http.register_view(GreePanelModelsView)
    hass.http.register_view(GreePanelNamesView)

    if "frontend" in hass.config.components:
        try:
            frontend.async_register_built_in_panel(
                hass,
                component_name="iframe",
                sidebar_title="Gree AC Cloud",
                sidebar_icon="mdi:air-conditioner",
                frontend_url_path="gree-ac-cloud",
                config={"url": PANEL_URL},
                require_admin=True,
            )
            _LOGGER.info("Panel registered in sidebar")
        except ValueError:
            _LOGGER.debug("Panel gree-ac-cloud already registered")


async def async_unregister_panel(hass: HomeAssistant):
    """Remove the panel."""
    from homeassistant.components import frontend

    frontend.async_remove_panel(hass, "gree-ac-cloud")


# ── Views ─────────────────────────────────────────────


class GreePanelView(HomeAssistantView):
    """Serves the panel HTML page."""

    url = PANEL_URL
    name = "api:gree_ac_cloud:panel"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        html = PANEL_HTML
        html = html.replace("__README_JSON__", json.dumps(_README_CACHE))
        html = html.replace("__CHANGELOG_JSON__", json.dumps(_CHANGELOG_CACHE))
        hass = request.app["hass"]
        names = hass.data.get(DOMAIN, {}).get("device_names", {})
        html = html.replace("__DEVICE_NAMES_JSON__", json.dumps(names))
        return web.Response(text=html, content_type="text/html")


class GreePanelDataView(HomeAssistantView):
    """Returns device data as JSON for the panel."""

    url = PANEL_DATA_URL
    name = "api:gree_ac_cloud:panel_data"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        data = []
        for entry in hass.config_entries.async_entries(DOMAIN):
            runtime = getattr(entry, "runtime_data", None)
            if not runtime:
                continue
            coordinators = runtime.get("coordinators", [])
            for coord in coordinators:
                device = coord.device
                state = dict(device.properties) if device.properties else {}
                data.append({
                    "mac": device.mac,
                    "name": device.name,
                    "connected": coord._mqtt.connected if hasattr(coord, "_mqtt") else False,
                    "state": state,
                })
        return self.json(data)


class GreePanelCommandView(HomeAssistantView):
    """Receives commands from the panel."""

    url = PANEL_CMD_URL
    name = "api:gree_ac_cloud:panel_command"
    requires_auth = False

    async def post(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        try:
            body = await request.json()
        except Exception:
            return self.json({"error": "invalid JSON"}, status=400)

        mac = body.get("mac")
        options = body.get("options", [])
        values = body.get("values", [])

        if not mac or not options or not values:
            return self.json({"error": "missing mac, options, or values"}, status=400)

        for entry in hass.config_entries.async_entries(DOMAIN):
            runtime = entry.runtime_data if hasattr(entry, "runtime_data") else {}
            mqtt = runtime.get("mqtt")
            coordinators = runtime.get("coordinators", [])
            if mqtt:
                for coord in coordinators:
                    if coord.device.mac == mac:
                        ok = await hass.async_add_executor_job(
                            mqtt.send_command, mac, options, values
                        )
                        if ok:
                            for opt, val in zip(options, values):
                                coord.device.properties[opt] = val
                            await coord.async_set_updated_data(dict(coord.device.properties))
                        return self.json({"ok": ok})

        return self.json({"error": "device not found"}, status=404)


class GreePanelLogView(HomeAssistantView):
    """Returns recent integration logs."""

    url = PANEL_LOG_URL
    name = "api:gree_ac_cloud:panel_log"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        return self.json(list(_log_handler.logs))


class GreePanelModelsView(HomeAssistantView):
    """Get/set device model mappings for energy estimation."""

    url = "/api/gree_ac_cloud/panel/models"
    name = "api:gree_ac_cloud:panel_models"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        models = hass.data.get(DOMAIN, {}).get("models", {})
        return self.json(models)

    async def post(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        try:
            body = await request.json()
        except Exception:
            return self.json({"error": "invalid JSON"}, status=400)
        mac = body.get("mac")
        model = body.get("model", "")
        if not mac:
            return self.json({"error": "missing mac"}, status=400)
        hass.data.setdefault(DOMAIN, {}).setdefault("models", {})
        if model:
            hass.data[DOMAIN]["models"][mac] = model
        else:
            hass.data[DOMAIN]["models"].pop(mac, None)
        store = Store(hass, STORAGE_VERSION, STORAGE_KEY_MODELS)
        await store.async_save(hass.data[DOMAIN].get("models", {}))
        _LOGGER.info("Model set: %s → %s", mac, model or "(unset)")
        return self.json({"ok": True, "mac": mac, "model": model})


class GreePanelNamesView(HomeAssistantView):
    """Get/set device custom names."""

    url = "/api/gree_ac_cloud/panel/names"
    name = "api:gree_ac_cloud:panel_names"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        names = hass.data.get(DOMAIN, {}).get("device_names", {})
        return self.json(names)

    async def post(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        try:
            body = await request.json()
        except Exception:
            return self.json({"error": "invalid JSON"}, status=400)
        mac = body.get("mac")
        name = body.get("name", "")
        if not mac:
            return self.json({"error": "missing mac"}, status=400)
        hass.data.setdefault(DOMAIN, {}).setdefault("device_names", {})
        if name:
            hass.data[DOMAIN]["device_names"][mac] = name
        else:
            hass.data[DOMAIN]["device_names"].pop(mac, None)
        store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.names")
        await store.async_save(hass.data[DOMAIN].get("device_names", {}))
        _LOGGER.info("Device name set: %s → %s", mac, name or "(unset)")
        return self.json({"ok": True, "mac": mac, "name": name})


# ── Panel HTML ────────────────────────────────────────

PANEL_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Gree AC Cloud</title>
<style>
:root {
  --primary: #03a9f4;
  --primary-glow: rgba(3, 169, 244, 0.25);
  --green: #4caf50;
  --red: #ef5350;
  --yellow: #ffa726;
  --mode-cool: #29b6f6;
  --mode-heat: #ef5350;
  --mode-fan: #66bb6a;
  --mode-dry: #ffa726;
  --mode-auto: #b0bec5;
  --bg: #0f1117;
  --card-bg: linear-gradient(145deg, #1a1d27, #14171f);
  --card-border: rgba(255,255,255,0.06);
  --text: #e8eaed;
  --text2: #9aa0a6;
  --border: rgba(255,255,255,0.08);
}

* { margin:0; padding:0; box-sizing:border-box; }

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: var(--bg);
  color: var(--text);
  padding: 12px;
  min-height: 100vh;
  overflow-x: hidden;
  max-width: 100vw;
}

/* ── header ─────────────────────────────────── */
.header {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 0;
  border-bottom: 1px solid var(--border);
  margin-bottom: 14px;
}
.header-top {
  display: flex;
  align-items: center;
  gap: 8px;
}
.header .icon-ac { font-size: 26px; color: var(--primary); flex-shrink: 0; }
.icon-ac { width:1em; height:1em; display:inline-block; vertical-align:middle; }
.icon-ac svg { width:100%; height:100%; fill:currentColor; }
.header h1 { font-size: 17px; font-weight: 600; letter-spacing: -0.3px; flex-shrink: 0; }
.header .status-badge {
  margin-left: auto;
  font-size: 10px;
  padding: 3px 10px;
  border-radius: 20px;
  background: var(--green);
  font-weight: 600;
  white-space: nowrap;
}

/* ── tab nav ─────────────────────────────────── */
.tab-nav {
  display: flex;
  gap: 4px;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  scrollbar-width: none;
}
.tab-nav::-webkit-scrollbar { display: none; }
.tab-btn {
  flex-shrink: 0;
  padding: 7px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: transparent;
  color: var(--text2);
  cursor: pointer;
  font-size: 11px;
  font-weight: 500;
  transition: all .2s;
  white-space: nowrap;
  -webkit-tap-highlight-color: transparent;
}
.tab-btn:active { background: rgba(255,255,255,0.08); }
.tab-btn.active { background: var(--primary); border-color: var(--primary); color: #fff; }

/* ── device cards ────────────────────────────── */
.devices { display: grid; gap: 12px; }

.card {
  position: relative;
  background: var(--card-bg);
  border-radius: 14px;
  padding: 14px;
  border: 1px solid var(--card-border);
  box-shadow: 0 2px 16px rgba(0,0,0,0.3);
  transition: box-shadow .3s;
  max-width: 100%;
  overflow: hidden;
}
.card.on { box-shadow: 0 2px 16px rgba(0,0,0,0.3), 0 0 30px rgba(3,169,244,0.04); }
.card.on::before {
  content: ''; position: absolute; inset: 0;
  border-radius: 14px;
  background: linear-gradient(135deg, rgba(3,169,244,0.04), transparent 60%);
  pointer-events: none;
}

/* ── card header ──────────────────────────── */
.card-header { margin-bottom: 8px; }
.header-row1 {
  display: flex; align-items: center; gap: 6px;
  margin-bottom: 2px;
}
.header-row1 .name-group {
  display: flex; align-items: center; gap: 6px;
  flex: 1; min-width: 0;
}
.header-row1 .name-group .icon-ac { font-size: 18px; color: var(--primary); flex-shrink: 0; }
.header-row1 h2 {
  font-size: 14px; font-weight: 600;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  flex: 1; cursor: pointer;
}
.header-row1 h2:hover { text-decoration: underline dotted var(--text2); }
.header-row1 .conn-badge {
  font-size: 9px; padding: 2px 8px; border-radius: 10px;
  background: rgba(76,175,80,0.12); color: var(--green); font-weight: 600;
  white-space: nowrap; flex-shrink: 0;
}
.header-row1 .conn-badge.off { background: rgba(239,83,80,0.12); color: var(--red); }
.header-row2 {
  display: flex; align-items: center; gap: 8px;
  padding-left: 24px;
}
.header-row2 .mac-label { font-size: 9px; color: var(--text2); }
body.desktop .card-header { display: flex; align-items: center; gap: 4px; }
body.desktop .header-row1 { flex: 1; min-width: 0; margin-bottom: 0; }
body.desktop .header-row1 .name-group { flex-wrap: wrap; }
body.desktop .header-row1 h2 { flex: initial; max-width: 100%; }
body.desktop .header-row2 { padding-left: 0; }

.model-select {
  font-size: 9px; padding: 3px 6px; border-radius: 6px;
  background: rgba(255,255,255,0.05); border: 1px solid var(--border);
  color: var(--text2); cursor: pointer;
  min-width: 160px; width: auto;
}
@media (min-width: 768px) {
  .model-select { min-width: 200px; }
}
.model-select option { background: #1a1d27; color: var(--text); }

/* ── power display ───────────────────────────── */
.power-row {
  display: flex; gap: 8px; margin: 8px 0 10px;
  font-size: 10px; color: var(--text2); justify-content: center;
  max-width: 100%;
}
.power-row .p-item {
  flex: 1; min-width: 0;
  padding: 5px 6px; background: rgba(255,255,255,0.03);
  border-radius: 8px; border: 1px solid var(--border);
  text-align: center;
}
.power-row .p-item .p-val { font-size: 13px; font-weight: 700; color: var(--yellow); }
.power-row .p-item .p-label { font-size: 8px; text-transform: uppercase; letter-spacing: 0.3px; }

/* ── sensors ─────────────────────────────────── */
.sensors {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px;
  margin-bottom: 12px;
  max-width: 100%;
}
.sensor {
  text-align: center;
  padding: 8px 4px;
  background: rgba(255,255,255,0.03);
  border-radius: 10px;
  border: 1px solid var(--border);
}
.sensor .value { font-size: 18px; font-weight: 700; }
.sensor .value.green { color: var(--green); }
.sensor .value.red { color: var(--red); }
.sensor .label { font-size: 9px; color: var(--text2); margin-top: 2px; text-transform: uppercase; letter-spacing: 0.3px; }

/* ── controls ────────────────────────────────── */
.controls { display: grid; gap: 8px; }

.control-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.control-row label {
  width: 100%;
  font-size: 10px;
  color: var(--text2);
  text-transform: uppercase;
  letter-spacing: 0.4px;
  font-weight: 600;
  padding-bottom: 2px;
}

.btn-group { display: flex; gap: 3px; flex-wrap: wrap; max-width: 100%; }

.btn {
  min-height: 36px;
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: transparent;
  color: var(--text2);
  cursor: pointer;
  font-size: 11px;
  font-weight: 500;
  transition: all .15s;
  -webkit-tap-highlight-color: transparent;
}
.btn:active { background: rgba(255,255,255,0.08); }
.btn.active {
  background: var(--primary);
  border-color: var(--primary);
  color: #fff;
  box-shadow: 0 0 10px var(--primary-glow);
}
.btn.danger.active { background: var(--red); border-color: var(--red); box-shadow: 0 0 10px rgba(239,83,80,0.3); }
.btn.mode-cool.active { background: var(--mode-cool); border-color: var(--mode-cool); }
.btn.mode-heat.active { background: var(--mode-heat); border-color: var(--mode-heat); }
.btn.mode-fan.active { background: var(--mode-fan); border-color: var(--mode-fan); }
.btn.mode-dry.active { background: var(--mode-dry); border-color: var(--mode-dry); }

.temp-control {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  max-width: 100%;
}
.temp-control button {
  width: 40px; height: 40px;
  border-radius: 50%;
  border: 1px solid var(--border);
  background: rgba(255,255,255,0.04);
  color: var(--text);
  font-size: 18px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all .15s;
  -webkit-tap-highlight-color: transparent;
}
.temp-control button:active { background: rgba(255,255,255,0.1); border-color: var(--primary); }
.temp-control .temp-value {
  font-size: 22px;
  font-weight: 700;
  min-width: 48px;
  text-align: center;
}

.switches {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  max-width: 100%;
}
.switch-btn {
  min-height: 32px;
  padding: 6px 10px;
  border-radius: 12px;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--text2);
  font-size: 10px;
  cursor: pointer;
  transition: all .15s;
  -webkit-tap-highlight-color: transparent;
}
.switch-btn.on {
  background: rgba(3,169,244,0.12);
  border-color: var(--primary);
  color: var(--primary);
}
.switch-btn:active { opacity: 0.7; }

/* ── setup message ───────────────────────────── */
.setup-msg {
  text-align: center;
  padding: 40px 16px;
  color: var(--text2);
}
.setup-msg .icon-ac { font-size: 48px; margin-bottom: 12px; }
.setup-msg h2 { margin-bottom: 6px; color: var(--text); font-size: 16px; }

/* ── wiki tab ────────────────────────────────── */
.wiki { font-size: 12px; }
.wiki h3 { font-size: 14px; color: var(--primary); margin: 16px 0 6px; }
.wiki h4 { font-size: 12px; margin: 10px 0 4px; color: var(--text); }
.wiki table.wt { display:block; width:100%; border-collapse:collapse; font-size:11px; overflow-x:auto; -webkit-overflow-scrolling:touch; }
.wiki table.wt th, .wiki table.wt td { text-align:left; padding:5px 6px; border-bottom:1px solid var(--border); }
.wiki table.wt th { background:rgba(255,255,255,0.05); color:var(--primary); font-weight:500; white-space:nowrap; }
.wiki table.wt td:first-child { font-family:monospace; white-space:nowrap; }
.wiki code { background:rgba(255,255,255,0.08); padding:1px 4px; border-radius:3px; font-size:10px; }

/* ── logs tab ────────────────────────────────── */
.log-toolbar { display:flex; gap:6px; margin-bottom:8px; align-items:center; flex-wrap:wrap; }
.log-toggle { font-size:11px; color:var(--text2); display:flex; align-items:center; gap:4px; cursor:pointer; }
#logCount { font-size:10px; color:var(--text2); margin-left:auto; white-space:nowrap; }
#logContainer { font-size: 10px; font-family: monospace; line-height: 1.5; max-height:60vh; overflow-y:auto; }
.log-entry { padding: 3px 4px; border-bottom: 1px solid var(--border); white-space: pre-wrap; word-break: break-all; }
.log-entry .log-time { color: var(--text2); margin-right: 4px; }
.log-entry .log-debug { color: #666; }
.log-entry .log-info { color: var(--primary); }
.log-entry .log-warning { color: var(--yellow); }
.log-entry .log-error { color: var(--red); }
.log-entry .log-critical { color: var(--red); font-weight: bold; }

/* ── markdown content (readme/changelog) ──────── */
.md-content {
  font-size: 12px; line-height: 1.6; color: var(--text);
}
.md-content h1 { font-size: 18px; font-weight: 600; margin: 16px 0 6px; color: var(--primary); }
.md-content h2 { font-size: 15px; font-weight: 600; margin: 14px 0 5px; color: var(--primary); }
.md-content h3 { font-size: 13px; font-weight: 600; margin: 12px 0 4px; }
.md-content h4 { font-size: 12px; font-weight: 600; margin: 10px 0 4px; color: var(--yellow); }
.md-content p { margin: 5px 0; }
.md-content a { color: var(--primary); text-decoration: none; word-break: break-all; }
.md-content code {
  background: rgba(255,255,255,0.07); padding: 1px 4px; border-radius: 3px; font-size: 11px;
}
.md-content pre {
  background: rgba(0,0,0,0.3); border: 1px solid var(--border); border-radius: 8px;
  padding: 10px; overflow-x: auto; margin: 6px 0;
}
.md-content pre code { background: none; padding: 0; font-size: 11px; }
.md-content table { width: 100%; border-collapse: collapse; margin: 6px 0; font-size: 11px; }
.md-content th, .md-content td {
  text-align: left; padding: 4px 6px; border-bottom: 1px solid var(--border);
}
.md-content th { background: rgba(255,255,255,0.05); color: var(--primary); font-weight: 500; }
.md-content tr:nth-child(even) { background: rgba(255,255,255,0.02); }
.md-content ul, .md-content ol { margin: 5px 0; padding-left: 18px; }
.md-content li { margin: 2px 0; }
.md-content hr { border: none; border-top: 1px solid var(--border); margin: 12px 0; }
.md-content blockquote {
  border-left: 3px solid var(--primary); padding: 4px 10px; margin: 6px 0;
  background: rgba(3,169,244,0.04); color: var(--text2); border-radius: 0 6px 6px 0;
}
.md-content img { max-width: 100%; border-radius: 6px; margin: 6px 0; height: auto; }

/* ── footer ──────────────────────────────────── */
.server-info {
  font-size: 11px;
  color: var(--text2);
  margin-top: 16px;
  text-align: center;
  padding-top: 12px;
  border-top: 1px solid var(--border);
}

/* ── desktop overrides (via JS class for iframe safety) ── */
body.desktop .header { flex-direction: row; align-items: center; gap: 12px; padding: 16px 0; margin-bottom: 20px; }
body.desktop .control-row label { width: auto; min-width: 60px; padding-bottom: 0; }

/* ── tablet / desktop enhancements ───────────── */
@media (min-width: 600px) {
  body { padding: 20px; }
  .header { flex-direction: row; align-items: center; gap: 12px; padding: 16px 0; margin-bottom: 20px; }
  .header h1 { font-size: 20px; }
  .header .icon-ac { font-size: 30px; }
  .tab-nav { gap: 6px; }
  .tab-btn { padding: 8px 16px; font-size: 12px; }
  .card { padding: 20px; }
  .card-header { margin-bottom: 14px; }
  .header-row1 h2 { font-size: 15px; }
  .sensors { gap: 8px; margin-bottom: 16px; }
  .sensor .value { font-size: 22px; }
  .control-row { gap: 8px; }
  .control-row label { width: auto; min-width: 60px; padding-bottom: 0; }
  .btn-group { gap: 4px; }
  .btn { min-height: 32px; padding: 5px 12px; }
  .temp-control button { width: 36px; height: 36px; font-size: 16px; }
  .power-row { gap: 12px; }
  .power-row .p-item { padding: 6px 14px; }
  .power-row .p-item .p-val { font-size: 15px; }
  .setup-msg { padding: 60px 20px; }
  .setup-msg .icon-ac { font-size: 64px; }
  #logContainer { font-size: 11px; max-height:70vh; }
  .md-content { font-size: 13px; }
  .md-content h1 { font-size: 20px; }
}
</style>
</head>
<body>

<div class="header">
  <div class="header-top">
    <span class="icon-ac"><svg viewBox="0 0 24 24"><path d="M22 11h-4.17l3.24-3.24-1.41-1.42L15 11h-2V9l4.66-4.66-1.42-1.41L13 6.17V2h-2v4.17L7.76 2.93 6.34 4.34 11 9v2H9L4.34 6.34 2.93 7.76 6.17 11H2v2h4.17l-3.24 3.24 1.41 1.42L9 13h2v2l-4.66 4.66 1.42 1.41L11 17.83V22h2v-4.17l3.24 3.24 1.42-1.41L13 15v-2h2l4.66 4.66 1.41-1.42L17.83 13H22z"/></svg></span>
    <h1>Gree AC Cloud</h1>
    <span class="status-badge" id="statusBadge">loading...</span>
  </div>
  <nav class="tab-nav">
    <button class="tab-btn active" data-tab="devices" onclick="switchTab('devices')">Devices</button>
    <button class="tab-btn" data-tab="wiki" onclick="switchTab('wiki')">Wiki</button>
    <button class="tab-btn" data-tab="logs" onclick="switchTab('logs')">Logs</button>
    <button class="tab-btn" data-tab="readme" onclick="switchTab('readme')">README</button>
    <button class="tab-btn" data-tab="changelog" onclick="switchTab('changelog')">Changelog</button>
  </nav>
</div>

<div id="content">
  <div id="tab-devices">
    <div class="setup-msg" id="setupMsg">
      <span class="icon-ac"><svg viewBox="0 0 24 24"><path d="M22 11h-4.17l3.24-3.24-1.41-1.42L15 11h-2V9l4.66-4.66-1.42-1.41L13 6.17V2h-2v4.17L7.76 2.93 6.34 4.34 11 9v2H9L4.34 6.34 2.93 7.76 6.17 11H2v2h4.17l-3.24 3.24 1.41 1.42L9 13h2v2l-4.66 4.66 1.42 1.41L11 17.83V22h2v-4.17l3.24 3.24 1.42-1.41L13 15v-2h2l4.66 4.66 1.41-1.42L17.83 13H22z"/></svg></span>
      <h2>No devices found</h2>
      <p>Configure the Gree AC Cloud integration in<br>
      Settings → Devices &amp; services → Add integration</p>
    </div>
    <div class="devices" id="devices"></div>
  </div>
  <div id="tab-wiki" style="display:none;">
    <div class="wiki">
      <h2 style="margin:0 0 4px;font-size:18px;font-weight:500;">Parameter Reference</h2>
      <p style="color:var(--text-secondary);font-size:13px;margin-bottom:16px;">XE7A-24/HC wired controller parameters from the official manual. These are accessed <strong>directly on the physical wired controller</strong>, not from HA.</p>

      <h3 style="color:var(--yellow);font-size:14px;">⚙ How to Access Settings</h3>
      <p style="color:var(--text-secondary);font-size:12px;margin-bottom:8px;">
      Per accedere alle impostazioni sul controller XE7A-24/HC:
      </p>
      <ol style="color:var(--text-secondary);font-size:12px;line-height:1.7;margin:0 0 16px 18px;">
      <li>Premere <strong>FUNCTION</strong> per 5 secondi per entrare in visualizzazione parametri (<strong>C00</strong>)</li>
      <li>Usa <strong>+ / -</strong> per scorrere i codici parametro (C00–C23)</li>
      <li><strong>ENTER</strong> per uscire</li>
      </ol>
      <p style="color:var(--text-secondary);font-size:12px;margin-bottom:8px;">
      Per modificare i parametri (codici <strong>P</strong>):
      </p>
      <ol style="color:var(--text-secondary);font-size:12px;line-height:1.7;margin:0 0 16px 18px;">
      <li>Da <strong>C00</strong>, premi <strong>FUNCTION</strong> di nuovo per 5 secondi → <strong>P00</strong></li>
      <li>Usa <strong>+ / -</strong> per selezionare il parametro</li>
      <li>Premi <strong>MODE</strong> per entrare in modifica (valore lampeggia)</li>
      <li><strong>+ / -</strong> per regolare, <strong>ENTER</strong> per confermare</li>
      <li><strong>ENTER</strong> per tornare indietro e uscire</li>
      </ol>

      <h3>Monitor (View C00–C23)</h3>
      <p style="color:var(--text-secondary);font-size:12px;">Read-only. C00 è la schermata iniziale — usa +/- per navigare agli altri codici.</p>
      <table class="wt"><tr><th>Code</th><th>Nome</th><th>Cosa mostra / Esempio</th><th>Range</th></tr>
      <tr><td>C00</td><td>Schermata iniziale</td><td>N° progetto unità interna attuale. Es: premi FUNCTION 5s → "C00" + numero (es. 1).</td><td>0–4</td></tr>
      <tr><td>C01</td><td>Diagnosi unità guaste</td><td>Premi MODE in C01, usa +/- per selezionare unità — quella selezionata emette bip, mostra errori nel campo temperatura. Es: se un'unità ha errore E1, la trovi C01.</td><td>1–255</td></tr>
      <tr><td>C03</td><td>Unità interne in rete</td><td>Quante unità interne ci sono nella rete di sistema. Es: 4 = quattro unità collegate.</td><td>1–100</td></tr>
      <tr><td>C06</td><td>Modalità prioritaria</td><td>00=normale, 01=prioritario. Es: in caso di sovraccarico, l'unità prioritaria continua, le altre si spengono.</td><td>00–01</td></tr>
      <tr><td>C07</td><td>Temperatura ambiente interna</td><td>Temp rilevata dall'unità interna. Es: 24.5°C.</td><td>—</td></tr>
      <tr><td>C08</td><td>Promemoria pulizia filtro</td><td>Giorni di funzionamento prima dell'avviso filtro. Es: 90 = promemoria dopo 90gg.</td><td>4–416 gg</td></tr>
      <tr><td>C09</td><td>Indirizzo controller</td><td>01=principale, 02=secondario. Es: 2 controller → mostra 01 sul main.</td><td>01, 02</td></tr>
      <tr><td>C11</td><td>Unità controllate</td><td>Quante unità questo controller comanda. Es: 2 unità in un open space.</td><td>1–16</td></tr>
      <tr><td>C12</td><td>Temperatura esterna</td><td>Sensore unità esterna. Es: 35°C d'estate.</td><td>—</td></tr>
      <tr><td>C17</td><td>Umidità interna</td><td>Umidità relativa (InHumi in HA). Es: 55% = comfort, >70% = umido.</td><td>0–100%</td></tr>
      <tr><td>C18</td><td>One-key project</td><td>Mostra su tutti i controller quali unità comandano. Premi MODE in C18, +/- per scorrere.</td><td>1–255</td></tr>
      <tr><td>C20</td><td>Aria fresca outlet</td><td>Temp uscita aria fresca (solo unità aria fresca). Es: 18°C.</td><td>—</td></tr>
      <tr><td>C23</td><td>Versione firmware</td><td>Versione software controller. Es: v3.02.</td><td>text</td></tr>
      </table>

      <h3>Settings (P01–P87)</h3>
      <p style="color:var(--text-secondary);font-size:12px;">
      ⚠ I parametri contrassegnati con <strong>"installazione"</strong> vanno modificati solo all'installazione iniziale.
      Altri parametri (timer, unità temp, step) sono sicuri da cambiare in qualsiasi momento.
      </p>

      <h4>Generali — sicuri da modificare</h4>
      <table class="wt"><tr><th>Code</th><th>Nome</th><th>Cosa fa / Esempio pratico</th><th>Valori</th></tr>
      <tr><td>P16</td><td>Unità temperatura</td><td>Passa da °C a °F. Es: ospiti americani → 01 per Fahrenheit.</td><td>00=°C, 01=°F</td></tr>
      <tr><td>P33</td><td>Tipo timer</td><td>Timer generale (conta alla rovescia) vs orologio (accensione a orario fisso). Es: spegnimento dopo 2h = generale.</td><td>00=generale, 01=orologio</td></tr>
      <tr><td>P34</td><td>Ripeti timer orario</td><td>Il timer orario si ripete ogni giorno? Es: accensione 7:00 tutti i giorni = 01.</td><td>00=una volta, 01=giornaliero</td></tr>
      <tr><td>P82</td><td>Formato ora</td><td>24h o 12h (AM/PM). Es: 01 mostra "3:00 PM" invece di "15:00".</td><td>00=24h, 01=12h</td></tr>
      <tr><td>P87</td><td>Step temperatura</td><td>Step 0.5°C o 1°C con +/-. Es: 01 passa da 24° a 24.5°. Nota: HA usa sempre 0.5°C.</td><td>00=1°C, 01=0.5°C</td></tr>
      </table>

      <h4>Installazione — da configurare all'avvio</h4>
      <table class="wt"><tr><th>Code</th><th>Nome</th><th>Cosa fa / Esempio pratico</th><th>Valori</th></tr>
      <tr><td>P10</td><td>Unità principale</td><td>Imposta questa unità come principale (icona si accende). Es: in un sistema master-slave, imposta 01 su quella principale. Non applicabile a unità parziali.</td><td>00=no change, 01=main</td></tr>
      <tr><td>P11</td><td>Ricevitore IR</td><td>Abilita il telecomando IR. Es: telecomando non funziona? Controlla che P11 sia 01.</td><td>00=off, 01=on</td></tr>
      <tr><td>P13</td><td>Indirizzo controller</td><td>Con 2 controller sullo stesso gruppo: 01=principale, 02=secondario. Il secondario imposta solo il proprio indirizzo.</td><td>01=main, 02=secondary</td></tr>
      <tr><td>P14</td><td>Unità gruppo comandato</td><td>Quante unità interne questo controller comanda. Es: 2 unità in un open space = 02.</td><td>00=off, 01–16</td></tr>
      <tr><td>P30</td><td>Pressione statica (ESP)</td><td>9 livelli (P1-P9) per condotti. Default P5=25Pa (24k) / 37Pa (29k). Range 0-160Pa. P9 massima pressione.<br/><b>Mapping ESP:</b> P1=S05/S03/S02/S01, P5=S09/S07/S06/S05 (default), P9=S13/S11/S10/S09.<br/>Es: condotti lunghi 15m → P7 o P8.</td><td>01–09</td></tr>
      <tr><td>P31</td><td>Soffitto alto</td><td>Soffitto >3m? 01 migliora distribuzione aria. Es: capannone con soffitto 4m → 01.</td><td>00=standard, 01=alto</td></tr>
      </table>

      <h4>HVAC — comportamento climatico</h4>
      <table class="wt"><tr><th>Code</th><th>Nome</th><th>Cosa fa / Esempio pratico</th><th>Valori</th></tr>
      <tr><td>P37</td><td>Auto cool temp</td><td>In Auto, temperatura per raffrescamento. Es: 26°C in estate — sopra questa soglia parte il raffrescamento.</td><td>17–30°C</td></tr>
      <tr><td>P38</td><td>Auto heat temp</td><td>In Auto, temperatura per riscaldamento. Es: 20°C in inverno — sotto questa soglia parte il riscaldamento. Differenza Cool-Heat ≥1°C.</td><td>16–29°C</td></tr>
      <tr><td>P43</td><td>Mod. funzionamento prioritaria</td><td>01=funzionamento prioritario. In caso di potenza elettrica insufficiente, le unità prioritarie continuano, le altre spente.</td><td>00=normale, 01=prioritario</td></tr>
      <tr><td>P46</td><td>Annullamento filtro</td><td>Resetta il tempo accumulato dopo pulizia filtro. Es: hai pulito i filtri → imposta 01 per resettare.</td><td>00=no, 01=annulla</td></tr>
      <tr><td>P49</td><td>Angolo ripresa aria</td><td>Angolo apertura piastra ritorno aria (solo unità con piastra). Es: 02 = 30° per flusso bilanciato.</td><td>01=25°, 02=30°, 03=35°</td></tr>
      <tr><td>P78</td><td>Antivento freddo</td><td>Ritardo ventola in riscaldamento per evitare aria fredda all'avvio. Es: 01=300s (5 min) aspetta che la batteria sia calda.</td><td>00=180s, 01=300s, 02=420s, 03=600s</td></tr>
      </table>

      <h4>Aria fresca (Fresh Air) — solo per unità aria fresca</h4>
      <table class="wt"><tr><th>Code</th><th>Nome</th><th>Cosa fa / Esempio</th><th>Valori</th></tr>
      <tr><td>P50</td><td>Fresh air cool temp</td><td>Temp aria in uscita in modalità raffrescamento. Es: 18°C per aria fresca fredda.</td><td>16–30 °C</td></tr>
      <tr><td>P51</td><td>Fresh air heat temp</td><td>Temp aria in uscita in modalità riscaldamento. Es: 22°C per aria fresca tiepida.</td><td>16–30 °C</td></tr>
      <tr><td>P54</td><td>Controllo comune</td><td>01=accesa/spenta assieme all'unità interna principale. Es: l'aria fresca si spegne quando l'AC si spegne.</td><td>00=senza, 01=con</td></tr>
      </table>

      <h4>Pulizia e deumidifica</h4>
      <table class="wt"><tr><th>Code</th><th>Nome</th><th>Cosa fa / Esempio</th><th>Valori</th></tr>
      <tr><td>P83</td><td>Cool mode ctrl (I-FEEL)</td><td>00=controllo temperatura ambiente, 01=controllo correzione temp+umidità. Es: 01 se hai funzione I-FEEL (sensore controller).</td><td>00=temp, 01=temp+umidità</td></tr>
      <tr><td>P84</td><td>Dry mode ctrl</td><td>00=controllo temperatura, 01=controllo umidità. Es: in cantina umida, 01 per target preciso.</td><td>00=temp, 01=umidità</td></tr>
      <tr><td>P85</td><td>Dry humidity temp</td><td>Setpoint per controllo umidità (solo se P84=01). Es: 16 = 60% umidità target (valore ×2 circa).</td><td>10–30°C</td></tr>
      <tr><td>P86</td><td>Pulizia automatica</td><td>Dopo spegnimento: 01=normale, 02=rapida, 03=accurata. Es: 03 per asciugare bene la batteria.</td><td>01=normale, 02=rapida, 03=accurata</td></tr>
      <tr><td>P76</td><td>Filtro PM2.5</td><td>Abilita filtro PM2.5 se installato. Es: hai modulo filtro? Imposta 01.</td><td>00=no, 01=sì</td></tr>
      </table>

      <h4>Recovery — ripresa dopo blackout</h4>
      <table class="wt"><tr><th>Code</th><th>Nome</th><th>Cosa fa / Esempio pratico</th><th>Valori</th></tr>
      <tr><td>P71</td><td>Funzione ripristino</td><td>Dopo blackout riparte con impostazioni precedenti. Es: consigliato 01 per non tornare e trovare tutto spento.</td><td>00=off, 01=on</td></tr>
      <tr><td>P72</td><td>Limite max ripristino</td><td>Temp max al riavvio. Es: 26°C in estate per risparmiare. Differenza P72-P73 ≥4°C.</td><td>20–30 °C</td></tr>
      <tr><td>P73</td><td>Limite min ripristino</td><td>Temp min al riavvio. Es: 20°C in inverno.</td><td>16–26 °C</td></tr>
      <tr><td>P74</td><td>Ripristino scheda</td><td>Con scheda hotel/gate: reinserendo la scheda riparte come prima. Es: 00 = inserendo scheda non cambia stato.</td><td>00=no, 01=sì</td></tr>
      </table>

      <h3>MQTT Protocol</h3>
      <p style="color:var(--text-secondary);font-size:12px;">Broker: <code>18.185.150.155:1984</code> (TLS), AES-128-ECB per device key.</p>
      <p style="color:var(--text-secondary);font-size:12px;">Topics: <code>request/{parent_mac}</code> → <code>response/{parent_mac}/#</code></p>

      <h3>Entities HA</h3>
      <table class="wt"><tr><th>Platform</th><th>Key</th><th>Description</th></tr>
      <tr><td>climate</td><td>—</td><td>Acceso/spento, modo (Auto/Cool/Heat/Fan/Dry), ventola, swing, temperatura</td></tr>
      <tr><td>sensor</td><td>InTem</td><td>Temperatura ambiente interna</td></tr>
      <tr><td>sensor</td><td>OutTem</td><td>Temperatura esterna</td></tr>
      <tr><td>sensor</td><td>SetDeciTem</td><td>Setpoint temperatura (decimi di °C)</td></tr>
      <tr><td>sensor</td><td>InHumi</td><td>Umidità interna (potrebbe non essere disponibile)</td></tr>
      <tr><td>switch</td><td>Health</td><td>Health mode</td></tr>
      <tr><td>switch</td><td>Quiet</td><td>Quiet (silenzioso)</td></tr>
      <tr><td>switch</td><td>Tur</td><td>Turbo (massima potenza)</td></tr>
      <tr><td>switch</td><td>StHt</td><td>Strong heat (riscaldamento intenso)</td></tr>
      <tr><td>switch</td><td>Blo</td><td>Blow (ventilazione dopo spegnimento)</td></tr>
      <tr><td>switch</td><td>SvSt</td><td>Energy saving (risparmio energetico)</td></tr>
      <tr><td>switch</td><td>SlpMod</td><td>Sleep (notte: regola temperatura gradualmente)</td></tr>
      <tr><td>switch</td><td>Lig</td><td>Light (retroilluminazione display)</td></tr>
      <tr><td>binary_sensor</td><td>Err</td><td>Errore attivo</td></tr>
      <tr><td>binary_sensor</td><td>Filter</td><td>Promemoria pulizia filtro</td></tr>
      </table>

      <h3>Codici Errore VRF</h3>
      <p style="color:var(--text-secondary);font-size:12px;">Quando il binary_sensor <strong>Err</strong> è ON, il device ha un problema. I codici appaiono sul display del controller wired.</p>

      <h4 style="color:var(--yellow);font-size:12px;">Unità Esterna — Protezioni (E)</h4>
      <table class="wt"><tr><th>Codice</th><th>Significato</th><th>Esempio / Cosa fare</th></tr>
      <tr><td>E0</td><td>Errore unità esterna</td><td>Anomalia generica — spegnere e riaccendere. Se persiste, chiamare assistenza.</td></tr>
      <tr><td>E1</td><td>Protezione alta pressione</td><td>Pressione mandata troppo alta. Es: filtri sporchi o condensa ostruita → pulire filtri e verificare flusso aria.</td></tr>
      <tr><td>E2</td><td>Protezione sottotemperatura scarico</td><td>Gas di scarico compressore troppo freddo. Es: carica refrigerante insufficiente → verificare perdite.</td></tr>
      <tr><td>E3</td><td>Protezione bassa pressione</td><td>Pressione aspirazione troppo bassa. Es: possibile perdita refrigerante → chiamare tecnico.</td></tr>
      <tr><td>E4</td><td>Protezione sovratemperatura scarico</td><td>Gas di scarico compressore troppo caldo (>limite). Es: carica insufficiente o restrizione nel circuito.</td></tr>
      <tr><td>Ed</td><td>Protezione bassa temp modulo comando</td><td>Modulo di comando esterno troppo freddo. Es: verificare ambiente installazione unità esterna.</td></tr>
      </table>

      <h4 style="color:var(--yellow);font-size:12px;">Unità Esterna — Sensori (F)</h4>
      <table class="wt"><tr><th>Codice</th><th>Significato</th><th>Esempio / Cosa fare</th></tr>
      <tr><td>F0</td><td>Scheda principale esterna</td><td>Malfunzionamento PCB esterna. Es: scheda bruciata o corto → sostituire scheda.</td></tr>
      <tr><td>F1</td><td>Sensore pressione alta</td><td>Sensore di pressione lato alta danneggiato. Es: sostituire sensore.</td></tr>
      <tr><td>F2</td><td>Sensore temp. ingresso scambiatore</td><td>Tubo ingresso scambiatore a piastre. Es: sensore scollegato o guasto.</td></tr>
      <tr><td>F3</td><td>Sensore pressione bassa</td><td>Sensore pressione lato bassa. Es: sostituire sensore.</td></tr>
      <tr><td>F4</td><td>Sensore temp. uscita scambiatore</td><td>Tubo uscita scambiatore a piastre. Es: verificare connessione sensore.</td></tr>
      <tr><td>F5</td><td>Sensore temp. scarico compressore 1</td><td>T sensore mandata compressore 1. Es: sensore interrotto → sostituire.</td></tr>
      <tr><td>F6–FA</td><td>Sensore scarico compressore 2–6</td><td>Idem per compressori aggiuntivi (sistemi multi-compressore).</td></tr>
      <tr><td>FC/FL/FE/FF/FJ</td><td>Sensore corrente compressore 2–6</td><td>Sensore di corrente su compressore N. Es: compressore non assorbe → cablaggio o sensore.</td></tr>
      </table>

      <h4 style="color:var(--yellow);font-size:12px;">Unità Esterna — Pannello Compressore (P/H)</h4>
      <table class="wt"><tr><th>Codice</th><th>Significato</th><th>Esempio / Cosa fare</th></tr>
      <tr><td>P0</td><td>Errore pannello comando compressore</td><td>Driver inverter compressore guasto. Es: modulo IPM bruciato → sostituire pannello.</td></tr>
      <tr><td>P1</td><td>Malfunzionamento pannello comando</td><td>Anomalia generica driver compressore. Es: reset e riprovare.</td></tr>
      <tr><td>P2</td><td>Protezione alimentazione</td><td>Tensione alimentazione driver fuori range. Es: verificare alimentazione 380V/220V.</td></tr>
      <tr><td>P3</td><td>Reset modulo pannello</td><td>Reset anomalo del modulo. Es: disturbo elettrico o surriscaldamento.</td></tr>
      <tr><td>H0</td><td>Errore pannello ventola</td><td>Driver motore ventola esterna. Es: ventola non gira → controllare cablaggio.</td></tr>
      <tr><td>H1</td><td>Malfunzionamento pannello ventola</td><td>Anomalia driver ventola. Es: modulo IPM ventola.</td></tr>
      <tr><td>H2</td><td>Protezione alimentaz. ventola</td><td>Tensione driver ventola fuori range.</td></tr>
      </table>

      <h4 style="color:var(--yellow);font-size:12px;">Unità Esterna — Compressore / Sistema (J, b)</h4>
      <table class="wt"><tr><th>Codice</th><th>Significato</th><th>Esempio / Cosa fare</th></tr>
      <tr><td>J1–J6</td><td>Sovracorrente compressore N</td><td>Compressore assorbe troppa corrente. Es: compressore bloccato o refrigerante liquido in aspirazione.</td></tr>
      <tr><td>J7</td><td>Perdita compressione valvola 4 vie</td><td>Valvola di inversione ciclo che perde. Es: sostituire valvola.</td></tr>
      <tr><td>J8</td><td>Sovrapressione sistema</td><td>Pressione troppo alta in qualsiasi condizione. Es: carica refrigerante eccessiva.</td></tr>
      <tr><td>J9</td><td>Sottopressione sistema</td><td>Pressione troppo bassa. Es: perdita refrigerante.</td></tr>
      <tr><td>JL</td><td>Sotto/sovrapressione</td><td>Protezione pressione anomala generale.</td></tr>
      <tr><td>b1</td><td>Sensore temp. ambiente esterna</td><td>Sensore T esterna guasto. Es: mostra -99°C → sostituire sensore.</td></tr>
      <tr><td>b2</td><td>Sensore sbrinamento 1</td><td>Sensore temperatura batteria esterna 1.</td></tr>
      <tr><td>b3</td><td>Sensore sbrinamento 2</td><td>Sensore temperatura batteria esterna 2.</td></tr>
      <tr><td>b4</td><td>Sensore sottoraffreddatore liquido</td><td>T uscita liquido sottoraffreddatore.</td></tr>
      <tr><td>b5</td><td>Sensore sottoraffreddatore gas</td><td>T uscita gas sottoraffreddatore.</td></tr>
      </table>

      <h4 style="color:var(--yellow);font-size:12px;">Unità Interna (L, d, y, o)</h4>
      <table class="wt"><tr><th>Codice</th><th>Significato</th><th>Esempio / Cosa fare</th></tr>
      <tr><td>L0</td><td>Errore unità interna</td><td>Anomalia generica unità interna. Es: resettare e riprovare.</td></tr>
      <tr><td>L1</td><td>Protezione ventola interna</td><td>Ventola interna bloccata o sovracorrente. Es: verificare ventola e cablaggio.</td></tr>
      <tr><td>L2</td><td>Protezione E-heater</td><td>Resistenza elettrica integrativa in protezione. Es: sovratemperatura.</td></tr>
      <tr><td>L4</td><td>Alimentazione comando a filo</td><td>Controller wired non alimentato correttamente. Es: verificare collegamento H1/H2.</td></tr>
      <tr><td>L5</td><td>Protezione antigelo</td><td>Rischio congelamento batteria. Es: temperatura batteria < 0°C → unità si ferma per proteggersi.</td></tr>
      <tr><td>L6</td><td>Conflitto modalità</td><td>Un unità in Cool e l'altra in Heat sulla stessa rete. Es: tutte le unità devono essere nella stessa modalità.</td></tr>
      <tr><td>L7</td><td>Nessuna unità principale</td><td>Manca unità interna principale nella rete. Es: impostare P10=01 su almeno un'unità.</td></tr>
      <tr><td>LA</td><td>Incompatibilità unità interne</td><td>Unità di modelli diversi non compatibili sulla stessa rete.</td></tr>
      <tr><td>LH</td><td>Scarsa qualità aria</td><td>Avvertimento: sensore CO2 o PM2.5 rileva aria insalubre.</td></tr>
      <tr><td>d1</td><td>Scheda elettronica unità interna</td><td>PCB interna guasta. Es: scheda bruciata → sostituire.</td></tr>
      <tr><td>d3</td><td>Sensore temperatura ambiente</td><td>Sensore T interna guasto. Es: mostra 0°C o 99°C → sostituire sensore.</td></tr>
      <tr><td>d4</td><td>Sensore temp. tubo ingresso</td><td>Sensore T batteria ingresso. Es: sensore aperto o corto.</td></tr>
      <tr><td>d5</td><td>Sensore temp. tubo centrale</td><td>Sensore T batteria centrale.</td></tr>
      <tr><td>d6</td><td>Sensore temp. tubo uscita</td><td>Sensore T batteria uscita.</td></tr>
      <tr><td>d7</td><td>Sensore umidità</td><td>Sensore umidità interna guasto.</td></tr>
      <tr><td>dH</td><td>Scheda elettronica controller</td><td>PCB del comando a filo guasta. Es: display danneggiato o touch non risponde → sostituire controller.</td></tr>
      <tr><td>dL</td><td>Sensore temp. aria uscita</td><td>T sensore mandata aria.</td></tr>
      </table>

      <h4 style="color:var(--yellow);font-size:12px;">Comunicazione / Sistema (C, U)</h4>
      <table class="wt"><tr><th>Codice</th><th>Significato</th><th>Esempio / Cosa fare</th></tr>
      <tr><td>C0</td><td>Comunicazione unità int-est / controller</td><td>Bus di comunicazione tra unità interna, esterna o controller interrotto. Es: verificare cablaggio e terminazioni.</td></tr>
      <tr><td>C4</td><td>Nessuna unità interna</td><td>Il sistema non rileva unità interne. Es: verificare indirizzi DIP switch.</td></tr>
      <tr><td>C5</td><td>Conflitto codici progetto</td><td>Due unità interne hanno lo stesso codice progetto. Es: verificare impostazione indirizzi.</td></tr>
      <tr><td>C7</td><td>Comunicazione scambiatore modalità</td><td>Errore di comunicazione con scambiatore di modalità.</td></tr>
      <tr><td>CH</td><td>Capacità nominale troppo alta</td><td>Configurazione capacità superiore al limite. Es: verificare DIP switch capacità.</td></tr>
      <tr><td>CL</td><td>Capacità nominale troppo bassa</td><td>Configurazione capacità inferiore al limite.</td></tr>
      <tr><td>U2</td><td>Codice capacità/cappuccio errato</td><td>Cappuccio ponticello o codice capacità unità esterna errato.</td></tr>
      <tr><td>U4</td><td>Insufficienza refrigerante</td><td>Carica refrigerante troppo bassa. Es: chiamare tecnico per verifica perdite.</td></tr>
      <tr><td>U8</td><td>Malfunzionamento tubo unità interna</td><td>Sensore temperatura tubo anomalo.</td></tr>
      <tr><td>Ud</td><td>Pannello comando collegamento rete</td><td>Errore pannello di comando nella connessione alla rete.</td></tr>
      </table>
      <p style="color:var(--text2);font-size:11px;margin-top:4px;">Nota: i codici sopra sono tratti dal manuale ufficiale XE7A-24/HC (sezioni 6.1.1–6.1.3). Possono variare in base al firmware e alla configurazione VRF.</p>

      <h3>Codici Errore U-Match (Installation Manual)</h3>
      <p style="color:var(--text-secondary);font-size:12px;">Questi sono i codici del manuale di installazione delle unità canalizzabili U-Match (GUD series). Possono sovrapporsi o differire dai codici VRF del controller.</p>

      <h4 style="color:var(--yellow);font-size:12px;">Comunicazione & Sensori (C, d)</h4>
      <table class="wt"><tr><th>Codice</th><th>Significato</th><th>Esempio / Cosa fare</th></tr>
      <tr><td>C0</td><td>Comunicazione controller ↔ unità interna</td><td>Cavo H1/H2 non collegato o danneggiato. Es: display spento → verificare cablaggio 2x0.75mm².</td></tr>
      <tr><td>C1</td><td>Sensore temperatura ambiente interna</td><td>Sonda T interna guasta. Es: mostra -99°C → sostituire sensore.</td></tr>
      <tr><td>C2</td><td>Sensore temperatura evaporatore</td><td>Sonda T batteria interna. Es: sensore aperto o corto.</td></tr>
      <tr><td>C3</td><td>Sensore temperatura condensatore</td><td>Sonda T batteria esterna.</td></tr>
      <tr><td>C6</td><td>Sensore temperatura scarico compressore</td><td>Sonda T mandata compressore.</td></tr>
      <tr><td>C7</td><td>Sensore meso-temperatura condensatore</td><td>Sonda T intermedia batteria esterna.</td></tr>
      <tr><td>CE</td><td>Sensore temperatura comando a filo</td><td>Sensore locale del controller guasto. Es: funzione I-FEEL non disponibile.</td></tr>
      <tr><td>PF</td><td>Sensore temperatura pannello comando</td><td>Sonda T scheda elettronica unità interna.</td></tr>
      <tr><td>CC (dc)</td><td>Sensore temperatura aspirazione compressore</td><td>Sonda T ritorno gas compressore.</td></tr>
      <tr><td>dH</td><td>Scheda elettronica controller</td><td>PCB comando a filo danneggiata → sostituire.</td></tr>
      <tr><td>dJ</td><td>Protezione sequenza/fase</td><td>Inversione o mancanza fase su alimentazione 3ph. Es: invertire due fasi.</td></tr>
      <tr><td>C4</td><td>Cappuccio ponticello esterno</td><td>Jumper capacità unità esterna non inserito o errato.</td></tr>
      <tr><td>CJ</td><td>Cappuccio ponticello interno</td><td>Jumper capacità unità interna errato.</td></tr>
      </table>

      <h4 style="color:var(--yellow);font-size:12px;">Protezioni Unità (E, H)</h4>
      <table class="wt"><tr><th>Codice</th><th>Significato</th><th>Esempio / Cosa fare</th></tr>
      <tr><td>E0</td><td>Errore ventola interna</td><td>Ventola interna bloccata o guasta. Es: motore DC non gira → sostituire.</td></tr>
      <tr><td>E1</td><td>Protezione alta pressione</td><td>Pressione mandata troppo alta. Es: filtri sporchi o condotti ostruiti.</td></tr>
      <tr><td>E2</td><td>Protezione antigelo</td><td>Batteria interna < 0°C. Es: flusso aria insufficiente o filtro sporco.</td></tr>
      <tr><td>E3</td><td>Carenza refrigerante / bassa pressione</td><td>Possibile perdita di gas. Es: chiamare tecnico per verifica.</td></tr>
      <tr><td>E4</td><td>Sovratemperatura scarico compressore</td><td>Gas troppo caldo in mandata. Es: carica insufficiente o restrizione.</td></tr>
      <tr><td>E6</td><td>Comunicazione unità int. ↔ est.</td><td>Cavo 4x1.0mm² tra ID e OD interrotto. Es: verificare cablaggio (max 100m).</td></tr>
      <tr><td>E7</td><td>Conflitto modalità</td><td>Un unità in Cool e l'altra in Heat sulla stessa rete.</td></tr>
      <tr><td>E9</td><td>Protezione riempimento acqua</td><td>Allarme livello acqua — pompa scarico ostruita.</td></tr>
      <tr><td>EE</td><td>Memoria chip lettura/scrittura</td><td>EEPROM scheda danneggiata → sostituire PCB.</td></tr>
      <tr><td>EL</td><td>Emergenza / allarme antincendio</td><td>Segnale da centrale antincendio → unità ferma per sicurezza.</td></tr>
      <tr><td>F3</td><td>Sensore temperatura esterna</td><td>Sonda T ambiente esterno guasta.</td></tr>
      <tr><td>Fo</td><td>Modalità recupero refrigerante</td><td>Modalità service attiva per recupero gas. Non è un errore.</td></tr>
      <tr><td>H1</td><td>Sbrinamento in corso</td><td>Normale operazione di sbrinamento in riscaldamento. Non è un errore.</td></tr>
      <tr><td>H4</td><td>Protezione sovraccarico</td><td>Compressore in overload. Es: attendere raffreddamento.</td></tr>
      <tr><td>H5</td><td>Modulo IPM sovracorrente</td><td>Modulo di potenza compressore in protezione. Es: verificare compressore.</td></tr>
      <tr><td>H7</td><td>Compressore offline</td><td>Comunicazione con driver compressore persa.</td></tr>
      </table>

      <h4 style="color:var(--yellow);font-size:12px;">Driver Compressore (P)</h4>
      <table class="wt"><tr><th>Codice</th><th>Significato</th><th>Esempio / Cosa fare</th></tr>
      <tr><td>P0</td><td>Reset driver</td><td>Reset anomalo del driver compressore. Es: disturbo elettrico.</td></tr>
      <tr><td>P5</td><td>Sovracorrente compressore</td><td>Compressore assorbe troppa corrente. Es: compressore bloccato.</td></tr>
      <tr><td>P6</td><td>Comunicazione master ↔ driver</td><td>Bus di comunicazione tra scheda principale e driver compressore.</td></tr>
      <tr><td>P7</td><td>Sensore temperatura modulo</td><td>Sensore T modulo IPM guasto.</td></tr>
      <tr><td>P8</td><td>Protezione temperatura modulo</td><td>Modulo IPM troppo caldo >limite.</td></tr>
      <tr><td>P9</td><td>Protezione contattore AC</td><td>Contattore compressore non chiude correttamente.</td></tr>
      <tr><td>PA</td><td>Sovracorrente AC esterna</td><td>Corrente assorbita unità esterna troppo alta.</td></tr>
      <tr><td>PH/PL</td><td>Tensione bus alta/bassa</td><td>Tensione DC bus driver fuori range. Es: verificare tensione rete.</td></tr>
      <tr><td>PP</td><td>Tensione AC input errata</td><td>Tensione alimentazione driver non corretta.</td></tr>
      </table>

      <h4 style="color:var(--yellow);font-size:12px;">Ventola Interna DC (q)</h4>
      <table class="wt"><tr><th>Codice</th><th>Significato</th><th>Esempio / Cosa fare</th></tr>
      <tr><td>q0/q1</td><td>Tensione bus ventola bassa/alta</td><td>Alimentazione driver ventola interna fuori range.</td></tr>
      <tr><td>q2</td><td>Sovracorrente ventola AC</td><td>Motore ventola assorbe troppo.</td></tr>
      <tr><td>q3</td><td>IPM ventola</td><td>Modulo IPM driver ventola in protezione.</td></tr>
      <tr><td>q5</td><td>Avvio ventola fallito</td><td>Motore ventola non parte. Es: cuscinetto bloccato.</td></tr>
      <tr><td>q6</td><td>Mancanza fase ventola</td><td>Fase alimentazione motore mancante.</td></tr>
      <tr><td>qE</td><td>Sensore temperatura modulo ventola</td><td>Sonda T driver ventola DC interna guasta.</td></tr>
      <tr><td>qo</td><td>Sensore temperatura scatola elettrica</td><td>Scheda elettronica ventola surriscaldata.</td></tr>
      <tr><td>qC</td><td>Comunicazione master ↔ ventola DC</td><td>Bus comunicazione scheda principale ↔ driver ventola.</td></tr>
      </table>
      <p style="color:var(--text2);font-size:11px;margin-top:4px;">Nota: questi codici sono dal manuale di installazione U-Match ducted (sezione 5.2). I codici visualizzati sul controller XE7A-24/HC possono essere un sottoinsieme di entrambe le tabelle (VRF + U-Match).</p>

      <h3>Specifiche Tecniche</h3>
      <p style="color:var(--text-secondary);font-size:12px;">Dati dalle schede prodotto U-Match 2026 per tutti i modelli della serie GUD.</p>
      <table class="wt"><tr><th>Modello</th><th>BTU</th><th>Cool kW</th><th>Heat kW</th><th>EER/COP</th><th>SEER</th><th>kW nom (c/h)</th><th>Max kW</th><th>ESP Pa</th><th>Flusso H m³/h</th><th>dB(A) H</th><th>Pipe</th><th>R32 kg</th><th>ID mm</th></tr>
      <tr><td>GUD35</td><td>12K</td><td>3.5</td><td>4.0</td><td>3.5/4.0</td><td>6.6</td><td>1.00/1.05</td><td>1.40</td><td>0-100</td><td>—</td><td>—</td><td>1/4-3/8</td><td>0.57</td><td>—</td></tr>
      <tr><td>GUD50</td><td>18K</td><td>5.0</td><td>5.5</td><td>3.5/4.0</td><td>6.6</td><td>1.45/1.50</td><td>2.00</td><td>0-100</td><td>—</td><td>—</td><td>1/4-1/2</td><td>0.85</td><td>—</td></tr>
      <tr><td style="color:var(--yellow)">GUD71</td><td style="color:var(--yellow)">24K</td><td>7.10</td><td>8.00</td><td>3.70/4.00</td><td>6.6</td><td>1.92/2.00</td><td>2.80</td><td>0-160</td><td>1100</td><td>37</td><td>3/8-5/8</td><td>1.50</td><td>260/900/655</td></tr>
      <tr><td style="color:var(--yellow)">GUD85</td><td style="color:var(--yellow)">29K</td><td>8.50</td><td>8.80</td><td>3.40/3.90</td><td>6.4</td><td>2.50/2.26</td><td>3.30</td><td>0-160</td><td>1400</td><td>43</td><td>3/8-5/8</td><td>1.50</td><td>260/900/655</td></tr>
      <tr><td>GUD100</td><td>36K</td><td>10.50</td><td>11.50</td><td>3.50/4.10</td><td>6.4</td><td>3.00/2.80</td><td>4.70</td><td>0-160</td><td>1700</td><td>39</td><td>3/8-5/8</td><td>2.10</td><td>260/1340/655</td></tr>
      <tr><td>GUD140</td><td>46K</td><td>13.40</td><td>15.50</td><td>2.91/3.30</td><td>—</td><td>4.60/4.70</td><td>5.60</td><td>0-160</td><td>2200</td><td>43</td><td>3/8-5/8</td><td>2.80</td><td>300/1400/700</td></tr>
      <tr><td>GUD160</td><td>54K</td><td>16.00</td><td>17.00</td><td>2.96/3.62</td><td>—</td><td>5.40/4.70</td><td>6.80</td><td>0-200</td><td>2600</td><td>44</td><td>3/8-5/8</td><td>3.50</td><td>300/1400/700</td></tr>
      </table>
      <p style="color:var(--text2);font-size:10px;margin-top:4px;">Dati GUD35/GUD50 stimati (serie PS). GUD140/GUD160: modelli 3Ph. I tuoi modelli in giallo. Refrigerante R32 (GWP 675).</p>

      <h3>Caratteristiche Principali</h3>
      <p style="color:var(--text-secondary);font-size:12px;">Funzionalità della serie U-Match GUD:</p>
      <ul style="color:var(--text-secondary);font-size:12px;margin:4px 0 12px 18px;line-height:1.7;">
      <li><b>Doppio sensore temperatura:</b> scegli se usare il sensore dell'unità interna o del comando a filo (I-FEEL)</li>
      <li><b>Pompa scarico integrata:</b> sollevamento fino a 1000 mm — nessuna pompa esterna necessaria</li>
      <li><b>Presa aria fresca:</b> collegabile direttamente all'unità per ricambio d'aria</li>
      <li><b>Batteria a V brevettata:</b> maggiore scambio termico in meno spazio</li>
      <li><b>Ventola centrifuga brevettata:</b> portata maggiore, rumore ridotto</li>
      <li><b>Motore DC:</b> ventola interna a commutazione elettronica, modulante</li>
      <li><b>WiFi opzionale:</b> via controller YAP1F6 (venduto separatamente)</li>
      <li><b>Modbus gateway:</b> ME50-00/EG(M) per integrazione BMS</li>
      <li><b>Controllo centralizzato:</b> CE58-00/EF(CM) per fino a 80 unità</li>
      <li><b>R32:</b> refrigerante ecologico GWP 675, carica ridotta</li>
      <li><b>Valvole di intercettazione:</b> chiudono il refrigerante per manutenzione senza perdite</li>
      <li><b>Sleep modes:</b> 3 modalità notte con regolazione graduale temperatura</li>
      <li><b>I-Demand:</b> risparmio energetico limitando la potenza massima</li>
      <li><b>Sbrinamento intelligente:</b> ottimizzato per ridurre i cicli di sbrinamento</li>
      <li><b>Antivento freddo:</b> ventola ritardata in riscaldamento fino a batteria calda</li>
      <li><b>Deumidifica a bassa temperatura:</b> funzione Dry anche con temperature basse</li>
      </ul>

      <h3>Stima Consumi</h3>
      <p style="color:var(--text-secondary);font-size:12px;">
      La stima si basa sui dati nominali dei modelli sopra. Per calcolare il consumo istantaneo:
      </p>
      <ul style="color:var(--text-secondary);font-size:12px;margin:4px 0 12px 18px;line-height:1.6;">
      <li><b>Off</b> = 0 W</li>
      <li><b>Fan only</b> = 5% della potenza nominale (solo ventola)</li>
      <li><b>Cool/Heat</b> = potenza nominale × fattore ventola × fattore carico termico</li>
      <li><b>Dry</b> = 70% della potenza nominale cool</li>
      <li><b>Turbo</b> = maggiorazione del 20%</li>
      <li><b>Fattore ventola:</b> Auto=90%, Bassa=70%, M-Bassa=80%, Media=90%, M-Alta=100%, Alta=110%</li>
      <li><b>Fattore carico:</b> 50% + (ΔT × 5%). Es: set 24°C, ambiente 27°C → ΔT=3 → 65% carico</li>
      </ul>
    </div>
  </div>
  <div id="tab-logs" style="display:none;">
    <div class="log-toolbar">
      <button class="btn" onclick="copyAllLogs()">📋 Copy all</button>
      <label class="log-toggle">
        <input type="checkbox" id="autoRefreshLogs" checked onchange="onLogAutoRefreshChange()">
        Auto-refresh
      </label>
      <span id="logCount"></span>
    </div>
    <div id="logContainer">
      <p style="color:var(--text2);font-size:12px;">Loading...</p>
    </div>
  </div>
  <div id="tab-readme" style="display:none;">
    <div class="md-content" id="readmeContainer">
      <p style="color:var(--text-secondary);font-size:13px;">Loading...</p>
    </div>
  </div>
  <div id="tab-changelog" style="display:none;">
    <div class="md-content" id="changelogContainer">
      <p style="color:var(--text-secondary);font-size:13px;">Loading...</p>
    </div>
  </div>
</div>

<div class="server-info" id="serverInfo"></div>
<div style="font-size:11px;color:var(--yellow);text-align:center;margin-top:6px;">
  ⚠ L'integrazione disconnette l'app Gree+ (una sessione per account)
</div>

<script>
const HA_BASE = window.location.origin;
const PANEL_DATA_URL = HA_BASE + '/api/gree_ac_cloud/panel/data';
const PANEL_CMD_URL = HA_BASE + '/api/gree_ac_cloud/panel/command';
const PANEL_NAMES_URL = HA_BASE + '/api/gree_ac_cloud/panel/names';

const __README_CONTENT__ = __README_JSON__;
const __CHANGELOG_CONTENT__ = __CHANGELOG_JSON__;
const __DEVICE_NAMES__ = __DEVICE_NAMES_JSON__;

async function apiFetch(url, opts = {}) {
  const resp = await fetch(url, opts);
  if (!resp.ok) throw new Error(resp.statusText);
  return resp.json();
}

async function sendCommand(mac, options, values) {
  try {
    const result = await apiFetch(PANEL_CMD_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mac, options, values }),
    });
    return result.ok;
  } catch (e) {
    console.error('Command failed:', e);
    return false;
  }
}

function parseTemp(val) {
  if (val == null || val === undefined) return '--';
  val = Number(val);
  if (val > 50) return (val / 2).toFixed(1);
  return String(val);
}

const MODELS = {
  'GUD35': { cool: 1.00, heat: 1.05, max: 1.40, btus: '12K', name: 'GUD35 (12K BTU/3.5kW)' },
  'GUD50': { cool: 1.45, heat: 1.50, max: 2.00, btus: '18K', name: 'GUD50 (18K BTU/5.0kW)' },
  'GUD71': { cool: 1.92, heat: 2.00, max: 2.80, btus: '24K', name: 'GUD71 (24K BTU/7.1kW)' },
  'GUD85': { cool: 2.50, heat: 2.26, max: 3.30, btus: '29K', name: 'GUD85 (29K BTU/8.5kW)' },
  'GUD100': { cool: 3.00, heat: 2.80, max: 4.70, btus: '36K', name: 'GUD100 (36K BTU/10.5kW)' },
  'GUD140': { cool: 4.60, heat: 4.70, max: 5.60, btus: '46K', name: 'GUD140 (46K BTU/13.4kW)' },
  'GUD160': { cool: 5.40, heat: 4.70, max: 6.80, btus: '54K', name: 'GUD160 (55K BTU/16.0kW)' },
};

let _serverModels = {};
async function loadModels() {
  try {
    _serverModels = await apiFetch(HA_BASE + '/api/gree_ac_cloud/panel/models');
  } catch (e) {
    console.warn('Failed to load server models, using localStorage:', e);
    _serverModels = {};
  }
}
function getModel(mac) { const k = _serverModels[mac] || localStorage.getItem('model_' + mac) || ''; return MODELS[k] || null; }
function getModelKey(mac) { return _serverModels[mac] || localStorage.getItem('model_' + mac) || ''; }
async function setModel(mac, val) {
  _serverModels[mac] = val;
  localStorage.setItem('model_' + mac, val);
  try {
    await fetch(HA_BASE + '/api/gree_ac_cloud/panel/models', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({mac, model: val}) });
  } catch (e) {
    console.warn('setModel server-side failed:', e);
  }
  loadData();
}

async function renameDevice(mac) {
  const current = __DEVICE_NAMES__[mac] || '';
  const name = prompt('Nome personalizzato per ' + mac, current);
  if (name === null) return;
  try {
    await fetch(HA_BASE + '/api/gree_ac_cloud/panel/names', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({mac, name})
    });
    __DEVICE_NAMES__[mac] = name;
  } catch (e) {
    console.warn('renameDevice failed:', e);
  }
  loadData();
}

function estimatePower(s, model) {
  if (!s || !s.Pow || !model) return 0;
  const mode = s.Mod;
  if (mode === 3) return Math.round(model.cool * 0.05 * 100) / 100;
  const base = (mode === 2) ? model.heat : model.cool;
  const dryFactor = (mode === 4) ? 0.7 : 1.0;
  const fanMap = {0: 0.9, 1: 0.7, 2: 0.8, 3: 0.9, 4: 1.0, 5: 1.1};
  const fanFactor = fanMap[s.WdSpd] || 0.9;
  const turboFactor = s.Tur ? 1.2 : 1.0;
  const setTem = (s.SetDeciTem != null) ? s.SetDeciTem / 10 : (s.SetTem || 24);
  const inTem = s.InTem ? (s.InTem > 50 ? s.InTem / 2 : s.InTem) : setTem;
  const delta = Math.abs(setTem - inTem);
  const loadFactor = Math.min(1.0, 0.5 + delta * 0.05);
  let power = base * fanFactor * turboFactor * loadFactor * dryFactor;
  power = Math.min(power, model.max);
  return Math.round(power * 100) / 100;
}

// Track kWh per device (in memory)
const _kwhTracker = {};

function renderDevice(d) {
  const s = d.state || {};
  const pow = s.Pow;
  const mod = s.Mod;
  const tem = s.SetDeciTem != null ? (s.SetDeciTem / 10).toFixed(1) : (s.SetTem || '--');
  const inTem = s.InTem;
  const outTem = s.OutTem;
  const inHumi = s.InHumi;
  const fan = s.WdSpd;
  const swingV = s.SwUpDn;
  const swingH = s.SwingLfRig;
  const connected = d.connected;
  const modelKey = getModelKey(d.mac);
  const model = MODELS[modelKey] || null;
  const estPower = estimatePower(s, model);
  
  // Track kWh: accumulate when card is rendered (every ~10s)
  if (pow && modelKey && estPower > 0) {
    if (!_kwhTracker[d.mac]) _kwhTracker[d.mac] = { lastRender: Date.now(), kwh: 0 };
    const t = _kwhTracker[d.mac];
    const elapsed = (Date.now() - t.lastRender) / 3600000;
    if (elapsed > 0.001) t.kwh += estPower * elapsed;
    t.lastRender = Date.now();
  } else if (!pow) {
    if (_kwhTracker[d.mac]) _kwhTracker[d.mac].kwh = 0;
  }
  const totalKwh = (_kwhTracker[d.mac] && _kwhTracker[d.mac].kwh)
    ? _kwhTracker[d.mac].kwh.toFixed(2) : '0.00';

  const modeCls = ['auto','cool','heat','fan','dry'];
  const modeLabels = ['Auto','Cool','Heat','Fan','Dry'];
  const modeTips = [
    'Auto: regola automaticamente fresco/caldo in base alla temperatura ambiente',
    'Cool: raffrescamento — abbassa la temperatura',
    'Heat: riscaldamento — alza la temperatura',
    'Fan: solo ventilazione — senza raffrescare o scaldare',
    'Dry: deumidifica — riduce l\'umidità mantenendo fresco'
  ];
  const fanTips = [
    'Auto: velocità regolata automaticamente dal device',
    'Bassa: ventilazione minima, silenzioso',
    'Media-Bassa: leggermente più potente',
    'Media: ventilazione media, bilanciato',
    'Media-Alta: ventilazione sostenuta',
    'Alta: massima potenza ventilazione'
  ];
  const switchTips = {
    Health: 'Health: ionizzatore / purificazione aria',
    Quiet: 'Quiet: modalità silenziosa, riduce rumore ventola',
    Tur: 'Turbo: massima potenza velocemente',
    StHt: 'Strong Heat: riscaldamento intenso per ambienti grandi',
    Blo: 'Blow: ventola continua dopo spegnimento per asciugare',
    SvSt: 'Energy Save: risparmio energetico',
    SlpMod: 'Sleep: regola temperatura gradualmente durante la notte',
    Lig: 'Light: retroilluminazione display controller',
  };

  const deviceName = __DEVICE_NAMES__[d.mac] || d.name || 'Condizionatore';

  let curSwing = 'off';
  if (swingV && swingH) curSwing = 'both';
  else if (swingV) curSwing = 'v';
  else if (swingH) curSwing = 'h';

  return `
<div class="card${pow ? ' on' : ''}" data-mac="${d.mac}" style="position:relative">
  <div class="card-header">
    <div class="header-row1">
      <div class="name-group">
        <span class="icon-ac"><svg viewBox="0 0 24 24"><path d="M22 11h-4.17l3.24-3.24-1.41-1.42L15 11h-2V9l4.66-4.66-1.42-1.41L13 6.17V2h-2v4.17L7.76 2.93 6.34 4.34 11 9v2H9L4.34 6.34 2.93 7.76 6.17 11H2v2h4.17l-3.24 3.24 1.41 1.42L9 13h2v2l-4.66 4.66 1.42 1.41L11 17.83V22h2v-4.17l3.24 3.24 1.42-1.41L13 15v-2h2l4.66 4.66 1.41-1.42L17.83 13H22z"/></svg></span>
        <h2 class="device-name" ondblclick="renameDevice('${d.mac}')" title="Doppio click per rinominare">${deviceName}</h2>
      </div>
      <span class="conn-badge${!connected ? ' off' : ''}">${connected ? '● online' : '○ offline'}</span>
    </div>
    <div class="header-row2">
      <span class="mac-label">${d.mac}</span>
      <select class="model-select" onchange="setModel('${d.mac}', this.value)" title="Seleziona modello per stima consumi">
        <option value="">— modello —</option>
        ${Object.entries(MODELS).map(([k,v]) => `<option value="${k}" ${modelKey === k ? 'selected' : ''}>${v.name}</option>`).join('')}
      </select>
    </div>
  </div>

  <div class="sensors">
    <div class="sensor">
      <div class="value ${pow ? 'green' : ''}">${parseTemp(inTem)}°</div>
      <div class="label">Interno</div>
    </div>
    <div class="sensor">
      <div class="value ${pow ? '' : ''}">${parseTemp(outTem)}°</div>
      <div class="label">Esterno</div>
    </div>
    <div class="sensor">
      <div class="value">${inHumi != null ? inHumi + '%' : '--'}</div>
      <div class="label">Umidità</div>
    </div>
  </div>

  ${modelKey ? `<div class="power-row">
    <div class="p-item"><div class="p-val">${estPower.toFixed(2)} kW</div><div class="p-label">Stima istantanea</div></div>
    <div class="p-item"><div class="p-val">${totalKwh} kWh</div><div class="p-label">Da accensione</div></div>
    <div class="p-item"><div class="p-val">${(estPower * 730).toFixed(0)} kWh</div><div class="p-label">Mese stimato</div></div>
  </div>` : ''}

  <div class="controls">
    <div class="control-row">
      <label>Power</label>
      <div class="btn-group">
        <button class="btn ${!pow ? 'danger active' : ''}" onclick="setPower('${d.mac}',0)" title="Spegne il condizionatore">Off</button>
        <button class="btn ${pow ? 'active' : ''}" onclick="setPower('${d.mac}',1)" title="Accende il condizionatore">On</button>
      </div>
    </div>

    <div class="control-row">
      <label>Mode</label>
      <div class="btn-group">
        ${[0,1,2,3,4].map(i => `<button class="btn mode-${modeCls[i]} ${mod === i && pow ? 'active' : ''}" onclick="setMode('${d.mac}',${i})" title="${modeTips[i]}">${modeLabels[i]}</button>`).join('')}
      </div>
    </div>

    <div class="control-row">
      <label>Temp</label>
      <div class="temp-control">
        <button onclick="setTemp('${d.mac}',-0.5)" title="Abbassa la temperatura di 0.5°C">−</button>
        <span class="temp-value">${tem}°</span>
        <button onclick="setTemp('${d.mac}',0.5)" title="Alza la temperatura di 0.5°C">+</button>
      </div>
    </div>

    <div class="control-row">
      <label>Fan</label>
      <div class="btn-group">
        ${[0,1,2,3,4,5].map(v => `<button class="btn ${fan === v && pow ? 'active' : ''}" onclick="setFan('${d.mac}',${v})" title="${fanTips[v]}">${['Auto','Bassa','M-Bassa','Media','M-Alta','Alta'][v]}</button>`).join('')}
      </div>
    </div>

    <div class="control-row">
      <label>Swing</label>
      <div class="btn-group">
        <button class="btn ${curSwing === 'off' && pow ? 'active' : ''}" onclick="setSwing('${d.mac}','off')" title="Swing disattivato">Off</button>
        <button class="btn ${curSwing === 'v' && pow ? 'active' : ''}" onclick="setSwing('${d.mac}','v')" title="Swing verticale: palette su/giù">V</button>
        <button class="btn ${curSwing === 'h' && pow ? 'active' : ''}" onclick="setSwing('${d.mac}','h')" title="Swing orizzontale: palette destra/sinistra">H</button>
        <button class="btn ${curSwing === 'both' && pow ? 'active' : ''}" onclick="setSwing('${d.mac}','both')" title="Swing verticale + orizzontale">Both</button>
      </div>
    </div>

    <div class="control-row">
      <label>Extra</label>
      <div class="switches">
        ${Object.entries({
          Health:'Health', Quiet:'Quiet', Tur:'Turbo', StHt:'S.Heat',
          Blo:'Blow', SvSt:'E.Save', SlpMod:'Sleep', Lig:'Light'
        }).map(([k,l]) => `<button class="switch-btn ${(d.state||{})[k] ? 'on' : ''}" onclick="toggleSwitch('${d.mac}','${k}')" title="${switchTips[k] || k}">${l}</button>`).join('')}
      </div>
    </div>
  </div>
</div>`;
}

async function loadData() {
  try {
    const data = await apiFetch(PANEL_DATA_URL);
    const container = document.getElementById('devices');
    const setupMsg = document.getElementById('setupMsg');
    const badge = document.getElementById('statusBadge');

    if (!data || data.length === 0) {
      setupMsg.style.display = 'block';
      container.innerHTML = '';
      badge.textContent = 'no devices';
      badge.style.background = 'var(--yellow)';
      return;
    }

    setupMsg.style.display = 'none';
    const allConnected = data.every(d => d.connected);
    badge.textContent = allConnected ? `${data.length} device${data.length > 1 ? 's' : ''} online` : `${data.filter(d => d.connected).length}/${data.length} online`;
    badge.style.background = allConnected ? 'var(--green)' : 'var(--yellow)';

    container.innerHTML = data.map(d => renderDevice(d)).join('');

    const info = document.getElementById('serverInfo');
    info.textContent = 'Gree AC Cloud v0.2.0 | ' + (data[0]?.state?.host || 'mqtt-eu.gree.com');
  } catch (e) {
    console.error('Load failed:', e);
    document.getElementById('statusBadge').textContent = 'error';
    document.getElementById('statusBadge').style.background = 'var(--red)';
  }
}

async function setPower(mac, val) {
  await sendCommand(mac, ['Pow'], [val]);
  setTimeout(loadData, 1000);
}

async function setMode(mac, val) {
  await sendCommand(mac, ['Pow', 'Mod'], [1, val]);
  setTimeout(loadData, 1000);
}

async function setTemp(mac, delta) {
  const card = document.querySelector(`[data-mac="${mac}"]`);
  if (!card) return;
  const el = card.querySelector('.temp-value');
  let cur = parseFloat(el.textContent) || 26;
  let newTemp = Math.max(16, Math.min(30, cur + delta));
  newTemp = Math.round(newTemp * 2) / 2;
  let deci = Math.round(newTemp * 10);
  await sendCommand(mac, ['SetDeciTem'], [deci]);
  setTimeout(loadData, 1000);
}

async function setFan(mac, val) {
  await sendCommand(mac, ['WdSpd'], [val]);
  setTimeout(loadData, 1000);
}

async function setSwing(mac, mode) {
  const v = (mode === 'v' || mode === 'both') ? 1 : 0;
  const h = (mode === 'h' || mode === 'both') ? 1 : 0;
  await sendCommand(mac, ['SwUpDn', 'SwingLfRig'], [v, h]);
  setTimeout(loadData, 1000);
}

async function toggleSwitch(mac, key) {
  const data = await apiFetch(PANEL_DATA_URL);
  const dev = data.find(d => d.mac === mac);
  if (!dev) return;
  const curVal = dev.state && dev.state[key] ? 1 : 0;
  await sendCommand(mac, [key], [curVal ? 0 : 1]);
  setTimeout(loadData, 1000);
}

// ── Markdown renderer ────────────────────────────────

function mdToHtml(md) {
  let h = md.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  h = h.replace(/```(\w*)\n?([\s\S]*?)```/g, '<pre><code>$2</code></pre>');
  h = h.replace(/^#{5}\s+(.+)$/gm, '<h5>$1</h5>');
  h = h.replace(/^#{4}\s+(.+)$/gm, '<h4>$1</h4>');
  h = h.replace(/^#{3}\s+(.+)$/gm, '<h3>$1</h3>');
  h = h.replace(/^#{2}\s+(.+)$/gm, '<h2>$1</h2>');
  h = h.replace(/^#\s+(.+)$/gm, '<h1>$1</h1>');
  h = h.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
  h = h.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  h = h.replace(/\*(.+?)\*/g, '<em>$1</em>');
  h = h.replace(/`([^`]+)`/g, '<code>$1</code>');
  h = h.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  h = h.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, '<img src="$2" alt="$1" style="max-width:100%;height:auto;">');
  h = h.replace(/^-{3,}$/gm, '<hr>');
  h = h.replace(/^\|(.+)\|$/gm, function(m) {
    if (m.includes('---')) return '';
    const cells = m.split('|').slice(1,-1).map(c => c.trim());
    return '<tr><td>' + cells.join('</td><td>') + '</td></tr>';
  });
  h = h.replace(/(<tr>.*<\/tr>\n?)+/g, function(m) {
    const rows = m.trim().split('\n').filter(r => r.trim());
    const isHeader = rows.length >= 2 && /^<tr>/.test(rows[1]);
    const tag = isHeader ? 'thead' : 'tbody';
    return '<table><' + tag + '>' + rows.join('') + '</' + tag + '></table>';
  });
  h = h.replace(/^> (.+)$/gm, '<blockquote><p>$1</p></blockquote>');
  h = h.replace(/<\/blockquote>\s*<blockquote>/g, '\n');
  h = h.replace(/^\s*[-*]\s+(.+)$/gm, '<li>$1</li>');
  h = h.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>');
  h = h.replace(/^\s*\d+\.\s+(.+)$/gm, '<li>$1</li>');
  h = h.replace(/^(?!<[a-z]|<\/[a-z]|$)(.+)$/gm, function(m) {
    m = m.trim();
    return m ? '<p>' + m + '</p>' : '';
  });
  h = h.replace(/\n{2,}/g, '\n');
  return '<div class="md-content">' + h + '</div>';
}

function loadReadme() {
  const el = document.getElementById('readmeContainer');
  if (!el) return;
  el.innerHTML = mdToHtml(__README_CONTENT__);
}

function loadChangelog() {
  const el = document.getElementById('changelogContainer');
  if (!el) return;
  el.innerHTML = mdToHtml(__CHANGELOG_CONTENT__);
}

let _logAutoRefreshTimer = null;
let _lastLogCount = 0;

async function loadLogs() {
  const container = document.getElementById('logContainer');
  if (!container) return;
  try {
    const data = await apiFetch(HA_BASE + '/api/gree_ac_cloud/panel/log');
    const wasAtBottom = container.scrollTop + container.clientHeight >= container.scrollHeight - 4;
    container.innerHTML = data.map(e =>
      `<div class="log-entry"><span class="log-time">${e.t}</span><span class="log-${e.l.toLowerCase()}">${e.l} ${e.m}</span></div>`
    ).join('');
    const countEl = document.getElementById('logCount');
    if (countEl) countEl.textContent = data.length + ' entries';
    _lastLogCount = data.length;
    if (wasAtBottom) container.scrollTop = container.scrollHeight;
  } catch (e) {
    container.innerHTML = '<p style="color:var(--red)">Failed to load logs.</p>';
  }
}

async function copyAllLogs() {
  try {
    const data = await apiFetch(HA_BASE + '/api/gree_ac_cloud/panel/log');
    const text = data.map(e => `[${e.t}] ${e.l} ${e.m}`).join('\n');
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed'; ta.style.left = '-9999px';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    }
    const btn = document.querySelector('button[onclick="copyAllLogs()"]');
    if (btn) {
      const orig = btn.textContent;
      btn.textContent = '✅ Copied!';
      setTimeout(() => btn.textContent = orig, 2000);
    }
  } catch (e) {
    alert('Failed to copy logs: ' + e.message);
  }
}

function onLogAutoRefreshChange() {
  if (_logAutoRefreshTimer) {
    clearInterval(_logAutoRefreshTimer);
    _logAutoRefreshTimer = null;
  }
  if (document.getElementById('autoRefreshLogs').checked) {
    _logAutoRefreshTimer = setInterval(loadLogs, 2000);
  }
}

function switchTab(tab) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  const tabs = ['devices','wiki','logs','readme','changelog'];
  tabs.forEach(t => {
    const el = document.getElementById('tab-' + t);
    if (el) el.style.display = t === tab ? 'block' : 'none';
  });
  const badge = document.getElementById('statusBadge');
  badge.style.display = tab === 'devices' ? 'inline' : 'none';
  if (tab === 'logs') {
    loadLogs();
    onLogAutoRefreshChange();
  } else {
    if (_logAutoRefreshTimer) {
      clearInterval(_logAutoRefreshTimer);
      _logAutoRefreshTimer = null;
    }
  }
  if (tab === 'readme') loadReadme();
  if (tab === 'changelog') loadChangelog();
}

// ── viewport detection (works in iframe context) ──
function updateViewportClass() {
  document.body.classList.toggle('desktop', window.innerWidth >= 600);
}
updateViewportClass();
window.addEventListener('resize', updateViewportClass);

loadModels();
loadData();
setInterval(loadData, 10000);
</script>
</body>
</html>"""
