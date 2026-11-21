import asyncio
import json
import os
import urllib.error
import urllib.request

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

PANEL = "http://127.0.0.1:8099"
MCP = "http://127.0.0.1:8100/mcp"


async def main():
    with urllib.request.urlopen(PANEL + "/health", timeout=5) as health:
        assert json.load(health)["version"] == os.environ["ULM_EXPECTED_VERSION"]
    try:
        urllib.request.urlopen(MCP, timeout=5)
    except urllib.error.HTTPError as error:
        assert error.code == 401
    else:
        raise AssertionError("Expected MCP to require authorization")
    token = os.environ["MCP_BEARER_TOKEN"]
    async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + token}, trust_env=False) as http:
        async with streamable_http_client(MCP, http_client=http) as (read, write):
            async with ClientSession(read, write) as session:
                result = await session.initialize()
                assert result.server_info.version == os.environ["ULM_EXPECTED_VERSION"]
                tools = await session.list_tools()
                assert any(tool.name == "create_draft" for tool in tools.tools)
                reply = await session.call_tool(
                    "create_draft",
                    {
                        "request_id": "container-smoke",
                        "fields": {"title": "Test w kontenerze"},
                    },
                )
                assert not reply.is_error, reply
    print("PASS docker smoke")


if __name__ == "__main__":
    asyncio.run(main())
