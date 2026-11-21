"""Validate the source tree and render metadata for Home Assistant's stable branch."""

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlencode

import yaml

APP = Path("olx_manager")
VERSION_PATTERN = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
REPO_PATTERN = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9-]*/[a-zA-Z0-9_.-]+")
BUTTON_PATTERN = re.compile(r"(?<=<!-- HA_INSTALL_BUTTON -->\n).*?(?=\n<!-- /HA_INSTALL_BUTTON -->)", re.S)


def version_parts(version):
    if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"Oczekiwano stabilnej wersji SemVer, otrzymano {version!r}.")
    return tuple(map(int, version.split(".")))


def validate(root):
    app = root / APP
    repo = yaml.safe_load((root / "repository.yaml").read_text())
    if not repo.get("name") or not repo.get("maintainer"):
        raise ValueError("repository.yaml wymaga name i maintainer.")
    config = yaml.safe_load((app / "config.yaml").read_text())
    version = config["version"]
    version_parts(version)
    if version != (app / "app/VERSION").read_text(encoding="ascii").strip():
        raise ValueError("Rozbieżna wersja w config.yaml i app/VERSION.")
    if config["slug"] != "olx_manager" or config["arch"] != ["amd64"]:
        raise ValueError("Wymagany olx_manager dla amd64.")
    if not config["ingress"] or config["ingress_port"] != 8099 or "ports" in config:
        raise ValueError("Panel ma działać przez Ingress bez portów publikowanych na HAOS.")
    if config["options"]["olx_write_enabled"] is not False or config["options"]["mcp_enabled"] is not False:
        raise ValueError("Niebezpieczne opcje domyślne.")
    if set(config["options"]) != set(config["schema"]):
        raise ValueError("Opcje i ich schemat są niespójne.")
    breaks = config.get("breaking_versions", [])
    if any(version_parts(entry) > version_parts(version) for entry in breaks) or breaks != sorted(
        set(breaks), key=version_parts
    ):
        raise ValueError("breaking_versions musi zawierać unikalne wersje w porządku rosnącym.")
    for filename in ("Dockerfile", "README.md", "DOCS.md", "app/static/index.html"):
        if not (app / filename).is_file():
            raise ValueError(f"Brak pliku {filename}.")
    notes(app / "CHANGELOG.md", version)
    component = root / "custom_components/universal_listings_manager/manifest.json"
    if component.is_file() or (root / "hacs.json").exists():
        manifest = json.loads(component.read_text())
        hacs = json.loads((root / "hacs.json").read_text())
        if (
            manifest["domain"] != "universal_listings_manager"
            or manifest["version"] != version
            or manifest["config_flow"] is not True
            or not hacs["homeassistant"]
        ):
            raise ValueError("The HACS manifest must match the app release version and declare UI setup.")
    return config


def notes(changelog, version):
    match = re.search(rf"^## {re.escape(version)}\s*\n(.*?)(?=^## |\Z)", changelog.read_text(), re.M | re.S)
    if not match or not match[1].strip():
        raise ValueError(f"Brak zmian dla {version} w CHANGELOG.md.")
    return match[1].strip()


def release_decision(candidate, stable):
    if stable is None:
        return "publish"
    target, current = version_parts(candidate), version_parts(stable)
    if target < current:
        raise ValueError("Wersja kandydata jest starsza niż wersja udostępniona w HA.")
    return "publish" if target > current else "unchanged"


def add_button(repository_url):
    query = urlencode({"repository_url": repository_url})
    return (
        "[![Dodaj do Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)]"
        f"(https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?{query})"
    )


def render(root, repository, sha, digest):
    if not REPO_PATTERN.fullmatch(repository) or repository.endswith(".git"):
        raise ValueError("Oczekiwano repozytorium GitHub w formacie owner/name.")
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("Nieprawidłowy SHA źródeł lub digest obrazu.")
    config = validate(root)
    image = "ghcr.io/" + repository.lower()
    url = "https://github.com/" + repository
    cfg_path = root / APP / "config.yaml"
    cfg = cfg_path.read_text()
    if "image" in config:
        raise ValueError("Gałąź development musi pozostawić pole image do wygenerowania po publikacji.")
    cfg_path.write_text(cfg.rstrip() + f'\nimage: "{image}"\n')
    repo_path = root / "repository.yaml"
    repo = repo_path.read_text()
    if "url" not in yaml.safe_load(repo):
        repo_path.write_text(repo.rstrip() + f'\nurl: "{url}"\n')
    doc_path = root / "README.md"
    contents, count = BUTTON_PATTERN.subn(add_button(url), doc_path.read_text())
    if count != 1:
        raise ValueError("README wymaga jednego miejsca na przycisk HA.")
    doc_path.write_text(contents)
    (root / "RELEASE_IMAGE.md").write_text(
        f"# Container image {config['version']}\n\n"
        f"- amd64 image: `{image}:{config['version']}`\n"
        f"- Digest: `{digest}`\n"
        f"- Source commit: `{sha}`\n"
    )
    if validate(root)["image"] != image:
        raise ValueError("Wygenerowany config.yaml nie zawiera adresu obrazu.")


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check")
    decision = commands.add_parser("decision")
    decision.add_argument("stable_version", nargs="?")
    rendering = commands.add_parser("render")
    rendering.add_argument("repository")
    rendering.add_argument("sha")
    rendering.add_argument("digest")
    args = parser.parse_args()
    root = Path.cwd()
    config = validate(root)
    if args.command == "check":
        print(config["version"])
    elif args.command == "decision":
        print(release_decision(config["version"], args.stable_version))
    else:
        render(root, args.repository, args.sha, args.digest)


if __name__ == "__main__":
    main()
