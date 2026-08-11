"""Protocol-level tests that do not require a Home Assistant installation."""

from __future__ import annotations

import ast
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "gree_ac_cloud"


def test_python_sources_parse() -> None:
    for source in COMPONENT.glob("*.py"):
        ast.parse(source.read_text())


def test_json_files_parse() -> None:
    for source in [
        COMPONENT / "manifest.json",
        COMPONENT / "strings.json",
        COMPONENT / "translations" / "it.json",
        ROOT / "hacs.json",
    ]:
        json.loads(source.read_text())


def test_panel_data_apis_require_authentication() -> None:
    source = (COMPONENT / "panel.py").read_text()
    # The HTML shell must stay public for the iframe; every data view is protected.
    history_source = (COMPONENT / "panel_history.py").read_text()
    profile_source = (COMPONENT / "panel_profiles.py").read_text()
    assert source.count("requires_auth = True") == 9
    assert history_source.count("requires_auth = True") == 1
    assert profile_source.count("requires_auth = True") == 1
    assert source.count("requires_auth = False") == 1


def test_persistent_history_is_modular_and_recorder_backed() -> None:
    panel_source = (COMPONENT / "panel.py").read_text()
    history_source = (COMPONENT / "panel_history.py").read_text()
    frontend_source = (COMPONENT / "frontend" / "panel_history.js").read_text()
    assert "get_significant_states" in history_source
    assert "get_instance(hass).async_add_executor_job" in history_source
    assert '"30d": timedelta(days=30)' in history_source
    assert "HISTORY_MAX_POINTS = 720" in history_source
    assert "__PANEL_HISTORY_JS__" in panel_source
    assert "__APEXCHARTS_JS__" in panel_source
    assert "new ApexCharts" in frontend_source
    assert "type:'datetime'" in frontend_source
    assert "shared:true" in frontend_source
    assert (COMPONENT / "frontend" / "apexcharts.min.js").stat().st_size > 500_000
    assert "MIT License" in (COMPONENT / "frontend" / "APEXCHARTS_LICENSE").read_text()
    assert "loadPersistentHistory" in frontend_source
    assert "shiftHistory" in frontend_source
    assert "goToLatestHistory" in frontend_source
    assert '"power": [estimated_power]' in history_source
    assert '"baselinePower": [baseline_power]' in history_source
    assert '"preset": [climate_entity]' in history_source
    assert "buildEnergyHistory" in frontend_source
    assert "renderEnergyIndicators" in frontend_source
    assert "Risparmio attribuito ai profili" in frontend_source


def test_energy_estimates_expose_baseline_and_saving_sensors() -> None:
    sensor_source = (COMPONENT / "sensor.py").read_text()
    coordinator_source = (COMPONENT / "coordinator.py").read_text()
    assert "Estimated Power" in sensor_source
    assert "Estimated Baseline Power" in sensor_source
    assert "Estimated Saving Power" in sensor_source
    assert "Estimated Energy" in sensor_source
    assert "estimated_baseline_power_w" in coordinator_source
    assert "estimated_saving_power_w" in coordinator_source


def test_panel_keeps_log_capture_and_d1_normalization() -> None:
    source = (COMPONENT / "panel.py").read_text()
    assert "_logger_root.setLevel(logging.DEBUG)" in source
    assert 'state["DREDEffective"]' in source
    assert 'state["IdemandActive"]' in source
    assert "Number(s.Idemand || 0) === 1" in source
    assert "I-Demand attivo" in source
    assert "Stato effettivo:" in source
    assert '"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"' in source
    assert 'state["StartupDRED"]' in source
    assert "setStartupDred" in source


def test_insecure_tls_is_not_used_by_component() -> None:
    sources = "\n".join(path.read_text() for path in COMPONENT.glob("*.py"))
    mqtt_source = (COMPONENT / "gree_mqtt.py").read_text()
    assert "verify=False" not in sources
    assert "ssl.CERT_NONE" not in sources
    assert "await asyncio.to_thread(ssl.create_default_context)" in mqtt_source
    assert "tls_context=self._tls_context" in mqtt_source


