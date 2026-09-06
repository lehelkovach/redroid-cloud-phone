#!/usr/bin/env python3
"""R2: mock agent deploys a phone, VLM-boxes a screenshot, fills via procedures.

Offline: fake Control API + fake Gemini. Live Gemini is skipped unless
CLOUD_PHONE_LIVE=1 and GEMINI_API_KEY are set (R4 — we do not call it here).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fixtures.fake_control import make_control_app
from fixtures.fake_vlm import FakeVlm
from fixtures.mock_agent import MockAgent
from harness import FlaskThread, OrchestratorProc, find_free_port


class MockAgentVlmFillTests(unittest.TestCase):
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

    def test_deploy_detect_fill_without_submit(self):
        vlm = FakeVlm()
        agent = MockAgent(self.orch, vlm=vlm, owner="mock-agent-vlm")
        session = agent.deploy_phone()
        self.assertEqual(session["runtime"], "redroid")
        self.assertEqual(session["purpose"], "automation")

        out = agent.fill_form(
            {"email": "bs@example.com", "password": "fake-password-not-real"},
            package="com.android.vending",
            include_submit=False,
        )
        self.assertEqual(out["procedure"]["status"], "done", out["procedure"].get("error"))
        self.assertEqual(out["detection"]["source"], "gemini-vision")
        self.assertEqual(out["detection"]["screenshot_source"], "adb-screencap")
        self.assertEqual(out["detection"]["vnc"]["protocol"], "rfb")
        self.assertEqual(len(vlm.calls), 1)

        packages = [c.get("package") for c in self.log if c["endpoint"] == "start_app"]
        self.assertIn("com.android.vending", packages)
        inputs = [c for c in self.log if c["endpoint"] == "device_input"]
        taps = [c for c in inputs if c["data"].get("type") == "tap"]
        texts = [c for c in inputs if c["data"].get("type") == "text"]
        self.assertGreaterEqual(len(taps), 2)
        self.assertEqual((taps[0]["data"]["x"], taps[0]["data"]["y"]), (640, 220))
        self.assertEqual((taps[1]["data"]["x"], taps[1]["data"]["y"]), (640, 320))
        typed = [c["data"].get("text") for c in texts]
        self.assertIn("bs@example.com", typed)
        self.assertIn("fake-password-not-real", typed)
        keys = [c for c in inputs if c["data"].get("type") == "key"]
        self.assertEqual(keys, [], "submit/enter must stay gated")

    def test_submit_without_approval_is_parked(self):
        agent = MockAgent(self.orch, owner="mock-agent-submit-gate")
        agent.deploy_phone()
        detection = agent.detect_form()
        from orchestrator import vlm_boxes
        plan = vlm_boxes.plan_fill_steps(
            detection["fields"],
            {"username": "u", "password": "p"},
            include_submit=True,
        )
        resp = self.orch.post("/procedures", json={
            "sync": True,
            "instance_id": agent.session["instance_id"],
            "steps": plan["steps"],
            "approve": False,
        }).json()
        self.assertEqual(resp["status"], "needs_approval")


@unittest.skipUnless(
    os.environ.get("CLOUD_PHONE_LIVE") == "1" and (
        os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    ),
    "live Gemini + phone: set CLOUD_PHONE_LIVE=1 and GEMINI_API_KEY",
)
class LiveGeminiVlmSkipTests(unittest.TestCase):
    def test_placeholder_does_not_run_offline(self):
        self.fail("live VLM is an R4 rung; this class is skip-gated")


if __name__ == "__main__":
    unittest.main()
