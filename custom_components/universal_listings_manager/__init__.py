"""Register the ULM integration and its Home Assistant LLM tool set."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import BridgeClient
from .const import CONF_HA_USER, CONF_HOST, CONF_TOKEN
from .integration_sync import start_integration_sync
from .llm_api import ULMAPI
from .mcp_file_metadata import install_chat_file_metadata

ULMConfigEntry = ConfigEntry[BridgeClient]


async def async_setup_entry(hass: HomeAssistant, entry: ULMConfigEntry) -> bool:
    """Expose tools for this configured app instance."""
    client = BridgeClient(async_get_clientsession(hass), entry.data[CONF_HOST], entry.data[CONF_TOKEN])
    entry.runtime_data = client
    entry.async_on_unload(install_chat_file_metadata())
    entry.async_on_unload(start_integration_sync(hass, client))
    entry.async_on_unload(llm.async_register_api(hass, ULMAPI(hass, client, entry.data[CONF_HA_USER])))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ULMConfigEntry) -> bool:
    """Let Home Assistant invoke the LLM unregister callback."""
    return True
