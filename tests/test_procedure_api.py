#!/usr/bin/env python3
"""HTTP surface for procedure runs on the orchestrator."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orchestrator import server as orch
from orchestrator import procedures as proc
from tests.fixtures.fake_phone import DEMO_APP, FakePhone, FakePhoneServer


class ProcedureApiTests(unittest.TestCase):
    def setUp(self):
        self.server = FakePhoneServer(FakePhone())
        self.server.__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)

        orch.ORCH_DEPLOY_MODE = "mock"
        orch.ORCH_API_TOKEN = ""
        orch.ORCH_MAX_INSTANCES = 2
        orch.ORCH_MOCK_API_URL = self.server.base_url
        orch._instances.clear()
        orch._leases.clear()
        orch._user_sessions.clear()
        orch._ops.clear()
        self.client = orch.app.test_client()
        self.phone = self.server.phone

    def test_surfaces_endpoint_lists_capabilities(self):
        body = self.client.get("/procedures/surfaces").get_json()
        self.assertIn(proc.MOBILE, body["surfaces"])
        self.assertIn("swipe", body["surfaces"][proc.MOBILE])
        self.assertIn("install", body["sensitive"])
        self.assertNotIn(proc.WEB, body["surfaces"], "no driver configured")

    def test_validate_accepts_a_good_procedure(self):
        body = self.client.post("/procedures/validate", json={
            "steps": [{"action": "open", "package": DEMO_APP}],
        }).get_json()
        self.assertTrue(body["valid"])
        self.assertEqual(body["count"], 1)

    def test_validate_rejects_an_unwired_surface(self):
        resp = self.client.post("/procedures/validate", json={
            "steps": [{"action": "type", "text": "x", "surface": "web"}],
        })
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.get_json()["valid"])

    def test_validate_flags_approval(self):
        body = self.client.post("/procedures/validate", json={
            "steps": [{"action": "install", "path": "/tmp/a.apk"}],
        }).get_json()
        self.assertFalse(body["valid"])
        self.assertTrue(body["needs_approval"])

    def test_sync_procedure_run_drives_the_phone(self):
        self.phone.set_proxy({"enabled": True, "host": "h", "port": 1})
        body = self.client.post("/procedures", json={
            "sync": True,
            "steps": proc.login_procedure(
                DEMO_APP, "bs@example.com", "fake-password-not-real",
                password_label="Password",
            ),
        }).get_json()
        self.assertEqual(body["status"], "done", body.get("error"))
        self.assertEqual(self.phone.account, {"email": "bs@example.com"})
        self.assertEqual(body["result"]["status"], "done")

    def test_sync_run_reports_failure_with_index(self):
        body = self.client.post("/procedures", json={
            "sync": True,
            "steps": [{"action": "open", "package": DEMO_APP}],
        }).get_json()
        self.assertEqual(body["status"], "failed")
        self.assertIn("proxy", body["error"])

    def test_run_needing_approval_is_parked_not_executed(self):
        body = self.client.post("/procedures", json={
            "sync": True,
            "steps": [{"action": "install", "path": "/tmp/a.apk"}],
        }).get_json()
        self.assertEqual(body["status"], "needs_approval")
        self.assertEqual(self.phone.calls, [])

    def test_empty_request_rejected(self):
        resp = self.client.post("/procedures", json={})
        self.assertEqual(resp.status_code, 400)

    def test_unknown_procedure_id(self):
        self.assertEqual(self.client.get("/procedures/nope").status_code, 404)

    def test_queued_run_is_pollable(self):
        created = self.client.post("/procedures", json={
            "steps": [{"action": "screenshot"}],
        })
        self.assertEqual(created.status_code, 202)
        pid = created.get_json()["procedure_id"]
        for _ in range(50):
            body = self.client.get(f"/procedures/{pid}").get_json()
            if body["status"] in {"done", "failed", "needs_approval"}:
                break
            import time
            time.sleep(0.05)
        self.assertEqual(body["status"], "done", body.get("error"))
        self.assertEqual(body["kind"], "procedure")


class TokenMismatchTests(unittest.TestCase):
    """A wrong token must read as a config problem, not a broken phone.

    The phone keeps `/health` open, so a mismatched token used to surface as a
    run where every step returned 401 — which looked like a dead device and is
    what kept the lab "down" for two weeks.
    """

    def setUp(self):
        self.server = FakePhoneServer(FakePhone(api_token="phone-token"))
        self.server.__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)

        orch.ORCH_DEPLOY_MODE = "mock"
        orch.ORCH_API_TOKEN = ""  # inbound: this test client is trusted
        orch.ORCH_MOCK_API_URL = self.server.base_url
        orch._instances.clear()
        orch._ops.clear()
        self.client = orch.app.test_client()
        self.addCleanup(setattr, orch, "ORCH_CONTROL_API_TOKEN", "")

    def _run(self):
        return self.client.post("/procedures", json={
            "sync": True,
            "steps": [{"action": "open", "package": DEMO_APP}],
        }).get_json()

    def test_wrong_token_fails_fast_and_names_the_config(self):
        orch.ORCH_CONTROL_API_TOKEN = "not-the-phone-token"
        body = self._run()
        self.assertEqual(body["status"], "failed")
        self.assertIn("ORCH_CONTROL_API_TOKEN", body["error"])
        self.assertNotIn("proxy", body["error"], "not a device failure")
        self.assertEqual(self.server.phone.calls, [], "no step should have run")

    def test_matching_token_reaches_the_device_error_instead(self):
        """With auth right, the run fails on the real reason: no proxy."""
        orch.ORCH_CONTROL_API_TOKEN = "phone-token"
        body = self._run()
        self.assertEqual(body["status"], "failed")
        self.assertIn("proxy", body["error"])

    def test_phone_health_endpoint_relays_the_unauthorized_verdict(self):
        orch.ORCH_CONTROL_API_TOKEN = "not-the-phone-token"
        created = self.client.post("/instances", json={}).get_json()
        body = self.client.get(f"/phones/{created['id']}/health").get_json()
        self.assertEqual(body["status"], "unauthorized")
        self.assertFalse(body["auth"]["ok"])
        self.assertTrue(body["adb_connected"], "the phone itself is fine")


if __name__ == "__main__":
    unittest.main()
