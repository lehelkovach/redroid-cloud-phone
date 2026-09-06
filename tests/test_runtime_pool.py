#!/usr/bin/env python3
"""Orchestrator runtime pool: Redroid default, Cuttlefish on camera purpose."""

import json
import unittest
from unittest.mock import MagicMock, patch

from orchestrator import server as orch
from orchestrator.runtimes import (
    PURPOSE_AUTOMATION,
    PURPOSE_CAMERA,
    RUNTIME_CUTTLEFISH,
    RUNTIME_REDROID,
    resolve_purpose,
    runtime_for_purpose,
)


class PurposeMappingTests(unittest.TestCase):
    def test_default_is_automation_redroid(self):
        self.assertEqual(resolve_purpose(), PURPOSE_AUTOMATION)
        self.assertEqual(runtime_for_purpose(), RUNTIME_REDROID)
        self.assertEqual(resolve_purpose(""), PURPOSE_AUTOMATION)
        self.assertEqual(resolve_purpose("play"), PURPOSE_AUTOMATION)

    def test_camera_aliases(self):
        for alias in ("camera", "ingest", "stream", "rtmp", "webrtc"):
            self.assertEqual(resolve_purpose(alias), PURPOSE_CAMERA)
            self.assertEqual(runtime_for_purpose(alias), RUNTIME_CUTTLEFISH)

    def test_runtime_overrides_purpose(self):
        self.assertEqual(resolve_purpose("automation", runtime="cuttlefish"), PURPOSE_CAMERA)
        self.assertEqual(resolve_purpose("camera", runtime="redroid"), PURPOSE_AUTOMATION)

    def test_unknown_purpose_rejected(self):
        with self.assertRaises(ValueError):
            resolve_purpose("toaster")


