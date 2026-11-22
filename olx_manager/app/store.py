import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from media import MAX_IMAGES, ConflictError, DomainError, decode, read_from, sha256
from models import DraftFields

SCHEMA_VERSION = 2
APPROVAL_TTL = 24 * 3600
MEDIA_QUOTA = 1024 * 1024 * 1024
EDITABLE = {"draft", "ready_for_review", "approved", "rejected", "failed"}
PUBLIC_FIELDS = set(DraftFields.model_fields) - {"notes"}


def now():
    return int(time.time())


def ident():
    return str(uuid.uuid4())


def packed(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(obj):
    return sha256(packed(obj).encode())


class ListingStore:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "listings.sqlite3"
        self.migrate()

    @contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(self.db_path, timeout=10)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def migrate(self):
        db = sqlite3.connect(self.db_path, timeout=15)
        try:
            db.execute("PRAGMA journal_mode=WAL")
        finally:
            db.close()
        with self.connect(write=True) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == SCHEMA_VERSION:
                return
            if version not in (0, 1):
                raise RuntimeError("Baza zapisana przez nowszą wersję aplikacji.")
            for statement in Path(__file__).with_name("schema.sql").read_text().split(";"):
                if statement.strip():
                    db.execute(statement)
            exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='listings'"
            ).fetchone()
            if version == 0 and exists:
                self._migrate_legacy(db)
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def _migrate_legacy(self, db):
        from datetime import datetime

        for old in db.execute("SELECT * FROM listings"):
            listing_id = old["id"]
            created_at = int(datetime.fromisoformat(old["created_at"]).timestamp())
            updated_at = int(datetime.fromisoformat(old["updated_at"]).timestamp())
            content = DraftFields(
                title=old["title"],
                description=old["description"],
                category_hint=old["category_hint"],
                condition=old["condition_label"],
                price_minor=None if old["price_pln"] is None else old["price_pln"] * 100,
                notes=old["notes"],
            )
            revision = old["revision"] + 1
            db.execute("INSERT INTO products VALUES (?,?,?)", (listing_id, old["title"], created_at))
            db.execute(
                "INSERT INTO drafts VALUES (?,?,'olx',?,'draft',?,?,?)",
                (listing_id, listing_id, packed(content.model_dump()), revision, created_at, updated_at),
            )
            for index, item in enumerate(json.loads(old["photos_json"])):
                filename = item["filename"]
                try:
                    image = decode(read_from(self.data_dir / "photos", listing_id, item["stored_file"]))
                    self._attach(db, listing_id, image, filename, index, enforce_quota=False)
                except DomainError as err:
                    if err.code != "DUPLICATE_IMAGE":
                        db.execute("INSERT INTO legacy_media VALUES (?,?,?)", (listing_id, filename, index))
            self.audit(db, listing_id, "system", "migrated_v0_1", revision)
        # Retain the pre-migration table and original files for recovery.

    def audit(self, db, draft_id, actor, event, revision=None):
        db.execute(
            "INSERT INTO audit_events(draft_id,actor,event,revision,happened_at) VALUES (?,?,?,?,?)",
            (draft_id, actor, event, revision, now()),
        )

    def _idempotency(self, db, actor, action, key, payload, entity_id):
        if key is None:
            return entity_id
        hashed = digest(payload)
        row = db.execute(
            "SELECT input_hash,entity_id FROM requests WHERE actor=? AND action=? AND key=?",
            (actor, action, key),
        ).fetchone()
        if row:
            if row["input_hash"] != hashed:
                raise DomainError("IDEMPOTENCY_CONFLICT", "Klucz należy już do innego żądania.", 409)
            return row["entity_id"]
        db.execute("INSERT INTO requests VALUES (?,?,?,?,?)", (actor, action, key, hashed, entity_id))
        return entity_id

    def create_product(self, name, actor, key=None):
        proposed = ident()
        with self.connect(write=True) as db:
            product = self._idempotency(db, actor, "product", key, name, proposed)
            if product == proposed:
                db.execute("INSERT INTO products VALUES (?,?,?)", (product, name, now()))
                self.audit(db, None, actor, "product_created")
            return dict(db.execute("SELECT * FROM products WHERE id=?", (product,)).fetchone())

    def _create(self, db, fields, actor, key, product_id, action="draft", origin=None, suggestion=None):
        proposed = ident()
        payload = fields.model_dump()
        draft = self._idempotency(db, actor, action, key, [payload, product_id, origin, suggestion], proposed)
        if draft != proposed:
            return draft
        if product_id is None:
            product_id = ident()
            db.execute("INSERT INTO products VALUES (?,?,?)", (product_id, fields.title, now()))
        elif not db.execute("SELECT 1 FROM products WHERE id=?", (product_id,)).fetchone():
            raise DomainError("PRODUCT_NOT_FOUND", "Produkt nie istnieje.", 404)
        db.execute(
            "INSERT INTO drafts(id,product_id,content,created_at,updated_at) VALUES (?,?,?,?,?)",
            (draft, product_id, packed(payload), now(), now()),
        )
        self.audit(db, draft, actor, "draft_created", 1)
        if suggestion:
            db.execute("INSERT INTO price_suggestions VALUES (?,?,?)", (draft, packed(suggestion), now()))
            self.audit(db, draft, actor, "price_suggestion_saved", 1)
        return draft

    def create_draft(self, fields, actor, key=None, product_id=None, suggestion=None):
        with self.connect(write=True) as db:
            draft = self._create(
                db, fields, actor, key, product_id, suggestion=suggestion.model_dump() if suggestion else None
            )
            return self._get(db, draft)

    def _get(self, db, draft_id):
        row = db.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
        if row is None:
            raise DomainError("DRAFT_NOT_FOUND", "Szkic nie istnieje.", 404)
        draft = dict(row)
        draft["fields"] = json.loads(draft.pop("content"))
        draft["media"] = [
            dict(x)
            for x in db.execute(
                """
            SELECT asset_id AS id, filename, position, rotation, mime, width, height, size
            FROM draft_media JOIN media_assets ON asset_id=media_assets.id
            WHERE draft_id=? ORDER BY position
        """,
                (draft_id,),
            )
        ]
        draft["legacy_media"] = [
            x["filename"]
            for x in db.execute(
                "SELECT filename FROM legacy_media WHERE draft_id=? ORDER BY position", (draft_id,)
            )
        ]
        approval = db.execute(
            "SELECT * FROM approvals WHERE draft_id=? AND revision=? AND revoked_at IS NULL",
            (draft_id, draft["revision"]),
        ).fetchone()
        draft["approval"] = dict(approval) if approval else None
        if approval:
            draft["approval"]["expired"] = approval["expires_at"] <= now()
        suggestion = db.execute("SELECT * FROM price_suggestions WHERE draft_id=?", (draft_id,)).fetchone()
        draft["price_suggestion"] = (
            {**json.loads(suggestion["payload"]), "created_at": suggestion["created_at"]}
            if suggestion
            else None
        )
        return draft

    def get(self, draft_id):
        with self.connect() as db:
            return self._get(db, draft_id)

    def list(self, status="all", limit=50, offset=0):
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    """
                SELECT id, product_id, status, revision, updated_at,
                       json_extract(content,'$.title') AS title,
                       json_extract(content,'$.category') AS category,
                       json_extract(content,'$.price_minor') AS price_minor,
                       (SELECT asset_id FROM draft_media WHERE draft_id=drafts.id ORDER BY position LIMIT 1) AS main_photo
                FROM drafts WHERE ?='all' OR status=? OR (
                    ?='published' AND (
                        SELECT status FROM marketplace_listings
                        WHERE draft_id=drafts.id AND mode='olx' AND status!='failed'
                        ORDER BY rowid DESC LIMIT 1
                    )='published'
                )
                ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?
            """,
                    (status, status, status, limit, offset),
                )
            ]

    def _check(self, db, draft_id, revision, allowed=EDITABLE):
        draft = self._get(db, draft_id)
        if revision != draft["revision"]:
            raise ConflictError(f"Aktualna rewizja to {draft['revision']}. Odśwież szkic.")
        if draft["status"] not in allowed:
            raise DomainError("INVALID_STATE", "Ta operacja jest niedostępna w bieżącym stanie.", 409)
        return draft

    def _change(self, db, draft, actor, event, status="draft", revoke=True):
        draft_id = draft["id"]
        revision = draft["revision"] + 1
        db.execute(
            "UPDATE drafts SET status=?, revision=?, updated_at=? WHERE id=?",
            (status, revision, now(), draft_id),
        )
        if revoke:
            db.execute(
                "UPDATE approvals SET revoked_at=? WHERE draft_id=? AND revoked_at IS NULL", (now(), draft_id)
            )
        self.audit(db, draft_id, actor, event, revision)
        return self._get(db, draft_id)

    def update(self, draft_id, revision, changes, actor):
        with self.connect(write=True) as db:
            draft = self._check(db, draft_id, revision)
            fields = DraftFields.model_validate({**draft["fields"], **changes.model_dump(exclude_unset=True)})
            if fields.model_dump() == draft["fields"]:
                return draft
            db.execute("UPDATE drafts SET content=? WHERE id=?", (packed(fields.model_dump()), draft_id))
            return self._change(db, draft, actor, "fields_updated")

    def set_suggestion(self, draft_id, revision, suggestion, actor):
        with self.connect(write=True) as db:
            draft = self._check(db, draft_id, revision)
            db.execute(
                "INSERT OR REPLACE INTO price_suggestions VALUES (?,?,?)",
                (draft_id, packed(suggestion.model_dump()), now()),
            )
            # Suggestion is private metadata and cannot alter the approved amount.
            self.audit(db, draft_id, actor, "price_suggestion_saved", revision)
            return self._get(db, draft["id"])

    def duplicate(self, draft_id, revision, actor, key, with_media):
        with self.connect(write=True) as db:
            old = self._check(db, draft_id, revision, EDITABLE | {"archived", "published"})
            new = self._create(
                db,
                DraftFields.model_validate(old["fields"]),
                actor,
                key,
                None,
                "duplicate",
                [draft_id, revision, with_media],
            )
            if with_media:
                # Replay cannot reattach media removed from a previously returned copy.
                copied = db.execute(
                    "SELECT 1 FROM audit_events WHERE draft_id=? AND event='media_copied'", (new,)
                ).fetchone()
                if not copied:
                    db.execute(
                        """
                        INSERT INTO draft_media SELECT ?,asset_id,filename,position,rotation
                        FROM draft_media WHERE draft_id=?
                    """,
                        (new, draft_id),
                    )
                    self.audit(db, new, actor, "media_copied", 1)
            return self._get(db, new)

    def _attach(self, db, draft_id, image, filename, position, enforce_quota=True):
        present = db.execute(
            "SELECT 1 FROM draft_media WHERE draft_id=? AND asset_id=?", (draft_id, image.sha256)
        ).fetchone()
        if present:
            raise DomainError("DUPLICATE_IMAGE", "To zdjęcie jest już w albumie.", 409)
        exists = db.execute("SELECT 1 FROM media_assets WHERE id=?", (image.sha256,)).fetchone()
        if not exists:
            if enforce_quota:
                used = db.execute(
                    "SELECT coalesce(sum(size+length(thumbnail)),0) FROM media_assets"
                ).fetchone()[0]
                if used + len(image.original) + len(image.thumbnail) > MEDIA_QUOTA:
                    raise DomainError("MEDIA_QUOTA", "Wykorzystano limit 1 GiB zdjęć.", 413)
            db.execute(
                "INSERT INTO media_assets VALUES (?,?,?,?,?,?,?)",
                (
                    image.sha256,
                    image.mime,
                    image.width,
                    image.height,
                    image.original,
                    image.thumbnail,
                    len(image.original),
                ),
            )
        db.execute(
            "INSERT INTO draft_media VALUES (?,?,?,?,0)", (draft_id, image.sha256, filename[:200], position)
        )

    def upload(self, draft_id, revision, image, filename, actor):
        with self.connect(write=True) as db:
            return self._upload(db, draft_id, revision, image, filename, actor)

    def _upload(self, db, draft_id, revision, image, filename, actor, *, primary=False):
        draft = self._check(db, draft_id, revision)
        if len(draft["media"]) >= MAX_IMAGES:
            raise DomainError("IMAGE_COUNT", "Limit 20 zdjęć na ogłoszenie.", 413)
        position = max((p["position"] for p in draft["media"]), default=-1) + 1
        if primary:
            db.execute("UPDATE draft_media SET position=position+1 WHERE draft_id=?", (draft_id,))
            position = 0
        self._attach(db, draft_id, image, filename, position)
        return self._change(db, draft, actor, "media_attached")

    def attach_inbox(self, draft_id, revision, inbox, filename, actor):
        if not 1 <= len(filename) <= 200:
            raise DomainError("INVALID_PATH", "Nieprawidłowa nazwa pliku.")
        return self.upload(draft_id, revision, decode(read_from(inbox, filename)), filename, actor)

    def reorder(self, draft_id, revision, photo_ids, actor):
        with self.connect(write=True) as db:
            draft = self._check(db, draft_id, revision)
            current = [photo["id"] for photo in draft["media"]]
            if len(photo_ids) != len(current) or set(photo_ids) != set(current):
                raise DomainError("MEDIA_ORDER", "Podaj każde zdjęcie albumu dokładnie raz.")
            if photo_ids == current:
                return draft
            db.executemany(
                "UPDATE draft_media SET position=? WHERE draft_id=? AND asset_id=?",
                [(i, draft_id, asset_id) for i, asset_id in enumerate(photo_ids)],
            )
            return self._change(db, draft, actor, "media_reordered")

    def alter_photo(self, draft_id, revision, photo_id, actor, rotation=None):
        with self.connect(write=True) as db:
            draft = self._check(db, draft_id, revision)
            photo = next((photo for photo in draft["media"] if photo["id"] == photo_id), None)
            if photo is None:
                raise DomainError("IMAGE_NOT_FOUND", "Zdjęcie nie istnieje w albumie.", 404)
            if rotation is None:
                db.execute("DELETE FROM draft_media WHERE draft_id=? AND asset_id=?", (draft_id, photo_id))
                db.execute(
                    "DELETE FROM media_assets WHERE id=? AND NOT EXISTS "
                    "(SELECT 1 FROM draft_media WHERE asset_id=?)",
                    (photo_id, photo_id),
                )
                event = "media_removed"
            else:
                if rotation == photo["rotation"]:
                    return draft
                db.execute(
                    "UPDATE draft_media SET rotation=? WHERE draft_id=? AND asset_id=?",
                    (rotation, draft_id, photo_id),
                )
                event = "media_rotated"
            return self._change(db, draft, actor, event)

    def photo(self, draft_id, photo_id, original=False):
        with self.connect() as db:
            column = "original" if original else "thumbnail"
            row = db.execute(
                f"""
                SELECT {column} AS data,mime,filename,rotation FROM media_assets JOIN draft_media ON asset_id=id
                WHERE draft_id=? AND asset_id=?
            """,
                (draft_id, photo_id),
            ).fetchone()
            if not row:
                raise DomainError("IMAGE_NOT_FOUND", "Zdjęcie nie istnieje w albumie.", 404)
            return dict(row)

    @staticmethod
    def review_digest(draft):
        return digest(
            {
                "v": 1,
                "platform": draft["platform"],
                "fields": {key: draft["fields"][key] for key in PUBLIC_FIELDS},
                "photos": [{"sha256": p["id"], "rotation": p["rotation"]} for p in draft["media"]],
            }
        )

    @staticmethod
    def missing(draft):
        fields = draft["fields"]
        labels = {"description": "opis", "condition": "stan", "location": "miejscowość"}
        missing = [label for key, label in labels.items() if not fields[key]]
        if fields["price_minor"] is None or fields["price_minor"] <= 0:
            missing.append("dodatnia cena")
        if not draft["media"]:
            missing.append("zdjęcie")
        if fields["delivery"] != "pickup" and fields["shipping_minor"] is None:
            missing.append("koszt wysyłki")
        return missing

    def preview(self, draft_id):
        draft = self.get(draft_id)
        return {**draft, "review_digest": self.review_digest(draft), "missing": self.missing(draft)}

    def transition(self, draft_id, revision, action, actor):
        states = {
            "request": (EDITABLE - {"approved"}, "ready_for_review", "review_requested"),
            "stage": ({"published"}, "draft", "active_revision_staged"),
            "reject": ({"ready_for_review"}, "rejected", "owner_rejected"),
            "revoke": ({"approved"}, "draft", "approval_revoked"),
            "delete": (EDITABLE, "archived", "draft_soft_deleted"),
            "sold": (EDITABLE | {"failed"}, "sold", "marked_sold_local"),
            "archive": (EDITABLE | {"failed", "sold"}, "archived", "draft_archived"),
        }
        allowed, target, event = states[action]
        with self.connect(write=True) as db:
            draft = self._check(db, draft_id, revision, allowed)
            if (
                action in {"archive", "delete", "sold"}
                and db.execute(
                    "SELECT 1 FROM marketplace_listings WHERE draft_id=? AND mode='olx' "
                    "AND status NOT IN ('ended','inactive','failed')",
                    (draft_id,),
                ).fetchone()
            ):
                raise DomainError("ACTIVE_ON_OLX", "Najpierw zakończ ofertę OLX w panelu.")
            if action == "request":
                if draft["status"] == target:
                    return draft
                missing = self.missing(draft)
                if missing:
                    raise DomainError("REVIEW_INCOMPLETE", "Uzupełnij: " + ", ".join(missing) + ".")
            return self._change(db, draft, actor, event, target)

    def approve(self, draft_id, revision, reviewed_digest, owner):
        with self.connect(write=True) as db:
            draft = self._check(db, draft_id, revision, {"ready_for_review"})
            current_digest = self.review_digest(draft)
            if reviewed_digest != current_digest:
                raise ConflictError("Podgląd jest nieaktualny.")
            if self.missing(draft):
                raise DomainError("REVIEW_INCOMPLETE", "Szkic wymaga uzupełnienia.")
            self._change(db, draft, owner, "owner_approved", "approved")
            db.execute(
                "INSERT INTO approvals VALUES (?,?,?,?,?,?,?,NULL)",
                (ident(), draft_id, revision + 1, current_digest, now(), now() + APPROVAL_TTL, owner),
            )
            return self._get(db, draft_id)

    def history(self, draft_id):
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM drafts WHERE id=?", (draft_id,)).fetchone():
                raise DomainError("DRAFT_NOT_FOUND", "Szkic nie istnieje.", 404)
            return [
                dict(row)
                for row in db.execute(
                    "SELECT actor,event,revision,happened_at FROM audit_events WHERE draft_id=? ORDER BY id DESC LIMIT 100",
                    (draft_id,),
                )
            ]

    def global_history(self):
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute("""
                SELECT e.actor,e.event,e.revision,e.happened_at,e.draft_id,
                       json_extract(d.content,'$.title') AS title
                FROM audit_events AS e LEFT JOIN drafts AS d ON d.id=e.draft_id
                ORDER BY e.id DESC LIMIT 100
            """)
            ]

    def save_account(self, encrypted):
        with self.connect(write=True) as db:
            db.execute("INSERT OR REPLACE INTO provider_accounts VALUES ('olx',?)", (encrypted,))

    def load_account(self):
        with self.connect() as db:
            row = db.execute("SELECT encrypted FROM provider_accounts WHERE platform='olx'").fetchone()
            return row[0] if row else None

    def clear_account(self):
        with self.connect(write=True) as db:
            db.execute("DELETE FROM provider_accounts WHERE platform='olx'")

    def stage_price(self, draft_id, revision, price, actor):
        with self.connect(write=True) as db:
            draft = self._check(db, draft_id, revision, EDITABLE | {"published"})
            if draft["fields"]["price_minor"] == price:
                return draft
            draft["fields"]["price_minor"] = price
            db.execute("UPDATE drafts SET content=? WHERE id=?", (packed(draft["fields"]), draft_id))
            return self._change(db, draft, actor, "price_changed")
