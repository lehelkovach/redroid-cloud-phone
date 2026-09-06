#!/usr/bin/env python3
"""R2 process integration: orchestrator HTTP → mock Control API phone routing."""

import unittest

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fixtures.fake_control import make_control_app
from harness import FlaskThread, OrchestratorProc, find_free_port


class OrchestratorIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app, log = make_control_app("redroid")
        cls.log = log
        cls.phone = FlaskThread(app, find_free_port()).start()
        cls.orch = OrchestratorProc(cls.phone.url).start()

    @classmethod
    def tearDownClass(cls):
        cls.orch.stop()
        cls.phone.stop()

    def test_health_defaults_to_redroid_pool(self):
        body = self.orch.get("/health").json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["default_runtime"], "redroid")
        self.assertEqual(body["default_purpose"], "automation")

    def test_phone_status_input_screenshot_and_jobs(self):
        created = self.orch.post("/instances")
        self.assertEqual(created.status_code, 201)
        instance_id = created.json()["id"]
        self.assertEqual(created.json()["runtime"], "redroid")

        status = self.orch.get(f"/phones/{instance_id}/status")
        status.raise_for_status()
        self.assertTrue(status.json()["connected"])

        tapped = self.orch.post(
            f"/phones/{instance_id}/input", json={"type": "tap", "x": 10, "y": 20}
        )
        tapped.raise_for_status()

        shot = self.orch.get(f"/phones/{instance_id}/screenshot")
        shot.raise_for_status()
        self.assertIn("image_base64", shot.json())

        queued = self.orch.post(
            f"/phones/{instance_id}/jobs",
            json={"type": "adb_shell", "payload": {"command": "echo ok"}},
        )
        self.assertEqual(queued.status_code, 202)
        job_id = queued.json()["job_id"]
        polled = self.orch.get(f"/phones/{instance_id}/jobs/{job_id}")
        polled.raise_for_status()
        self.assertEqual(polled.json()["status"], "done")

        endpoints = [c["endpoint"] for c in self.log]
        self.assertIn("device_input", endpoints)
        self.assertIn("screenshot", endpoints)
        self.assertIn("jobs", endpoints)

    def test_verbose_appium_commandlet_vnc_logs(self):
        created = self.orch.post("/instances")
        self.assertEqual(created.status_code, 201)
        instance_id = created.json()["id"]

        ui = self.orch.post(
            f"/phones/{instance_id}/ui",
            json={"action": "tap", "xp": 50, "yp": 50},
        )
        self.assertEqual(ui.status_code, 200, ui.text)
        self.assertEqual(ui.json()["backend"], "adb")
        self.assertEqual(ui.json()["commands"], ["input tap 640 360"])

        appium = self.orch.get(f"/phones/{instance_id}/appium")
        appium.raise_for_status()
        self.assertIn("url", appium.json())

        vnc = self.orch.get(f"/phones/{instance_id}/vnc")
        vnc.raise_for_status()
        self.assertEqual(vnc.json()["port"], 5900)
        self.assertEqual(vnc.json()["width"], 1280)

        logs = self.orch.get(f"/phones/{instance_id}/logs?type=CMD,APM,VNC")
        logs.raise_for_status()
        types = {item["type"] for item in logs.json()["logs"]}
        self.assertTrue({"CMD", "APM", "VNC"} <= types, logs.json())

    def test_auth_required(self):
        import requests
        resp = requests.get(f"{self.orch.url}/instances", timeout=5)
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
