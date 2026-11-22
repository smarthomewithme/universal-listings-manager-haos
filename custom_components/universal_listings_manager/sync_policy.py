"""Pure, conservative checks for syncing the ULM Marketplace integration.

The HA Core integration may update *only itself*, via the normal Marketplace
update entity, when the installed HAOS app and signed stable release agree.
This file deliberately has no HA or filesystem dependencies so it is testable
without a running Home Assistant instance.
"""

import re

RELEASE_PREFIX = "https://github.com/smarthomewithme/universal-listings-manager-haos/releases/tag/v"
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def _parts(value):
    if not isinstance(value, str):
        return None
    normalized = value.removeprefix("v")
    if not VERSION_RE.fullmatch(normalized):
        return None
    return tuple(int(part) for part in normalized.split("."))


def may_sync(*, state, installed_version, latest_version, app_version, release_url):
    """True only for a published, matching stable upgrade to this exact integration."""
    old = _parts(installed_version)
    new = _parts(latest_version)
    app = _parts(app_version)
    if state != "on" or old is None or new is None or app is None:
        return False
    if new <= old or new != app:
        return False
    return release_url == RELEASE_PREFIX + ".".join(str(part) for part in new)
