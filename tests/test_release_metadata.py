import hashlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ghcr_manifest import manifest  # noqa: E402
from release_metadata import release_decision, render, validate  # noqa: E402


class ReleaseMetadataTests(unittest.TestCase):
    def test_promotion_uses_the_repository_and_matching_image(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copytree(ROOT / "olx_manager", root / "olx_manager")
            shutil.copytree(ROOT / "custom_components", root / "custom_components")
            for name in ("README.md", "repository.yaml", "hacs.json"):
                shutil.copy(ROOT / name, root / name)
            version = validate(root)["version"]
            repo = "tester-xyz/fixture-haos"
            render(root, repo, "1" * 40, "sha256:" + "2" * 64)
            ready = validate(root)
            self.assertEqual(ready["image"], "ghcr.io/tester-xyz/fixture-haos")
            self.assertEqual(ready["version"], version)
            button = (root / "README.md").read_text().split("](https://my.home-assistant.io/redirect/")[1]
            query = parse_qs(urlsplit(button.split(")")[0]).query)
            self.assertEqual(query, {"repository_url": ["https://github.com/" + repo]})

    def test_release_gate_prevents_unchanged_versions_and_downgrades(self):
        self.assertEqual(release_decision("1.10.0", "1.9.9"), "publish")
        self.assertEqual(release_decision("1.1.0", None), "publish")
        self.assertEqual(release_decision("1.1.0", "1.1.0"), "unchanged")
        with self.assertRaises(ValueError):
            release_decision("1.0.0", "1.1.0")

    def test_registry_manifest_digest_is_verified(self):
        doc = b'{"schemaVersion":2}'
        digest = "sha256:" + hashlib.sha256(doc).hexdigest()

        def fake(req, timeout):
            response = io.BytesIO(doc if "/manifests/" in req.full_url else b'{"token":"test-only"}')
            response.headers = {"Docker-Content-Digest": digest}
            if "/token?" in req.full_url:
                self.assertIsNone(req.get_header("Authorization"))
            return response

        with patch("ghcr_manifest.urlopen", side_effect=fake):
            self.assertEqual(manifest("ghcr.io/tester/fixture:1.1.0"), digest)
            digest = "sha256:" + "0" * 64
            with self.assertRaises(ValueError):
                manifest("ghcr.io/tester/fixture:1.1.0")

    def test_registry_permission_error_cannot_be_treated_as_empty_tag(self):
        image = "ghcr.io/tester/fixture:1.1.0"
        with patch.dict("os.environ", {"GH_TOKEN": "fixture-value", "GITHUB_ACTOR": "fixture"}):
            for status, code, missing in [(403, "DENIED", False), (404, "MANIFEST_UNKNOWN", True)]:
                error = HTTPError(
                    "https://ghcr.io",
                    status,
                    "fixture",
                    Message(),
                    io.BytesIO(json.dumps({"errors": [{"code": code}]}).encode()),
                )
                with patch(
                    "ghcr_manifest.urlopen", side_effect=[io.BytesIO(b'{"token":"test-only"}'), error]
                ):
                    if missing:
                        self.assertEqual(manifest(image, authenticated=True, allow_missing=True), "")
                    else:
                        with self.assertRaises(HTTPError):
                            manifest(image, authenticated=True, allow_missing=True)
