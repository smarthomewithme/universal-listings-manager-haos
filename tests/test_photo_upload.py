import base64
import secrets
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from starlette.testclient import TestClient

from common import OWNER, ready, sample_bytes
from ha_llm_bridge import API_PATH
from media import MAX_BYTES, MAX_IMAGES, sha256
from models import DraftChanges, DraftFields
from photo_upload import SESSION_TTL
from photo_upload_crypto import CHUNK_BYTES, seal_chunk
from server import Service
from settings import Settings
from store import now


class PhotoUploadTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.settings = Settings(data_dir=Path(directory.name), ha_llm_token=secrets.token_urlsafe(32))
        self.service, self.bridge = self.connect()
        self.draft = self.service.store.create_draft(DraftFields(title="Photo bridge test"), OWNER)
        rng = __import__("random").Random(12)
        with Image.frombytes("RGB", (350, 200), rng.randbytes(350 * 200 * 3)) as image:
            with BytesIO() as sink:
                image.save(sink, "PNG")
                self.raw = sink.getvalue()

    def connect(self):
        service = Service(self.settings)
        bridge = self.enterContext(
            TestClient(
                service,
                base_url="http://ulm:8102",
                client=("172.30.32.1", 12345),
                headers={"Authorization": "Bearer " + self.settings.ha_llm_token},
            )
        )
        return service, bridge

    def prepare_request(self, raw=None, **extra):
        raw = self.raw if raw is None else raw
        return {
            "draft_id": self.draft["id"],
            "expected_revision": self.draft["revision"],
            "request_id": "test-photo-" + secrets.token_hex(8),
            "filename": "example.png",
            "mime_type": "image/png",
            "size_bytes": len(raw),
            "sha256": sha256(raw),
            **extra,
        }

    def call(self, name, arguments, *, error=None, status=None, **kwargs):
        res = self.bridge.post(API_PATH + "/" + name, json={"arguments": arguments}, **kwargs)
        val = res.json()
        if error is None:
            self.assertEqual(res.status_code, 200, val)
            return val["result"]
        self.assertEqual(val["code"], error, val)
        if status is not None:
            self.assertEqual(res.status_code, status)
        return val

    def chunk(self, session, offset, raw, **kwargs):
        packet = seal_chunk(
            session["session_id"], session["public_key"], offset, raw[offset : offset + CHUNK_BYTES]
        )
        return self.call(
            "upload_photo_chunk",
            {"session_id": session["session_id"], "offset": offset, "encrypted_chunk": packet},
            **kwargs,
        )

    def send(self, session, raw):
        for offset in range(session["offset"], len(raw), CHUNK_BYTES):
            self.chunk(session, offset, raw)

    def test_restart_resume_idempotency_cover_and_originals(self):
        first = self.prepare_request()
        session = self.call("prepare_photo_upload", first)
        self.assertEqual(session["offset"], 0)
        self.assertEqual(self.chunk(session, 0, self.raw)["offset"], CHUNK_BYTES)
        self.assertEqual(self.chunk(session, 0, self.raw)["offset"], CHUNK_BYTES)
        self.chunk(session, 0, b"a" * len(self.raw), error="CHUNK_CONFLICT")
        with self.service.store.connect() as db:
            encrypted = db.execute("SELECT packet FROM photo_upload_chunks").fetchone()[0]
            self.assertNotIn(self.raw[:100], encrypted)
        self.service, self.bridge = self.connect()
        resumed = self.call("prepare_photo_upload", first)
        self.assertEqual(resumed["offset"], CHUNK_BYTES)
        self.assertEqual(resumed["session_id"], session["session_id"])
        self.send(resumed, self.raw)
        args = {"session_id": session["session_id"]}
        with ThreadPoolExecutor(max_workers=2) as pool:
            receipts = list(pool.map(lambda _: self.call("finish_photo_upload", args), range(2)))
        receipt = receipts[0]
        self.assertEqual(receipts[1], receipt)
        self.assertEqual(self.call("prepare_photo_upload", first), receipt)
        self.assertEqual(receipt["revision"], 2)
        self.assertEqual(receipt["photo_id"], first["sha256"])

        second = sample_bytes(fmt="WEBP", color="blue")
        self.draft = self.service.store.get(self.draft["id"])
        other = self.prepare_request(second, primary=True, filename="cover.webp", mime_type="image/webp")
        session2 = self.call("prepare_photo_upload", other)
        self.send(session2, second)
        receipt2 = self.call("finish_photo_upload", {"session_id": session2["session_id"]})
        self.assertEqual(receipt2["main_photo"], sha256(second))
        self.assertEqual(receipt2["photo_count"], 2)
        self.service, self.bridge = self.connect()
        current = self.service.store.get(self.draft["id"])
        self.assertEqual(current["fields"], self.draft["fields"])
        self.assertEqual(current["revision"], 3)
        self.assertEqual([p["id"] for p in current["media"]], [sha256(second), sha256(self.raw)])
        for raw in (second, self.raw):
            self.assertEqual(self.service.store.photo(current["id"], sha256(raw), original=True)["data"], raw)
            thumb = self.call("get_image", {"draft_id": current["id"], "photo_id": sha256(raw)})
            with Image.open(BytesIO(base64.b64decode(thumb["data"]))) as image:
                self.assertEqual(image.format, "JPEG")
        self.assertIsNone(self.call("get_publication_status", {"draft_id": current["id"]})["olx"])
        with self.service.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM photo_upload_chunks").fetchone()[0], 0)
            self.assertEqual(
                db.execute("SELECT count(*) FROM photo_uploads WHERE private_key IS NOT NULL").fetchone()[0],
                0,
            )
        self.assertEqual(
            sum(e["event"] == "media_attached" for e in self.service.store.history(current["id"])), 2
        )

    def test_invalid_images_mime_integrity_and_incomplete(self):
        for raw, extra, code in [
            (b"not an image", {}, "INVALID_IMAGE"),
            (self.raw, {"mime_type": "image/jpeg"}, "IMAGE_MIME"),
            (self.raw, {"sha256": "0" * 64}, "PHOTO_INTEGRITY"),
        ]:
            with self.subTest(code=code):
                session = self.call("prepare_photo_upload", self.prepare_request(raw, **extra))
                args = {"session_id": session["session_id"]}
                self.call("finish_photo_upload", args, error="UPLOAD_INCOMPLETE")
                self.send(session, raw)
                self.call("finish_photo_upload", args, error=code)
                self.assertEqual(self.call("cancel_photo_upload", args)["status"], "cancelled")
                self.assertEqual(self.call("cancel_photo_upload", args)["status"], "cancelled")
        self.assertEqual(self.service.store.get(self.draft["id"])["media"], [])

    def test_authorization_order_tampering_expiry_and_limits(self):
        request = self.prepare_request()
        self.call(
            "prepare_photo_upload",
            request,
            error="UNAUTHORIZED",
            status=401,
            headers={"Authorization": "Bearer invalid"},
        )
        self.call(
            "prepare_photo_upload", {**request, "size_bytes": MAX_BYTES + 1}, error="VALIDATION", status=422
        )
        self.call(
            "prepare_photo_upload",
            {**request, "mime_type": "application/octet-stream"},
            error="VALIDATION",
            status=422,
        )
        session = self.call("prepare_photo_upload", request)
        self.chunk(session, CHUNK_BYTES, self.raw, error="UPLOAD_OFFSET")
        packet = seal_chunk(session["session_id"], session["public_key"], 0, self.raw[:CHUNK_BYTES])
        bad = bytearray(base64.b64decode(packet))
        bad[-1] ^= 1
        self.call(
            "upload_photo_chunk",
            {
                "session_id": session["session_id"],
                "offset": 0,
                "encrypted_chunk": base64.b64encode(bad).decode(),
            },
            error="CHUNK_INTEGRITY",
        )
        self.chunk(session, 0, b"short", error="CHUNK_SIZE")
        self.chunk(session, 0, self.raw)
        with patch("photo_upload.now", return_value=now() + SESSION_TTL + 1):
            self.service.photos.cleanup()
            self.call("finish_photo_upload", {"session_id": session["session_id"]}, error="UPLOAD_EXPIRED")
        with self.service.store.connect() as db:
            self.assertIsNone(db.execute("SELECT private_key FROM photo_uploads").fetchone()[0])
            self.assertEqual(db.execute("SELECT count(*) FROM photo_upload_chunks").fetchone()[0], 0)

    def test_revision_duplicate_album_limit_and_approval(self):
        self.draft = ready(self.service.store)
        kwargs = self.prepare_request()
        session = self.call("prepare_photo_upload", kwargs)
        self.call("prepare_photo_upload", {**kwargs, "filename": "other.png"}, error="IDEMPOTENCY_CONFLICT")
        self.send(session, self.raw)
        store = self.service.store
        updated = store.update(
            self.draft["id"], self.draft["revision"], DraftChanges(notes="Edited before finish"), OWNER
        )
        args = {"session_id": session["session_id"]}
        self.call("finish_photo_upload", args, error="REVISION_CONFLICT")
        self.call("cancel_photo_upload", args)
        self.draft = store.transition(updated["id"], updated["revision"], "request", OWNER)
        self.draft = store.approve(
            self.draft["id"], self.draft["revision"], store.review_digest(self.draft), OWNER
        )
        session = self.call("prepare_photo_upload", self.prepare_request())
        self.send(session, self.raw)
        self.call("finish_photo_upload", {"session_id": session["session_id"]})
        current = store.get(self.draft["id"])
        self.assertEqual(current["fields"], self.draft["fields"])
        self.assertIsNone(current["approval"])
        self.assertEqual(current["status"], "draft")
        self.draft = current
        self.call("prepare_photo_upload", self.prepare_request(), error="DUPLICATE_IMAGE")
        for i in range(len(current["media"]), MAX_IMAGES):
            from media import decode

            current = store.upload(
                current["id"],
                current["revision"],
                decode(sample_bytes(color=(i, i, i))),
                "extra.png",
                OWNER,
            )
        self.draft = current
        self.call("prepare_photo_upload", self.prepare_request(), error="IMAGE_COUNT")

    def test_declared_size_limit_accepts_twelve_mebibytes(self):
        request = self.prepare_request(size_bytes=MAX_BYTES)
        self.assertEqual(self.call("prepare_photo_upload", request)["status"], "pending")
