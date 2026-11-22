"""Accept ChatGPT-provided file references through the existing, scoped ULM MCP tools.

The ChatGPT client injects a temporary download URL only when openai/fileParams is
advertised. The file is fetched by HA Core, checked, and sent to the existing
private ULM Photo Bridge; no public ULM port or OpenAI API key is required.
"""

import base64
import hashlib
import ipaddress
import secrets
import socket
import uuid
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout, TCPConnector
from aiohttp.resolver import DefaultResolver
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .client import BridgeError

MAX_IMAGE_BYTES = 12 * 1024 * 1024
CHUNK_BYTES = 48 * 1024
MAX_IMAGES = 20
MIME_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


class PublicAddressResolver(DefaultResolver):
    """Pin DNS resolution to publicly routable addresses, preventing private-network SSRF."""

    async def resolve(self, host, port=0, family=socket.AF_INET):
        records = await super().resolve(host, port, family)
        public = [record for record in records if ipaddress.ip_address(record["host"]).is_global]
        if not public:
            raise OSError("No public address for file download")
        return public


def _image_type(raw):
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        return "image/webp"
    raise BridgeError("IMAGE_MIME", "Załącznik nie jest zdjęciem JPG, PNG ani WebP.")


def _filename(file_info, mime):
    candidate = file_info.get("file_name")
    if not isinstance(candidate, str):
        candidate = "chatgpt-photo"
    candidate = "".join(char for char in candidate if char.isprintable() and char not in "/\\").strip()
    stem = candidate.rsplit(".", 1)[0] if "." in candidate else candidate
    return (stem[:160] or "chatgpt-photo") + "." + MIME_EXT[mime]


def _valid_source(file_info):
    file_id = file_info["file_id"]
    if not file_id.startswith("file_") or len(file_id) > 240:
        raise BridgeError("PHOTO_SOURCE", "Brak prawidłowego identyfikatora pliku ChatGPT.")
    url = file_info["download_url"]
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.port not in (None, 443)
            or parsed.username
            or parsed.password
            or len(url) > 4096
        ):
            raise ValueError
    except ValueError:
        raise BridgeError("PHOTO_SOURCE", "Nieprawidłowy bezpieczny adres pliku ChatGPT.") from None
    return url


async def _read_chat_file(file_info):
    """Fetch the exact attached bytes, with strict size, TLS, and DNS guards."""
    url = _valid_source(file_info)
    connector = TCPConnector(resolver=PublicAddressResolver(), use_dns_cache=False, limit=1)
    timeout = ClientTimeout(total=75, connect=15, sock_read=35)
    try:
        async with ClientSession(connector=connector, timeout=timeout, trust_env=False) as session:
            async with session.get(url, allow_redirects=False, headers={"Accept": "image/*"}) as response:
                if response.status != 200:
                    raise BridgeError("PHOTO_SOURCE", "Nie można pobrać załącznika ChatGPT.")
                if response.content_length is not None and response.content_length > MAX_IMAGE_BYTES:
                    raise BridgeError("IMAGE_SIZE", "Zdjęcie przekracza limit 12 MB.")
                data = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    data.extend(chunk)
                    if len(data) > MAX_IMAGE_BYTES:
                        raise BridgeError("IMAGE_SIZE", "Zdjęcie przekracza limit 12 MB.")
    except (ClientError, TimeoutError, OSError):
        raise BridgeError("PHOTO_SOURCE", "Bezpieczne pobranie zdjęcia z ChatGPT nie powiodło się.") from None
    raw = bytes(data)
    if not raw:
        raise BridgeError("IMAGE_SIZE", "Załącznik jest pusty.")
    mime = _image_type(raw)
    claimed = file_info.get("mime_type")
    if claimed and claimed not in (mime, "application/octet-stream"):
        raise BridgeError("IMAGE_MIME", "Typ załącznika nie zgadza się z zawartością.")
    return raw, mime, _filename(file_info, mime)