def test_panel_javascript_parses_when_node_is_available() -> None:
    """Catch syntax errors in the embedded custom-panel JavaScript."""
    node = shutil.which("node")
    if node is None:
        return

    tree = ast.parse((COMPONENT / "panel.py").read_text())
    panel_html = None
    for statement in tree.body:
        if not isinstance(statement, ast.Assign):
            continue
        if any(
            isinstance(target, ast.Name) and target.id == "PANEL_HTML"
            for target in statement.targets
        ):
            panel_html = ast.literal_eval(statement.value)
            break

    assert panel_html is not None
    panel_html = (
        panel_html.replace(
            "__APEXCHARTS_JS__",
            (COMPONENT / "frontend" / "apexcharts.min.js").read_text(),
        )
        .replace(
            "__PANEL_HISTORY_JS__",
            (COMPONENT / "frontend" / "panel_history.js").read_text(),
        )
        .replace(
            "__PANEL_PROFILES_JS__",
            (COMPONENT / "frontend" / "panel_profiles.js").read_text(),
        )
    )
    scripts = []
    offset = 0
    while (script_start := panel_html.find("<script>", offset)) >= 0:
        script_end = panel_html.find("</script>", script_start)
        assert script_end > script_start
        scripts.append(panel_html[script_start + len("<script>") : script_end])
        offset = script_end + len("</script>")
    assert len(scripts) == 2

    for source in scripts:
        with tempfile.NamedTemporaryFile("w", suffix=".js") as script:
            script.write(source)
            script.flush()
            subprocess.run([node, "--check", script.name], check=True, capture_output=True)


