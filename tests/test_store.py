import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from backup import backup
from common import OWNER, ready, sample_bytes
from media import ConflictError, DomainError, decode
from models import DraftChanges, DraftFields, PriceSuggestion
from store import ListingStore, now


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.store = ListingStore(self.path)

    def test_save_restart_and_backup(self):
        approved = ready(self.store)
        copy = self.path.parent / (self.path.name + "-backup")
        self.addCleanup(lambda: __import__("shutil").rmtree(copy))
        backup(self.path, copy)
        restarted = ListingStore(self.path)
        restored = ListingStore(copy)
        for store in [restarted, restored]:
            self.assertEqual(
                store.get(approved["id"])["approval"]["digest"], self.store.review_digest(approved)
            )
            photo = store.photo(approved["id"], approved["media"][0]["id"], original=True)
            self.assertEqual(photo["data"], sample_bytes())
            with store.connect() as db:
                self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "wal")

    def test_create_replay_revision_conflict(self):
        fields = DraftFields(title="Piła")
        first = self.store.create_draft(fields, OWNER, "unique-test-one")
        self.assertEqual(first["revision"], 1)
        self.assertEqual(self.store.create_draft(fields, OWNER, "unique-test-one")["id"], first["id"])
        with self.assertRaisesRegex(DomainError, "innego żądania"):
            self.store.create_draft(DraftFields(title="Laptop"), OWNER, "unique-test-one")
        with self.assertRaises(ConflictError):
            self.store.update(first["id"], 2, DraftChanges(title="Nowy tytuł"), OWNER)

    def test_price_revokes_approval(self):
        draft = ready(self.store)
        updated = self.store.update(draft["id"], draft["revision"], DraftChanges(price_minor=12199), OWNER)
        self.assertEqual(updated["status"], "draft")
        self.assertIsNone(updated["approval"])
        self.assertNotEqual(draft["approval"]["digest"], self.store.review_digest(updated))
        self.assertEqual(updated["fields"]["price_minor"], 12199)

    def test_change_each_photo_operation_revokes(self):
        for operation in ("upload", "remove", "rotate", "reorder"):
            with self.subTest(operation=operation):
                draft = ready(self.store, approved=False)
                if operation in {"remove", "reorder"}:
                    draft = self.store.upload(
                        draft["id"],
                        draft["revision"],
                        decode(sample_bytes(color="yellow")),
                        "inna.png",
                        OWNER,
                    )
                    draft = self.store.transition(draft["id"], draft["revision"], "request", OWNER)
                draft = self.store.approve(
                    draft["id"], draft["revision"], self.store.review_digest(draft), OWNER
                )
                args = (draft["id"], draft["revision"])
                p = draft["media"][0]
                if operation == "upload":
                    changed = self.store.upload(*args, decode(sample_bytes(color="blue")), "blue.png", OWNER)
                elif operation == "reorder":
                    changed = self.store.reorder(*args, [q["id"] for q in reversed(draft["media"])], OWNER)
                else:
                    changed = self.store.alter_photo(
                        *args, p["id"], OWNER, 90 if operation == "rotate" else None
                    )
                self.assertEqual(changed["status"], "draft")
                self.assertIsNone(changed["approval"])
                self.assertNotEqual(draft["approval"]["digest"], self.store.review_digest(changed))

    def test_missing_and_stale_preview(self):
        draft = self.store.create_draft(DraftFields(title="Lampka", description="OK", price_minor=20), OWNER)
        with self.assertRaisesRegex(DomainError, "zdjęcie"):
            self.store.transition(draft["id"], draft["revision"], "request", OWNER)
        draft = ready(self.store, approved=False)
        with self.assertRaises(ConflictError):
            self.store.approve(draft["id"], draft["revision"], "0" * 64, OWNER)
        updated = self.store.update(draft["id"], draft["revision"], DraftChanges(location="Olsztyn"), OWNER)
        with self.assertRaises(ConflictError):
            self.store.approve(draft["id"], draft["revision"], self.store.review_digest(draft), OWNER)
        self.assertEqual(updated["status"], "draft")

    def test_invalid_file_path_symlink_and_duplicate(self):
        draft = self.store.create_draft(DraftFields(title="Rower"), OWNER)
        inbox = self.path / "inbox"
        inbox.mkdir()
        (inbox / "real.png").write_bytes(sample_bytes())
        (inbox / "fake.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 75)
        (inbox / "link.png").symlink_to(inbox / "real.png")
        for name in ("../real.png", r"..\real.png", "link.png", "fake.png"):
            with self.subTest(name=name), self.assertRaises(DomainError):
                self.store.attach_inbox(draft["id"], 1, inbox, name, OWNER)
        current = self.store.attach_inbox(draft["id"], 1, inbox, "real.png", OWNER)
        with self.assertRaisesRegex(DomainError, "już w albumie"):
            self.store.attach_inbox(draft["id"], current["revision"], inbox, "real.png", OWNER)
        self.assertEqual(len(self.store.get(draft["id"])["media"]), 1)

    def test_photo_limits_wrong_mime_and_rotation(self):
        for raw, mime in [
            (sample_bytes(), "image/jpeg"),
            (sample_bytes(size=(9001, 3)), "image/png"),
            (b"x" * (12 * 1024 * 1024 + 1), "image/png"),
            (sample_bytes(fmt="JPEG")[:-7], "image/jpeg"),
        ]:
            with self.subTest(mime=mime, size=len(raw)), self.assertRaises(DomainError):
                decode(raw, mime)

    def test_concurrent_edits_only_one_committed(self):
        draft = self.store.create_draft(DraftFields(title="Piła"), OWNER)

        def write(n):
            try:
                return self.store.update(draft["id"], 1, DraftChanges(price_minor=n), "mcp")["revision"]
            except ConflictError:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as executor:
            out = list(executor.map(write, [50, 60]))
        self.assertCountEqual(out, [2, "conflict"])

    def test_failed_transaction_does_not_leave_photo_or_change_draft(self):
        draft = self.store.create_draft(DraftFields(title="Stół"), OWNER)
        with patch.object(self.store, "audit", side_effect=RuntimeError("forced after blob insert")):
            with self.assertRaises(RuntimeError):
                self.store.upload(draft["id"], 1, decode(sample_bytes()), "x.png", OWNER)
        self.assertEqual(self.store.get(draft["id"])["revision"], 1)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM media_assets").fetchone()[0], 0)

    def test_v01_migration_preserves_ids_money_notes_images_and_revision(self):
        other = self.path / "legacy"
        other.mkdir()
        photos = other / "photos" / "v0-draft"
        photos.mkdir(parents=True)
        raw = sample_bytes()
        (photos / "saved.png").write_bytes(raw)
        (photos / "invalid.png").write_bytes(b"invalid")
        with sqlite3.connect(other / "listings.sqlite3") as db:
            db.execute("""
                CREATE TABLE listings(id,title,description,category_hint,condition_label,price_pln,
                    photos_json,notes,status,revision,approved_digest,approved_at,created_at,updated_at)
            """)
            date = "2026-11-21T09:00:00+00:00"
            db.execute(
                "INSERT INTO listings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "v0-draft",
                    "Audi",
                    "Opis",
                    "części",
                    "używany",
                    149,
                    json.dumps(
                        [
                            {"filename": "oryg.png", "stored_file": "saved.png"},
                            {"filename": "uszkodzone.png", "stored_file": "invalid.png"},
                        ]
                    ),
                    "prywatne",
                    "approved",
                    7,
                    "not-valid-for-new-format",
                    date,
                    date,
                    date,
                ),
            )
        for _ in range(2):
            new = ListingStore(other)
            draft = new.get("v0-draft")
            self.assertEqual(draft["revision"], 8)
            self.assertEqual(draft["fields"]["price_minor"], 14_900)
            self.assertEqual(draft["fields"]["notes"], "prywatne")
            self.assertEqual(draft["legacy_media"], ["uszkodzone.png"])
            self.assertEqual(new.photo(draft["id"], draft["media"][0]["id"], True)["data"], raw)
            self.assertIsNone(draft["approval"])
        self.assertTrue((photos / "invalid.png").is_file())

    def test_private_suggestion_does_not_change_asking_price(self):
        draft = ready(self.store)
        data = self.store.set_suggestion(
            draft["id"], draft["revision"], PriceSuggestion(price_minor=15000), "mcp"
        )
        self.assertEqual(data["approval"]["digest"], draft["approval"]["digest"])
        self.assertEqual(data["fields"]["price_minor"], draft["fields"]["price_minor"])
        self.assertGreaterEqual(data["price_suggestion"]["created_at"], now() - 2)

    def test_unknown_schema_version_refused(self):
        with self.store.connect(write=True) as db:
            db.execute("PRAGMA user_version=999")
        with self.assertRaises(RuntimeError):
            ListingStore(self.path)
