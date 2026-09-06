#!/usr/bin/env python3
"""R3 TDD ladder: dual-pool process e2e (Redroid+GApps vs Cuttlefish ingest).

Offline: two fake Control APIs + a real orchestrator process. No Docker, OCI, or
proprietary GApps zip. Live bake is R4 (`CLOUD_PHONE_LIVE=1`).
"""

import os
import subprocess
import time
import unittest

from fixtures.fake_control import make_control_app
from harness import ROOT, FlaskThread, OrchestratorProc, find_free_port


class DualPoolLadderE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        redroid_app, redroid_log = make_control_app("redroid")
        camera_app, camera_log = make_control_app("cuttlefish")
        cls.redroid_log = redroid_log
        cls.camera_log = camera_log
        cls.redroid = FlaskThread(redroid_app, find_free_port()).start()
        cls.camera = FlaskThread(camera_app, find_free_port()).start()
        cls.orch = OrchestratorProc(cls.redroid.url, camera_url=cls.camera.url).start()

    @classmethod
    def tearDownClass(cls):
        cls.orch.stop()
        cls.redroid.stop()
        cls.camera.stop()

    def test_r3_01_health_advertises_split_pools(self):
        body = self.orch.get("/health").json()
        self.assertEqual(body["default_runtime"], "redroid")
        self.assertEqual(body["default_purpose"], "automation")
        self.assertIn("automation", body["pool"])
        self.assertIn("camera", body["pool"])

    def test_r3_02_default_session_is_gapps_redroid(self):
        resp = self.orch.post("/sessions", json={"owner_user_id": "alice"})
        self.assertIn(resp.status_code, (200, 201))
        sess = resp.json()["session"]
        self.assertEqual(sess["runtime"], "redroid")
        self.assertEqual(sess["purpose"], "automation")
        health = self.orch.get(f"/phones/{sess['instance_id']}/health").json()
        self.assertTrue(health["gapps"]["ready"], health)
        self.assertEqual(health["runtime"], "redroid")

    def test_r3_03_camera_session_is_cuttlefish_without_gapps(self):
        resp = self.orch.post(
            "/sessions", json={"owner_user_id": "cam-user", "purpose": "camera"}
        )
        self.assertIn(resp.status_code, (200, 201))
        sess = resp.json()["session"]
        self.assertEqual(sess["runtime"], "cuttlefish")
        self.assertEqual(sess["purpose"], "camera")
        health = self.orch.get(f"/phones/{sess['instance_id']}/health").json()
        self.assertFalse(health["gapps"]["ready"], health)
        self.assertTrue(health.get("ingest", {}).get("nginx_rtmp"))

        pool = self.orch.get("/pool").json()["pool"]
        self.assertGreaterEqual(pool["automation"]["total"], 1)
        self.assertGreaterEqual(pool["camera"]["total"], 1)

    def _alice_session(self):
        got = self.orch.get("/sessions/alice")
        if got.status_code == 200:
            return got.json()
        created = self.orch.post("/sessions", json={"owner_user_id": "alice"})
        self.assertIn(created.status_code, (200, 201), created.text)
        return created.json()["session"]

    def test_r3_04_play_launch_hits_redroid_not_cuttlefish(self):
        alice = self._alice_session()
        queued = self.orch.post("/operations", json={
            "operation": "login",
            "instance_id": alice["instance_id"],
            "purpose": "automation",
            "app_package": "com.android.vending",
            "login": {"username": "dogfood", "password": "fake"},
        })
        self.assertEqual(queued.status_code, 202)
        op_id = queued.json()["operation_id"]
        for _ in range(40):
            poll = self.orch.get(f"/operations/{op_id}").json()
            if poll.get("status") in {"done", "failed"}:
                self.assertEqual(poll["status"], "done", poll)
                break
            time.sleep(0.15)
        else:
            self.fail("login operation did not finish")

        redroid_starts = [
            c["package"] for c in self.redroid_log if c["endpoint"] == "start_app"
        ]
        camera_starts = [
            c["package"] for c in self.camera_log if c["endpoint"] == "start_app"
        ]
        self.assertIn("com.android.vending", redroid_starts)
        self.assertNotIn("com.android.vending", camera_starts)

    def test_r3_05_leased_redroid_not_shared_without_provision(self):
        self._alice_session()
        blocked = self.orch.post(
            "/sessions",
            json={"owner_user_id": "carol", "provision": False},
        )
        self.assertEqual(blocked.status_code, 409)

    def test_r3_06_release_returns_phone_to_idle_pool(self):
        self._alice_session()
        released = self.orch.delete("/sessions/alice")
        self.assertEqual(released.status_code, 200)
        reused = self.orch.post("/sessions", json={"owner_user_id": "dave"})
        self.assertIn(reused.status_code, (200, 201))
        self.assertEqual(reused.json()["session"]["runtime"], "redroid")

    def test_r3_07_verify_phone_script_requires_gapps_on_redroid_only(self):
        script = ROOT / "scripts" / "verify-redroid-phone.sh"
        ok = subprocess.run(
            ["bash", str(script), "--vm", "127.0.0.1", "--require-gapps"],
            capture_output=True,
            text=True,
            env={**os.environ, "API_PORT": str(self.redroid.port)},
        )
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)

        bad = subprocess.run(
            ["bash", str(script), "--vm", "127.0.0.1", "--require-gapps"],
            capture_output=True,
            text=True,
            env={**os.environ, "API_PORT": str(self.camera.port)},
        )
        self.assertNotEqual(bad.returncode, 0, "cuttlefish health must fail --require-gapps")

    def test_r3_08_verbose_logs_cover_appium_commandlets_and_vnc(self):
        alice = self._alice_session()
        instance_id = alice["instance_id"]
        ui = self.orch.post(
            f"/phones/{instance_id}/ui",
            json={"action": "tap", "xp": 50, "yp": 50},
        )
        self.assertEqual(ui.status_code, 200, ui.text)
        appium = self.orch.get(f"/phones/{instance_id}/appium").json()
        self.assertTrue(appium.get("ready"), appium)
        vnc = self.orch.get(f"/phones/{instance_id}/vnc").json()
        self.assertEqual(vnc["port"], 5900)
        logs = self.orch.get(f"/phones/{instance_id}/logs?type=CMD,APM,VNC").json()
        types = {item["type"] for item in logs["logs"]}
        self.assertTrue({"CMD", "APM", "VNC"} <= types, logs)
        orch_logs = self.orch.get("/logs?type=CMD,APM,VNC").json()
        self.assertGreater(orch_logs["count"], 0)


if __name__ == "__main__":
    unittest.main()
