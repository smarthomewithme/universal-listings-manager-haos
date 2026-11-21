import logging
import socket
from contextlib import AsyncExitStack, ExitStack

import uvicorn
from starlette.responses import JSONResponse, PlainTextResponse

from marketplace import OlxMarketplaceAdapter
from mcp_service import build_mcp
from publication import PublicationService
from security import SCOPE, McpAuth
from settings import MCP_PORT, OLX_PORT, PANEL_PORT, Settings
from store import ListingStore
from version import VERSION
from web import SAFE, build_olx_public, build_web

LOG = logging.getLogger("ulm")


class Service:
    def __init__(self, settings, *, olx_http=None, jwks_http=None):
        self.settings = settings
        self.store = ListingStore(settings.data_dir)
        self.olx = OlxMarketplaceAdapter(self.store, settings, olx_http)
        self.publication = PublicationService(self.store, self.olx, settings)
        self.panel = build_web(self.store, self.publication, self.olx, settings)
        self.olx_public = build_olx_public(self.olx, self.publication)
        self.auth = McpAuth(settings, jwks_http) if settings.mcp_enabled else None
        self.mcp = (
            build_mcp(self.store, self.publication, self.olx, settings) if settings.mcp_enabled else None
        )

    async def lifespan(self, receive, send):
        await receive()
        async with AsyncExitStack() as stack:
            if self.auth:
                await stack.enter_async_context(self.auth.http)
            stack.callback(self.olx.http.close)
            if self.mcp:
                await stack.enter_async_context(self.mcp.router.lifespan_context(self.mcp))
            self.publication.recover_interrupted()
            await send({"type": "lifespan.startup.complete"})
            await receive()
        await send({"type": "lifespan.shutdown.complete"})

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.lifespan(receive, send)
        if scope["type"] != "http":
            return
        port = scope["server"][1]
        if port == PANEL_PORT:
            return await self.panel(scope, receive, send)
        if port == OLX_PORT and self.settings.olx_public_base:
            return await self.olx_public(scope, receive, send)
        if port == MCP_PORT and self.mcp:
            return await self.handle_mcp(scope, receive, send)
        return await PlainTextResponse("404", 404, headers=SAFE)(scope, receive, send)

    async def handle_mcp(self, scope, receive, send):
        path = scope["path"]
        headers = dict(scope.get("headers", []))
        resource = self.settings.mcp_resource_url
        if b"origin" in headers or b"upgrade" in headers:
            response = PlainTextResponse("403", 403, headers=SAFE)
        elif resource and path in {
            "/.well-known/oauth-protected-resource",
            "/.well-known/oauth-protected-resource/mcp",
        }:
            response = JSONResponse(self.auth.metadata, headers=SAFE)
        elif path != "/mcp":
            response = PlainTextResponse("404", 404, headers=SAFE)
        elif not await self.auth.authorized(headers.get(b"authorization", b"").decode("latin1")):
            challenge = "Bearer"
            if resource:
                challenge += (
                    ' resource_metadata="'
                    + resource.removesuffix("/mcp")
                    + '/.well-known/oauth-protected-resource/mcp"'
                )
                challenge += f', scope="{SCOPE}"'
            response = PlainTextResponse("Unauthorized", 401, headers={**SAFE, "WWW-Authenticate": challenge})
        else:
            return await self.mcp(scope, receive, send)
        return await response(scope, receive, send)


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # Provider URLs, inbound query strings and OAuth headers stay out of logs.
    for name in ("httpx", "httpcore", "httpx2", "httpcore2", "mcp"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    try:
        settings = Settings.load()
        service = Service(settings)
    except Exception:  # noqa: BLE001 - Startup validation may contain credentials.
        LOG.error("Start nieudany. Sprawdź opcje aplikacji, lokalną bazę i klucz OLX.")
        raise SystemExit(1) from None
    ports = [PANEL_PORT]
    if settings.mcp_enabled:
        ports.append(MCP_PORT)
    if settings.olx_public_base:
        ports.append(OLX_PORT)
    host = "127.0.0.1" if settings.dev_panel else "0.0.0.0"
    with ExitStack() as stack:
        sockets = []
        for port in ports:
            sock = stack.enter_context(socket.socket())
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
            sock.listen(64)
            sockets.append(sock)
        LOG.info("Universal Listings Manager %s uruchomiony. Nasłuch wewnętrzny %s.", VERSION, ports)
        config = uvicorn.Config(
            service,
            proxy_headers=False,
            access_log=False,
            ws="none",
            server_header=False,
            lifespan="on",
            limit_concurrency=64,
            timeout_keep_alive=4,
            timeout_graceful_shutdown=90,
        )
        uvicorn.Server(config).run(sockets=sockets)


if __name__ == "__main__":
    main()
