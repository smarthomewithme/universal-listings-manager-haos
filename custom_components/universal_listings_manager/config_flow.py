"""Configure the ULM app connector in Home Assistant."""

import probatio
from homeassistant.config_entries import ConfigFlow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .client import BridgeClient, BridgeError
from .const import CONF_HA_USER, CONF_HOST, CONF_TOKEN, DEFAULT_HOST, DOMAIN, NAME
from .llm_api import TOOL_NAMES


class ULMConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up or reconfigure the authenticated internal connection."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        self._async_abort_entries_match()
        return await self._step("user", user_input, {})

    async def async_step_reconfigure(self, user_input=None):
        entry = self._get_reconfigure_entry()
        return await self._step("reconfigure", user_input, entry.data)

    async def _step(self, step, user_input, saved):
        users = await self.hass.auth.async_get_users()
        admins = [
            {"value": user.id, "label": user.name or user.id}
            for user in users
            if user.is_admin and user.is_active and not user.system_generated
        ]
        if not admins:
            return self.async_abort(reason="no_admin")
        errors = {}
        if user_input is not None:
            fields = {**user_input, CONF_HOST: user_input[CONF_HOST].strip().lower()}
            if fields[CONF_HA_USER] not in {item["value"] for item in admins}:
                errors[CONF_HA_USER] = "invalid_user"
            else:
                errors = await self._validate(fields)
            if not errors:
                if step == "reconfigure":
                    return self.async_update_reload_and_abort(self._get_reconfigure_entry(), data=fields)
                return self.async_create_entry(title=NAME, data=fields)

        selected = (user_input or saved).get(CONF_HA_USER)
        if selected not in {item["value"] for item in admins}:
            selected = admins[0]["value"]
        schema = probatio.Schema(
            {
                probatio.Required(CONF_HOST, default=(user_input or saved).get(CONF_HOST, DEFAULT_HOST)): str,
                probatio.Required(CONF_TOKEN): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
                probatio.Required(CONF_HA_USER, default=selected): SelectSelector(
                    SelectSelectorConfig(options=admins)
                ),
            }
        )
        return self.async_show_form(step_id=step, data_schema=schema, errors=errors)

    async def _validate(self, fields):
        try:
            client = BridgeClient(async_get_clientsession(self.hass), fields[CONF_HOST], fields[CONF_TOKEN])
            status = await client.status()
            if not TOOL_NAMES <= status.tools:
                return {"base": "incompatible"}
        except BridgeError as exc:
            errors = {
                "INVALID_HOST": {CONF_HOST: "invalid_host"},
                "INVALID_TOKEN": {CONF_TOKEN: "invalid_token"},
                "UNAUTHORIZED": {"base": "invalid_auth"},
                "INCOMPATIBLE": {"base": "incompatible"},
            }
            return errors.get(exc.code, {"base": "cannot_connect"})
        return {}
