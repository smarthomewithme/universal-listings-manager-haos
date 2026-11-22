"""Narrow, reversible metadata adapter for the official HA 2026.11 MCP server.

HA Core's MCP formatter discards custom tool metadata. Add fileParams solely
to ULM's own high-level file-upload tool, without changing the endpoint or OAuth.
The compatibility adapter is removed when this integration unloads.
"""

from .const import DOMAIN

TOOL_NAME = "ulm_attach_chat_photos"


def install_chat_file_metadata():
    """Return a cleanup callback restoring the native formatter."""
    from homeassistant.components.mcp_server import server

    original = server._format_tool  # HA Core 2026.11 private compatibility seam
    if getattr(original, "_ulm_file_params_adapter", False):
        return lambda: None

    def format_with_file_params(tool, custom_serializer):
        result = original(tool, custom_serializer)
        if tool.name == TOOL_NAME and getattr(tool, "integration", None) == DOMAIN:
            result.meta = {**(result.meta or {}), "openai/fileParams": ["files"]}
        return result

    format_with_file_params._ulm_file_params_adapter = True
    server._format_tool = format_with_file_params

    def restore_native_formatter():
        if server._format_tool is format_with_file_params:
            server._format_tool = original

    return restore_native_formatter
