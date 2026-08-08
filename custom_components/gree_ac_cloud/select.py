"""Select entities for enumerated Gree U-Match functions."""

from homeassistant.components.select import SelectEntity
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, DRED_OPTIONS, DRED_OPTIONS_REV
from .entity import GreeDeviceEntity


async def async_setup_entry(hass, entry, async_add_entities: AddEntitiesCallback):
    """Set up U-Match select entities and expose I-Demand controls in HA."""
    coordinators = entry.runtime_data["coordinators"]

    # Versions up to 0.2.11 registered this control disabled by the
    # integration. Re-enable only entries disabled by the integration itself;
    # a control explicitly disabled by the user remains untouched.
    registry = er.async_get(hass)
    for coordinator in coordinators:
        unique_id = f"{coordinator.device.mac}_dred_level"
        entity_id = registry.async_get_entity_id("select", DOMAIN, unique_id)
        if entity_id is None:
            continue
        registry_entry = registry.async_get(entity_id)
        if (
            registry_entry
            and registry_entry.disabled_by == er.RegistryEntryDisabler.INTEGRATION
        ):
            registry.async_update_entity(entity_id, disabled_by=None)

    async_add_entities(
        GreeDemandResponseSelect(coordinator) for coordinator in coordinators
    )


class GreeDemandResponseSelect(GreeDeviceEntity, SelectEntity):
    """Control the DRED level selected by the wired controller I-DEMAND menu."""

    _attr_translation_key = "dred_level"
    _attr_icon = "mdi:transmission-tower-import"
    _attr_options = list(DRED_OPTIONS_REV)
    # This is an operating control, not a diagnostic/config-only entity. Keep
    # it enabled and in the main device controls so it is usable in HA cards,
    # automations and the select.select_option service.
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator):
        super().__init__(coordinator, coordinator.device, key_suffix="dred_level")
        self._attr_unique_id = f"{coordinator.device.mac}_dred_level"

    @property
    def available(self) -> bool:
        """DRED is available only when explicitly supported and in cooling."""
        data = self.coordinator.data
        return (
            super().available
            and data.get("DREDEn") == 1
            and "DRED" in data
            and data.get("Pow") == 1
            and data.get("Mod") == 1
        )

    @property
    def current_option(self) -> str | None:
        data = self.coordinator.data
        raw = data.get("DRED")
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None
        # One verified controller reports I-Demand/D1 as Idemand=1,DRED=0;
        # another reports D1 directly as DRED=1.
        try:
            idemand_active = int(data.get("Idemand", 0)) == 1
        except (TypeError, ValueError):
            idemand_active = False
        if value == 0 and idemand_active:
            value = 1
        return DRED_OPTIONS.get(value)

    @property
    def extra_state_attributes(self):
        return {
            "protocol_property": "DRED",
            "i_demand_flag": self.coordinator.data.get("Idemand"),
            "dred_enabled": self.coordinator.data.get("DREDEn"),
            "verified_levels": [0, 1, 2, 3],
            "d1_verified": True,
            "level_meanings": {
                "D1": "compressor disabled; indoor fan may continue",
                "D2": "electrical demand capped at no more than 50%",
                "D3": "electrical demand capped at no more than 75%",
            },
            "note": "All levels were verified from the XE7A wired controllers; percentages are DRED demand ceilings, not measured consumption.",
        }

    async def async_select_option(self, option: str) -> None:
        if option not in DRED_OPTIONS_REV:
            raise ValueError(f"Unsupported DRED option: {option}")

        value = DRED_OPTIONS_REV[option]
        if await self.coordinator._mqtt.send_command(
            self.coordinator.device.mac, ["DRED"], [value]
        ):
            self.coordinator.device.properties["DRED"] = value
            # The device proved that entering a DRED level cancels Quiet.
            if value:
                self.coordinator.device.properties["Quiet"] = 0
            self.coordinator.async_set_updated_data(
                dict(self.coordinator.device.properties)
            )