def _load_protocol_module():
    package_name = "custom_components.gree_ac_cloud"
    package = types.ModuleType(package_name)
    package.__path__ = [str(COMPONENT)]
    sys.modules[package_name] = package
    spec = importlib.util.spec_from_file_location(
        f"{package_name}.gree_api", COMPONENT / "gree_api.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_optional_environment_sensors_honor_protocol_sentinels() -> None:
    sensor_source = (COMPONENT / "sensor.py").read_text()
    climate_source = (COMPONENT / "climate.py").read_text()
    panel_source = (COMPONENT / "panel.py").read_text()
    const_source = (COMPONENT / "const.py").read_text()
    assert 'self._key == "InTem"' in sensor_source
    assert "Home Assistant may expose the cloud value" in sensor_source
    assert "return raw - 40" in sensor_source
    assert "raw != 0" in sensor_source
    assert 'self._key == "InHumi"' in sensor_source
    assert 'enabled = self.coordinator.data.get("InHumiEn")' in sensor_source
    assert "0 < raw <= 100" in sensor_source
    assert 'raw = self.coordinator.data.get("InTem")' in climate_source
    assert '"gree_indoor_temperature_sensor_enabled"' in climate_source
    assert '"gree_indoor_humidity_sensor_enabled"' in climate_source
    assert 'state["InTemEnableRaw"]' in panel_source
    assert 'state["InTemEnabled"]' in panel_source
    assert 'state["InHumiEnableRaw"]' in panel_source
    assert 'state["TemSenEnabled"]' in panel_source
    assert 'state["InHumiEnabled"]' in panel_source
    assert '"InTemEn"' in const_source
    assert '"InHumiEn"' in const_source
    assert "Number(s.InHumiEn) === 1" in panel_source
    assert "const measuredAir = s.InTem" in panel_source
    assert "return Number.isFinite(raw) && raw !== 0 ? raw - 40 : null" in panel_source
    assert "parseProbeTemp(s.InTem)" in panel_source
    assert "parseProbeTemp(s.OutTem)" in panel_source
    assert "toggleNativeSensor" in panel_source


def test_manual_fan_override_and_turbo_are_exposed() -> None:
    climate_source = (COMPONENT / "climate.py").read_text()
    panel_source = (COMPONENT / "panel.py").read_text()
    assert "self._smart_manual_fan" in climate_source
    assert "smart_manual_fan_override" in climate_source
    assert "if self._smart_manual_fan in FAN_MAP_REV" in climate_source
    assert "ClimateEntityId" in panel_source
    assert "/api/services/climate/set_fan_mode" in panel_source
    assert "🚀 TURBO" in panel_source
    assert "setTurbo('${safeMac}'" in panel_source
    assert "['Tur','WdSpd','Quiet','DRED']" in panel_source
    assert '6: "Turbo"' in (COMPONENT / "const.py").read_text()
    switch_source = (COMPONENT / "switch.py").read_text()
    assert 'options = ["Tur", "WdSpd"]' in switch_source
    assert "values = [1, 6]" in switch_source


def test_gree_mode_mapping_matches_wire_protocol() -> None:
    const_source = (COMPONENT / "const.py").read_text()
    coordinator_source = (COMPONENT / "coordinator.py").read_text()
    assert '0: "auto"' in const_source
    assert '1: "cool"' in const_source
    assert '2: "dry"' in const_source
    assert '3: "fan_only"' in const_source
    assert '4: "heat"' in const_source
    assert 'model["heat"] if mode == 4' in coordinator_source
    assert "if mode == 2:" in coordinator_source


def test_dred_control_keeps_verified_protocol_mapping() -> None:
    const_source = (COMPONENT / "const.py").read_text()
    select_source = (COMPONENT / "select.py").read_text()
    assert '0: "Off"' in const_source
    assert '1: "D1"' in const_source
    assert '2: "D2"' in const_source
    assert '3: "D3"' in const_source
    assert '["DRED"]' in select_source
    assert 'data.get("DREDEn") == 1' in select_source
    assert 'data.get("Mod") == 1' in select_source
    assert 'int(data.get("Idemand", 0)) == 1' in select_source
    assert '"verified_levels": [0, 1, 2, 3]' in select_source
    assert "_attr_entity_registry_enabled_default = True" in select_source
    assert "RegistryEntryDisabler.INTEGRATION" in select_source
    assert "registry.async_update_entity(entity_id, disabled_by=None)" in select_source
    assert '_attr_translation_key = "dred_level"' in select_source
    assert "class GreeStartupDemandResponseSelect" in select_source
    assert '_attr_translation_key = "startup_dred_level"' in select_source
    assert 'STARTUP_DRED_NO_ACTION = "No action"' in const_source
    assert "async_set_startup_dred" in select_source


def test_smart_hysteresis_boundaries() -> None:
    """Cooling reaches target before stopping; deadband only delays restart."""

    def demand(mode: str, current: float, target: float, margin: float, active: str):
        if mode == "cool":
            return (
                "cool"
                if (active == "cool" and current > target) or current > target + margin
                else None
            )
        if mode == "heat":
            return (
                "heat"
                if (active == "heat" and current < target) or current < target - margin
                else None
            )
        return None

    assert demand("cool", 26.4, 26.0, 0.5, "cool") == "cool"
    assert demand("cool", 26.0, 26.0, 0.5, "cool") is None
    assert demand("cool", 26.4, 26.0, 0.5, "off") is None
    assert demand("cool", 26.6, 26.0, 0.5, "off") == "cool"
    assert demand("heat", 25.6, 26.0, 0.5, "heat") == "heat"
    assert demand("heat", 26.0, 26.0, 0.5, "heat") is None
    assert demand("heat", 25.4, 26.0, 0.5, "off") == "heat"


def test_external_sensor_and_preset_options_are_exposed() -> None:
    flow_source = (COMPONENT / "config_flow.py").read_text()
    climate_source = (COMPONENT / "climate.py").read_text()
    assert "GreeACCloudOptionsFlow" in flow_source
    assert "EntitySelectorConfig" in flow_source
    assert "CONF_TEMPERATURE_SENSORS" in flow_source
    assert "CONF_HUMIDITY_SENSORS" in flow_source
    assert "CONF_OUTDOOR_TEMPERATURE_SENSOR" in flow_source
    assert "CONF_OUTDOOR_HUMIDITY_SENSOR" in flow_source
    assert "if user_input is not None:" in flow_source
    assert "multiple=True" in flow_source
    assert "PRESET_DAY" in flow_source
    assert "CONF_PRESET_ALLOWED_MODES" in flow_source
    assert "multiple=True" in flow_source
    assert "ClimateEntityFeature.PRESET_MODE" in climate_source
    assert "async_track_state_change_event" in climate_source
    assert "async_set_preset_mode" in climate_source
    assert "_async_evaluate_smart_profile" in climate_source
    assert "SMART_COMMAND_COOLDOWN_SECONDS" in climate_source
    assert "smart_effective_target" in climate_source
    assert "_smart_fan_for_demand" in climate_source
    assert "CONF_PRESET_WORK_CURVE" in climate_source
    assert "PRESET_WORK_CURVE_RAPID" in climate_source
    assert "thresholds =" in climate_source
    assert "demand = max(0.0, error)" in climate_source
    assert "Subtracting it from" in climate_source
    assert "_smart_stall_demand_boost" in climate_source
    assert "smart_unmet_minutes" in climate_source
    assert "demand_boost" in climate_source
    assert "full_power_at, reduced_at" in climate_source
    assert '"smart_work_curve"' in climate_source
    assert '"smart_work_curve",' in (COMPONENT / "panel.py").read_text()
    assert "_temperature_hysteresis_mode" in climate_source
    assert "CONF_PRESET_ALLOWED_MODES" in climate_source
    assert "mode_names.get(desired_mode) not in allowed_modes" in climate_source
    assert "active_mode == HVACMode.COOL and current > target" in climate_source
    assert "current > target + deadband" in climate_source
    assert "active_mode == HVACMode.HEAT and current < target" in climate_source
    assert "current < target - deadband" in climate_source
    assert "smart_manual_power_override" in climate_source
    assert "self._smart_dred_level = self._effective_dred_label" in climate_source
    assert "External power change for %s classified as %s" in climate_source
    assert "smart_manual_override_explicit" in climate_source
    assert "command_age" in climate_source
    assert "Ignoring delayed power echo" in climate_source
    assert "External power change" in climate_source
    assert "Profile target updated from climate control" in climate_source
    assert "_expect_power_echo" in climate_source
    assert 'options = ["Pow", "Mod", "SetDeciTem"]' in climate_source
    assert "PRESET_MANUAL" in climate_source
    assert "_smart_dred_for_profile" in climate_source
    assert "Smart must never request it while" in climate_source
    assert (
        'return "D1"'
        not in climate_source[
            climate_source.index("def _smart_dred_for_profile") : climate_source.index(
                "def _effective_smart_target"
            )
        ]
    )
    assert "PRESET_HOLD_FAN" in climate_source
    assert "PRESET_HOLD_D1" in climate_source
    assert '"comfort_circulation_fan"' in climate_source
    assert '"comfort_circulation_d1"' in climate_source
    assert "smart_dred_level" in climate_source
    assert "smart_dred_applied" in climate_source
    assert "smart_dred_verified" in climate_source
    assert '"manual_off"' in climate_source
    assert "_last_observed_power" in climate_source
    assert "def _handle_coordinator_update" in climate_source
    assert "current_humidity" in climate_source
    assert "_average_entities" in climate_source
    assert "sum(values) / len(values)" in climate_source
    panel_source = (COMPONENT / "panel.py").read_text()
    assert "GreePanelRoomSensorsView" in panel_source
    assert "openSensorSettings" in panel_source
    assert "temperature_sensors" in panel_source
    assert 'state["RoomTemperature"]' in panel_source
    assert "Temperatura ambiente${externalRoomTemp" in panel_source
    assert (
        "Profili automatici"
        not in panel_source[
            panel_source.index("async function openSensorSettings") : panel_source.index(
                "async function sendCommand"
            )
        ]
    )
    assert "Salva sensori esterni" in panel_source
    assert "dashboard-summary" in panel_source
    assert "Profili ambiente" in panel_source
    assert "Dettagli tecnici e sonde diagnostiche" in panel_source
    assert 'sidebar-action-label">Configura' in panel_source
    assert "async function setPreset" in panel_source
    assert "Gree Control operations interface" in panel_source
    assert "Controllo climatizzazione" in panel_source
    assert "ops-overview" in panel_source
    assert "renderOperationsDevice" in panel_source
    assert "APRI CONTROLLI AVANZATI" in panel_source
    assert "sidebar-connection" in panel_source
    assert "Riferimento termico esterno" in panel_source
    assert "nav-icon" in panel_source
    assert "Aggiorna ora" in panel_source
    assert "value.name" in panel_source
    assert "config-dialog" in panel_source
    assert "saveOutdoorSensors" in panel_source
    assert "openRoomSensorSettings" in panel_source
    assert "saveRoomSensorAssociations" in panel_source
    assert "roomSensorOptions" in panel_source
    assert "room-sensor-checkbox:checked" in panel_source
    assert "room-sensor-settings" in panel_source
    assert "room-sensor-group ${isTemperature" in panel_source
    assert "'temperature' : 'humidity'" in panel_source
    assert "outdoor-sensor-settings" in panel_source
    assert "Giorno, Notte e Assente si modificano esclusivamente" in panel_source
    sensor_modal_source = panel_source[
        panel_source.index("async function openSensorSettings") : panel_source.index(
            "async function sendCommand"
        )
    ]
    assert "smart_enabled" not in sensor_modal_source
    assert "const presets" not in sensor_modal_source
    assert "profile_control_enabled" not in sensor_modal_source
    assert "function getAccessToken()" in panel_source
    assert "window.localStorage" in panel_source
    assert "window.parent.localStorage" in panel_source
    assert "let _rejectedAccessToken = null" in panel_source
    assert "Prefer the live Home Assistant auth object" in panel_source
    assert "opts.credentials = 'same-origin'" in panel_source
    assert "showPanelAuthFailure" in panel_source
    assert "schedulePanelAuthRetry" in panel_source
    assert "if (!_panelAuthFailureShown) loadData()" in panel_source
    assert "if (!opts.headers.Authorization" in panel_source
    assert "await hass.async_add_executor_job(_load_panel_assets_sync)" in panel_source
    assert "Sessione non disponibile" in panel_source
    assert "Smart (profilo)" not in sensor_modal_source
    assert "Regolazione profili attiva" not in sensor_modal_source
    history_source = (COMPONENT / "frontend" / "panel_history.js").read_text()
    assert "renderEnvironmentChart" in history_source
    assert "ClimateTargetTemperature" in history_source
    assert "outdoorHumiditySensor" in panel_source
    assert "renderChartsPage" in history_source
    assert "new ApexCharts" in history_source
    assert "shared:true" in history_source
    assert "zoom: {enabled:!config.compact" in history_source
    assert "toolbar: {show:false}" in history_source
    assert "item.css === 'outdoor' ? 4" in history_source
    assert "enabledOnSeries:outdoorSeries" in history_source
    assert "fixedMin:0,fixedMax:100" not in history_source
    assert "destroyApexCharts" in history_source
    assert "renderControlCharts" in history_source
    assert "HA Recorder · periodo" in history_source
    assert "compact:true" in history_source
    assert "loadPersistentHistory" in history_source
    assert "shiftHistory" in history_source
    assert "Memoria persistente HA Recorder" in history_source
    assert "GreePanelHistoryView" in (COMPONENT / "panel_history.py").read_text()
    profiles_source = (COMPONENT / "frontend" / "panel_profiles.js").read_text()
    profile_api_source = (COMPONENT / "panel_profiles.py").read_text()
    assert "renderProfilesPage" in profiles_source
    assert "openProfileEditor" in profiles_source
    assert "saveProfileEditor" in profiles_source
    assert "profileEditorModal" in panel_source
    assert "PANEL_PROFILE_URL" in panel_source
    assert "async_reload" in profile_api_source
    assert "Update a single profile" in profile_api_source
    assert 'data-tab="profiles"' in panel_source
    assert "Auto profilo non è Auto Gree" in profiles_source
    assert "allowed_modes" in profiles_source
    assert "hold_action" in profiles_source
    assert "Solo ventola" in profiles_source
    assert "Cool + D1" in profiles_source
    assert "CONF_PRESET_HOLD_ACTION" in profile_api_source
    assert "PRESET_WORK_CURVE_OPTIONS" in profile_api_source
    assert "invalid work curve" in profile_api_source
    assert "pe-work-curve" in profiles_source
    assert "work_curve:document.getElementById('pe-work-curve').value" in profiles_source
    assert "È isteresi di riavvio, non tolleranza" in profiles_source
    assert "ogni due minuti e a ogni variazione" in profiles_source
    assert "smart_temperature_trend_c_per_hour" in profiles_source
    climate_source = (COMPONENT / "climate.py").read_text()
    assert "_record_smart_temperature" in climate_source
    assert "Recorder remains the persistent source" in climate_source
    assert "_trend_adjusted_deadband" in climate_source
    assert "Clima ed energia" in panel_source
    assert "toggleChartExpand" in history_source
    assert "chart-point-group" in panel_source
    assert "aspect-ratio:460 / 580" in panel_source
    assert "orientation:portrait" in history_source
    assert "mobile-menu-button" in panel_source
    assert "mobile-menu-scrim" in panel_source
    assert "toggleMobileMenu" in panel_source
    assert "mobile-menu-open" in panel_source
    assert "body.mobile-menu-open .header" in panel_source
    assert "z-index:43" in panel_source
    assert "ops-power-icon" in panel_source
    assert "toLocaleString" in history_source
    assert "Override manuale" in panel_source
    assert "I-Demand Smart" in panel_source
    assert "applicato" in panel_source
    assert "D3 · limite 75%" in panel_source
    assert "api/services/climate/${val ? 'turn_on' : 'turn_off'}" in panel_source
    assert "api/services/climate/set_temperature" in panel_source


def test_device_command_round_trip() -> None:
    module = _load_protocol_module()
    device = module.GreeDevice(mac="001122334455", name="Test", key="0123456789abcdef")
    encrypted = device.build_command_pack(["Pow", "Mod"], [1, 2])
    assert device.decrypt_pack(encrypted) == {
        "t": "cmd",
        "opt": ["Pow", "Mod"],
        "p": [1, 2],
    }


def test_subunit_parent_mac() -> None:
    module = _load_protocol_module()
    device = module.GreeDevice(mac="00112233445501", name="Test", key="0123456789abcdef")
    assert device.parent_mac == "001122334455"