def _seal_chunk(session_id, server_public_key, offset, raw):
    """Match the existing ULM X25519/HKDF/ChaCha20-Poly1305 wire protocol."""
    private = X25519PrivateKey.generate()
    peer = X25519PublicKey.from_public_bytes(base64.b64decode(server_public_key, validate=True))
    shared = private.exchange(peer)
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=uuid.UUID(session_id).bytes,
        info=b"ULM photo upload v1",
    ).derive(shared)
    nonce = secrets.token_bytes(12)
    ciphertext = ChaCha20Poly1305(key).encrypt(nonce, raw, offset.to_bytes(4, "big"))
    return base64.b64encode(private.public_key().public_bytes_raw() + nonce + ciphertext).decode(
        "ascii"
    )


async def attach_chat_photos(client, args):
    """Attach the originals to a single draft, one atomic backend upload per file."""
    draft_id = args["draft_id"]
    expected = args["expected_revision"]
    files = args["files"]
    primary_index = args.get("primary_index", 0)
    if primary_index >= len(files):
        raise BridgeError("INVALID_ARGUMENTS", "Indeks zdjęcia głównego jest poza albumem.")
    draft = await client.call("get_draft", {"draft_id": draft_id})
    if draft["revision"] != expected:
        raise BridgeError("REVISION_CONFLICT", "Szkic został zmieniony. Odczytaj jego nową rewizję.")
    if len(draft["media"]) + len(files) > MAX_IMAGES:
        raise BridgeError("IMAGE_COUNT", "Ogłoszenie może zawierać maksymalnie 20 zdjęć.")

    uploaded = []
    for index, file_info in enumerate(files):
        raw, mime, filename = await _read_chat_file(file_info)
        checksum = hashlib.sha256(raw).hexdigest()
        if checksum in {photo["id"] for photo in draft["media"]}:
            raise BridgeError("DUPLICATE_IMAGE", "To zdjęcie już należy do albumu.")

        stable_key = hashlib.sha256(
            (draft_id + ":" + file_info["file_id"] + ":" + checksum).encode()
        ).hexdigest()
        prepare = {
            "draft_id": draft_id,
            "expected_revision": expected,
            "request_id": "chatgpt-" + stable_key[:56],
            "filename": filename,
            "mime_type": mime,
            "size_bytes": len(raw),
            "sha256": checksum,
            "primary": index == primary_index,
        }
        transfer = await client.call("prepare_photo_upload", prepare)
        if transfer.get("status") == "pending":
            offset = transfer["offset"]
            if transfer["chunk_bytes"] != CHUNK_BYTES or not 0 <= offset <= len(raw):
                raise BridgeError("PHOTO_PROTOCOL", "Nieobsługiwana odpowiedź mostu zdjęć.")
            while offset < len(raw):
                payload = raw[offset : offset + CHUNK_BYTES]
                encrypted = _seal_chunk(
                    transfer["session_id"], transfer["public_key"], offset, payload
                )
                state = await client.call(
                    "upload_photo_chunk",
                    {
                        "session_id": transfer["session_id"],
                        "offset": offset,
                        "encrypted_chunk": encrypted,
                    },
                )
                next_offset = state["offset"]
                if next_offset != offset + len(payload):
                    raise BridgeError("PHOTO_PROTOCOL", "Błąd przesyłania fragmentu zdjęcia.")
                offset = next_offset
            receipt = await client.call(
                "finish_photo_upload", {"session_id": transfer["session_id"]}
            )
        else:
            receipt = transfer

        if receipt.get("photo_id") != checksum or receipt.get("revision") != expected + 1:
            raise BridgeError("PHOTO_INTEGRITY", "Nie udało się potwierdzić zdjęcia w ULM.")
        expected = receipt["revision"]
        draft["media"].append({"id": checksum})
        uploaded.append({"file_name": filename, "sha256": checksum, "photo_id": checksum})

    album = await client.call("list_media", {"draft_id": draft_id})
    if not all(item["photo_id"] in {p["id"] for p in album["items"]} for item in uploaded):
        raise BridgeError("PHOTO_INTEGRITY", "Album ULM nie potwierdził wszystkich zdjęć.")
    return {
        "status": "attached",
        "draft_id": draft_id,
        "revision": expected,
        "count_added": len(uploaded),
        "photo_count": len(album["items"]),
        "photos": uploaded,
        "published": False,
    }
