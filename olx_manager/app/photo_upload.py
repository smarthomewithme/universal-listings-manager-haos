"""Resumable, atomic photo uploads for the scoped Home Assistant MCP connection."""

import base64
import binascii
import json
import uuid
from typing import Annotated, Literal

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from pydantic import Field, StringConstraints

from media import MAX_BYTES, MAX_IMAGES, DomainError, decode, sha256
from models import Key, Model, Revision
from photo_upload_crypto import CHUNK_BYTES, OVERHEAD, PACKET_MAX, open_chunk
from store import digest, now, packed

SESSION_TTL = 30 * 60
RECEIPT_TTL = 24 * 3600
MAX_ACTIVE = 20
RESERVED_BYTES = 64 * 1024 * 1024
HASH = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$", min_length=64, max_length=64)]
SessionID = Annotated[str, StringConstraints(pattern=r"^[0-9a-f-]{36}$", min_length=36, max_length=36)]


class PreparePhoto(Model):
    draft_id: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    expected_revision: Revision
    request_id: Key
    filename: Annotated[str, StringConstraints(min_length=1, max_length=200, pattern=r"^[^/\\\p{Cc}]+$")]
    mime_type: Literal["image/jpeg", "image/png", "image/webp"]
    size_bytes: Annotated[int, Field(strict=True, ge=1, le=MAX_BYTES)]
    sha256: HASH
    primary: Annotated[bool, Field(strict=True)] = False


class UploadSession(Model):
    session_id: SessionID


class UploadChunk(UploadSession):
    offset: Annotated[int, Field(strict=True, ge=0, lt=MAX_BYTES)]
    encrypted_chunk: Annotated[
        str, StringConstraints(min_length=84, max_length=PACKET_MAX, pattern=r"^[A-Za-z0-9+/=]+$")
    ]


