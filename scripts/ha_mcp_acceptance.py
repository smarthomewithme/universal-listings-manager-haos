"""Exercise the official HA Core MCP server against an isolated ULM app."""

import base64
import io
import json
import os
import random
import secrets
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from photo_bridge_client import photo_request, upload_packets
from PIL import Image

DOMAIN = "universal_listings_manager"
HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}
CORE_URL, CORE_CONTAINER, NETWORK, IMAGE, APP_CONTAINER = (
    sys.argv[1:] if __name__ == "__main__" else (None,) * 5
)
TOKEN = ""


def request(path, payload=None, auth=True, *, form=False, allow_error=False):
    headers = HEADERS.copy()
    if auth:
        headers["Authorization"] = "Bearer " + TOKEN
    wire = None
    if payload is not None:
        if form:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            wire = urlencode(payload).encode()
        else:
            wire = json.dumps(payload).encode()
    try:
        with urlopen(Request(CORE_URL + path, data=wire, headers=headers), timeout=40) as response:
            return json.load(response)
    except HTTPError as exc:
        if allow_error:
            return exc.code
        # Avoid printing credential-bearing config or authentication requests.
        raise RuntimeError(f"HTTP {exc.code} for {path}") from None


def setup_admin():
    global TOKEN

    for _ in range(75):
        try:
            if isinstance(request("/api/onboarding", auth=False, allow_error=True), list):
                break
        except (URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(2)
    else:
        raise RuntimeError("Home Assistant onboarding did not become available.")
    created = request(
        "/api/onboarding/users",
        {
            "client_id": CORE_URL + "/",
            "name": "CI Admin",
            "username": "ci-admin",
            "password": secrets.token_urlsafe(28),
            "language": "en",
        },
        auth=False,
    )
    login = request(
        "/auth/token",
        {
            "client_id": CORE_URL + "/",
            "grant_type": "authorization_code",
            "code": created["auth_code"],
        },
        auth=False,
        form=True,
    )
    TOKEN = login["access_token"]
    # Read the user ID from the disposable Core fixture, without logging auth data.
    code = (
        "import asyncio; from pathlib import Path; p=Path('/config/.storage/auth'); "
        "import json; print(next(u['id'] for u in json.loads(p.read_text())['data']['users'] "
        "if u['name']=='CI Admin'))"
    )
    for _ in range(22):
        try:
            return docker_exec("docker", "exec", CORE_CONTAINER, "python", "-c", code).strip()
        except subprocess.CalledProcessError:
            time.sleep(1)
    raise RuntimeError("The fixture admin's user ID was not persisted.")


def docker_exec(*args, input_text=None):
    return subprocess.run(
        args,
        input=input_text,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def add_integration(domain, values):
    base = "/api/config/config_entries/flow"
    flow = request(base, {"handler": domain})
    assert flow["type"] == "form", (domain, flow.get("reason"))
    result = request(base + "/" + flow["flow_id"], values)
    assert result["type"] == "create_entry", (domain, result.get("errors"), result.get("reason"))
    return result["result"]["entry_id"]


def set_read_only(entry_id, enabled):
    base = "/api/config/config_entries/options/flow"
    flow = request(base, {"handler": entry_id})
    assert flow["type"] == "form"
    payload = {
        "all_llm_apis": False,
        "llm_hass_api": ["assist", DOMAIN],
        "require_admin": True,
        "read_only": enabled,
    }
    result = request(base + "/" + flow["flow_id"], payload)
    assert result["type"] == "create_entry", result.get("errors")


def rpc(method, args=None, auth=True):
    return request(
        "/api/mcp",
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": args or {},
        },
        auth=auth,
        allow_error=not auth,
    )


def listed():
    return {tool["name"]: tool for tool in rpc("tools/list")["result"]["tools"]}


def tool(name, *, error=False, **arguments):
    result = rpc("tools/call", {"name": "ulm_" + name, "arguments": arguments})["result"]
    assert bool(result.get("isError")) == error, (name, result.get("content"))
    body = result["content"][0]["text"]
    return body if error else json.loads(body)


def ingress_fixture(draft_id, revision, action, digest=None):
    # In a disposable HA subnet this container emulates the Supervisor ingress hop.
    code = """
import io, json, sys, urllib.request
from PIL import Image
base="http://6a2752e9-olx-manager:8099"
path="/api/drafts/"+sys.argv[1]
headers={"X-Remote-User-Id":"fixture-owner","X-Ingress-Path":"/fixture-ingress"}
def req(path, body=None, extra=None):
    return json.load(urllib.request.urlopen(urllib.request.Request(
        base+path, data=body, headers={**headers,**(extra or {})}),timeout=10))
headers["X-CSRF-Token"]=req("/api/session")["csrf"]
version=int(sys.argv[2])
if sys.argv[3]=="photo":
    png=io.BytesIO()
    Image.new("RGB",(512,300),"green").save(png,format="PNG")
    result=req(path+"/photos",png.getvalue(),{"Content-Type":"image/png","X-Expected-Revision":str(version),
                                              "X-Filename":"fixture.png"})
else:
    result=req(path+"/action/approve",json.dumps({"expected_revision":version,"digest":sys.argv[4]}).encode(),
               {"Content-Type":"application/json"})
print(json.dumps({"revision":result["revision"],"status":result["status"]}))
"""
    args = [
        "docker",
        "run",
        "--rm",
        "-i",
        "--network",
        NETWORK,
        "--ip",
        "172.30.32.2",
        "--entrypoint",
        "python",
        IMAGE,
        "-",
        draft_id,
        str(revision),
        action,
    ]
    if digest is not None:
        args.append(digest)
    return json.loads(docker_exec(*args, input_text=code))


def fixture_photo(kind, seed):
    rng = random.Random(seed)
    with Image.frombytes("RGB", (400, 300), rng.randbytes(400 * 300 * 3)) as image:
        with io.BytesIO() as output:
            image.save(output, format=kind)
            return output.getvalue()


def restart_app():
    docker_exec("docker", "restart", APP_CONTAINER)
    for _ in range(40):
        status = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Health.Status}}", APP_CONTAINER],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if status == "healthy":
            return
        time.sleep(2)
    raise RuntimeError("The ULM app did not become healthy after restart.")


