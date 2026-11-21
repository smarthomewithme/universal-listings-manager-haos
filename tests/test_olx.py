import io
import json
import secrets
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
from PIL import Image
from starlette.testclient import TestClient

from common import OWNER, ready, sample_bytes
from marketplace import API, TOKEN, OlxMarketplaceAdapter
from media import ConflictError, DomainError, decode
from publication import PublicationService
from server import Service
from settings import Settings
from store import ListingStore, now


class OlxFixture:
    def __init__(self):
        self.access = secrets.token_urlsafe(24)
        self.refresh = secrets.token_urlsafe(24)
        self.calls = []
        self.writes = []
        self.persisted = {}
        self.live_status = "new"
        self.drop_response = False
        self.empty_attribute = False

    def http(self):
        return httpx.Client(transport=httpx.MockTransport(self.handle), trust_env=False)

    def handle(self, request):
        self.calls.append((request.method, request.url.path))

        def reply(data, status=200):
            return httpx.Response(status, json={"data": data})

        if str(request.url) == TOKEN:
            form = json.loads(request.content)
            if form["grant_type"] == "refresh_token" and form["refresh_token"] != self.refresh:
                return reply({}, 401)
            return httpx.Response(
                200, json={"access_token": self.access, "refresh_token": self.refresh, "expires_in": 3600}
            )
        assert str(request.url).startswith(API + "/")
        assert request.headers["Authorization"] == f"Bearer {self.access}"
        path = request.url.path.removeprefix("/api/partner")
        if path == "/users/me":
            return reply({"id": 11, "is_business": False, "status": "confirmed"})
        if path == "/categories/123":
            return reply({"id": 123, "is_leaf": True, "photos_limit": 5})
        if path == "/categories/123/attributes":
            return reply(
                [
                    {
                        "code": "quality",
                        "validation": {"type": "attribute", "required": True},
                        "values": [{"code": "used"}],
                    },
                    {"code": "delivery", "validation": {"type": "package", "required": False}, "values": []},
                    {
                        "code": "price",
                        "validation": {"type": "price", "numeric": True, "required": True, "min": 1},
                    },
                ]
            )
        if path == "/cities/321":
            return reply({"id": 321, "name": "Gdańsk"})
        if path == "/cities/321/districts":
            return reply([])
        if request.method == "GET" and path == "/adverts":
            return reply(
                [x for key, x in self.persisted.items() if key == request.url.params.get("external_id")]
            )
        if request.method == "GET" and path == "/adverts/45":
            advert = next(iter(self.persisted.values()))
            return reply({**advert, "status": self.live_status})
        if request.method in ("POST", "PUT") and path in ("/adverts", "/adverts/45", "/adverts/45/commands"):
            payload = json.loads(request.content)
            self.writes.append((request.method, path, payload))
            if path.endswith("/commands"):
                assert payload == {"command": "deactivate", "is_success": True}
                self.live_status = "removed_by_user"
                return httpx.Response(204)
            result = {
                "id": 45,
                "external_id": payload["external_id"],
                "status": self.live_status,
                "url": "https://www.olx.pl/oferta/test-fixture.html",
            }
            self.persisted[result["external_id"]] = result
            if self.drop_response:
                raise httpx.ReadTimeout("simulated dropped response")
            return reply(result)
        raise AssertionError(f"Unexpected fixture request {request.method} {path}")


class PublicationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        self.store = ListingStore(self.data)
        self.fixture = OlxFixture()
        self.setup_adapter()

    def setup_adapter(self, **options):
        params = {
            "data_dir": self.data,
            "olx_write_enabled": True,
            "olx_public_base": "https://example.invalid",
            "olx_allowed_categories": "123",
            "olx_no_charge_confirmed": True,
        }
        self.settings = Settings.model_validate({**params, **options})
        self.adapter = OlxMarketplaceAdapter(self.store, self.settings, self.fixture.http())
        self.addCleanup(self.adapter.http.close)
        self.pub = PublicationService(self.store, self.adapter, self.settings)

    def link_account(self):
        url = self.adapter.start_oauth("generated-fixture-client", secrets.token_urlsafe(36))
        state = parse_qs(urlsplit(url).query)["state"][0]
        self.adapter.callback(state, "fixture-code")
        return state

    def draft(self, **extra):
        return ready(
            self.store,
            **{
                "olx_category_id": 123,
                "olx_city_id": 321,
                "olx_contact_name": "Testowy sprzedawca",
                "olx_attributes": {"quality": "used"},
                **extra,
            },
        )

    def test_mock_twice_is_single_result_and_live_is_blocked_without_oauth(self):
        draft = self.draft()
        args = (draft["id"], draft["revision"])
        with self.assertRaisesRegex(DomainError, "oczekuje na połączenie"):
            self.pub.publish(*args, "olx", "mcp")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.pub.publish(*args, "mock", "mcp"), [0, 1]))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0]["demo"]["status"], "mock_published")
        self.assertTrue(results[0]["demo"]["provider_url"].startswith("#demo/"))
        self.assertIsNone(results[0]["olx"])
        self.assertEqual(self.fixture.calls, [])

    def test_oauth_state_is_single_use_and_tokens_are_encrypted_and_refreshable(self):
        self.adapter.start_oauth("fixture", secrets.token_urlsafe(36))
        with self.assertRaises(DomainError):
            self.adapter.callback("bad-state", "unused")
        self.assertEqual(self.fixture.calls, [])
        state = self.link_account()
        with self.assertRaises(DomainError):
            self.adapter.callback(state, "replay")
        account = self.adapter._load()
        raw = self.store.load_account()
        self.assertNotIn(self.fixture.access.encode(), raw)
        self.assertNotIn(self.fixture.refresh.encode(), raw)
        self.assertNotIn(self.fixture.access.encode(), self.store.db_path.read_bytes())
        account["expiry"] = now()
        self.adapter._save(account)
        self.setup_adapter()
        self.assertEqual(self.adapter.account()["access_token"], self.fixture.access)
        self.assertEqual(len([x for x in self.fixture.calls if x[1].endswith("/token")]), 2)

    def test_gates_zero_writes(self):
        self.link_account()
        for case, data, code in [
            ("closed", {"olx_category_id": 999}, "OLX_CATEGORY_CLOSED"),
            ("missing", {"olx_attributes": {}}, "OLX_ATTRIBUTES"),
            ("location", {"location": "Warszawa"}, "OLX_LOCATION"),
            ("delivery", {"delivery": "shipping", "shipping_minor": 2000}, "OLX_DELIVERY_UNSUPPORTED"),
        ]:
            draft = self.draft(**data)
            with self.subTest(case=case), self.assertRaises(DomainError) as caught:
                self.pub.publish(draft["id"], draft["revision"], "olx", "mcp")
            self.assertEqual(caught.exception.code, code)
        draft = self.draft()
        changed = self.store.stage_price(draft["id"], draft["revision"], 15500, "mcp")
        with self.assertRaises(ConflictError):
            self.pub.publish(draft["id"], draft["revision"], "olx", "mcp")
        with self.assertRaises(DomainError):
            self.pub.publish(changed["id"], changed["revision"], "olx", "mcp")
        draft = self.draft()
        with self.store.connect(write=True) as db:
            db.execute("UPDATE approvals SET expires_at=0 WHERE draft_id=?", (draft["id"],))
        with self.assertRaises(DomainError) as expired:
            self.pub.publish(draft["id"], draft["revision"], "olx", "mcp")
        self.assertEqual(expired.exception.code, "APPROVAL_INVALID")
        self.setup_adapter(olx_write_enabled=False)
        draft = self.draft()
        with self.assertRaises(DomainError) as disabled:
            self.pub.publish(draft["id"], draft["revision"], "olx", "mcp")
        self.assertEqual(disabled.exception.code, "OLX_WRITE_DISABLED")
        self.setup_adapter(olx_no_charge_confirmed=False)
        with self.assertRaises(DomainError) as fee:
            self.pub.publish(draft["id"], draft["revision"], "olx", "mcp")
        self.assertEqual(fee.exception.code, "OLX_FEES_UNVERIFIED")
        self.assertEqual(self.fixture.writes, [])

    def test_changed_blob_blocks_both_adapters(self):
        self.link_account()
        draft = self.draft()
        with self.store.connect(write=True) as db:
            db.execute("UPDATE media_assets SET original=? WHERE id=?", (b"wrong", draft["media"][0]["id"]))
        for mode in ("mock", "olx"):
            with self.assertRaises(DomainError) as bad:
                self.pub.publish(draft["id"], draft["revision"], mode, "mcp")
            self.assertEqual(bad.exception.code, "IMAGE_INTEGRITY")
        self.assertEqual(self.fixture.writes, [])

    def test_olx_timeout_read_back_and_no_resend_after_restart(self):
        self.link_account()
        draft = self.draft()
        args = (draft["id"], draft["revision"], "olx", "mcp")
        self.fixture.drop_response = True
        result = self.pub.publish(*args)
        self.assertEqual(result["status"], "verification_required")
        self.assertIsNone(result["olx"]["provider_id"])
        self.setup_adapter()
        self.fixture.persisted.clear()
        result = self.pub.publish(*args)
        self.assertEqual(result["status"], "verification_required")
        self.fixture.persisted[draft["id"]] = {
            "id": 45,
            "external_id": draft["id"],
            "status": "active",
            "url": "https://www.olx.pl/oferta/test-fixture.html",
        }
        result = self.pub.publish(*args)
        self.assertEqual(result["status"], "published")
        self.assertEqual(result["olx"]["provider_id"], "45")
        self.assertEqual(len(self.fixture.writes), 1)

    def test_concurrent_real_calls_send_once_and_confirm_remote_active_status(self):
        self.link_account()
        draft = self.draft()
        args = (draft["id"], draft["revision"], "olx", "mcp")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.pub.publish(*args), range(2)))
        self.assertTrue(all(x["olx"]["status"] == "submitted" for x in results))
        self.assertTrue(all(x["olx"]["provider_url"] is None for x in results))
        self.fixture.live_status = "active"
        found = self.pub.refresh(draft["id"], "mcp")
        self.assertEqual(found["olx"]["status"], "published")
        self.assertEqual(len(self.fixture.writes), 1)
        duplicate = self.draft()
        with self.assertRaises(DomainError) as error:
            self.pub.publish(duplicate["id"], duplicate["revision"], "olx", "mcp")
        self.assertEqual(error.exception.code, "DUPLICATE_SUBMISSION")

    def test_price_update_then_end_explicitly_without_activation(self):
        self.link_account()
        self.fixture.live_status = "active"
        draft = self.draft()
        self.pub.publish(draft["id"], draft["revision"], "olx", "mcp")
        updated = self.store.stage_price(draft["id"], draft["revision"], 14299, "mcp")
        with self.assertRaises(DomainError):
            self.pub.publish(updated["id"], updated["revision"], "olx", "mcp")
        queued = self.store.transition(updated["id"], updated["revision"], "request", "mcp")
        approved = self.store.approve(
            queued["id"], queued["revision"], self.store.review_digest(queued), OWNER
        )
        self.pub.publish(approved["id"], approved["revision"], "olx", "mcp")
        self.assertEqual(self.fixture.writes[-1][0:2], ("PUT", "/adverts/45"))
        self.assertEqual(self.fixture.writes[-1][2]["price"]["value"], 142.99)
        last = self.pub.end(approved["id"], approved["revision"], OWNER)
        self.assertEqual(last["status"], "sold")
        self.assertEqual(self.fixture.writes[-1][2], {"command": "deactivate", "is_success": True})

    def test_external_id_from_an_older_backup_cannot_be_submitted_twice(self):
        self.link_account()
        draft = self.draft()
        self.fixture.persisted[draft["id"]] = {"id": 45, "external_id": draft["id"], "status": "active"}
        with self.assertRaises(DomainError) as result:
            self.pub.publish(draft["id"], draft["revision"], "olx", "mcp")
        self.assertEqual(result.exception.code, "OLX_EXTERNAL_ALREADY_EXISTS")
        self.assertEqual(self.fixture.writes, [])

    def test_pre_write_crash_is_not_replayed(self):
        self.link_account()
        draft = self.draft()
        with self.store.connect(write=True) as db:
            self.pub._reserve(db, draft, "olx", "create", "11", None, "mcp")
        self.pub.recover_interrupted()
        status = self.pub.publish(draft["id"], draft["revision"], "olx", "mcp")
        self.assertEqual(status["status"], "verification_required")
        self.assertEqual(status["olx"]["error"], "SERVICE_RESTARTED")
        self.assertEqual(self.fixture.writes, [])

    def test_paid_listing_status_can_be_reported_but_never_purchased(self):
        self.link_account()
        self.fixture.live_status = "limited"
        draft = self.draft()
        status = self.pub.publish(draft["id"], draft["revision"], "olx", "mcp")
        self.assertEqual(status["olx"]["status"], "payment_required")
        self.assertIsNone(status["olx"]["provider_url"])
        self.assertEqual(len(self.fixture.writes), 1)
        self.assertEqual(self.fixture.writes[0][0:2], ("POST", "/adverts"))

    def test_signed_publication_photo_uses_allowed_attempt_and_strips_metadata(self):
        self.link_account()
        draft = self.draft()
        draft = self.store.transition(draft["id"], draft["revision"], "revoke", OWNER)
        exif = Image.Exif()
        exif[270] = "private metadata"
        original = sample_bytes(fmt="JPEG", color="blue", exif=exif)
        draft = self.store.upload(draft["id"], draft["revision"], decode(original), "exif.jpg", OWNER)
        draft = self.store.transition(draft["id"], draft["revision"], "request", OWNER)
        draft = self.store.approve(draft["id"], draft["revision"], self.store.review_digest(draft), OWNER)
        result = self.pub.publish(draft["id"], draft["revision"], "olx", "mcp")
        images = self.fixture.writes[-1][2]["images"]
        item = urlsplit(images[1]["url"])
        url = item.path + "?" + item.query
        service = Service(self.settings, olx_http=self.fixture.http())
        self.addCleanup(service.olx.http.close)
        with TestClient(service, base_url="http://listener:8101") as client:
            self.assertEqual(client.get(item.path).status_code, 404)
            correct = client.get(url)
            self.assertEqual(correct.status_code, 200)
            with Image.open(io.BytesIO(correct.content)) as photo:
                self.assertFalse(photo.getexif())
            self.assertEqual(self.store.photo(draft["id"], draft["media"][1]["id"], True)["data"], original)
            expired = self.adapter.photo_url(result["olx"]["id"], draft["media"][1]["id"], now() - 5)
            self.assertEqual(client.get(expired).status_code, 404)
