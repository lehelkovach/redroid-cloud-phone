#!/usr/bin/env python3
"""GApps image build script, host check, and the off-by-default GApps phone.

Offline: the build script runs only with --dry-run (no Docker) and every zip
is a fake with the right file names and no Google code in it.
"""

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "scripts" / "build-gapps-image.sh"
HOST_CHECK = ROOT / "scripts" / "check-redroid-host.sh"
COMPOSE = ROOT / "docker" / "redroid-gapps-compose.yml"
UNIT = ROOT / "systemd" / "redroid-gapps-container.service"
SECRET = "sig-SECRET-do-not-log-0123"


def run(cmd, env=None, **kwargs):
    full_env = dict(os.environ)
    # The script reads these as defaults; keep the caller's shell out of it.
    for key in ("GAPPS_ZIP", "GAPPS_ZIP_URL", "GAPPS_ZIP_SHA256", "REDROID_GAPPS_ANDROID",
                "REDROID_GAPPS_ARCH", "REDROID_GAPPS_BASE", "REDROID_GAPPS_TAG"):
        full_env.pop(key, None)
    full_env.update(env or {})
    kwargs.setdefault("timeout", 30)
    return subprocess.run(["bash", *map(str, cmd)], text=True, capture_output=True, env=full_env, **kwargs)


def fake_mtg(path, entries=None):
    entries = entries if entries is not None else {
        "META-INF/com/google/android/update-binary": "#!/sbin/sh\n",
        "system/product/priv-app/PrebuiltGmsCore/PrebuiltGmsCore.apk": "PK fake",
        "system/product/priv-app/Phonesky/Phonesky.apk": "PK fake",
        "system/system_ext/priv-app/GoogleServicesFramework/GoogleServicesFramework.apk": "PK fake",
        "system/product/etc/permissions/privapp-permissions-google-product.xml": "<permissions/>",
    }
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class BuildArgsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.zip = fake_mtg(self.dir / "MindTheGapps-13.0.0-arm64-20240226.zip")

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, *args):
        return run([BUILD, *args])

    def test_help(self):
        r = self.build("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("--zip-url", r.stdout)

    def test_unknown_option_is_usage_error(self):
        self.assertEqual(self.build("--frobnicate").returncode, 64)

    def test_needs_a_source(self):
        r = self.build("--dry-run")
        self.assertEqual(r.returncode, 64)
        self.assertIn("ships none", r.stderr)

    def test_zip_and_url_together_refused(self):
        r = self.build("--zip", self.zip, "--zip-url", "https://x/y.zip", "--sha256", "a" * 64, "--dry-run")
        self.assertEqual(r.returncode, 64)

    def test_url_needs_sha256(self):
        r = self.build("--zip-url", f"https://dl.example.com/g.zip?token={SECRET}", "--dry-run")
        self.assertEqual(r.returncode, 64)
        self.assertIn("--sha256", r.stderr)
        self.assertNotIn(SECRET, r.stdout + r.stderr)

    def test_url_must_be_https(self):
        r = self.build("--zip-url", "http://dl.example.com/g.zip", "--sha256", "a" * 64, "--dry-run")
        self.assertEqual(r.returncode, 64)

    def test_url_dry_run_never_logs_the_query(self):
        r = self.build("--zip-url", f"https://dl.example.com/MindTheGapps-13.0.0-arm64.zip?token={SECRET}",
                       "--sha256", "a" * 64, "--arch", "arm64", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(SECRET, r.stdout + r.stderr)
        self.assertIn("COPY --chown=0:0 gapps/system/ /system/", r.stdout)

    def test_bad_sha_format(self):
        self.assertEqual(self.build("--zip", self.zip, "--sha256", "xyz", "--dry-run").returncode, 64)

    def test_bad_arch_and_version_and_tag(self):
        self.assertEqual(self.build("--zip", self.zip, "--arch", "mips", "--dry-run").returncode, 64)
        self.assertEqual(self.build("--zip", self.zip, "--android", "thirteen", "--dry-run").returncode, 64)
        self.assertEqual(self.build("--zip", self.zip, "--tag", "Bad Tag", "--dry-run").returncode, 64)


class BuildZipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def dry(self, zip_path, *extra):
        return run([BUILD, "--zip", zip_path, "--arch", "arm64", "--dry-run", *extra])

    def test_plan_for_a_good_zip(self):
        z = fake_mtg(self.dir / "MindTheGapps-13.0.0-arm64-20240226.zip")
        r = self.dry(z)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("FROM redroid/redroid:13.0.0-latest", r.stdout)
        self.assertIn("COPY --chown=0:0 gapps/system/ /system/", r.stdout)
        self.assertIn('"ro.setupwizard.mode=DISABLED"]', r.stdout)
        self.assertIn('"/init", "qemu=1", "androidboot.hardware=redroid"', r.stdout)
        self.assertNotIn("RUN ", r.stdout, "no RUN step: the arm64 image must build on x86 without qemu")
        self.assertIn(sha(z), r.stdout, "the zip's checksum is recorded as a label")

    def test_keep_setup_wizard_drops_the_entrypoint(self):
        z = fake_mtg(self.dir / "MindTheGapps-13.0.0-arm64.zip")
        r = self.dry(z, "--keep-setup-wizard")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("ENTRYPOINT", r.stdout)

    def test_json_and_emit_dockerfile(self):
        z = fake_mtg(self.dir / "MindTheGapps-13.0.0-arm64.zip")
        out = self.dir / "Dockerfile"
        r = self.dry(z, "--json", "--emit-dockerfile", out, "--tag", "registry.example/redroid-gapps:13")
        self.assertEqual(r.returncode, 0, r.stderr)
        body = json.loads(r.stdout)
        self.assertEqual(body["status"], "planned")
        self.assertEqual(body["platform"], "linux/arm64")
        self.assertEqual(body["zip_arch"], "arm64")
        self.assertEqual(body["tag"], "registry.example/redroid-gapps:13")
        self.assertIn("COPY", out.read_text())

    def test_matching_sha_passes_and_mismatch_stops(self):
        z = fake_mtg(self.dir / "MindTheGapps-13.0.0-arm64.zip")
        self.assertEqual(self.dry(z, "--sha256", sha(z).upper()).returncode, 0)
        r = self.dry(z, "--sha256", "0" * 64)
        self.assertEqual(r.returncode, 3)
        self.assertIn("mismatch", r.stderr)

    def test_missing_and_empty_zip(self):
        self.assertEqual(self.dry(self.dir / "nope.zip").returncode, 1)
        empty = self.dir / "MindTheGapps-13.0.0-arm64.zip"
        empty.write_bytes(b"")
        r = self.dry(empty)
        self.assertEqual(r.returncode, 2)
        self.assertIn("empty", r.stderr)

    def test_opengapps_zip_is_refused_with_a_reason(self):
        z = fake_mtg(self.dir / "open_gapps-arm64-11.0-pico.zip",
                     {"Core/gmscore-arm64.tar.lz": "x", "Core/vending-all.tar.lz": "x"})
        r = self.dry(z, "--android", "11.0.0")
        self.assertEqual(r.returncode, 2)
        self.assertIn("OpenGApps", r.stderr)

    def test_zip_without_play_store_is_refused(self):
        z = fake_mtg(self.dir / "MindTheGapps-13.0.0-arm64.zip",
                     {"system/product/priv-app/PrebuiltGmsCore/PrebuiltGmsCore.apk": "x"})
        r = self.dry(z)
        self.assertEqual(r.returncode, 2)
        self.assertIn("Phonesky", r.stderr)

    def test_not_a_zip(self):
        bad = self.dir / "MindTheGapps-13.0.0-arm64.zip"
        bad.write_text("this is not a zip")
        self.assertEqual(self.dry(bad).returncode, 2)

    def test_arch_mismatch(self):
        z = fake_mtg(self.dir / "MindTheGapps-13.0.0-x86_64-20240226.zip")
        r = self.dry(z)
        self.assertEqual(r.returncode, 2)
        self.assertIn("arch mismatch", r.stderr)

    def test_unknown_arch_warns_or_refuses(self):
        z = fake_mtg(self.dir / "gapps-13.0.0-custom.zip")
        self.assertEqual(self.dry(z).returncode, 0)
        self.assertEqual(self.dry(z, "--require-arch-match").returncode, 2)

    def test_version_mismatch(self):
        z = fake_mtg(self.dir / "MindTheGapps-14.0.0-arm64-20240226.zip")
        r = self.dry(z)
        self.assertEqual(r.returncode, 2)
        self.assertIn("version mismatch", r.stderr)
        self.assertEqual(self.dry(z, "--allow-version-mismatch").returncode, 0)
        self.assertEqual(self.dry(z, "--android", "14.0.0").returncode, 0)

    def test_64only_tag_and_12_1_zip(self):
        z = fake_mtg(self.dir / "MindTheGapps-12.1.0-arm64-20240619.zip")
        r = self.dry(z, "--android", "12.0.0_64only")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("FROM redroid/redroid:12.0.0_64only-latest", r.stdout)


class HostCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.proc = Path(self.tmp.name) / "proc"
        self.dev = Path(self.tmp.name) / "dev"
        self.proc.mkdir()
        self.dev.mkdir()
        (self.proc / "meminfo").write_text("MemTotal:        8000000 kB\n")

    def tearDown(self):
        self.tmp.cleanup()

    def check(self):
        r = run([HOST_CHECK, "--json"], env={"CHECK_PROC_ROOT": str(self.proc), "CHECK_DEV_ROOT": str(self.dev)})
        return r, json.loads(r.stdout)

    def test_binderfs_detected(self):
        (self.proc / "filesystems").write_text("nodev\tsysfs\nnodev\tbinder\n")
        r, body = self.check()
        self.assertEqual(body["binder"], "binderfs")
        self.assertEqual(body["mem_gb"], 7)
        self.assertEqual(r.returncode, 0 if body["redroid_ok"] else 1)

    def test_no_binder_means_no_redroid(self):
        (self.proc / "filesystems").write_text("nodev\tsysfs\n")
        r, body = self.check()
        if body["binder"] == "missing":
            self.assertFalse(body["redroid_ok"])
            self.assertEqual(r.returncode, 1)

    def test_kvm_is_what_cuttlefish_needs(self):
        (self.proc / "filesystems").write_text("")
        _, body = self.check()
        self.assertFalse(body["cuttlefish_ok"])
        (self.dev / "kvm").write_text("")
        _, body = self.check()
        self.assertTrue(body["cuttlefish_ok"])

    def test_never_loads_modules(self):
        text = HOST_CHECK.read_text()
        code = "\n".join(l for l in text.splitlines() if not l.strip().startswith("#") and "problems+=" not in l)
        self.assertNotIn("modprobe", code)
        self.assertNotIn("insmod", code)


class GappsPhoneUnitTests(unittest.TestCase):
    def test_compose_is_off_by_default_and_has_no_cameras(self):
        raw = COMPOSE.read_text()
        yaml_only = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("#"))
        self.assertIn('profiles: ["gapps"]', yaml_only)
        self.assertIn("REDROID_GAPPS_IMAGE:?", yaml_only)
        self.assertNotIn("/dev/video", yaml_only)
        self.assertNotIn("devices:", yaml_only)

    def test_compose_binds_adb_to_loopback_by_default(self):
        # The phone holds the owner's signed-in Google account; ADB on a public
        # port would hand that account to anyone who connects.
        self.assertIn('"${ADB_BIND:-127.0.0.1}:${ADB_PORT:-5555}:5555"', COMPOSE.read_text())

    def test_unit_keeps_the_signed_in_data_volume(self):
        text = UNIT.read_text()
        stop = [l for l in text.splitlines() if l.startswith("ExecStop=")]
        self.assertEqual(len(stop), 1)
        self.assertNotIn(" -v", stop[0])
        self.assertIn("Conflicts=redroid-container.service", text)
        self.assertIn("binder", text)

    def test_installer_ships_but_does_not_enable_the_unit(self):
        text = (ROOT / "scripts" / "install-redroid-cloud-phone.sh").read_text()
        self.assertIn("redroid-gapps-container.service", text)
        self.assertNotIn("enable redroid-gapps-container", text)
        self.assertNotIn("enable --now redroid-gapps-container", text)
        self.assertIn("api/app_install.py", text, "server.py imports app_install; the installer must ship it")


if __name__ == "__main__":
    unittest.main()