def verify_photos(draft, originals):
    before = tool("get_draft", draft_id=draft["id"])
    before_status = tool("get_publication_status", draft_id=draft["id"])
    expected = before["revision"]
    hashes = []
    for i, (filename, mime, raw) in enumerate(originals):
        params = photo_request(
            raw, draft["id"], expected, filename, mime, f"core-photo-fixture-{i}", primary=i == 1
        )
        session = tool("prepare_photo_upload", **params)
        args = {"session_id": session["session_id"]}
        assert "UPLOAD_INCOMPLETE" in tool("finish_photo_upload", **args, error=True)
        first = next(upload_packets(session, raw))
        sent = tool("upload_photo_chunk", **first)
        assert tool("upload_photo_chunk", **first) == sent
        if i == 0:
            restart_app()
        session = tool("prepare_photo_upload", **params)
        assert session["offset"] == sent["offset"]
        for packet in upload_packets(session, raw):
            tool("upload_photo_chunk", **packet)
        saved = tool("finish_photo_upload", **args)
        assert tool("finish_photo_upload", **args) == saved
        assert saved["photo_id"] == params["sha256"]
        assert saved["revision"] == expected + 1
        expected = saved["revision"]
        hashes.append(params["sha256"])
    restart_app()
    current = tool("get_draft", draft_id=draft["id"])
    assert current["revision"] == expected and current["fields"] == before["fields"]
    assert [photo["id"] for photo in current["media"]] == hashes[::-1]
    assert tool("get_publication_status", draft_id=draft["id"])["olx"] == before_status["olx"]
    for (filename, _, _), checksum in zip(originals, hashes, strict=True):
        thumb = tool("get_image", draft_id=draft["id"], photo_id=checksum)
        with Image.open(io.BytesIO(base64.b64decode(thumb["data"]))) as image:
            assert image.format == "JPEG" and image.width == 256
        result = docker_exec(
            "docker",
            "exec",
            APP_CONTAINER,
            "python",
            "-c",
            "import hashlib, sqlite3, sys; "
            "db=sqlite3.connect('/data/listings.sqlite3'); "
            "row=db.execute('SELECT original,filename FROM media_assets JOIN draft_media "
            "ON asset_id=id WHERE draft_id=? AND asset_id=?',sys.argv[1:]).fetchone(); "
            "print(hashlib.sha256(row[0]).hexdigest(),row[1])",
            draft["id"],
            checksum,
        ).strip()
        assert result == f"{checksum} {filename}"
    return current


