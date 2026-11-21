"""Read and verify a GHCR manifest, using either explicit GH credentials or anonymous access."""

import argparse
import base64
import hashlib
import json
import os
import re
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

MIME = ",".join(
    [
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    ]
)


def manifest(image, authenticated=False, allow_missing=False):
    match = re.fullmatch(r"ghcr\.io/([a-z0-9-]+/[a-z0-9_.-]+):([0-9]+\.[0-9]+\.[0-9]+)", image)
    if not match:
        raise ValueError("Oczekiwano adresu GHCR oznaczonego wersją.")
    package, tag = match.groups()
    scope = f"repository:{package}:pull,push" if authenticated else f"repository:{package}:pull"
    auth_url = "https://ghcr.io/token?" + urlencode({"service": "ghcr.io", "scope": scope})
    headers = {}
    if authenticated:
        encoded = base64.b64encode(f"{os.environ['GITHUB_ACTOR']}:{os.environ['GH_TOKEN']}".encode())
        headers["Authorization"] = "Basic " + encoded.decode("ascii")
    with urlopen(Request(auth_url, headers=headers), timeout=20) as response:
        token = json.load(response)["token"]
    req = Request(
        f"https://ghcr.io/v2/{package}/manifests/{tag}",
        headers={"Accept": MIME, "Authorization": "Bearer " + token},
    )
    try:
        with urlopen(req, timeout=20) as response:
            data = response.read(2_000_001)
            digest = response.headers.get("Docker-Content-Digest")
            if len(data) > 2_000_000 or digest != "sha256:" + hashlib.sha256(data).hexdigest():
                raise ValueError("Manifest GHCR nie zgadza się z digestem.")
            return digest
    except HTTPError as error:
        if allow_missing and error.code == 404:
            codes = {item.get("code") for item in json.loads(error.read(8192)).get("errors", [])}
            if codes & {"MANIFEST_UNKNOWN", "NAME_UNKNOWN"}:
                return ""
        raise


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("image")
    cli.add_argument("--authenticated", action="store_true")
    cli.add_argument("--allow-missing", action="store_true")
    args = cli.parse_args()
    if args.allow_missing and not args.authenticated:
        cli.error("Sprawdzanie zajętego tagu wymaga uwierzytelnienia.")
    try:
        print(manifest(args.image, args.authenticated, args.allow_missing))
    except HTTPError as error:
        cli.exit(1, f"GHCR zwrócił HTTP {error.code}. Sprawdź istnienie i widoczność pakietu.\n")


if __name__ == "__main__":
    main()
