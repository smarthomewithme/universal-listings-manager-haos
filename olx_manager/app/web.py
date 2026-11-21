import json
import secrets
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import unquote

import anyio
from pydantic import Field, ValidationError
from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from marketplace import public_description
from media import MAX_BYTES, MAX_IMAGES, DomainError, decode, rendered
from models import FIELDS, DraftChanges, DraftFields, Key, Model, PriceSuggestion, Revision, Short
from security import csrf, equal, ingress_user
from settings import SUPERVISOR_IP

STATIC = Path(__file__).with_name("static")
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob:; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
)
SAFE = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": CSP,
}
HTTP_LIMIT = 128 * 1024


class CreateForm(Model):
    fields: DraftFields
    request_id: Key
    suggestion: PriceSuggestion | None = None


class RevisionForm(Model):
    expected_revision: Revision


class UpdateForm(RevisionForm):
    changes: DraftChanges


class OrderForm(RevisionForm):
    photo_ids: list[str] = Field(max_length=MAX_IMAGES)


class RotateForm(RevisionForm):
    rotation: Literal[0, 90, 180, 270]


class InboxForm(RevisionForm):
    filename: Short


class ApproveForm(RevisionForm):
    digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class DuplicateForm(RevisionForm):
    request_id: Key
    with_media: bool = False


class OAuthForm(Model):
    client_id: str = Field(min_length=1, max_length=250)
    client_secret: str = Field(min_length=1, max_length=1000)


def error_response(err, request=None):
    return JSONResponse({"code": err.code, "message": str(err)}, status_code=err.status, headers=SAFE)


async def validation_error(request, err):
    paths = [".".join(map(str, item["loc"])) for item in err.errors(include_input=False)]
    return error_response(DomainError("VALIDATION", "Sprawdź pola: " + ", ".join(paths) + ".", 422))


async def stream_body(request, limit):
    header = request.headers.get("content-length")
    if header and (not header.isdecimal() or int(header) > limit):
        raise DomainError("BODY_SIZE", "Rozmiar żądania przekracza limit.", 413)
    body = bytearray()
    try:
        with anyio.fail_after(60):
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > limit:
                    raise DomainError("BODY_SIZE", "Rozmiar żądania przekracza limit.", 413)
    except TimeoutError as err:
        raise DomainError("UPLOAD_TIMEOUT", "Przesyłanie trwało zbyt długo.", 408) from err
    return bytes(body)


async def form(request, model):
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise DomainError("CONTENT_TYPE", "Wymagany format JSON.", 415)
    try:
        raw = json.loads(await stream_body(request, HTTP_LIMIT))
    except (ValueError, RecursionError) as err:
        if isinstance(err, DomainError):
            raise
        raise DomainError("BAD_JSON", "Nieprawidłowy JSON.") from err
    return model.model_validate(raw)


def positive_id(value):
    if not value.isascii() or not value.isdecimal() or len(value) > 10 or int(value) < 1:
        raise DomainError("VALIDATION", "Podaj dodatni identyfikator.")
    return int(value)


