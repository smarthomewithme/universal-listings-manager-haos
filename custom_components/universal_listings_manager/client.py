"""Authenticated, local client for the Universal Listings Manager app."""

import json
import re
from dataclasses import dataclass

from aiohttp import ClientError, ClientSession, ClientTimeout

from .const import BRIDGE_PATH, BRIDGE_PORT, PROTOCOL

HASS_APP_HOST = re.compile(r"(?:[a-f0-9]{8}|local)-olx-manager")
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{32,200}")
MAX_REPLY_BYTES = 2 * 1024 * 1024
ERROR_CODE = re.compile(r"[A-Z0-9_]{1,50}")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


class BridgeError(Exception):
    """A sanitized bridge failure suitable for displaying in Home Assistant."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class BridgeStatus:
    """The capabilities of the connected app."""

    version: str
    tools: frozenset[str]


class BridgeClient:
    """Read or call the scoped app adapter over the Home Assistant network."""

    def __init__(self, session: ClientSession, host: str, token: str) -> None:
        if not HASS_APP_HOST.fullmatch(host):
            raise BridgeError("INVALID_HOST", "Enter the internal hostname of the ULM app.")
        if not TOKEN_PATTERN.fullmatch(token):
            raise BridgeError("INVALID_TOKEN", "Enter a valid integration token.")
        self._session = session
        self._url = f"http://{host}:{BRIDGE_PORT}{BRIDGE_PATH}"
        self._token = token

    async def _request(self, method, suffix="", arguments=None):
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        try:
            async with self._session.request(
                method,
                self._url + suffix,
                headers=headers,
                allow_redirects=False,
                timeout=ClientTimeout(total=12, connect=5),
                json=arguments,
            ) as response:
                if response.status in (401, 403):
                    raise BridgeError(
                        "UNAUTHORIZED", "ULM denied the integration token or network connection."
                    )
                if response.status == 404 and not suffix:
                    raise BridgeError("INCOMPATIBLE", "Update the ULM app and enable its integration token.")
                if not 200 <= response.status < 500:
                    raise BridgeError("UNAVAILABLE", "The ULM app returned an unavailable response.")
                body = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    body.extend(chunk)
                    if len(body) > MAX_REPLY_BYTES:
                        raise BridgeError("INVALID_RESPONSE", "The ULM app returned an oversized response.")
                value = json.loads(body)
                if not isinstance(value, dict):
                    raise ValueError
                if response.status >= 400:
                    code = value.get("code")
                    if type(code) is not str or not ERROR_CODE.fullmatch(code):
                        code = "TOOL_REJECTED"
                    message = value.get("message")
                    if type(message) is not str or not 1 <= len(message) <= 400 or self._token in message:
                        message = "ULM rejected this action."
                    raise BridgeError(code, message)
                return value
        except (ClientError, TimeoutError):
            raise BridgeError("UNAVAILABLE", "Could not connect to the ULM app.") from None
        except (ValueError, RecursionError):
            raise BridgeError("INVALID_RESPONSE", "The ULM app returned an invalid response.") from None

    async def status(self) -> BridgeStatus:
        """Check connectivity, authentication and supported bridge tools."""
        data = await self._request("GET")
        tools, version = data.get("tools"), data.get("version")
        if (
            data.get("protocol") != PROTOCOL
            or type(version) is not str
            or not VERSION.fullmatch(version)
            or not isinstance(tools, list)
            or not all(type(it) is str for it in tools)
        ):
            raise BridgeError("INCOMPATIBLE", "Update the ULM app and the integration to matching versions.")
        return BridgeStatus(version, frozenset(tools))

    async def call(self, tool, arguments):
        """Send a tool call without automatic retries or redirects."""
        if not re.fullmatch(r"[a-z_]{1,40}", tool):
            raise BridgeError("INVALID_TOOL", "Invalid ULM tool name.")
        data = await self._request("POST", "/" + tool, {"arguments": arguments})
        result = data.get("result")
        if not isinstance(result, (dict, list)):
            raise BridgeError("INVALID_RESPONSE", "The ULM app returned an invalid tool result.")
        return result
