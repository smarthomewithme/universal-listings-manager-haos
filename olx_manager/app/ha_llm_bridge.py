"""Private, allowlisted transport from Home Assistant Core to ULM's existing tools."""

import base64
import binascii
from io import BytesIO
from ipaddress import ip_address, ip_network

import anyio
from mcp import types
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from PIL import Image
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from media import DomainError
from models import Model
from security import equal
from version import VERSION
from web import SAFE, error_response, form, validation_error

API_PATH = "/bridge/v1/tools"
HA_NETWORK = ip_network("172.30.32.0/23")
SAFE_TOOLS = frozenset(
    {
        "list_drafts",
        "get_draft",
        "create_draft",
        "update_draft",
        "list_media",
        "get_image",
        "update_price",
        "reorder_media",
        "get_review_preview",
        "request_review",
        "get_approval_status",
        "simulate_publication",
        "get_publication_status",
        "get_activity_log",
    }
)


class ToolCall(Model):
    arguments: dict


def _response(result, status=200):
    return JSONResponse(result, status_code=status, headers=SAFE)


def _thumbnail(content):
    # HA's MCP server serializes LLM results as JSON text. Bound the single thumbnail
    # that can be included in that JSON, while keeping the direct ULM MCP unchanged.
    try:
        with Image.open(BytesIO(base64.b64decode(content.data, validate=True))) as image:
            image.thumbnail((256, 256))
            output = BytesIO()
            image.convert("RGB").save(output, format="JPEG", quality=68)
            return {
                "mime_type": "image/jpeg",
                "encoding": "base64",
                "data": base64.b64encode(output.getvalue()).decode(),
                "width": image.width,
                "height": image.height,
            }
    except (OSError, ValueError, binascii.Error) as exc:
        raise DomainError("THUMBNAIL_UNAVAILABLE", "The preview image could not be read.", 502) from exc


def _run(registry, name, args):
    target = name
    if name == "simulate_publication":
        if set(args) != {"draft_id", "expected_revision"}:
            raise DomainError("INVALID_ARGUMENTS", "Supply a draft ID and its approved revision.", 422)
        target = "publish_approved"
        args = {**args, "mode": "mock"}

    result = anyio.run(registry.call_tool, target, args)
    if not isinstance(result, types.CallToolResult):
        raise DomainError("INVALID_RESPONSE", "Unexpected ULM response.", 502)
    if result.is_error:
        details = result.structured_content or {}
        raise DomainError(
            details.get("code", "INVALID_ARGUMENTS"), details.get("message", "The request is invalid."), 409
        )
    if result.structured_content is not None:
        return result.structured_content
    if name == "get_image" and len(result.content) == 1 and isinstance(result.content[0], types.ImageContent):
        return _thumbnail(result.content[0])
    # The ULM SDK converts a list of Python records to one text block per record.
    # A single dictionary becomes one JSON text block.
    import json

    values = []
    for part in result.content:
        if not isinstance(part, types.TextContent):
            raise DomainError("INVALID_RESPONSE", "Unexpected ULM response.", 502)
        values.append(json.loads(part.text))
    if name in {"list_drafts", "list_media", "get_activity_log"}:
        return values
    if len(values) != 1 or not isinstance(values[0], dict):
        raise DomainError("INVALID_RESPONSE", "Unexpected ULM response.", 502)
    return values[0]


def build_ha_llm_bridge(settings, registry):
    def require_access(request):
        client = request.client
        if client is None:
            raise DomainError("FORBIDDEN", "Home Assistant network access required.", 403)
        address = ip_address(client.host)
        if address not in HA_NETWORK and not (settings.dev_panel and address.is_loopback):
            raise DomainError("FORBIDDEN", "Home Assistant network access required.", 403)
        header = request.headers.get("authorization", "")
        if (
            "origin" in request.headers
            or "upgrade" in request.headers
            or len(header) > 256
            or not equal(header, "Bearer " + settings.ha_llm_token)
        ):
            raise DomainError("UNAUTHORIZED", "Invalid Home Assistant integration token.", 401)

    async def status(request):
        require_access(request)
        return _response({"protocol": 1, "version": VERSION, "tools": sorted(SAFE_TOOLS)})

    async def execute(request):
        require_access(request)
        name = request.path_params["name"]
        if name not in SAFE_TOOLS:
            raise DomainError("TOOL_NOT_ALLOWED", "The tool is not exposed to Home Assistant.", 404)
        data = await form(request, ToolCall)
        try:
            result = await anyio.to_thread.run_sync(_run, registry, name, data.arguments)
        except UnexpectedToolError as exc:
            raise DomainError("TOOL_FAILURE", "ULM failed to process the request.", 502) from exc
        except ToolError as exc:
            raise DomainError("INVALID_ARGUMENTS", "The tool arguments are invalid.", 422) from exc
        return _response({"result": result})

    async def domain_error(request, exc):
        return error_response(exc)

    from pydantic import ValidationError

    return Starlette(
        routes=[Route(API_PATH, status), Route(API_PATH + "/{name}", execute, methods=["POST"])],
        exception_handlers={DomainError: domain_error, ValidationError: validation_error},
    )
