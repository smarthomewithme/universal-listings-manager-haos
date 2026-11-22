"""Regression tests for ChatGPT file metadata validation without HA imports."""

import ast
import ipaddress
import unittest
from pathlib import Path
from urllib.parse import urlsplit


class BridgeError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def load_validator():
    path = (
        Path(__file__).resolve().parents[1]
        / "custom_components"
        / "universal_listings_manager"
        / "chat_photo.py"
    )
    parsed = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    method = next(
        node for node in parsed.body if isinstance(node, ast.FunctionDef) and node.name == "_valid_source"
    )
    namespace = {"BridgeError": BridgeError, "ipaddress": ipaddress, "urlsplit": urlsplit}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)
    return namespace["_valid_source"]


class ChatFileSourceTests(unittest.TestCase):
    def setUp(self):
        self.validate = load_validator()

    def test_accept_opaque_host_file_identifiers(self):
        url = "https://files.example.com/private/download?token=signed"
        for file_id in ("file_12345", "opaque-12345", "upload:identifier"):
            with self.subTest(file_id=file_id):
                self.assertEqual(
                    self.validate({"file_id": file_id, "download_url": url}),
                    url,
                )

    def test_missing_metadata_reports_source_error(self):
        bad = (
            "file_12345",
            {},
            {"file_id": "file_12345"},
            {"download_url": "https://files.example.com/photo.jpg"},
            {"file_id": ""},
            {"file_id": " "},
            {"file_id": "a" * 241},
        )
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(BridgeError) as error:
                    self.validate(value)
                self.assertEqual(error.exception.code, "PHOTO_SOURCE")

    def test_unsafe_download_addresses_are_rejected(self):
        for url in (
            "http://files.example.com/photo",
            "https://127.0.0.1/photo",
            "https://192.168.1.1/photo",
            "https://files.example.com:8443/photo",
            "https://user:pass@files.example.com/photo",
        ):
            with self.subTest(url=url):
                with self.assertRaises(BridgeError):
                    self.validate({"file_id": "opaque", "download_url": url})


if __name__ == "__main__":
    unittest.main()
