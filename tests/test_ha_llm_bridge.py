"""Exercise the transport that Home Assistant Core uses to call the ULM app."""

import base64
import secrets
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image
from pydantic import ValidationError
from starlette.testclient import TestClient

from common import sample_bytes
from ha_llm_bridge import API_PATH, SAFE_TOOLS
from server import Service
from settings import SUPERVISOR_IP, Settings


class HALLMBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.token = secrets.token_urlsafe(32)
        self.direct_token = secrets.token_urlsafe(32)
        self.app = Service(
            Settings(
                data_dir=Path(self.temp.name),
                ha_llm_token=self.token,
                owner_user_id="owner1",
                mcp_enabled=True,
                mcp_bearer_token=self.direct_token,
            )
        )
        self.bridge = self.enterContext(
            TestClient(
                self.app,
                base_url="http://ulm:8102",
                client=("172.30.32.1", 10000),
                headers={"Authorization": "Bearer " + self.token},
            )
        )
        self.panel = TestClient(
            self.app,
            base_url="http://ulm:8099",
            client=(SUPERVISOR_IP, 10001),
            headers={"x-remote-user-id": "owner1", "x-ingress-path": "/ingress/fixture"},
        )
        self.csrf = {"x-csrf-token": self.panel.get("/api/session").json()["csrf"]}

    def tool(self, name, **arguments):
        return self.bridge.post(API_PATH + "/" + name, json={"arguments": arguments})

    def call(self, name, **arguments):
        response = self.tool(name, **arguments)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["result"]

    def test_private_authorization_and_allowed_tools(self):
        self.assertEqual(
            set(self.bridge.get(API_PATH).json()["tools"]),
            SAFE_TOOLS
            | {"prepare_photo_upload", "upload_photo_chunk", "finish_photo_upload", "cancel_photo_upload"},
        )
        self.assertEqual(
            self.bridge.get(API_PATH, headers={"origin": "https://test.invalid"}).status_code, 401
        )
        self.assertEqual(
            self.bridge.get(API_PATH, headers={"Authorization": "Bearer " + self.direct_token}).status_code,
            401,
        )
        outside = TestClient(
            self.app,
            base_url="http://ulm:8102",
            client=("192.168.1.25", 10000),
            headers={"Authorization": "Bearer " + self.token},
        )
        self.assertEqual(outside.get(API_PATH).status_code, 403)
        for forbidden in ("publish_approved", "listings_approve", "refresh_publication", "delete_draft"):
            self.assertEqual(self.tool(forbidden).status_code, 404)
        self.assertEqual(
            self.tool("simulate_publication", draft_id="bad", expected_revision=1, mode="olx").status_code,
            422,
        )
        self.assertEqual(self.bridge.get("/mcp").status_code, 404)
        direct = TestClient(
            self.app,
            base_url="http://ulm:8100",
            client=("172.30.32.1", 10003),
            headers={"Authorization": "Bearer " + self.token},
        )
        self.assertEqual(direct.post("/mcp", json={}).status_code, 401)
        with self.assertRaises(ValidationError):
            Settings(ha_llm_token=self.token, mcp_bearer_token=self.token)

    def test_disabled_bridge_and_direct_mcp(self):
        cal = Service(Settings(data_dir=Path(self.temp.name) / "disabled"))
        with TestClient(cal, base_url="http://ulm:8102", client=("172.30.32.1", 10000)) as disabled:
            self.assertEqual(disabled.get(API_PATH).status_code, 404)

        enabled = Service(Settings(data_dir=Path(self.temp.name) / "enabled", ha_llm_token=self.token))
        with TestClient(
            enabled,
            base_url="http://ulm:8102",
            client=("172.30.32.1", 10000),
            headers={"Authorization": "Bearer " + self.token},
        ) as enabled_bridge:
            self.assertEqual(enabled_bridge.get(API_PATH).status_code, 200)
            self.assertEqual(enabled_bridge.get("/mcp").status_code, 404)

    def test_draft_photo_review_and_mock(self):
        fields = {
            "title": "Example draft",
            "description": "Test description",
            "category": "car_parts",
            "condition": "used",
            "location": "Olsztyn",
            "price_minor": 15500,
        }
        params = {"request_id": "fixture-hass-create", "fields": fields}
        draft = self.call("create_draft", **params)
        draft_id = draft["id"]
        self.assertEqual(self.call("create_draft", **params)["id"], draft_id)
        self.assertEqual([item["id"] for item in self.call("list_drafts")], [draft_id])
        detail = {"draft_id": draft_id}
        updated = self.call(
            "update_draft", **detail, expected_revision=draft["revision"], changes={"notes": "fixture"}
        )
        updated = self.call(
            "update_price", **detail, expected_revision=updated["revision"], price_minor=16000
        )
        self.assertEqual(updated["fields"]["price_minor"], 16000)
        stale = self.tool("update_price", **detail, expected_revision=draft["revision"], price_minor=999)
        self.assertEqual(stale.json()["code"], "REVISION_CONFLICT")
        path = f"/api/drafts/{draft_id}"
        for count, color in enumerate(("red", "blue")):
            photo = self.panel.post(
                path + "/photos",
                content=sample_bytes(color=color, size=(500, 300)),
                headers={
                    **self.csrf,
                    "content-type": "image/png",
                    "x-filename": f"{count}.png",
                    "x-expected-revision": str(updated["revision"]),
                },
            )
            self.assertEqual(photo.status_code, 200, photo.text)
            updated = photo.json()
        photos = self.call("list_media", **detail)
        self.assertEqual(len(photos), 2)
        thumb = self.call("get_image", **detail, photo_id=photos[0]["id"])
        self.assertEqual(thumb["mime_type"], "image/jpeg")
        with Image.open(BytesIO(base64.b64decode(thumb["data"]))) as image:
            self.assertEqual(image.width, 256)
            self.assertEqual(image.format, "JPEG")
        updated = self.call(
            "reorder_media",
            **detail,
            expected_revision=updated["revision"],
            photo_ids=[photo["id"] for photo in photos[::-1]],
        )
        self.assertEqual(self.call("list_media", **detail)[0]["id"], photos[1]["id"])
        queued = self.call("request_review", **detail, expected_revision=updated["revision"])
        preview = self.call("get_review_preview", **detail)
        self.assertEqual(preview["missing"], [])
        self.assertEqual(self.call("get_approval_status", **detail)["status"], "ready_for_review")
        self.assertIn(
            "code", self.tool("simulate_publication", **detail, expected_revision=queued["revision"]).json()
        )

        approved = self.panel.post(
            path + "/action/approve",
            headers=self.csrf,
            json={
                "expected_revision": queued["revision"],
                "digest": preview["review_digest"],
            },
        ).json()
        result = self.call("simulate_publication", **detail, expected_revision=approved["revision"])
        self.assertEqual(result["demo"]["status"], "mock_published")
        self.assertIsNone(self.call("get_publication_status", **detail)["olx"])
        self.assertTrue(any(item["actor"] == "ha_llm" for item in self.call("get_activity_log", **detail)))
        updated = self.call(
            "update_price", **detail, expected_revision=approved["revision"], price_minor=16100
        )
        self.assertIsNone(updated["approval"])