def main():
    admin_id = setup_admin()
    ulm = add_integration(
        DOMAIN,
        {
            "host": "6a2752e9-olx-manager",
            "token": os.environ["HA_LLM_TOKEN"],
            "ha_user_id": admin_id,
        },
    )
    assert ulm
    mcp = add_integration("mcp_server", {})
    assert rpc("tools/list", auth=False) == 401
    initial = rpc(
        "initialize",
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "ulm-acceptance", "version": "1"},
        },
    )
    assert initial["result"]["serverInfo"]["name"] == "home-assistant"

    set_read_only(mcp, True)
    tools = listed()
    ulm_tools = {name: item for name, item in tools.items() if name.startswith("ulm_")}
    assert len(ulm_tools) == 9, list(ulm_tools)
    assert "ulm_create_draft" not in tools and "ulm_get_draft" in tools
    assert all(item["annotations"]["readOnlyHint"] for item in ulm_tools.values())
    assert "create_draft" in tool("create_draft", error=True)
    assert "prepare_photo_upload" not in tools
    assert "upload_photo_chunk" in tool("upload_photo_chunk", error=True)
    print("PASS: official HA MCP read-only filters listings and rejects writes", flush=True)

    set_read_only(mcp, False)
    tools = listed()
    assert len([name for name in tools if name.startswith("ulm_")]) == 21
    assert "ulm_list_backend_tools" in tools and "ulm_call_backend_tool" in tools
    attached = tools["ulm_attach_chat_photos"]
    assert attached["_meta"]["openai/fileParams"] == ["files"], attached
    file_schema = attached["inputSchema"]["properties"]["files"]["items"]
    assert set(file_schema["required"]) == {"download_url", "file_id"}, file_schema
    assert {"mime_type", "file_name"} <= set(file_schema["properties"]), file_schema
    assert any(not name.startswith("ulm_") for name in tools)
    assert "ulm_approve" not in tools and "ulm_publish_approved" not in tools

    fields = {
        "title": "Sample listing for acceptance",
        "description": "Fixture",
        "condition": "used",
        "category": "electronics",
        "location": "Olsztyn",
        "price_minor": 15500,
    }
    params = {
        "request_id": "ha-mcp-fixture",
        "fields": fields,
        "suggestion": {
            "price_minor": 15500,
            "confidence": "low",
            "notes": "Estimate without comparable listings",
        },
    }
    created = tool("create_draft", **params)
    assert tool("create_draft", **params)["id"] == created["id"]
    assert created["price_suggestion"]["confidence"] == "low"
    assert len(tool("list_drafts")["items"]) == 1
    draft_id = created["id"]
    args = {"draft_id": draft_id}
    catalog = tool("list_backend_tools")
    names = {item["name"] for item in catalog["items"]}
    assert {"get_draft", "create_draft", "list_drafts", "simulate_publication"} <= names
    assert "publish_approved" not in names and "delete_draft" not in names
    loaded = tool("get_draft", **args)
    assert tool("call_backend_tool", name="get_draft", arguments=args) == loaded
    forbidden = tool("call_backend_tool", error=True, name="publish_approved", arguments={})
    assert "TOOL_NOT_ALLOWED" in forbidden
    updated = tool("update_price", **args, expected_revision=loaded["revision"], price_minor=15600)
    assert updated["fields"]["price_minor"] == 15600
    assert "REVISION_CONFLICT" in tool(
        "update_price", error=True, **args, expected_revision=loaded["revision"], price_minor=999
    )
    uploaded = verify_photos(
        updated,
        [
            ("first.jpg", "image/jpeg", fixture_photo("JPEG", 17)),
            ("cover.png", "image/png", fixture_photo("PNG", 32)),
        ],
    )
    print("PASS: two exact originals through official HA MCP, mid-upload restart, thumbnails and cover")
    queued = tool("request_review", **args, expected_revision=uploaded["revision"])
    assert tool("get_approval_status", **args)["status"] == "ready_for_review"
    tool("simulate_publication", error=True, **args, expected_revision=queued["revision"])
    preview = tool("get_review_preview", **args)
    approved = ingress_fixture(draft_id, queued["revision"], "approve", preview["review_digest"])
    assert approved["status"] == "approved"
    demo = tool("simulate_publication", **args, expected_revision=approved["revision"])
    assert demo["demo"]["status"] == "mock_published" and demo["olx"] is None
    assert tool("get_publication_status", **args)["olx"] is None
    assert any(event["actor"] == "ha_llm" for event in tool("get_activity_log", **args)["items"])
    print(
        "PASS: official HA MCP lists and calls ULM alongside Assist, with owner review and simulated publication"
    )


if __name__ == "__main__":
    main()
