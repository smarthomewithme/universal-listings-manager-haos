import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path

import httpx
import httpx2
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.testclient import TestClient

from common import live_server, sample_bytes
from security import SCOPE
from server import Service
from settings import Settings
from store import now

MCP = "http://127.0.0.1:8100/mcp"
PANEL = "http://127.0.0.1:8099"
ACCEPT = {"accept": "application/json, text/event-stream"}


def tool_body(result):
    return result.structured_content or json.loads(result.content[0].text)


class McpTests(unittest.TestCase):
    def test_streamable_http_sdk_tools_and_restart(self):
        with tempfile.TemporaryDirectory() as data, live_server(data) as token:
            self.assertEqual(httpx.post(MCP, json={}, headers=ACCEPT, trust_env=False).status_code, 401)
            self.assertEqual(httpx.get(PANEL + "/mcp", trust_env=False).status_code, 404)
            self.assertEqual(httpx.get(MCP.replace("/mcp", "/api/drafts"), trust_env=False).status_code, 404)

            async def exercise():
                async with httpx2.AsyncClient(
                    headers={"Authorization": "Bearer " + token}, trust_env=False
                ) as transport:
                    async with streamable_http_client(MCP, http_client=transport) as (read, write):
                        async with ClientSession(read, write) as session:
                            init = await session.initialize()
                            self.assertEqual(init.server_info.name, "Universal Listings Manager")
                            listing = await session.list_tools()
                            descriptors = {entry.name: entry for entry in listing.tools}
                            self.assertEqual(len(descriptors), 27)
                            self.assertTrue(descriptors["get_draft"].annotations.read_only_hint)
                            self.assertTrue(descriptors["publish_approved"].annotations.destructive_hint)
                            self.assertNotIn("approve_listing", descriptors)
                            options = {
                                "fields": {
                                    "title": "Obiektyw",
                                    "category": "electronics",
                                    "location": "Olsztyn",
                                    "condition": "używany",
                                    "description": "Przedmiot demonstracyjny.",
                                },
                                "request_id": "fixed-test-req",
                            }
                            result = await session.call_tool("create_draft", options)
                            self.assertFalse(result.is_error, result.content)
                            draft = tool_body(result)
                            duplicate = tool_body(await session.call_tool("create_draft", options))
                            self.assertEqual(draft["id"], duplicate["id"])
                            result = await session.call_tool(
                                "update_draft",
                                {
                                    "draft_id": draft["id"],
                                    "expected_revision": 1,
                                    "changes": {"price_minor": 15000},
                                },
                            )
                            changed = tool_body(result)
                            self.assertEqual(changed["revision"], 2)
                            stale = await session.call_tool(
                                "update_draft",
                                {
                                    "draft_id": draft["id"],
                                    "expected_revision": 1,
                                    "changes": {"price_minor": 10},
                                },
                            )
                            self.assertTrue(stale.is_error, stale.content)
                            approval = tool_body(
                                await session.call_tool("listings_approve", {"draft_id": draft["id"]})
                            )
                            self.assertEqual(approval["code"], "OWNER_PANEL_REQUIRED")
                            draft_path = "/api/drafts/" + draft["id"]
                            async with httpx.AsyncClient(base_url=PANEL, trust_env=False) as panel:
                                csrf = (await panel.get("/api/session")).json()["csrf"]
                                uploaded = await panel.post(
                                    draft_path + "/photos",
                                    content=sample_bytes(),
                                    headers={
                                        "x-csrf-token": csrf,
                                        "x-expected-revision": "2",
                                        "content-type": "image/png",
                                        "x-filename": "mcp.png",
                                    },
                                )
                                self.assertEqual(uploaded.status_code, 200, uploaded.text)
                                uploaded = uploaded.json()
                                media = await session.call_tool(
                                    "get_image",
                                    {"draft_id": draft["id"], "photo_id": uploaded["media"][0]["id"]},
                                )
                                self.assertFalse(media.is_error, media.content)
                                self.assertEqual(media.content[0].mime_type, "image/jpeg")
                                self.assertTrue(
                                    base64.b64decode(media.content[0].data).startswith(b"\xff\xd8")
                                )
                                queued = tool_body(
                                    await session.call_tool(
                                        "request_review",
                                        {"draft_id": draft["id"], "expected_revision": uploaded["revision"]},
                                    )
                                )
                                pending = await session.call_tool(
                                    "publish_approved",
                                    {
                                        "draft_id": draft["id"],
                                        "expected_revision": queued["revision"],
                                        "mode": "mock",
                                    },
                                )
                                self.assertTrue(pending.is_error)
                                preview = (await panel.get(draft_path)).json()
                                approved = await panel.post(
                                    draft_path + "/action/approve",
                                    headers={"x-csrf-token": csrf},
                                    json={
                                        "expected_revision": queued["revision"],
                                        "digest": preview["review_digest"],
                                    },
                                )
                                self.assertEqual(approved.status_code, 200, approved.text)
                            mock = tool_body(
                                await session.call_tool(
                                    "publish_approved",
                                    {
                                        "draft_id": draft["id"],
                                        "expected_revision": approved.json()["revision"],
                                        "mode": "mock",
                                    },
                                )
                            )
                            self.assertEqual(mock["demo"]["status"], "mock_published")
                            result = await session.call_tool("list_drafts", {})
                            self.assertFalse(result.is_error)
                            return draft["id"]

            draft_id = asyncio.run(exercise())
            listing = httpx.get(PANEL + "/api/drafts/" + draft_id, trust_env=False)
            self.assertEqual(listing.json()["fields"]["price_minor"], 15000)
        with tempfile.TemporaryDirectory() as data:
            work = Path(data)
            with live_server(work):
                with httpx.Client(base_url=PANEL, trust_env=False) as client:
                    csrf = client.get("/api/session").json()["csrf"]
                    item = client.post(
                        "/api/drafts",
                        headers={"x-csrf-token": csrf},
                        json={"request_id": "restart-check", "fields": {"title": "Dane po restarcie"}},
                    ).json()
                    original = sample_bytes()
                    uploaded = client.post(
                        "/api/drafts/" + item["id"] + "/photos",
                        headers={
                            "x-csrf-token": csrf,
                            "x-expected-revision": "1",
                            "content-type": "image/png",
                            "x-filename": "restart.png",
                        },
                        content=original,
                    )
                    self.assertEqual(uploaded.status_code, 200, uploaded.text)
            with live_server(work, enabled=False):
                address = PANEL + "/api/drafts/" + item["id"]
                result = httpx.get(address, trust_env=False).json()
                self.assertEqual(result["fields"]["title"], "Dane po restarcie")
                photo = httpx.get(
                    address + "/photos/" + result["media"][0]["id"] + "?original=1", trust_env=False
                )
                self.assertEqual(photo.content, original)
                with self.assertRaises(httpx.ConnectError):
                    httpx.get(MCP, timeout=1, trust_env=False)

    def test_remote_oauth_signature_issuer_subject_scope_and_audience(self):
        with tempfile.TemporaryDirectory() as folder:
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
            jwk.update({"kid": "fixture", "alg": "RS256", "use": "sig"})
            settings = Settings(
                data_dir=Path(folder),
                mcp_enabled=True,
                mcp_resource_url="https://mcp.example.invalid/mcp",
                mcp_issuer="https://id.example.invalid",
                mcp_jwks_url="https://id.example.invalid/jwks",
                mcp_subject="owner-account",
            )

            def respond(req):
                self.assertEqual(str(req.url), settings.mcp_jwks_url)
                return httpx.Response(200, json={"keys": [jwk]})

            jwks = httpx.AsyncClient(transport=httpx.MockTransport(respond))
            app = Service(settings, jwks_http=jwks)
            with TestClient(app, base_url="http://private:8100") as client:
                response = client.post("/mcp", json={}, headers=ACCEPT)
                self.assertEqual(response.status_code, 401)
                self.assertIn("resource_metadata", response.headers["www-authenticate"])
                self.assertEqual(
                    client.get("/.well-known/oauth-protected-resource/mcp").json()["resource"],
                    settings.mcp_resource_url,
                )
                body = {
                    "iss": settings.mcp_issuer,
                    "aud": settings.mcp_resource_url,
                    "sub": settings.mcp_subject,
                    "iat": now(),
                    "exp": now() + 60,
                    "scope": SCOPE,
                }

                def call(changes, extra=None):
                    token = jwt.encode(
                        {**body, **changes}, key, algorithm="RS256", headers={"kid": "fixture"}
                    )
                    return client.post(
                        "/mcp",
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                            "params": {
                                "protocolVersion": "2025-11-25",
                                "capabilities": {},
                                "clientInfo": {"name": "test", "version": "1"},
                            },
                        },
                        headers={**ACCEPT, "Authorization": "Bearer " + token, **(extra or {})},
                    )

                for payload in [
                    {"sub": "other"},
                    {"aud": "wrong"},
                    {"scope": "read"},
                    {"iss": "https://forged.invalid"},
                    {"exp": now() - 10},
                ]:
                    self.assertEqual(call(payload).status_code, 401)
                self.assertEqual(call({}, {"origin": "https://external.invalid"}).status_code, 403)
                response = call({})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(
                    response.json()["result"]["serverInfo"]["name"], "Universal Listings Manager"
                )
