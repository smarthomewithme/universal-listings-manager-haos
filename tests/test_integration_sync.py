"""Pure policy checks: never install a wrong, stale or untrusted integration update."""

import importlib.util
import unittest
from pathlib import Path

source = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "universal_listings_manager"
    / "sync_policy.py"
)
spec = importlib.util.spec_from_file_location("ulm_sync_policy", source)
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


class IntegrationSyncPolicyTests(unittest.TestCase):
    def settings(self, **changes):
        result = dict(
            state="on",
            installed_version="v1.4.0",
            latest_version="v1.5.0",
            app_version="1.5.0",
            release_url=policy.RELEASE_PREFIX + "1.5.0",
        )
        result.update(changes)
        return result

    def test_matching_stable_release(self):
        self.assertTrue(policy.may_sync(**self.settings()))

    def test_no_sync_without_new_marketplace_release(self):
        for changes in (
            {"state": "off"},
            {"state": "unavailable"},
            {"latest_version": "v1.4.0"},
            {"latest_version": "v1.6.0"},
            {"app_version": "1.4.0"},
            {"release_url": "https://evil.invalid/releases/tag/v1.5.0"},
            {"release_url": None},
            {"installed_version": None},
            {"latest_version": "v1.5.0-rc1"},
            {"latest_version": "v1.2.0"},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(policy.may_sync(**self.settings(**changes)))
