#!/usr/bin/env python3
"""R2/R3 process e2e: login operation through orchestrator onto a GApps mock phone."""

import time
import unittest

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fixtures.fake_control import make_control_app
from harness import FlaskThread, OrchestratorProc, find_free_port


class OrchestratorLoginE2ETests(unittest.TestCase):
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

    def test_login_operation_starts_app_and_types_on_redroid(self):
        queued = self.orch.post("/operations", json={
            "operation": "login",
            "app_package": "com.android.vending",
            "purpose": "automation",
            "login": {"username": "testuser", "password": "testpass"},
        })
        self.assertEqual(queued.status_code, 202)
        op_id = queued.json()["operation_id"]

        result = None
        for _ in range(40):
            poll = self.orch.get(f"/operations/{op_id}").json()
            if poll.get("status") in {"done", "failed"}:
                result = poll
                break
            time.sleep(0.15)
        self.assertIsNotNone(result, "operation did not finish")
        self.assertEqual(result["status"], "done", result)
        instance = result["result"]["instance"]
        self.assertEqual(instance["runtime"], "redroid")
        self.assertEqual(instance["purpose"], "automation")

        packages = [c.get("package") for c in self.log if c["endpoint"] == "start_app"]
        self.assertIn("com.android.vending", packages)
        inputs = [c for c in self.log if c["endpoint"] == "device_input"]
        self.assertGreaterEqual(len(inputs), 2)


if __name__ == "__main__":
    unittest.main()
