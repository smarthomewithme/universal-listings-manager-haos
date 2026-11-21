import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from models import Model

SUPERVISOR_IP = "172.30.32.2"
PANEL_PORT = 8099
MCP_PORT = 8100
OLX_PORT = 8101


def https_url(value):
    try:
        parts = urlsplit(value)
        return (
            parts.scheme == "https"
            and parts.hostname
            and not parts.username
            and not parts.password
            and not parts.query
            and not parts.fragment
            and not any(c.isspace() for c in value)
        )
    except ValueError:
        return False


class Settings(Model):
    data_dir: Path = Path("/data")
    inbox_dir: Path = Path("/share/olx_manager/inbox")
    # Development mode is only available outside HAOS and listens on loopback.
    dev_panel: bool = False
    owner_user_id: str = ""
    mcp_enabled: bool = False
    mcp_bearer_token: str = ""
    mcp_resource_url: str = ""
    mcp_issuer: str = ""
    mcp_jwks_url: str = ""
    mcp_subject: str = ""
    mock_enabled: bool = True
    olx_write_enabled: bool = False
    olx_public_base: str = ""
    olx_allowed_categories: str = ""
    olx_no_charge_confirmed: bool = False
    olx_daily_limit: int = Field(3, ge=1, le=10)

    @model_validator(mode="after")
    def config_valid(self):
        oauth = [self.mcp_resource_url, self.mcp_issuer, self.mcp_jwks_url, self.mcp_subject]
        if any(oauth) and (
            not all(oauth)
            or not all(https_url(u) for u in oauth[:3])
            or urlsplit(self.mcp_resource_url).path != "/mcp"
        ):
            raise ValueError("Uzupełnij issuer, JWKS, subject i pełny adres HTTPS MCP zakończony /mcp.")
        if self.mcp_bearer_token and not re.fullmatch(r"[A-Za-z0-9_-]{32,200}", self.mcp_bearer_token):
            raise ValueError("Ustaw losowy token MCP base64url o długości co najmniej 32 znaków.")
        if self.mcp_enabled and not (self.mcp_bearer_token or self.mcp_resource_url):
            raise ValueError("Włączony MCP wymaga uwierzytelnienia.")
        if self.olx_public_base and (
            not https_url(self.olx_public_base) or urlsplit(self.olx_public_base).path.strip("/")
        ):
            raise ValueError("Adres publiczny OLX musi być samą domeną HTTPS.")
        if self.olx_allowed_categories and not re.fullmatch(
            r"[1-9][0-9]{0,9}(?: *, *[1-9][0-9]{0,9}){0,49}", self.olx_allowed_categories
        ):
            raise ValueError("Kategorie OLX podaj jako numery oddzielone przecinkiem.")
        return self

    @property
    def allowed_categories(self):
        values = self.olx_allowed_categories
        return {int(x) for x in values.split(",")} if values else set()

    @property
    def redirect_uri(self):
        return self.olx_public_base.rstrip("/") + "/olx/callback"

    @classmethod
    def load(cls):
        options = Path("/data/options.json")
        if options.is_file():
            values = json.loads(options.read_text())
            base = cls()
            allowed = type(base).model_fields.keys() - {"dev_panel", "data_dir", "inbox_dir"}
            return cls.model_validate({k: v for k, v in values.items() if k in allowed})
        return cls.model_validate(
            {k: os.environ[k.upper()] for k in cls.model_fields if k.upper() in os.environ}
        )
