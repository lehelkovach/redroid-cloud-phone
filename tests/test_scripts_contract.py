#!/usr/bin/env python3
"""R0/R1 script contracts: platform flags, GApps zip, verify-phone, CLI."""

import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(cmd, env=None, timeout=20):
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(cmd, text=True, capture_output=True, env=merged, timeout=timeout)


class DeployScriptContractTests(unittest.TestCase):
    def test_deploy_from_golden_rejects_unknown_platform(self):
        r = run(["bash", str(ROOT / "scripts" / "deploy-from-golden.sh"), "--platform", "toaster"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("unsupported", (r.stderr + r.stdout).lower())

    def test_deploy_from_golden_redroid_requires_compartment(self):
        r = run([
            "bash", str(ROOT / "scripts" / "deploy-from-golden.sh"),
            "--platform", "redroid",
            "--image-id", "ocid1.image.fake",
        ])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("COMPARTMENT_ID", r.stderr + r.stdout)

    def test_deploy_from_golden_help_lists_both_platforms(self):
        r = run(["bash", str(ROOT / "scripts" / "deploy-from-golden.sh"), "--help"])
        self.assertEqual(r.returncode, 0)
        self.assertIn("redroid", r.stdout)
        self.assertIn("cuttlefish", r.stdout)

    def test_fleet_rejects_unknown_platform(self):
        r = run([
            "bash", str(ROOT / "scripts" / "deploy-golden-fleet.sh"),
            "--platform", "waydroid",
            "--image-id", "ocid1.image.fake",
        ])
        self.assertNotEqual(r.returncode, 0)

    def test_prepare_golden_help_lists_redroid(self):
        r = run(["bash", str(ROOT / "scripts" / "prepare-golden-image.sh"), "--help"])
        self.assertEqual(r.returncode, 0)
        self.assertIn("redroid", r.stdout.lower())

    def test_install_redroid_dry_run_has_no_camera(self):
        r = run(["bash", str(ROOT / "scripts" / "install-redroid-cloud-phone.sh"), "--dry-run"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("camera_devices: none", r.stdout)

    def test_install_copies_logging_commandlets_and_viewport(self):
        text = (ROOT / "scripts" / "install-redroid-cloud-phone.sh").read_text()
        self.assertIn("cloudphone_logging.py", text)
        self.assertIn("ui_control.py", text)
        self.assertIn("viewport.py", text)

    def test_redroid_down_dry_run(self):
        r = run([
            "bash", str(ROOT / "scripts" / "redroid-up.sh"),
            "--dry-run", "--json", "--down", "--name", "pool-ci",
        ])
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(data["status"], "removed")


class GappsInstallerContractTests(unittest.TestCase):
    def test_validate_only_without_adb_fails_cleanly(self):
        r = run(
            [
                "bash", str(ROOT / "scripts" / "install-gapps-redroid.sh"),
                "--validate-only", "--adb", "127.0.0.1:1",
            ],
            env={"ADB_BIN": "/usr/bin/false"},
        )
        self.assertNotEqual(r.returncode, 0)

    def test_tiny_nonempty_non_apk_zip_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            zpath = Path(td) / "gapps.zip"
            with zipfile.ZipFile(zpath, "w") as zf:
                zf.writestr("system/etc/foo.txt", "x")
            r = run(["bash", str(ROOT / "scripts" / "install-gapps-redroid.sh"), "--check-zip", str(zpath)])
        self.assertNotEqual(r.returncode, 0)


class CloudPhoneCliTests(unittest.TestCase):
    def test_unknown_command(self):
        r = run(["bash", str(ROOT / "cloud-phone"), "not-a-command"])
        self.assertEqual(r.returncode, 1)

    def test_test_command_lists_rungs_via_runner(self):
        runner = ROOT / "scripts" / "run-tests.sh"
        if not runner.exists():
            self.skipTest("run-tests.sh not present")
        r = run(["bash", str(runner), "--list"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("logging", r.stdout)
        self.assertIn("runtime-pool", r.stdout)
        self.assertIn("ladder-e2e", r.stdout)
        self.assertIn("ui-control", r.stdout)
        self.assertIn("vlm-boxes", r.stdout)
        self.assertIn("agent-vlm-fill", r.stdout)


if __name__ == "__main__":
    unittest.main()
