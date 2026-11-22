"""Prepare structured central MCP requests from originals available to a byte client."""

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "olx_manager" / "app"))

from photo_upload_crypto import CHUNK_BYTES, seal_chunk  # noqa: E402


def photo_request(raw, draft_id, revision, filename, mime, request_id, *, primary=False):
    return {
        "draft_id": draft_id,
        "expected_revision": revision,
        "request_id": request_id,
        "filename": filename,
        "mime_type": mime,
        "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "primary": primary,
    }


def upload_packets(session, raw):
    """Yield packets from the offset reported by prepare; never print source bytes."""
    if session["status"] != "pending" or session["chunk_bytes"] != CHUNK_BYTES:
        raise ValueError("Unsupported upload session")
    offset = session["offset"]
    if not 0 <= offset <= len(raw) or (offset < len(raw) and offset % CHUNK_BYTES):
        raise ValueError("Invalid upload cursor")
    for start in range(offset, len(raw), CHUNK_BYTES):
        session_id = session["session_id"]
        yield {
            "session_id": session_id,
            "offset": start,
            "encrypted_chunk": seal_chunk(
                session_id, session["public_key"], start, raw[start : start + CHUNK_BYTES]
            ),
        }
