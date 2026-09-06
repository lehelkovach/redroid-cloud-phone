#!/usr/bin/env python3
"""R1: Control API in-process (patched ADB, no device)."""

import unittest
from unittest.mock import patch

from api import cloudphone_logging as cpl
from api import server as api
from api import viewport


class ControlApiComponentTests(unittest.TestCase):
    def setUp(self):
        api.API_TOKEN = ""
        viewport.reset()
        cpl.clear_logs()
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
        self.assertIn("appium", body)
        self.assertIn("vnc", body)
        self.assertEqual(body["vnc"]["port"], 5900)

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

    def test_commandlet_tap_percent_logs_cmd(self):
        with patch.object(api, "run_adb_shell", return_value=(True, "Physical size: 1280x720", "")):
            resp = self.client.post("/ui/command", json={"action": "tap", "xp": 50, "yp": 50})
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        body = resp.get_json()
        self.assertEqual(body["backend"], "adb")
        self.assertEqual(body["commands"], ["input tap 640 360"])
        cmd_lines = cpl.recent_logs(log_type="CMD")
        self.assertTrue(any("commandlet" in item["msg"] for item in cmd_lines), cmd_lines)

    def test_appium_backend_unavailable_logs_apm(self):
        with patch.object(api, "run_adb_shell", return_value=(True, "Physical size: 1280x720", "")):
            resp = self.client.post(
                "/ui/command",
                json={"action": "tap", "x": 1, "y": 2, "backend": "appium"},
            )
        self.assertEqual(resp.status_code, 501)
        body = resp.get_json()
        self.assertEqual(body["backend"], "appium")
        self.assertIn("w3c", body)
        apm_lines = cpl.recent_logs(log_type="APM")
        self.assertTrue(
            any("unavailable" in item["msg"] or "w3c" in item["msg"] for item in apm_lines),
            apm_lines,
        )

    def test_appium_status_endpoint(self):
        body = self.client.get("/appium/status").get_json()
        self.assertIn("url", body)
        self.assertIn("ready", body)
        self.assertFalse(body["ready"])
        self.assertTrue(cpl.recent_logs(log_type="APM"))

    def test_vnc_viewport_logs(self):
        body = self.client.get("/vnc/status").get_json()
        self.assertEqual(body["protocol"], "rfb")
        self.assertEqual(body["port"], 5900)
        self.assertEqual(body["width"], 1280)
        self.assertEqual(body["height"], 720)
        attached = self.client.post("/vnc/attach")
        self.assertEqual(attached.status_code, 201)
        self.assertEqual(attached.get_json()["clients"], 1)
        vnc_lines = cpl.recent_logs(log_type="VNC")
        self.assertTrue(any("viewport" in item["msg"] for item in vnc_lines), vnc_lines)

    def test_logs_endpoint_filters_types(self):
        with patch.object(api, "run_adb_shell", return_value=(True, "Physical size: 1280x720", "")):
            self.client.post("/ui/command", json={"action": "key", "key": "back"})
        self.client.get("/vnc/status")
        self.client.get("/appium/status")
        filtered = self.client.get("/logs?type=CMD,APM,VNC").get_json()
        types = {item["type"] for item in filtered["logs"]}
        self.assertTrue({"CMD", "APM", "VNC"} <= types, types)


if __name__ == "__main__":
    unittest.main()
