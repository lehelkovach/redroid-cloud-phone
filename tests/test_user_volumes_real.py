#!/usr/bin/env python3
"""Real volumes on a real kernel: opt-in, root only.

    ORCH_REAL_VOLUME_TEST=1 sudo -E python3 -m unittest tests.test_user_volumes_real -v

Skipped unless ORCH_REAL_VOLUME_TEST=1. Each class then checks what it needs
and skips with the reason when the host lacks it:

  RealLuksHeader   cryptsetup only (no device-mapper): formats a 64 MiB image
                   with owner A's derived key exactly as the manager does, and
                   checks A's key opens its header while B's key, and A's key
                   for the other kind, do not.
  RealExt4Limit    root + loop devices: the image size is a hard limit. ext4
                   on a 64 MiB loop-mounted image answers ENOSPC (no LUKS; it
                   shows the filesystem limit the encrypted volume relies on).
  RealFullVolume   root + loop + device-mapper (dm_crypt): the whole manager
                   lifecycle with the real runner: create, mount, write past
                   the quota -> ENOSPC, close, reopen (data kept), B's key
                   refused, delete.

Every key here is derived from a master generated for the run; nothing is kept.
"""

import errno
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from orchestrator import browser_pool as bp
from orchestrator import user_volumes as uv

OWNER_A = "avery-example"
OWNER_B = "contoso-labs"
MIB = 1024 * 1024
ENABLED = os.environ.get("ORCH_REAL_VOLUME_TEST", "") == "1"


def _have(cmd):
    return shutil.which(cmd) is not None


def _is_root():
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _dm_available():
    """The kernel has device-mapper (the /dev/mapper/control node alone can be stale)."""
    try:
        with open("/proc/misc") as fh:
            return "device-mapper" in fh.read()
    except OSError:
        return False


def _fill(path, limit_bytes):
    """Write 1 MiB chunks until the filesystem refuses. Returns (bytes written, errno)."""
    chunk = b"\0" * MIB
    written = 0
    try:
        with open(path, "wb") as fh:
            while written < limit_bytes * 2:
                fh.write(chunk)
                fh.flush()
                os.fsync(fh.fileno())
                written += len(chunk)
    except OSError as exc:
        return written, exc.errno
    return written, None


def _master_file(tmp):
    path = os.path.join(tmp, "master.key")
    with open(path, "wb") as fh:
        fh.write(os.urandom(32))
    os.chmod(path, 0o600)
    return path