def build_web(store, publication, olx, settings):
    key = secrets.token_bytes(32)
    uploads = anyio.CapacityLimiter(2)

    def identity(request):
        user = ingress_user(request, settings)
        if user is None:
            raise DomainError("INGRESS_REQUIRED", "Otwórz aplikację przez Home Assistant Ingress.", 403)
        owner = settings.owner_user_id or ("local-developer" if settings.dev_panel else "")
        return user, bool(owner and equal(user, owner))

    def require_owner(request):
        user, owner = identity(request)
        if not owner:
            raise DomainError(
                "OWNER_REQUIRED", "Panel jest dostępny wyłącznie dla przypisanego właściciela.", 403
            )
        if request.method not in {"GET", "HEAD"}:
            token = csrf(key, user, request.headers.get("x-ingress-path", ""))
            if not equal(request.headers.get("x-csrf-token", ""), token) or request.headers.get(
                "sec-fetch-site"
            ) in {"cross-site", "same-site"}:
                raise DomainError("CSRF", "Odśwież panel przed ponowieniem.", 403)
        return "owner:" + user

    def boot(request):
        user, owner = identity(request)
        return JSONResponse(
            {
                "owner": owner,
                "your_user_id": user if not settings.owner_user_id else None,
                "csrf": csrf(key, user, request.headers.get("x-ingress-path", "")) if owner else None,
                "categories": FIELDS,
                "mock_enabled": settings.mock_enabled,
            },
            headers=SAFE,
        )

    def index(request):
        identity(request)
        return FileResponse(STATIC / "index.html", headers=SAFE)

    def asset(request):
        identity(request)
        filename = request.path_params["filename"]
        if filename not in {"app.js", "app.css", "icon.svg"}:
            return PlainTextResponse("404", 404, headers=SAFE)
        return FileResponse(STATIC / filename, headers=SAFE)

    def health(request):
        peer = request.client.host if request.client else ""
        if peer not in {"127.0.0.1", "::1", SUPERVISOR_IP}:
            return PlainTextResponse("404", 404, headers=SAFE)
        with store.connect() as db:
            db.execute("SELECT 1 FROM drafts LIMIT 1")
        return JSONResponse({"status": "ok", "version": "1.0.0"}, headers=SAFE)

    def dashboard(request):
        require_owner(request)
        status = request.query_params.get("status", "all")
        statuses = {
            "all",
            "draft",
            "ready_for_review",
            "approved",
            "published",
            "sold",
            "archived",
            "rejected",
            "failed",
            "submitted",
            "verification_required",
            "publishing",
        }
        if status not in statuses:
            raise DomainError("VALIDATION", "Nieznany status.")
        page = positive_id(request.query_params.get("page", "1"))
        return JSONResponse(store.list(status, 50, (page - 1) * 50), headers=SAFE)

    async def create(request):
        actor = require_owner(request)
        value = await form(request, CreateForm)
        data = await anyio.to_thread.run_sync(
            store.create_draft, value.fields, actor, value.request_id, None, value.suggestion
        )
        return JSONResponse(data, status_code=201, headers=SAFE)

    def draft_view(request):
        require_owner(request)
        draft_id = request.path_params["draft_id"]
        view = store.preview(draft_id)
        view["olx_description"] = public_description(view["fields"])
        view["publication"] = publication.status(draft_id)
        return JSONResponse(view, headers=SAFE)

    async def edit(request):
        actor = require_owner(request)
        value = await form(request, UpdateForm)
        draft_id = request.path_params["draft_id"]
        data = await anyio.to_thread.run_sync(
            store.update, draft_id, value.expected_revision, value.changes, actor
        )
        return JSONResponse(data, headers=SAFE)

    async def upload(request):
        actor = require_owner(request)
        draft_id = request.path_params["draft_id"]
        revision = positive_id(request.headers.get("x-expected-revision", ""))
        mime = request.headers.get("content-type", "").split(";")[0]
        if mime not in {"image/jpeg", "image/png", "image/webp", "application/octet-stream"}:
            raise DomainError("IMAGE_MIME", "Akceptowane formaty to JPG, PNG i WebP.", 415)
        filename = unquote(request.headers.get("x-filename", "zdjecie"))[:200]
        async with uploads:
            raw = await stream_body(request, MAX_BYTES)
            image = await anyio.to_thread.run_sync(decode, raw, mime)
            data = await anyio.to_thread.run_sync(store.upload, draft_id, revision, image, filename, actor)
        return JSONResponse(data, headers=SAFE)

    def photo(request):
        require_owner(request)
        params = request.path_params
        original = request.query_params.get("original") == "1"
        asset_data = store.photo(params["draft_id"], params["photo_id"], original)
        headers = SAFE.copy()
        if original:
            suffix = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[asset_data["mime"]]
            headers["Content-Disposition"] = f'attachment; filename="oryginal.{suffix}"'
        return Response(
            asset_data["data"] if original else rendered(asset_data),
            media_type=asset_data["mime"] if original else "image/jpeg",
            headers=headers,
        )

    async def photo_action(request):
        actor = require_owner(request)
        params = request.path_params
        if params["action"] == "rotate":
            value = await form(request, RotateForm)
            rotation = value.rotation
        elif params["action"] == "remove":
            value = await form(request, RevisionForm)
            rotation = None
        else:
            raise DomainError("NOT_FOUND", "Nieznana operacja zdjęcia.", 404)
        data = await anyio.to_thread.run_sync(
            store.alter_photo,
            params["draft_id"],
            value.expected_revision,
            params["photo_id"],
            actor,
            rotation,
        )
        return JSONResponse(data, headers=SAFE)

    def history(request):
        require_owner(request)
        return JSONResponse(store.history(request.path_params["draft_id"]), headers=SAFE)

    def global_history(request):
        require_owner(request)
        return JSONResponse(store.global_history(), headers=SAFE)

    async def action(request):
        actor = require_owner(request)
        params = request.path_params
        draft_id, name = params["draft_id"], params["action"]
        args = ()
        if name in {"request", "revoke", "reject", "archive", "delete", "sold", "stage"}:
            operation, model, args = store.transition, RevisionForm, (name, actor)
        elif name == "approve":
            operation, model = store.approve, ApproveForm
        elif name == "duplicate":
            operation, model = store.duplicate, DuplicateForm
        elif name == "inbox":
            operation, model = store.attach_inbox, InboxForm
        elif name == "reorder":
            operation, model = store.reorder, OrderForm
        elif name in {"simulate", "publish"}:
            operation, model, args = (
                publication.publish,
                RevisionForm,
                ("mock" if name == "simulate" else "olx", actor),
            )
        elif name == "end":
            operation, model, args = publication.end, RevisionForm, (actor,)
        elif name == "refresh":
            await form(request, Model)
            data = await anyio.to_thread.run_sync(publication.refresh, draft_id, actor)
            return JSONResponse(data, headers=SAFE)
        elif name == "stage_price":
            operation, model = store.stage_price, UpdateForm
        else:
            raise DomainError("NOT_FOUND", "Nieznana operacja.", 404)
        value = await form(request, model)
        if name == "approve":
            args = (value.digest, actor)
        elif name == "duplicate":
            args = (actor, value.request_id, value.with_media)
        elif name == "inbox":
            args = (settings.inbox_dir, value.filename, actor)
        elif name == "reorder":
            args = (value.photo_ids, actor)
        elif name == "stage_price":
            args = (value.changes.price_minor, actor)
        data = await anyio.to_thread.run_sync(operation, draft_id, value.expected_revision, *args)
        return JSONResponse(data, headers=SAFE)

    def connections(request):
        require_owner(request)
        return JSONResponse(
            {
                "olx": olx.status(),
                "mcp_local": settings.mcp_enabled,
                "remote_mcp": "configured_unverified"
                if settings.mcp_enabled and settings.mcp_resource_url
                else "disconnected",
            },
            headers=SAFE,
        )

    async def oauth_start(request):
        require_owner(request)
        value = await form(request, OAuthForm)
        url = await anyio.to_thread.run_sync(olx.start_oauth, value.client_id, value.client_secret)
        return JSONResponse({"url": url}, headers=SAFE)

    async def oauth_disconnect(request):
        require_owner(request)
        await form(request, Model)
        await anyio.to_thread.run_sync(olx.disconnect)
        return JSONResponse(olx.status(), headers=SAFE)

    def category(request):
        require_owner(request)
        value = request.path_params.get("category_id")
        return JSONResponse(olx.categories(positive_id(value) if value else None), headers=SAFE)

    def city(request):
        require_owner(request)
        return JSONResponse(olx.city(positive_id(request.path_params["city_id"])), headers=SAFE)

    routes = [
        Route("/", index),
        Route("/static/{filename}", asset),
        Route("/health", health),
        Route("/api/session", boot),
        Route("/api/drafts", dashboard),
        Route("/api/drafts", create, methods=["POST"]),
        Route("/api/drafts/{draft_id}", draft_view),
        Route("/api/drafts/{draft_id}", edit, methods=["PATCH"]),
        Route("/api/drafts/{draft_id}/history", history),
        Route("/api/drafts/{draft_id}/photos", upload, methods=["POST"]),
        Route("/api/drafts/{draft_id}/photos/{photo_id}", photo),
        Route("/api/drafts/{draft_id}/photos/{photo_id}/{action}", photo_action, methods=["POST"]),
        Route("/api/drafts/{draft_id}/action/{action}", action, methods=["POST"]),
        Route("/api/connections", connections),
        Route("/api/history", global_history),
        Route("/api/olx/start", oauth_start, methods=["POST"]),
        Route("/api/olx/disconnect", oauth_disconnect, methods=["POST"]),
        Route("/api/olx/categories", category),
        Route("/api/olx/categories/{category_id}", category),
        Route("/api/olx/cities/{city_id}", city),
    ]

    async def domain(request, err):
        return error_response(err)

    return Starlette(
        routes=routes, exception_handlers={DomainError: domain, ValidationError: validation_error}
    )