class RuntimePoolTests(unittest.TestCase):
    def setUp(self):
        orch._instances.clear()
        orch._leases.clear()
        orch._user_sessions.clear()
        orch._ops.clear()
        orch.ORCH_DEPLOY_MODE = "mock"
        orch.ORCH_API_TOKEN = ""
        orch.ORCH_MAX_INSTANCES = 5
        orch.ORCH_MAX_REDROID_INSTANCES = 3
        orch.ORCH_MAX_CUTTLEFISH_INSTANCES = 2
        orch.ORCH_REDROID_GOLDEN_IMAGE_ID = "ocid1.image.redroid"
        orch.ORCH_CUTTLEFISH_GOLDEN_IMAGE_ID = "ocid1.image.cuttlefish"
        self.client = orch.app.test_client()

    def tearDown(self):
        orch._instances.clear()
        orch._leases.clear()
        orch._user_sessions.clear()

    def test_default_instance_is_redroid_automation(self):
        resp = self.client.post("/instances")
        self.assertEqual(resp.status_code, 201)
        body = resp.get_json()
        self.assertEqual(body["runtime"], RUNTIME_REDROID)
        self.assertEqual(body["purpose"], PURPOSE_AUTOMATION)
        self.assertTrue(body["gapps"])

    def test_camera_instance_is_cuttlefish_and_separate_pool(self):
        auto = self.client.post("/instances").get_json()
        cam = self.client.post("/instances", json={"purpose": "camera"}).get_json()
        self.assertEqual(cam["runtime"], RUNTIME_CUTTLEFISH)
        self.assertEqual(cam["purpose"], PURPOSE_CAMERA)
        self.assertFalse(cam["gapps"])
        self.assertNotEqual(auto["id"], cam["id"])
        pool = self.client.get("/pool").get_json()["pool"]
        self.assertEqual(pool["automation"]["total"], 1)
        self.assertEqual(pool["camera"]["total"], 1)

    def test_second_automation_reuses_idle_redroid(self):
        first = orch._provision_instance()
        reused = orch._get_or_create_instance(purpose="automation")
        self.assertEqual(first["id"], reused["id"])
        self.assertEqual(len(orch._instances), 1)

    def test_camera_does_not_reuse_redroid(self):
        phone = orch._provision_instance(purpose="automation")
        cam = orch._get_or_create_instance(purpose="camera")
        self.assertNotEqual(phone["id"], cam["id"])
        self.assertEqual(cam["runtime"], RUNTIME_CUTTLEFISH)
        self.assertEqual(len(orch._instances), 2)

    def test_leased_redroid_is_not_reused(self):
        first = orch._provision_instance()
        orch._set_lease(first["id"], "alice", 300)
        second = orch._get_or_create_instance(purpose="automation")
        self.assertNotEqual(first["id"], second["id"])

    def test_session_default_is_redroid(self):
        resp = self.client.post("/sessions", json={"owner_user_id": "alice"})
        self.assertIn(resp.status_code, (200, 201))
        sess = resp.get_json()["session"]
        self.assertEqual(sess["runtime"], RUNTIME_REDROID)
        self.assertEqual(sess["purpose"], PURPOSE_AUTOMATION)

    def test_session_renews_same_owner(self):
        first = self.client.post("/sessions", json={"owner_user_id": "alice"}).get_json()
        second = self.client.post("/sessions", json={"owner_user_id": "alice"}).get_json()
        self.assertFalse(second["created"])
        self.assertEqual(first["session"]["instance_id"], second["session"]["instance_id"])

    def test_session_purpose_switch_releases_phone_and_takes_camera(self):
        auto = self.client.post("/sessions", json={"owner_user_id": "alice"}).get_json()["session"]
        cam = self.client.post(
            "/sessions", json={"owner_user_id": "alice", "purpose": "camera"}
        ).get_json()["session"]
        self.assertEqual(cam["runtime"], RUNTIME_CUTTLEFISH)
        self.assertNotEqual(auto["instance_id"], cam["instance_id"])
        self.assertFalse(orch._is_lease_valid(auto["instance_id"]))

    def test_redroid_pool_limit(self):
        orch.ORCH_MAX_REDROID_INSTANCES = 1
        orch._provision_instance(purpose="automation")
        with self.assertRaises(RuntimeError):
            orch._provision_instance(purpose="automation")
        cam = orch._provision_instance(purpose="camera")
        self.assertEqual(cam["runtime"], RUNTIME_CUTTLEFISH)

    def test_health_reports_default_runtime_and_pool(self):
        self.client.post("/instances")
        body = self.client.get("/health").get_json()
        self.assertEqual(body["default_runtime"], RUNTIME_REDROID)
        self.assertEqual(body["default_purpose"], PURPOSE_AUTOMATION)
        self.assertEqual(body["pool"]["automation"]["total"], 1)
        self.assertEqual(body["pool"]["camera"]["total"], 0)

    def test_oci_automation_passes_redroid_platform(self):
        orch.ORCH_DEPLOY_MODE = "oci"
        info = MagicMock()
        info.exists.return_value = True
        info.read_text.return_value = json.dumps({
            "public_ip": "10.0.1.50",
            "instance_ocid": "ocid1.instance.redroid",
        })
        with patch("orchestrator.server.subprocess.check_call") as call, \
             patch("orchestrator.server.Path", return_value=info):
            inst = orch._provision_instance(purpose="automation")
        cmd = call.call_args[0][0]
        self.assertIn("--platform", cmd)
        self.assertEqual(cmd[cmd.index("--platform") + 1], "redroid")
        self.assertIn("ocid1.image.redroid", cmd)
        self.assertEqual(inst["runtime"], RUNTIME_REDROID)
        self.assertEqual(inst["instance_ocid"], "ocid1.instance.redroid")

    def test_oci_camera_passes_cuttlefish_platform(self):
        orch.ORCH_DEPLOY_MODE = "oci"
        info = MagicMock()
        info.exists.return_value = True
        info.read_text.return_value = json.dumps({
            "public_ip": "10.0.1.51",
            "instance_ocid": "ocid1.instance.cvd",
        })
        with patch("orchestrator.server.subprocess.check_call") as call, \
             patch("orchestrator.server.Path", return_value=info):
            inst = orch._provision_instance(purpose="stream")
        cmd = call.call_args[0][0]
        self.assertEqual(cmd[cmd.index("--platform") + 1], "cuttlefish")
        self.assertIn("ocid1.image.cuttlefish", cmd)
        self.assertEqual(inst["runtime"], RUNTIME_CUTTLEFISH)


class ScriptSmokeTests(unittest.TestCase):
    def test_redroid_up_dry_run_json(self):
        from pathlib import Path
        import subprocess
        root = Path(__file__).resolve().parents[1]
        r = subprocess.run(
            ["bash", str(root / "scripts" / "redroid-up.sh"), "--dry-run", "--json", "--name", "pool-ci"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(data["runtime"], "redroid")
        self.assertEqual(data["status"], "started")
        self.assertIn("no camera", r.stderr.lower())

    def test_install_redroid_dry_run(self):
        from pathlib import Path
        import subprocess
        root = Path(__file__).resolve().parents[1]
        r = subprocess.run(
            ["bash", str(root / "scripts" / "install-redroid-cloud-phone.sh"), "--dry-run"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        out = r.stdout.lower()
        self.assertIn("redroid", out)
        self.assertIn("none", out)

    def test_deploy_redroid_dry_run(self):
        from pathlib import Path
        import subprocess
        root = Path(__file__).resolve().parents[1]
        r = subprocess.run(
            ["bash", str(root / "scripts" / "deploy-redroid-oci.sh"), "--dry-run", "--name", "lab"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("runtime: redroid", r.stdout.lower())

    def test_cli_help_lists_both_runtimes(self):
        from pathlib import Path
        import subprocess
        root = Path(__file__).resolve().parents[1]
        r = subprocess.run(["bash", str(root / "cloud-phone"), "help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("redroid-up", r.stdout)
        self.assertIn("gapps-install", r.stdout)
        self.assertIn("deploy-redroid", r.stdout)
        self.assertIn("Cuttlefish", r.stdout)


if __name__ == "__main__":
    unittest.main()