@unittest.skipUnless(ENABLED, "set ORCH_REAL_VOLUME_TEST=1 to run real-volume tests")
class RealLuksHeader(unittest.TestCase):
    def setUp(self):
        if not _have("cryptsetup"):
            self.skipTest("cryptsetup not installed")
        self.tmp = tempfile.mkdtemp(prefix="orch-real-luks-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_owner_key_opens_only_its_own_header(self):
        master = uv.load_master_key(_master_file(self.tmp))
        ta, tb = bp.owner_token(OWNER_A), bp.owner_token(OWNER_B)
        img = os.path.join(self.tmp, "phone-data.img")
        uv.VolumeManager._create_image(img, 64 * MIB)
        ka = uv.derive_key(master, ta, uv.KIND_PHONE)
        res = uv.subprocess_runner(uv.luks_format_argv(img), input=ka)
        self.assertEqual(res.returncode, 0, res.stderr)
        dump = uv.subprocess_runner(["cryptsetup", "luksDump", img])
        self.assertRegex(dump.stdout, r"Version:\s+2")
        self.assertEqual(uv.subprocess_runner(uv.luks_test_key_argv(img), input=ka).returncode, 0)
        kb = uv.derive_key(master, tb, uv.KIND_PHONE)
        self.assertNotEqual(uv.subprocess_runner(uv.luks_test_key_argv(img), input=kb).returncode, 0,
                            "owner B's key opened owner A's volume")
        ka_other = uv.derive_key(master, ta, uv.KIND_BROWSER)
        self.assertNotEqual(uv.subprocess_runner(uv.luks_test_key_argv(img), input=ka_other).returncode, 0,
                            "A's browser-profile key opened A's phone-data volume")
        st = os.stat(img)
        self.assertLess(st.st_blocks * 512, st.st_size, "the image stays sparse after luksFormat")


@unittest.skipUnless(ENABLED, "set ORCH_REAL_VOLUME_TEST=1 to run real-volume tests")
class RealExt4Limit(unittest.TestCase):
    def setUp(self):
        if not _is_root():
            self.skipTest("needs root")
        if not (os.path.exists("/dev/loop-control") and _have("losetup") and _have("mkfs.ext4")):
            self.skipTest("needs loop devices, losetup and mkfs.ext4")
        self.tmp = tempfile.mkdtemp(prefix="orch-real-ext4-")
        self.loop = None
        self.mnt = os.path.join(self.tmp, "mnt")

    def tearDown(self):
        if os.path.ismount(self.mnt):
            subprocess.run(["umount", self.mnt], check=False)
        if self.loop:
            subprocess.run(["losetup", "-d", self.loop], check=False)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_image_size_is_a_hard_limit(self):
        img = os.path.join(self.tmp, "browser-profile.img")
        uv.VolumeManager._create_image(img, 64 * MIB)
        subprocess.run(["mkfs.ext4", "-q", "-F", "-m", "0", img], check=True)
        self.loop = subprocess.run(["losetup", "--find", "--show", img], check=True, capture_output=True,
                                   text=True).stdout.strip()
        os.mkdir(self.mnt, 0o700)
        subprocess.run(["mount", "-t", "ext4", "-o", uv.MOUNT_OPTIONS[uv.KIND_BROWSER], self.loop, self.mnt],
                       check=True)
        written, err = _fill(os.path.join(self.mnt, "fill"), 64 * MIB)
        self.assertEqual(err, errno.ENOSPC)
        self.assertLessEqual(written, 64 * MIB)
        self.assertLessEqual(os.stat(img).st_size, 64 * MIB, "the host file never grew past the quota")


@unittest.skipUnless(ENABLED, "set ORCH_REAL_VOLUME_TEST=1 to run real-volume tests")
class RealFullVolume(unittest.TestCase):
    def setUp(self):
        if not _is_root():
            self.skipTest("needs root")
        missing = [c for c in ("cryptsetup", "losetup", "mkfs.ext4", "mount", "umount") if not _have(c)]
        if missing or not os.path.exists("/dev/loop-control"):
            self.skipTest(f"needs loop devices and {', '.join(missing) or 'the tools'}")
        if not _dm_available():
            self.skipTest("no device-mapper in /proc/misc: dm_mod/dm_crypt not available in this kernel")
        self.tmp = tempfile.mkdtemp(prefix="orch-real-vol-")
        quota = os.path.join(self.tmp, "quotas.json")
        with open(quota, "w") as fh:
            json.dump({OWNER_A: {uv.KIND_PHONE: 64 / 1024}}, fh)
        self.vm = uv.VolumeManager(os.path.join(self.tmp, "volumes"), _master_file(self.tmp), quota_file=quota,
                                   host_min_free_gb=0, lease_check=lambda tok, kind: False)

    def tearDown(self):
        try:
            self.vm.close_volume(OWNER_A, uv.KIND_PHONE)
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_lifecycle_enospc_and_key_separation(self):
        info = self.vm.open_volume(OWNER_A, uv.KIND_PHONE)
        self.assertTrue(info["created"])
        self.assertEqual(info["size_bytes"], 64 * MIB)
        self.assertTrue(os.path.ismount(info["mount"]))
        written, err = _fill(os.path.join(info["mount"], "fill"), 64 * MIB)
        self.assertEqual(err, errno.ENOSPC, "writing past the quota must fail with ENOSPC")
        self.assertLess(written, 64 * MIB)
        with open(os.path.join(info["mount"], "marker"), "w") as fh:
            fh.write("kept")
        self.assertGreater(self.vm.usage(OWNER_A, uv.KIND_PHONE)["used_pct"], 80)
        self.assertTrue(self.vm.close_volume(OWNER_A, uv.KIND_PHONE))
        self.assertFalse(os.path.ismount(info["mount"]))
        again = self.vm.open_volume(OWNER_A, uv.KIND_PHONE)
        self.assertFalse(again["created"])
        with open(os.path.join(again["mount"], "marker")) as fh:
            self.assertEqual(fh.read(), "kept")
        self.vm.close_volume(OWNER_A, uv.KIND_PHONE)
        kb = uv.derive_key(self.vm._master(), bp.owner_token(OWNER_B), uv.KIND_PHONE)
        res = uv.subprocess_runner(uv.luks_test_key_argv(info["image"]), input=kb)
        self.assertNotEqual(res.returncode, 0, "owner B's key opened owner A's volume")
        self.assertTrue(self.vm.delete_volume(OWNER_A, uv.KIND_PHONE))
        self.assertFalse(os.path.exists(info["image"]))


if __name__ == "__main__":
    unittest.main()
