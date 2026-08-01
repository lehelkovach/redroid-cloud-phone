#!/usr/bin/env python3
"""Unit tests for one-phone-per-user orchestrator sessions."""

import time
import unittest

from orchestrator import server as orch


class UserSessionTests(unittest.TestCase):
    def setUp(self):
        orch._instances.clear()
        orch._leases.clear()
        orch._user_sessions.clear()
        self._mode = orch.ORCH_DEPLOY_MODE
        self._max = orch.ORCH_MAX_INSTANCES
        orch.ORCH_DEPLOY_MODE = "mock"
        orch.ORCH_MAX_INSTANCES = 2

    def tearDown(self):
        orch.ORCH_DEPLOY_MODE = self._mode
        orch.ORCH_MAX_INSTANCES = self._max
        orch._instances.clear()
        orch._leases.clear()
        orch._user_sessions.clear()

    def test_acquire_renew_release(self):
        sess = orch._acquire_user_session("alice", ttl_seconds=60)
        self.assertEqual(sess["owner_user_id"], "alice")
        self.assertFalse(sess["renewed"])
        self.assertIn(sess["instance_id"], orch._instances)

        again = orch._acquire_user_session("alice", ttl_seconds=120)
        self.assertTrue(again["renewed"])
        self.assertEqual(again["instance_id"], sess["instance_id"])

        released = orch._release_user_session("alice")
        self.assertEqual(released["instance_id"], sess["instance_id"])
        self.assertIsNone(orch._release_user_session("alice"))

    def test_second_user_blocked_when_sole_phone_leased(self):
        orch.ORCH_MAX_INSTANCES = 1
        orch.ORCH_DEPLOY_MODE = "external"
        orch._create_instance_record("http://127.0.0.1:18080", "dogfood-phone")
        orch._acquire_user_session("alice", ttl_seconds=60, provision=False)
        with self.assertRaises(RuntimeError) as ctx:
            orch._acquire_user_session("bob", ttl_seconds=60, provision=False)
        self.assertIn("leased to another user", str(ctx.exception))

    def test_http_session_routes(self):
        client = orch.app.test_client()
        r = client.post("/sessions", json={"owner_user_id": "carol", "ttl_seconds": 30})
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["session"]["owner_user_id"], "carol")

        self.assertEqual(client.get("/sessions/carol").status_code, 200)
        self.assertEqual(client.get("/sessions").get_json()["count"], 1)
        r4 = client.delete("/sessions/carol")
        self.assertEqual(r4.status_code, 200)
        self.assertTrue(r4.get_json()["released"])

    def test_swipe_step_normalized(self):
        steps = orch._normalize_steps([
            {"action": "swipe", "x1": 1, "y1": 2, "x2": 3, "y2": 4, "duration": 200}
        ])
        self.assertEqual(steps[0]["action"], "swipe")
        with self.assertRaises(ValueError):
            orch._normalize_steps([{"action": "swipe", "x1": 1}])

    def test_session_requires_owner(self):
        with self.assertRaises(ValueError):
            orch._acquire_user_session("", ttl_seconds=30)

    def test_expired_session_frees_phone(self):
        sess = orch._acquire_user_session("alice", ttl_seconds=60)
        with orch._user_sessions_lock:
            orch._user_sessions["alice"]["expires_at"] = time.time() - 1
        orch._release_user_session("alice")
        bob = orch._acquire_user_session("bob", ttl_seconds=60)
        self.assertEqual(bob["owner_user_id"], "bob")
        self.assertEqual(bob["instance_id"], sess["instance_id"])


if __name__ == "__main__":
    unittest.main()
