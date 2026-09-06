#!/usr/bin/env python3
"""Control API GApps health reporting (no device)."""

import unittest
from unittest.mock import patch

from api import server as api


class GappsHealthTests(unittest.TestCase):
    def test_ready_when_gms_and_play_present(self):
        def fake_shell(command, timeout=30):
            if "com.android.vending" in command or "com.google.android.gms" in command:
                return True, "package:/system/priv-app/Phonesky/Phonesky.apk", ""
            if "com.google.android.gsf" in command:
                return False, "", "not found"
            return False, "", ""

        with patch.object(api, "run_adb_shell", side_effect=fake_shell):
            status = api._gapps_status()
        self.assertTrue(status["gms"])
        self.assertTrue(status["play_store"])
        self.assertFalse(status["gsf"])
        self.assertTrue(status["ready"])

    def test_spoof_without_packages_is_not_ready(self):
        with patch.object(api, "run_adb_shell", return_value=(True, "", "")):
            status = api._gapps_status()
        self.assertFalse(status["ready"])
        self.assertFalse(status["play_store"])

    def test_health_includes_gapps_when_disconnected(self):
        api.API_TOKEN = ""
        with patch.object(api, "ensure_adb_connected", return_value=False):
            client = api.app.test_client()
            body = client.get("/health").get_json()
        self.assertEqual(body["status"], "degraded")
        self.assertFalse(body["gapps"]["ready"])


if __name__ == "__main__":
    unittest.main()
