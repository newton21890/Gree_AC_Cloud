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
    assert source.count("requires_auth = True") == 9
    assert source.count("requires_auth = False") == 1


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
    assert "verify=False" not in sources
    assert "ssl.CERT_NONE" not in sources


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
    script_start = panel_html.find("<script>")
    script_end = panel_html.rfind("</script>")
    assert script_start >= 0 and script_end > script_start

    with tempfile.NamedTemporaryFile("w", suffix=".js") as script:
        script.write(panel_html[script_start + len("<script>"):script_end])
        script.flush()
        subprocess.run(
            [node, "--check", script.name], check=True, capture_output=True
        )


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
    assert 'class GreeStartupDemandResponseSelect' in select_source
    assert '_attr_translation_key = "startup_dred_level"' in select_source
    assert 'STARTUP_DRED_NO_ACTION = "No action"' in const_source
    assert "async_set_startup_dred" in select_source


def test_external_sensor_and_preset_options_are_exposed() -> None:
    flow_source = (COMPONENT / "config_flow.py").read_text()
    climate_source = (COMPONENT / "climate.py").read_text()
    assert "GreeACCloudOptionsFlow" in flow_source
    assert "EntitySelectorConfig" in flow_source
    assert "CONF_TEMPERATURE_SENSORS" in flow_source
    assert "CONF_HUMIDITY_SENSORS" in flow_source
    assert "CONF_OUTDOOR_TEMPERATURE_SENSOR" in flow_source
    assert "multiple=True" in flow_source
    assert "PRESET_DAY" in flow_source
    assert "ClimateEntityFeature.PRESET_MODE" in climate_source
    assert "async_track_state_change_event" in climate_source
    assert "async_set_preset_mode" in climate_source
    assert "_async_evaluate_smart_profile" in climate_source
    assert "SMART_COMMAND_COOLDOWN_SECONDS" in climate_source
    assert "smart_effective_target" in climate_source
    assert "_smart_fan_for_demand" in climate_source
    assert "smart_manual_power_override" in climate_source
    assert "smart_manual_override_explicit" in climate_source
    assert "_expect_power_echo" in climate_source
    assert 'options = ["Pow", "Mod", "SetDeciTem"]' in climate_source
    assert "PRESET_MANUAL" in climate_source
    assert "_smart_dred_for_profile" in climate_source
    assert "smart_dred_level" in climate_source
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
    assert "Profili automatici" in panel_source
    assert "Salva configurazione" in panel_source
    assert "dashboard-summary" in panel_source
    assert "Profili ambiente" in panel_source
    assert "Dettagli tecnici e sonde diagnostiche" in panel_source
    assert "sidebar-action-label\">Configura" in panel_source
    assert "async function setPreset" in panel_source
    assert "Gree Control operations interface" in panel_source
    assert "Controllo climatizzazione" in panel_source
    assert "ops-overview" in panel_source
    assert "renderOperationsDevice" in panel_source
    assert "APRI CONTROLLI AVANZATI" in panel_source
    assert "sidebar-connection" in panel_source
    assert "nav-icon" in panel_source
    assert "Aggiorna ora" in panel_source
    assert "value.name" in panel_source
    assert "config-dialog" in panel_source
    assert "saveAllRoomSensors" in panel_source
    assert "smart_enabled" in panel_source
    assert "outdoor_compensation" in panel_source
    assert "Isteresi" in panel_source
    assert "function getAccessToken()" in panel_source
    assert "window.localStorage" in panel_source
    assert "window.parent.localStorage" in panel_source
    assert "opts.credentials = 'same-origin'" in panel_source
    assert "Sessione non disponibile" in panel_source
    assert "Smart (profilo)" in panel_source
    assert "Regolazione profili attiva" in panel_source
    assert "renderEnvironmentChart" in panel_source
    assert "renderChartsPage" in panel_source
    assert "Andamento climatico" in panel_source
    assert "Override manuale" in panel_source
    assert "I-Demand Smart" in panel_source
    assert "api/services/climate/${val ? 'turn_on' : 'turn_off'}" in panel_source


def test_device_command_round_trip() -> None:
    module = _load_protocol_module()
    device = module.GreeDevice(
        mac="001122334455", name="Test", key="0123456789abcdef"
    )
    encrypted = device.build_command_pack(["Pow", "Mod"], [1, 2])
    assert device.decrypt_pack(encrypted) == {
        "t": "cmd",
        "opt": ["Pow", "Mod"],
        "p": [1, 2],
    }


def test_subunit_parent_mac() -> None:
    module = _load_protocol_module()
    device = module.GreeDevice(
        mac="00112233445501", name="Test", key="0123456789abcdef"
    )
    assert device.parent_mac == "001122334455"
