"""Diagnostics without endpoints, credentials or private listing data."""

from . import ULMConfigEntry
from .client import BridgeError
from .llm_api import TOOL_NAMES


async def async_get_config_entry_diagnostics(hass, entry: ULMConfigEntry):
    """Return a redacted health check from the ULM app."""
    try:
        status = await entry.runtime_data.status()
    except BridgeError as exc:
        return {"connected": False, "error_code": exc.code}
    return {
        "connected": True,
        "compatible": TOOL_NAMES <= status.tools,
        "backend_version": status.version,
        "bridge_tool_count": len(status.tools),
    }