class PhotoUploads:
    def __init__(self, store):
        self.store = store

    def cleanup(self):
        with self.store.connect(write=True) as db:
            self._cleanup(db)

    @staticmethod
    def _cleanup(db):
        timestamp = now()
        db.execute(
            "DELETE FROM photo_upload_chunks WHERE upload_id IN "
            "(SELECT id FROM photo_uploads WHERE state='pending' AND expires_at<=?)",
            (timestamp,),
        )
        db.execute(
            "UPDATE photo_uploads SET state='expired',private_key=NULL WHERE state='pending' AND expires_at<=?",
            (timestamp,),
        )
        db.execute("DELETE FROM photo_uploads WHERE expires_at<=?", (timestamp - RECEIPT_TTL,))

    @staticmethod
    def _lookup(db, session_id):
        row = db.execute("SELECT * FROM photo_uploads WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise DomainError("UPLOAD_NOT_FOUND", "Nie znaleziono sesji przesyłania.", 404)
        return row

    @staticmethod
    def _status(row):
        if row["state"] == "expired" or (row["state"] == "pending" and row["expires_at"] <= now()):
            raise DomainError("UPLOAD_EXPIRED", "Sesja przesyłania wygasła.", 410)
        if row["state"] == "complete":
            return json.loads(row["receipt"])
        result = {"session_id": row["id"], "status": row["state"]}
        if row["state"] == "pending":
            private = X25519PrivateKey.from_private_bytes(row["private_key"])
            result.update(
                offset=row["received"],
                chunk_bytes=CHUNK_BYTES,
                public_key=base64.b64encode(private.public_key().public_bytes_raw()).decode("ascii"),
                expires_at=row["expires_at"],
            )
        return result

    def prepare(self, data: PreparePhoto):
        fingerprint = digest(data.model_dump())
        with self.store.connect(write=True) as db:
            self._cleanup(db)
            row = db.execute("SELECT * FROM photo_uploads WHERE request_id=?", (data.request_id,)).fetchone()
            if row:
                if row["input_hash"] != fingerprint:
                    raise DomainError("IDEMPOTENCY_CONFLICT", "Klucz należy do innego przesyłania.", 409)
                return self._status(row)
            draft = self.store._check(db, data.draft_id, data.expected_revision)
            if len(draft["media"]) >= MAX_IMAGES:
                raise DomainError("IMAGE_COUNT", "Limit 20 zdjęć na ogłoszenie.", 413)
            if any(item["id"] == data.sha256 for item in draft["media"]):
                raise DomainError("DUPLICATE_IMAGE", "To zdjęcie jest już w albumie.", 409)
            active, reserved = db.execute(
                "SELECT count(*),coalesce(sum(size),0) FROM photo_uploads WHERE state='pending'"
            ).fetchone()
            if active >= MAX_ACTIVE or reserved + data.size_bytes > RESERVED_BYTES:
                raise DomainError("UPLOAD_QUOTA", "Za dużo rozpoczętych transferów zdjęć.", 429)
            key = X25519PrivateKey.generate()
            session_id = str(uuid.uuid4())
            db.execute(
                """INSERT INTO photo_uploads(
                id,request_id,input_hash,draft_id,revision,filename,mime,size,sha256,primary_photo,
                private_key,state,expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,'pending',?)""",
                (
                    session_id,
                    data.request_id,
                    fingerprint,
                    data.draft_id,
                    data.expected_revision,
                    data.filename,
                    data.mime_type,
                    data.size_bytes,
                    data.sha256,
                    data.primary,
                    key.private_bytes_raw(),
                    now() + SESSION_TTL,
                ),
            )
            return self._status(self._lookup(db, session_id))

    def chunk(self, data: UploadChunk):
        with self.store.connect(write=True) as db:
            row = self._lookup(db, data.session_id)
            state = self._status(row)
            if state["status"] != "pending":
                return state
            offset = data.offset
            size = min(CHUNK_BYTES, row["size"] - offset)
            if offset % CHUNK_BYTES or offset > row["received"] or size <= 0:
                raise DomainError("UPLOAD_OFFSET", "Nieprawidłowa pozycja fragmentu.", 409)
            try:
                packet = base64.b64decode(data.encrypted_chunk, validate=True)
                if len(packet) != size + OVERHEAD:
                    raise DomainError("CHUNK_SIZE", "Nieprawidłowy rozmiar fragmentu.", 422)
                clear = open_chunk(data.session_id, row["private_key"], offset, packet)
            except DomainError:
                raise
            except (ValueError, binascii.Error, InvalidTag):
                raise DomainError("CHUNK_INTEGRITY", "Fragment nie przeszedł weryfikacji.", 422) from None
            fragment_hash = sha256(clear)
            if offset < row["received"]:
                previous = db.execute(
                    "SELECT sha256 FROM photo_upload_chunks WHERE upload_id=? AND start=?",
                    (row["id"], offset),
                ).fetchone()
                if previous is None or previous["sha256"] != fragment_hash:
                    raise DomainError("CHUNK_CONFLICT", "Ponowiony fragment zawiera inne dane.", 409)
            else:
                db.execute(
                    "INSERT INTO photo_upload_chunks VALUES (?,?,?,?)",
                    (row["id"], offset, fragment_hash, packet),
                )
                state["offset"] = offset + size
                db.execute("UPDATE photo_uploads SET received=? WHERE id=?", (state["offset"], row["id"]))
            return {"session_id": row["id"], "status": "pending", "offset": state["offset"]}

    def finish(self, data: UploadSession):
        with self.store.connect() as db:
            row = self._lookup(db, data.session_id)
            state = self._status(row)
            if state["status"] != "pending":
                return state
            if row["received"] != row["size"]:
                raise DomainError("UPLOAD_INCOMPLETE", "Brak części zdjęcia.", 409)
            parts = db.execute(
                "SELECT start,packet FROM photo_upload_chunks WHERE upload_id=? ORDER BY start", (row["id"],)
            )
            raw = b"".join(
                open_chunk(row["id"], row["private_key"], part["start"], part["packet"]) for part in parts
            )
        if len(raw) != row["size"] or sha256(raw) != row["sha256"]:
            raise DomainError("PHOTO_INTEGRITY", "Rozmiar lub SHA-256 oryginału jest nieprawidłowy.", 422)
        image = decode(raw, row["mime"])
        with self.store.connect(write=True) as db:
            current = self._lookup(db, data.session_id)
            state = self._status(current)
            if state["status"] != "pending":
                return state
            draft = self.store._upload(
                db,
                row["draft_id"],
                row["revision"],
                image,
                row["filename"],
                "ha_llm",
                primary=bool(row["primary_photo"]),
            )
            receipt = {
                "session_id": row["id"],
                "status": "complete",
                "draft_id": draft["id"],
                "revision": draft["revision"],
                "photo_id": image.sha256,
                "size_bytes": len(image.original),
                "mime_type": image.mime,
                "main_photo": draft["media"][0]["id"],
                "photo_count": len(draft["media"]),
            }
            self._close(db, row["id"], "complete", receipt)
            return receipt

    @staticmethod
    def _close(db, session_id, state, receipt=None):
        db.execute("DELETE FROM photo_upload_chunks WHERE upload_id=?", (session_id,))
        db.execute(
            "UPDATE photo_uploads SET state=?, receipt=?, private_key=NULL, expires_at=? WHERE id=?",
            (state, packed(receipt) if receipt else None, now(), session_id),
        )

    def cancel(self, data: UploadSession):
        with self.store.connect(write=True) as db:
            row = self._lookup(db, data.session_id)
            state = self._status(row)
            if state["status"] == "pending":
                self._close(db, row["id"], "cancelled")
                state = {"session_id": row["id"], "status": "cancelled"}
            return state
