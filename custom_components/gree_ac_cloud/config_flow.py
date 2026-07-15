import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult

from .const import CONF_SERVER, DOMAIN, GREE_CLOUD_SERVERS
from .gree_api import api_login

_LOGGER = logging.getLogger(__name__)


class GreeACCloudConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors = {}

        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_USERNAME])
            self._abort_if_unique_id_configured()

            server = GREE_CLOUD_SERVERS.get(
                user_input[CONF_SERVER], "eugrih.gree.com"
            )
            try:
                uid, token = await self.hass.async_add_executor_job(
                    api_login,
                    server,
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                )
                _LOGGER.info("Gree Cloud login OK: uid=%s", uid)
                _LOGGER.warning(
                    "Gree AC Cloud connected. Note: this will log out "
                    "the official Gree+ App (one session per account)."
                )
                return self.async_create_entry(
                    title=f"Gree AC Cloud ({user_input[CONF_USERNAME]})",
                    data=user_input,
                )
            except ValueError as err:
                errors["base"] = "invalid_auth"
                _LOGGER.warning("Auth failed: %s", err)
            except Exception as err:
                errors["base"] = "cannot_connect"
                _LOGGER.error("Connection failed: %s", err)

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_SERVER, default="Europe"
                ): vol.In(list(GREE_CLOUD_SERVERS)),
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )
