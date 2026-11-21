import io
import os
import secrets
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
from PIL import Image

APP = Path(__file__).resolve().parents[1] / "olx_manager" / "app"
sys.path.insert(0, str(APP))

from media import decode  # noqa: E402
from models import DraftFields  # noqa: E402

OWNER = "owner:test-owner"


def sample_bytes(fmt="PNG", color="red", size=(100, 65), exif=None):
    stream = io.BytesIO()
    Image.new("RGB", size, color).save(stream, fmt, **({"exif": exif} if exif else {}))
    return stream.getvalue()


def ready(store, approved=True, **extra):
    fields = {
        "title": "Przedmiot testowy",
        "description": "Przykładowy opis produktu testowego.",
        "location": "Gdańsk",
        "condition": "używany",
        "price_minor": 12050,
    }
    draft = store.create_draft(DraftFields.model_validate({**fields, **extra}), OWNER)
    draft = store.upload(draft["id"], draft["revision"], decode(sample_bytes()), "obraz.png", OWNER)
    draft = store.transition(draft["id"], draft["revision"], "request", OWNER)
    if approved:
        return store.approve(draft["id"], draft["revision"], store.review_digest(draft), OWNER)
    return draft


@contextmanager
def live_server(data_dir, enabled=True, command=None):
    bearer = secrets.token_urlsafe(36)
    env = {
        **os.environ,
        "DEV_PANEL": "true",
        "DATA_DIR": str(data_dir),
        "MCP_ENABLED": str(enabled).lower(),
        "MCP_BEARER_TOKEN": bearer,
        "MOCK_ENABLED": "true",
        "OLX_WRITE_ENABLED": "false",
        "OLX_PUBLIC_BASE": "",
        "OWNER_USER_ID": "",
        "MCP_RESOURCE_URL": "",
        "MCP_ISSUER": "",
        "MCP_JWKS_URL": "",
        "MCP_SUBJECT": "",
    }
    args = command or [sys.executable, str(APP / "server.py")]
    process = subprocess.Popen(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for _ in range(80):
            if process.poll() is not None:
                raise RuntimeError(process.stdout.read())
            try:
                if httpx.get("http://127.0.0.1:8099/health", timeout=0.5, trust_env=False).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("Aplikacja HTTP nie wystartowała.")
        yield bearer
    finally:
        process.terminate()
        try:
            process.communicate(timeout=4)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
