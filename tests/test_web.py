import secrets
import tempfile
import unittest
from pathlib import Path

from starlette.testclient import TestClient

from common import sample_bytes
from server import Service
from settings import SUPERVISOR_IP, Settings

INGRESS_HEADERS = {"x-remote-user-id": "test-owner", "x-ingress-path": "/api/hassio_ingress/example"}


class WebTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        self.app = Service(Settings(data_dir=self.data, owner_user_id="test-owner"))
        self.site = TestClient(
            self.app, base_url="http://internal:8099", client=(SUPERVISOR_IP, 1444), headers=INGRESS_HEADERS
        )
        self.site.__enter__()
        self.addCleanup(self.site.__exit__, None, None, None)
        boot = self.site.get("/api/session")
        self.assertEqual(boot.status_code, 200)
        self.csrf = {"x-csrf-token": boot.json()["csrf"]}

    def create(self, category="car_parts"):
        result = self.site.post(
            "/api/drafts",
            headers=self.csrf,
            json={
                "request_id": secrets.token_hex(16),
                "fields": {
                    "title": "Przykładowa część Audi" if category == "car_parts" else "Przykładowa lampka",
                    "description": "Przedmiot demonstracyjny.",
                    "category": category,
                    "location": "Olsztyn",
                    "condition": "używany",
                    "price_minor": 15000,
                    "attributes": {"oem": "oznaczenie testowe"} if category == "car_parts" else {},
                },
            },
        )
        self.assertEqual(result.status_code, 201, result.text)
        return result.json()

    def action(self, draft, name, **extra):
        return self.site.post(
            f"/api/drafts/{draft['id']}/action/{name}",
            headers=self.csrf,
            json={"expected_revision": draft["revision"], **extra},
        )

    def test_draft_to_simulated_and_price_edit(self):
        draft = self.create()
        second = self.create("electronics")
        self.assertNotEqual(draft["id"], second["id"])
        base = f"/api/drafts/{draft['id']}"
        for fmt, mime, color in [("PNG", "png", "blue"), ("JPEG", "jpeg", "red"), ("WEBP", "webp", "yellow")]:
            result = self.site.post(
                base + "/photos",
                headers={
                    **self.csrf,
                    "content-type": "image/" + mime,
                    "x-filename": "example." + mime,
                    "x-expected-revision": str(draft["revision"]),
                },
                content=sample_bytes(fmt=fmt, color=color),
            )
            self.assertEqual(result.status_code, 200, result.text)
            draft = result.json()
        ordered = [photo["id"] for photo in reversed(draft["media"])]
        draft = self.action(draft, "reorder", photo_ids=ordered).json()
        self.assertEqual(draft["media"][0]["id"], ordered[0])
        self.assertEqual(self.site.get(base + "/photos/" + ordered[0]).headers["content-type"], "image/jpeg")
        remove = self.site.post(
            base + "/photos/" + ordered[2] + "/remove",
            headers=self.csrf,
            json={"expected_revision": draft["revision"]},
        )
        self.assertEqual(remove.status_code, 200, remove.text)
        draft = self.action(remove.json(), "request").json()
        preview = self.site.get(base).json()
        self.assertEqual(preview["missing"], [])
        self.assertEqual(preview["fields"]["price_minor"], 15000)
        draft = self.action(draft, "approve", digest=preview["review_digest"]).json()
        self.assertEqual(draft["status"], "approved")
        self.assertEqual(self.action(draft, "publish").json()["code"], "OLX_NOT_CONFIGURED")
        result = self.action(draft, "simulate")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["demo"]["status"], "mock_published")
        self.assertEqual(self.action(draft, "simulate").json(), result.json())
        edit = self.site.patch(
            base,
            headers=self.csrf,
            json={"expected_revision": draft["revision"], "changes": {"price_minor": 14999}},
        )
        self.assertEqual(edit.status_code, 200, edit.text)
        self.assertEqual(edit.json()["status"], "draft")
        self.assertIsNone(edit.json()["approval"])
        self.assertEqual(
            self.site.patch(
                base,
                headers=self.csrf,
                json={"expected_revision": draft["revision"], "changes": {"price_minor": 15000}},
            ).status_code,
            409,
        )

    def test_reject_nonowner_forged_hop_csrf_and_cross_site(self):
        draft = self.create()
        base = f"/api/drafts/{draft['id']}"
        self.assertEqual(self.site.get(base, headers={"x-remote-user-id": "second-user"}).status_code, 403)
        self.assertEqual(self.site.get(base, headers={"x-remote-user-id": ""}).status_code, 403)
        foreign = TestClient(
            self.app,
            base_url="http://internal:8099",
            client=("172.30.33.9", 2333),
            headers={**INGRESS_HEADERS, "x-forwarded-for": SUPERVISOR_IP},
        )
        self.assertEqual(foreign.get(base).status_code, 403)
        self.assertEqual(foreign.get(base + "/photos/key").status_code, 403)
        self.assertEqual(self.site.post(base + "/action/approve", json={}).json()["code"], "CSRF")
        self.assertEqual(
            self.site.post(
                base + "/action/approve", headers={**self.csrf, "sec-fetch-site": "cross-site"}, json={}
            ).json()["code"],
            "CSRF",
        )
        self.assertEqual(
            self.site.post(
                base + "/action/approve", headers=self.csrf, json={"expected_revision": 1, "digest": "0" * 64}
            ).json()["code"],
            "INVALID_STATE",
        )
        self.assertEqual(self.site.get("/mcp").status_code, 404)

    def test_log_does_not_reflect_client_secrets(self):
        secret = secrets.token_urlsafe(32)
        result = self.site.post(
            "/api/olx/start", headers=self.csrf, json={"client_id": "", "client_secret": secret}
        )
        self.assertEqual(result.status_code, 422)
        self.assertNotIn(secret, result.text)

    def test_unbound_setup_shows_only_own_id(self):
        other = Service(Settings(data_dir=self.data / "unbound"))
        with TestClient(
            other, base_url="http://internal:8099", client=(SUPERVISOR_IP, 123), headers=INGRESS_HEADERS
        ) as client:
            data = client.get("/api/session").json()
            self.assertFalse(data["owner"])
            self.assertEqual(data["your_user_id"], "test-owner")
            self.assertIsNone(data["csrf"])
            self.assertEqual(client.get("/api/drafts").status_code, 403)
