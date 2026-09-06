#!/usr/bin/env python3
"""R1: Control API in-process (patched ADB, no device)."""

import unittest
from unittest.mock import patch

from api import server as api


class ControlApiComponentTests(unittest.TestCase):
    def setUp(self):
        api.API_TOKEN = ""
        self.client = api.app.test_client()

    def test_health_gapps_ready_when_play_packages_present(self):
        def fake_shell(command, timeout=30):
            if "pm path" in command and (
                "vending" in command or "gms" in command or "gsf" in command
            ):
                return True, "package:/system/priv-app/Phonesky/Phonesky.apk", ""
            return True, "", ""

        with patch.object(api, "ensure_adb_connected", return_value=True), \
             patch.object(api, "run_adb_shell", side_effect=fake_shell):
            body = self.client.get("/health").get_json()
        self.assertEqual(body["status"], "healthy")
        self.assertTrue(body["gapps"]["ready"])
        self.assertTrue(body["gapps"]["play_store"])

    def test_health_gapps_not_ready_without_packages(self):
        with patch.object(api, "ensure_adb_connected", return_value=True), \
             patch.object(api, "run_adb_shell", return_value=(False, "", "not found")):
            body = self.client.get("/health").get_json()
        self.assertFalse(body["gapps"]["ready"])

    def test_start_play_store_uses_monkey_fallback(self):
        calls = []

        def fake_shell(command, timeout=30):
            calls.append(command)
            if "resolve-activity" in command:
                return False, "", "missing"
            return True, "ok", ""

        with patch.object(api, "run_adb_shell", side_effect=fake_shell):
            resp = self.client.post("/apps/com.android.vending/start")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(any("monkey -p com.android.vending" in c for c in calls))

    def test_device_tap(self):
        with patch.object(api, "_do_device_input", return_value={"success": True, "type": "tap"}):
            resp = self.client.post("/device/input", json={"type": "tap", "x": 10, "y": 20})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["success"])

    def test_jobs_require_type(self):
        resp = self.client.post("/jobs", json={})
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
