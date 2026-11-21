"""Run inside the image to seed or verify persistent data without leaving the container."""

import argparse
import hashlib
import io
import json
import urllib.request
from pathlib import Path

from PIL import Image

BASE = "http://127.0.0.1:8099"


def request(path, payload=None, headers=None, method=None, raw=False):
    args = dict(headers or {})
    if payload is not None and not isinstance(payload, bytes):
        payload = json.dumps(payload).encode()
        args["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, payload, args, method=method)
    with urllib.request.urlopen(req, timeout=8) as response:
        return response.read() if raw else json.load(response)


def seed(csrf):
    assert b"Universal Listings Manager" in request("/", raw=True)
    draft = request(
        "/api/drafts",
        {
            "request_id": "container-e2e",
            "fields": {
                "title": "Próbny album",
                "description": "Historia aktualizacji obrazu",
                "location": "Olsztyn",
                "condition": "używany",
                "price_minor": 15000,
            },
        },
        csrf,
    )
    path = "/api/drafts/" + draft["id"]
    draft = request(
        path,
        {"expected_revision": draft["revision"], "changes": {"price_minor": 15100}},
        csrf,
        method="PATCH",
    )
    output = io.BytesIO()
    Image.new("RGB", (31, 29), "blue").save(output, format="PNG")
    original = output.getvalue()
    draft = request(
        path + "/photos",
        original,
        {
            **csrf,
            "Content-Type": "image/png",
            "X-Expected-Revision": str(draft["revision"]),
            "X-Filename": "releases.png",
        },
    )
    draft = request(path + "/action/request", {"expected_revision": draft["revision"]}, csrf)
    preview = request(path)
    approved = request(
        path + "/action/approve",
        {"expected_revision": draft["revision"], "digest": preview["review_digest"]},
        csrf,
    )
    simulate = request(path + "/action/simulate", {"expected_revision": approved["revision"]}, csrf)
    assert simulate["demo"]["status"] == "mock_published"
    assert simulate["olx"] is None
    state = {
        "path": path,
        "approval": approved["approval"],
        "asset": draft["media"][0]["id"],
        "demo_id": simulate["demo"]["id"],
        "revision": approved["revision"],
        "sha": hashlib.sha256(original).hexdigest(),
    }
    Path("/data/container-test.json").write_text(json.dumps(state))


def verify():
    state = json.loads(Path("/data/container-test.json").read_text())
    draft = request(state["path"])
    assert draft["approval"] == state["approval"]
    assert draft["approval"]["digest"] == draft["review_digest"]
    assert draft["fields"]["price_minor"] == 15100 and draft["revision"] == state["revision"]
    assert draft["publication"]["demo"]["id"] == state["demo_id"] and draft["publication"]["olx"] is None
    photo = request(state["path"] + "/photos/" + state["asset"] + "?original=1", raw=True)
    assert hashlib.sha256(photo).hexdigest() == state["sha"]


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("action", choices=["seed", "verify"])
    cli.add_argument("version")
    args = cli.parse_args()
    assert request("/health") == {"status": "ok", "version": args.version}
    if args.action == "seed":
        token = request("/api/session")["csrf"]
        seed({"X-CSRF-Token": token})
    else:
        verify()
    print(f"PASS {args.action} {args.version}")


if __name__ == "__main__":
    main()
