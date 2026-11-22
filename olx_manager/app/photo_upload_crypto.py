"""Wire protocol for encrypted photo chunks, shared by the bridge and byte clients."""

import base64
import uuid

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

CHUNK_BYTES = 48 * 1024
OVERHEAD = 32 + 12 + 16
PACKET_MAX = 4 * ((CHUNK_BYTES + OVERHEAD + 2) // 3)


def _cipher(private, peer, session_id):
    shared = private.exchange(X25519PublicKey.from_public_bytes(peer))
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=uuid.UUID(session_id).bytes,
        info=b"ULM photo upload v1",
    ).derive(shared)
    return ChaCha20Poly1305(key)


def seal_chunk(session_id, public_key, offset, content):
    """Encrypt known source bytes for the given upload, without including them in MCP logs."""
    import secrets

    private = X25519PrivateKey.generate()
    nonce = secrets.token_bytes(12)
    cipher = _cipher(private, base64.b64decode(public_key, validate=True), session_id)
    packet = private.public_key().public_bytes_raw() + nonce
    packet += cipher.encrypt(nonce, content, offset.to_bytes(4, "big"))
    return base64.b64encode(packet).decode("ascii")


def open_chunk(session_id, private_key, offset, packet):
    private = X25519PrivateKey.from_private_bytes(private_key)
    cipher = _cipher(private, packet[:32], session_id)
    return cipher.decrypt(packet[32:44], packet[44:], offset.to_bytes(4, "big"))