def build_olx_public(olx, publication):
    def callback(request):
        params = request.query_params
        try:
            olx.callback(params.get("state", ""), params.get("code", ""))
            return PlainTextResponse(
                "Połączono OLX. Wróć do panelu Ogłoszenia i odśwież ustawienia.", headers=SAFE
            )
        except DomainError as err:
            return PlainTextResponse(str(err), status_code=err.status, headers=SAFE)

    def signed_photo(request):
        params = request.path_params
        expires = request.query_params.get("exp", "")
        sig = request.query_params.get("sig", "")
        if len(expires) != 10 or not expires.isascii() or not expires.isdecimal() or len(sig) != 64:
            return PlainTextResponse("404", 404, headers=SAFE)
        if not olx.check_photo_signature(params["attempt_id"], params["photo_id"], int(expires), sig):
            return PlainTextResponse("404", 404, headers=SAFE)
        try:
            image = publication.signed_photo(params["attempt_id"], params["photo_id"])
            return Response(rendered(image, publication=True), media_type="image/jpeg", headers=SAFE)
        except DomainError:
            return PlainTextResponse("404", 404, headers=SAFE)

    return Starlette(
        routes=[
            Route("/olx/callback", callback, methods=["GET"]),
            Route("/olx/media/{attempt_id}/{photo_id}", signed_photo),
        ]
    )
