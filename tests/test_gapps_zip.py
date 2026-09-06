#!/usr/bin/env python3
"""GApps zip checks + Redroid launcher dry-run (no Docker, no proprietary blobs)."""

import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GAPPS_SH = ROOT / "scripts" / "install-gapps-redroid.sh"
REDROID_UP = ROOT / "scripts" / "redroid-up.sh"
COMPOSE = ROOT / "docker" / "redroid-compose.yml"


def run(cmd, **kwargs):
    return subprocess.run(cmd, text=True, capture_output=True, **kwargs)


def write_fake_gapps_zip(path: Path):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("system/priv-app/GmsCore/GmsCore.apk", b"PK-fake-gms")
        zf.writestr("system/priv-app/Phonesky/Phonesky.apk", b"PK-fake-play")
        zf.writestr("system/etc/permissions/privapp-permissions-google.xml", "<permissions/>")


class GappsZipTests(unittest.TestCase):
    def test_missing_zip(self):
        r = run(["bash", str(GAPPS_SH), "--check-zip", "/no/such/gapps.zip"])
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("not found", r.stderr.lower() + r.stdout.lower())

    def test_empty_zip_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            empty = Path(td) / "gapps.zip"
            empty.write_bytes(b"")
            r = run(["bash", str(GAPPS_SH), "--check-zip", str(empty)])
            self.assertEqual(r.returncode, 2, r.stderr + r.stdout)
            self.assertIn("empty", (r.stderr + r.stdout).lower())

    def test_valid_layout_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            zpath = Path(td) / "MindTheGapps-arm64.zip"
            write_fake_gapps_zip(zpath)
            r = run(["bash", str(GAPPS_SH), "--check-zip", str(zpath)])
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertIn("zip ok", r.stdout.lower() + r.stderr.lower())

    def test_non_gapps_zip_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            zpath = Path(td) / "noise.zip"
            with zipfile.ZipFile(zpath, "w") as zf:
                zf.writestr("readme.txt", "hello")
            r = run(["bash", str(GAPPS_SH), "--check-zip", str(zpath)])
            self.assertNotEqual(r.returncode, 0)

    def test_dry_run_install_does_not_need_adb_device(self):
        with tempfile.TemporaryDirectory() as td:
            zpath = Path(td) / "gapps.zip"
            write_fake_gapps_zip(zpath)
            r = run(
                ["bash", str(GAPPS_SH), "--dry-run", "--zip", str(zpath), "--adb", "127.0.0.1:5555"]
            )
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertIn("dry-run", (r.stdout + r.stderr).lower())


class RedroidUpTests(unittest.TestCase):
    def test_compose_has_no_camera_devices(self):
        raw = COMPOSE.read_text()
        yaml_only = "\n".join(
            line for line in raw.splitlines() if not line.strip().startswith("#")
        )
        self.assertNotIn("video42", yaml_only)
        self.assertNotIn("v4l2", yaml_only.lower())
        self.assertNotIn("/dev/video", yaml_only)
        self.assertNotIn("devices:", yaml_only)
        self.assertIn("redroid/redroid", yaml_only)

    def test_dry_run_json(self):
        r = run(
            [
                "bash", str(REDROID_UP),
                "--dry-run", "--json",
                "--name", "phone-ci",
                "--adb-port", "5556",
            ]
        )
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        data = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(data["runtime"], "redroid")
        self.assertEqual(data["name"], "phone-ci")
        self.assertEqual(data["adb_connect"], "127.0.0.1:5556")
        self.assertEqual(data["status"], "started")
        self.assertIn("no camera", r.stderr.lower())

    def test_cli_help_lists_redroid(self):
        r = run(["bash", str(ROOT / "cloud-phone"), "help"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("redroid-up", r.stdout)
        self.assertIn("gapps-install", r.stdout)
        self.assertIn("Cuttlefish", r.stdout)


if __name__ == "__main__":
    unittest.main()
