#!/usr/bin/env python3
"""
Unit tests for orchestrator components.
"""

import unittest
from unittest.mock import patch

from orchestrator import server as orch


class OrchestratorUnitTests(unittest.TestCase):
    def test_build_login_steps_default(self):
        payload = {
            "app_package": "com.example.app",
            "login": {"username": "user", "password": "pass"}
        }
        steps = orch._build_login_steps(payload)
        self.assertEqual(steps[0]["action"], "start_app")
        self.assertEqual(steps[0]["package"], "com.example.app")
        self.assertEqual(steps[2]["action"], "input_text")
        self.assertEqual(steps[2]["text"], "user")
        self.assertEqual(steps[-1]["action"], "key")
        self.assertEqual(steps[-1]["keycode"], 66)

    def test_build_login_steps_with_taps(self):
        payload = {
            "app_package": "com.example.app",
            "login": {
                "username": "user",
                "password": "pass",
                "password_tap": {"x": 100, "y": 200},
                "submit_tap": {"x": 300, "y": 400}
            }
        }
        steps = orch._build_login_steps(payload)
        self.assertEqual(steps[4]["action"], "tap")
        self.assertEqual(steps[4]["x"], 100)
        self.assertEqual(steps[4]["y"], 200)
        self.assertEqual(steps[-1]["action"], "tap")
        self.assertEqual(steps[-1]["x"], 300)
        self.assertEqual(steps[-1]["y"], 400)

    @patch("orchestrator.server._control_post")
    def test_run_steps_happy_path(self, mock_post):
        mock_post.return_value = {"success": True}
        steps = [
            {"action": "start_app", "package": "com.example.app"},
            {"action": "tap", "x": 10, "y": 20},
            {"action": "input_text", "text": "hi"},
            {"action": "key", "keycode": 66},
        ]
        result = orch._run_steps("http://mock", steps)
        self.assertEqual(len(result), 4)
        self.assertEqual(mock_post.call_count, 4)

    def test_run_steps_invalid_action(self):
        steps = [{"action": "unknown"}]
        with self.assertRaises(ValueError):
            orch._run_steps("http://mock", steps)

    @patch("orchestrator.server._control_get")
    @patch("orchestrator.server._run_steps")
    @patch("orchestrator.server._get_or_create_instance")
    def test_run_operation_success(self, mock_get_instance, mock_run_steps, mock_get):
        mock_get_instance.return_value = {"api_url": "http://mock", "id": "id1", "name": "n1"}
        mock_get.return_value = {"status": "healthy"}
        mock_run_steps.return_value = [{"success": True}]
        op_id = "op1"
        orch._ops[op_id] = {"id": op_id, "status": "queued"}
        payload = {"operation": "login", "app_package": "com.example.app", "login": {"username": "u", "password": "p"}}
        orch._run_operation(op_id, payload)
        self.assertEqual(orch._ops[op_id]["status"], "done")
        self.assertIn("result", orch._ops[op_id])

    def test_max_instances_guard(self):
        orig = orch.ORCH_MAX_INSTANCES
        try:
            orch.ORCH_MAX_INSTANCES = 1
            orch._instances.clear()
            orch._create_instance_record("http://mock", "one")
            with self.assertRaises(RuntimeError):
                orch._provision_instance()
        finally:
            orch.ORCH_MAX_INSTANCES = orig

    def test_normalize_steps_validation(self):
        steps = [{"action": "start_app", "package": "com.app"}]
        self.assertEqual(orch._normalize_steps(steps), steps)
        with self.assertRaises(ValueError):
            orch._normalize_steps({"action": "tap"})
        with self.assertRaises(ValueError):
            orch._normalize_steps([{"action": "unknown"}])

    def test_leases(self):
        orch._leases.clear()
        orch._set_lease("id1", "owner1", 30)
        self.assertTrue(orch._is_lease_valid("id1"))
        self.assertTrue(orch._is_lease_valid("id1", owner="owner1"))
        self.assertFalse(orch._is_lease_valid("id1", owner="owner2"))
        orch._clear_lease("id1")
        self.assertFalse(orch._is_lease_valid("id1"))


if __name__ == "__main__":
    unittest.main()
