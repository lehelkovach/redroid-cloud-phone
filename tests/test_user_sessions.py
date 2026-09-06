#!/usr/bin/env python3
"""Playwright-like session acquire/release on the orchestrator (mock runtime)."""

import os
import unittest

from orchestrator import server as orch


class UserSessionTests(unittest.TestCase):
    def setUp(self):
        orch.ORCH_DEPLOY_MODE = "mock"
        orch.ORCH_MAX_INSTANCES = 2
        orch.ORCH_API_TOKEN = ""
        orch._instances.clear()
        orch._leases.clear()
        orch._user_sessions.clear()
        orch._ops.clear()
        self.client = orch.app.test_client()

    def test_acquire_renew_release(self):
        r = self.client.post("/sessions", json={"owner_user_id": "alice", "ttl_seconds": 30})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        body = r.get_json()
        sess = body["session"]
        self.assertEqual(sess["owner_user_id"], "alice")
        self.assertEqual(sess["runtime"], "redroid")
        self.assertTrue(sess["instance_id"])

        r2 = self.client.post("/sessions", json={"owner_user_id": "alice", "ttl_seconds": 60})
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.get_json()["session"]["instance_id"], sess["instance_id"])

        listed = self.client.get("/sessions").get_json()
        self.assertEqual(listed["count"], 1)

        gone = self.client.delete("/sessions/alice")
        self.assertEqual(gone.status_code, 200)
        self.assertEqual(self.client.get("/sessions/alice").status_code, 404)

    def test_second_owner_409_when_full(self):
        orch.ORCH_MAX_INSTANCES = 1
        first = self.client.post("/sessions", json={"owner_user_id": "alice", "ttl_seconds": 30})
        self.assertEqual(first.status_code, 201, first.get_data(as_text=True))
        second = self.client.post("/sessions", json={"owner_user_id": "bob", "ttl_seconds": 30})
        self.assertEqual(second.status_code, 409, second.get_data(as_text=True))

    def test_provision_second_phone(self):
        orch.ORCH_MAX_INSTANCES = 2
        a = self.client.post("/sessions", json={"owner_user_id": "alice", "ttl_seconds": 30})
        self.assertEqual(a.status_code, 201)
        b = self.client.post(
            "/sessions",
            json={"owner_user_id": "bob", "ttl_seconds": 30, "provision": True, "purpose": "play"},
        )
        self.assertEqual(b.status_code, 201, b.get_data(as_text=True))
        self.assertNotEqual(
            a.get_json()["session"]["instance_id"],
            b.get_json()["session"]["instance_id"],
        )
        self.assertEqual(b.get_json()["session"]["purpose"], "automation")
        health = self.client.get("/health").get_json()
        self.assertEqual(health["instances"], 2)
        self.assertEqual(health["sessions"], 2)
        self.assertEqual(health["runtime"], "mock")
        self.assertEqual(health["default_runtime"], "redroid")
        self.assertIn("pool", health)

    def test_owner_required(self):
        r = self.client.post("/sessions", json={})
        self.assertEqual(r.status_code, 400)


class RedroidProvisionTests(unittest.TestCase):
    def setUp(self):
        orch.ORCH_DEPLOY_MODE = "redroid"
        orch.ORCH_MAX_INSTANCES = 3
        orch.ORCH_API_TOKEN = ""
        orch._instances.clear()
        orch._leases.clear()
        orch._user_sessions.clear()
        self._prev_dry = os.environ.get("ORCH_REDROID_DRY_RUN")
        os.environ["ORCH_REDROID_DRY_RUN"] = "1"

    def tearDown(self):
        orch.ORCH_DEPLOY_MODE = "mock"
        if self._prev_dry is None:
            os.environ.pop("ORCH_REDROID_DRY_RUN", None)
        else:
            os.environ["ORCH_REDROID_DRY_RUN"] = self._prev_dry

    def test_redroid_dry_run_provision(self):
        inst = orch._provision_instance()
        self.assertEqual(inst["runtime"], "redroid")
        self.assertTrue(inst["adb_connect"].startswith("127.0.0.1:"))
        self.assertEqual(inst["mode"], "redroid")


if __name__ == "__main__":
    unittest.main()
