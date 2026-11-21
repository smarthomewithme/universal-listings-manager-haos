import asyncio
import hashlib
import hmac
import re
import time

import httpx
import jwt

from settings import SUPERVISOR_IP

OWNER = re.compile(r"[A-Za-z0-9_-]{1,80}")
SCOPE = "listings:manage"
ALGORITHMS = ["RS256", "ES256"]


def equal(left, right):
    return hmac.compare_digest(left.encode(), right.encode())


def mac(key, *parts):
    return hmac.new(key, "\n".join(map(str, parts)).encode(), hashlib.sha256).hexdigest()


def ingress_user(request, settings):
    peer = request.client.host if request.client else ""
    if settings.dev_panel and peer in {"127.0.0.1", "::1"}:
        return "local-developer"
    if peer != SUPERVISOR_IP:
        return None
    user = request.headers.get("x-remote-user-id", "")
    return user if OWNER.fullmatch(user) else None


def csrf(key, user, ingress_path):
    return mac(key, "csrf", user, ingress_path)


class McpAuth:
    def __init__(self, settings, client=None):
        self.settings = settings
        self.http = client or httpx.AsyncClient(timeout=5, follow_redirects=False, trust_env=False)
        self.jwks = {}
        self.expires_at = 0
        self.jwks_lock = asyncio.Lock()

    @property
    def metadata(self):
        settings = self.settings
        return {
            "resource": settings.mcp_resource_url,
            "authorization_servers": [settings.mcp_issuer],
            "bearer_methods_supported": ["header"],
            "scopes_supported": [SCOPE],
        }

    async def authorized(self, value):
        if not value.startswith("Bearer ") or len(value) > 8192:
            return False
        token = value[7:]
        settings = self.settings
        if settings.mcp_bearer_token and equal(token, settings.mcp_bearer_token):
            return True
        if not settings.mcp_issuer:
            return False
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") not in ALGORITHMS or not isinstance(header.get("kid"), str):
                return False
            if self.jwks_lock.locked() or time.monotonic() >= self.expires_at:
                await self.refresh_keys()
            key = self.jwks.get(header["kid"])
            if not key or key.algorithm_name != header["alg"]:
                return False
            claims = jwt.decode(
                token,
                key.key,
                algorithms=ALGORITHMS,
                audience=settings.mcp_resource_url,
                issuer=settings.mcp_issuer,
                subject=settings.mcp_subject,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
            scopes = claims.get("scope", "")
            return isinstance(scopes, str) and SCOPE in scopes.split()
        except (jwt.PyJWTError, httpx.HTTPError, ValueError, TypeError, KeyError):
            return False

    async def refresh_keys(self):
        async with self.jwks_lock:
            if time.monotonic() < self.expires_at:
                return
            self.jwks = {}
            self.expires_at = time.monotonic() + 5
            async with self.http.stream("GET", self.settings.mcp_jwks_url) as response:
                response.raise_for_status()
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 131_072:
                        return
            keys = jwt.PyJWKSet.from_json(raw)
            self.jwks = {key.key_id: key for key in keys.keys if key.public_key_use in (None, "sig")}
            self.expires_at = time.monotonic() + 120
