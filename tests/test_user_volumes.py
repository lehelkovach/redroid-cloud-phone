#!/usr/bin/env python3
"""Per-owner, size-limited, encrypted volumes (orchestrator/user_volumes.py).

Each owner's phone /data and browser profile live in their own LUKS2 + ext4
image under ORCH_VOLUME_ROOT/<owner token>/, sized to the owner's quota and
unlocked with a key derived for that owner and that kind only. These tests pin
the rules with a fake host (tests/fixtures/fake_volumes.py) standing in for
losetup, cryptsetup, mkfs.ext4 and mount: no real device is touched.

Fake owners and fake key material only.
"""

import base64
import json
import logging
import os
import stat
import tempfile
import time
import unittest

from orchestrator import browser_pool as bp
from orchestrator import server as orch
from orchestrator import user_volumes as uv
from tests.fixtures.fake_volumes import GIB, FakeVolumeHost

OWNER_A = "avery-example"
OWNER_B = "contoso-labs"
FAKE_MASTER = bytes(range(32))  # a fixed, obviously fake master for stable-derivation checks


def write_key(path, data=None, mode=0o600):
    with open(path, "wb") as fh:
        fh.write(data if data is not None else os.urandom(32))
    os.chmod(path, mode)
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="orch-vol-")
        self.root = os.path.join(self.tmp, "volumes")
        self.keyfile = write_key(os.path.join(self.tmp, "master.key"), FAKE_MASTER)
        self.host = FakeVolumeHost()
        self.log = logging.getLogger(f"test.volumes.{id(self)}")
        self.registry = {}

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def manager(self, **kw):
        opts = dict(phone_quota_gb=8, browser_quota_gb=1, host_min_free_gb=10, logger=self.log)
        opts.update(self.host.hooks())
        keyfile = kw.pop("master_key_file", self.keyfile)
        opts.update(kw)
        return uv.VolumeManager(self.root, keyfile, **opts)


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------

class KeyDerivationTests(unittest.TestCase):
    def test_hkdf_matches_rfc5869_test_case_1(self):
        okm = uv.hkdf_sha256(bytes([0x0b] * 22), info=bytes(range(0xf0, 0xfa)), length=42,
                             salt=bytes(range(0x0d)))
        self.assertEqual(okm.hex(), "3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf"
                                    "34007208d5b887185865")

    def test_key_is_hkdf_of_the_master_with_the_documented_info(self):
        tok = bp.owner_token(OWNER_A)
        expected = uv.hkdf_sha256(FAKE_MASTER, info=f"orch-volume:{tok}:phone-data".encode(), length=64)
        self.assertEqual(uv.derive_key(FAKE_MASTER, tok, uv.KIND_PHONE), expected)

    def test_key_is_stable_and_differs_per_owner_and_per_kind(self):
        ta, tb = bp.owner_token(OWNER_A), bp.owner_token(OWNER_B)
        keys = {
            (t, k): uv.derive_key(FAKE_MASTER, t, k)
            for t in (ta, tb) for k in uv.KINDS
        }
        self.assertEqual(len(set(keys.values())), 4, "every (owner, kind) needs its own key")
        self.assertEqual(uv.derive_key(FAKE_MASTER, ta, uv.KIND_PHONE), keys[(ta, uv.KIND_PHONE)])
        self.assertEqual(len(keys[(ta, uv.KIND_PHONE)]), 64)
        other_master = uv.derive_key(bytes(32), ta, uv.KIND_PHONE)
        self.assertNotEqual(other_master, keys[(ta, uv.KIND_PHONE)])

    def test_unknown_kind_refused(self):
        with self.assertRaises(uv.VolumeError):
            uv.derive_key(FAKE_MASTER, bp.owner_token(OWNER_A), "swap")


class MasterKeyFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="orch-key-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def code(self, path):
        with self.assertRaises(uv.VolumeError) as ctx:
            uv.load_master_key(path)
        return ctx.exception

    def test_missing_or_unset(self):
        self.assertEqual(self.code("").code, "volume_key_missing")
        self.assertEqual(self.code(os.path.join(self.tmp, "nope")).code, "volume_key_missing")

    def test_readable_by_others_refused(self):
        path = write_key(os.path.join(self.tmp, "k"), mode=0o640)
        err = self.code(path)
        self.assertEqual(err.code, "volume_key_insecure")
        self.assertEqual(err.status, 503)

    def test_symlink_refused(self):
        real = write_key(os.path.join(self.tmp, "real"))
        link = os.path.join(self.tmp, "link")
        os.symlink(real, link)
        self.assertEqual(self.code(link).code, "volume_key_insecure")

    def test_short_key_refused(self):
        path = write_key(os.path.join(self.tmp, "short"), b"x" * 16)
        self.assertEqual(self.code(path).code, "volume_key_insecure")

    def test_good_key_loads_and_errors_never_carry_it(self):
        secret = os.urandom(48)
        path = write_key(os.path.join(self.tmp, "good"), secret)
        self.assertEqual(uv.load_master_key(path), secret)
        os.chmod(path, 0o644)
        err = self.code(path)
        for form in (secret.hex(), base64.b64encode(secret).decode()):
            self.assertNotIn(form, str(err))


# ---------------------------------------------------------------------------
# Paths: owners map strictly under the root
# ---------------------------------------------------------------------------

class PathTests(Base):
    def test_layout_and_mapper_name(self):
        m = self.manager()
        p = m.paths(OWNER_A, uv.KIND_PHONE)
        tok = bp.owner_token(OWNER_A)
        self.assertEqual(p["dir"], os.path.join(self.root, tok))
        self.assertEqual(p["image"], os.path.join(self.root, tok, "phone-data.img"))
        self.assertEqual(p["mount"], os.path.join(self.root, tok, "phone-data.mnt"))
        self.assertTrue(p["mapper"].startswith("orchv-"))
        self.assertNotIn("avery", p["mapper"], "mapper names carry a digest, not the owner's name")
        self.assertNotEqual(p["mapper"], m.paths(OWNER_B, uv.KIND_PHONE)["mapper"])
        self.assertNotEqual(p["mapper"], m.paths(OWNER_A, uv.KIND_BROWSER)["mapper"])

    def test_traversal_owners_stay_under_the_root(self):
        m = self.manager()
        for owner in ("..", "../../etc", "a/../../b", "/abs/path", ".", "x/y"):
            p = m.paths(owner, uv.KIND_PHONE)
            self.assertEqual(os.path.dirname(p["dir"]), self.root, owner)
            self.assertNotIn("..", os.path.relpath(p["image"], self.root))
        with self.assertRaises(uv.VolumeError):
            m.paths("", uv.KIND_PHONE)
        with self.assertRaises(uv.VolumeError):
            m.paths(OWNER_A, "../phone-data")

    def test_bad_tokens_refused(self):
        m = self.manager()
        for tok in ("..", "a/b", "", ".hidden", "x" * 200, "a b"):
            with self.assertRaises(uv.VolumeError, msg=tok):
                m.paths_for_token(tok, uv.KIND_PHONE)

    def test_symlinked_owner_dir_refused(self):
        m = self.manager()
        os.makedirs(self.root, mode=0o700)
        elsewhere = os.path.join(self.tmp, "elsewhere")
        os.mkdir(elsewhere)
        os.symlink(elsewhere, os.path.join(self.root, bp.owner_token(OWNER_B)))
        with self.assertRaises(uv.VolumeError) as ctx:
            m.open_volume(OWNER_B, uv.KIND_PHONE)
        self.assertEqual(ctx.exception.code, "volume_path_unsafe")
        self.assertEqual(self.host.calls, [])

    def test_symlinked_image_or_mount_refused(self):
        m = self.manager()
        p = m.paths(OWNER_A, uv.KIND_PHONE)
        os.makedirs(p["dir"], mode=0o700)
        target = os.path.join(self.tmp, "victim.img")
        open(target, "wb").close()
        os.symlink(target, p["image"])
        with self.assertRaises(uv.VolumeError) as ctx:
            m.open_volume(OWNER_A, uv.KIND_PHONE)
        self.assertEqual(ctx.exception.code, "volume_path_unsafe")
        os.unlink(p["image"])
        os.symlink(self.tmp, p["mount"])
        with self.assertRaises(uv.VolumeError):
            m.open_volume(OWNER_A, uv.KIND_PHONE)
        self.assertNotIn(("cryptsetup", "open"), [a[:2] for a in self.host.commands()])

    def test_hardlinked_image_refused(self):
        m = self.manager()
        m.open_volume(OWNER_A, uv.KIND_PHONE)
        m.close_volume(OWNER_A, uv.KIND_PHONE)
        p = m.paths(OWNER_A, uv.KIND_PHONE)
        os.link(p["image"], os.path.join(self.tmp, "second-name.img"))
        with self.assertRaises(uv.VolumeError) as ctx:
            m.open_volume(OWNER_A, uv.KIND_PHONE)
        self.assertEqual(ctx.exception.code, "volume_path_unsafe")

    def test_symlinked_root_refused(self):
        real = os.path.join(self.tmp, "realroot")
        os.mkdir(real)
        os.symlink(real, self.root)
        with self.assertRaises(uv.VolumeError) as ctx:
            self.manager().open_volume(OWNER_A, uv.KIND_PHONE)
        self.assertEqual(ctx.exception.code, "volume_path_unsafe")


# ---------------------------------------------------------------------------
# Lifecycle: exact command sequences against the fake host
# ---------------------------------------------------------------------------

class LifecycleTests(Base):
    def test_first_open_creates_a_sparse_image_formats_opens_and_mounts(self):
        m = self.manager()
        info = m.open_volume(OWNER_A, uv.KIND_PHONE)
        self.assertTrue(info["created"])
        self.assertEqual(self.host.verbs(), [
            "cryptsetup luksFormat",
            "losetup -j",
            "losetup --find",
            "cryptsetup open",
            "mkfs.ext4 -q",
            "mount -t",
        ])
        p = m.paths(OWNER_A, uv.KIND_PHONE)
        st = os.stat(p["image"])
        self.assertEqual(st.st_size, 8 * GIB)
        self.assertLess(st.st_blocks * 512, st.st_size, "the image must be sparse")
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(p["mount"]).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(p["dir"]).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(self.root).st_mode), 0o700)
        self.assertEqual(self.host.mounted_image(p["mount"]), p["image"])
        fmt = self.host.commands()[0]
        self.assertIn("luks2", fmt)
        self.assertEqual(fmt[-1], p["image"])
        mount = self.host.commands()[-1]
        self.assertEqual(mount[-2:], (f"/dev/mapper/{p['mapper']}", p["mount"]))
        self.assertIn("nodev,nosuid", " ".join(mount))

    def test_browser_profile_is_mounted_noexec(self):
        m = self.manager()
        m.open_volume(OWNER_A, uv.KIND_BROWSER)
        mount = self.host.commands()[-1]
        self.assertIn("nodev,nosuid,noexec", " ".join(mount))
        self.assertEqual(os.path.getsize(m.paths(OWNER_A, uv.KIND_BROWSER)["image"]), 1 * GIB)

    def test_open_is_idempotent_while_mounted_but_rechecks_the_key(self):
        m = self.manager()
        m.open_volume(OWNER_A, uv.KIND_PHONE)
        self.host.calls.clear()
        again = m.open_volume(OWNER_A, uv.KIND_PHONE)
        self.assertFalse(again["created"])
        self.assertEqual(self.host.commands(), [tuple(uv.luks_test_key_argv(m.paths(OWNER_A, uv.KIND_PHONE)["image"]))],
                         "an open volume is not re-opened, only its key re-checked")
        self.assertEqual(self.host.calls[0][1], uv.derive_key(FAKE_MASTER, bp.owner_token(OWNER_A), uv.KIND_PHONE))

    def test_misrouted_path_is_refused_by_the_key(self):
        """If a bug pointed B at A's paths, B's key still opens nothing of A's."""
        class Misrouted(uv.VolumeManager):
            def paths_for_token(self, tok, kind):
                return super().paths_for_token(bp.owner_token(OWNER_A), kind)

        m = self.manager()
        m.open_volume(OWNER_A, uv.KIND_PHONE)
        bad = Misrouted(self.root, self.keyfile, logger=self.log, **self.host.hooks())
        with self.assertRaises(uv.VolumeError) as ctx:   # A's volume is open
            bad.open_volume(OWNER_B, uv.KIND_PHONE)
        self.assertEqual((ctx.exception.code, ctx.exception.status), ("volume_key_mismatch", 403))
        m.close_volume(OWNER_A, uv.KIND_PHONE)
        with self.assertRaises(uv.VolumeError) as ctx:   # A's volume is closed
            bad.open_volume(OWNER_B, uv.KIND_PHONE)
        self.assertEqual(ctx.exception.code, "volume_command_failed")
        self.assertEqual(self.host.mounts, {})
        self.assertTrue(os.path.exists(m.paths(OWNER_A, uv.KIND_PHONE)["image"]), "A's image survives the attempt")

    def test_close_unmounts_closes_and_detaches(self):
        m = self.manager()
        m.open_volume(OWNER_A, uv.KIND_PHONE)
        self.host.calls.clear()
        self.assertTrue(m.close_volume(OWNER_A, uv.KIND_PHONE))
        self.assertEqual(self.host.verbs(), ["umount " + m.paths(OWNER_A, uv.KIND_PHONE)["mount"],
                                             "cryptsetup close", "losetup -j", "losetup -d"])
        self.assertEqual((self.host.mounts, self.host.mappers, self.host.loops), ({}, {}, {}))
        self.assertTrue(os.path.exists(m.paths(OWNER_A, uv.KIND_PHONE)["image"]), "close keeps the data")
        self.assertFalse(m.close_volume(OWNER_A, uv.KIND_PHONE), "closing a closed volume is a no-op")

    def test_reopen_after_close_never_reformats(self):
        m = self.manager()
        m.open_volume(OWNER_A, uv.KIND_PHONE)
        m.close_volume(OWNER_A, uv.KIND_PHONE)
        self.host.calls.clear()
        info = m.open_volume(OWNER_A, uv.KIND_PHONE)
        self.assertFalse(info["created"])
        self.assertEqual(self.host.verbs(), ["losetup -j", "losetup --find", "cryptsetup open", "mount -t"])

    def test_key_goes_on_stdin_never_in_argv(self):
        m = self.manager()
        m.open_volume(OWNER_A, uv.KIND_PHONE)
        m.close_volume(OWNER_A, uv.KIND_PHONE)
        m.open_volume(OWNER_A, uv.KIND_PHONE)
        key = uv.derive_key(FAKE_MASTER, bp.owner_token(OWNER_A), uv.KIND_PHONE)
        forms = {key.hex(), base64.b64encode(key).decode(), FAKE_MASTER.hex()}
        keyed = 0
        for argv, stdin in self.host.calls:
            joined = " ".join(argv)
            for form in forms:
                self.assertNotIn(form, joined)
            if argv[:2] in (("cryptsetup", "luksFormat"), ("cryptsetup", "open")):
                self.assertIn("--key-file=-", argv)
                self.assertEqual(stdin, key)
                keyed += 1
            else:
                self.assertIsNone(stdin, f"{argv[0]} must not receive stdin")
        self.assertEqual(keyed, 3)

    def test_owner_a_volume_is_never_opened_for_b(self):
        m = self.manager()
        m.open_volume(OWNER_A, uv.KIND_PHONE)
        m.open_volume(OWNER_B, uv.KIND_PHONE)
        pa, pb = m.paths(OWNER_A, uv.KIND_PHONE), m.paths(OWNER_B, uv.KIND_PHONE)
        ka = uv.derive_key(FAKE_MASTER, bp.owner_token(OWNER_A), uv.KIND_PHONE)
        kb = uv.derive_key(FAKE_MASTER, bp.owner_token(OWNER_B), uv.KIND_PHONE)
        loops = {}
        for argv, stdin in self.host.calls:
            if argv[:3] == ("losetup", "--find", "--show"):
                loops[len(loops)] = argv[3]
            if argv[:2] == ("cryptsetup", "open"):
                img = self.host.loops[argv[-2]]
                self.assertEqual(stdin, ka if img == pa["image"] else kb)
        self.assertEqual(self.host.mounted_image(pa["mount"]), pa["image"])
        self.assertEqual(self.host.mounted_image(pb["mount"]), pb["image"])
        self.assertNotEqual(pa["image"], pb["image"])
        # And B's key cannot unlock A's image even if someone tried.
        loop = self.host(["losetup", "--find", "--show", pa["image"]]).stdout.strip()
        res = self.host(["cryptsetup", "open", "--type", "luks2", "--key-file=-", loop, "probe"], input=kb)
        self.assertEqual(res.returncode, 2)

    def test_failed_create_leaves_nothing_behind(self):
        m = self.manager()
        self.host.fail_on = ("mkfs.ext4",)
        with self.assertRaises(uv.VolumeError) as ctx:
            m.open_volume(OWNER_A, uv.KIND_PHONE)
        self.assertEqual(ctx.exception.code, "volume_command_failed")
        self.assertFalse(os.path.exists(m.paths(OWNER_A, uv.KIND_PHONE)["image"]))
        self.assertEqual((self.host.mounts, self.host.mappers, self.host.loops), ({}, {}, {}))
        # The next try starts clean and succeeds.
        self.assertTrue(m.open_volume(OWNER_A, uv.KIND_PHONE)["created"])

    def test_failed_open_of_existing_image_keeps_the_image(self):
        m = self.manager()
        m.open_volume(OWNER_A, uv.KIND_PHONE)
        m.close_volume(OWNER_A, uv.KIND_PHONE)
        self.host.fail_on = ("cryptsetup", "open")
        with self.assertRaises(uv.VolumeError):
            m.open_volume(OWNER_A, uv.KIND_PHONE)
        self.assertTrue(os.path.exists(m.paths(OWNER_A, uv.KIND_PHONE)["image"]), "never delete on a failed open")
        self.assertEqual(self.host.loops, {})

    def test_command_errors_do_not_echo_stdin(self):
        m = self.manager()
        self.host.fail_on = ("cryptsetup", "luksFormat")
        with self.assertRaises(uv.VolumeError) as ctx:
            m.open_volume(OWNER_A, uv.KIND_PHONE)
        key = uv.derive_key(FAKE_MASTER, bp.owner_token(OWNER_A), uv.KIND_PHONE)
        self.assertNotIn(key.hex(), str(ctx.exception))

    def test_delete_refused_while_leased(self):
        m = self.manager(lease_check=lambda token, kind: token == bp.owner_token(OWNER_A))
        m.open_volume(OWNER_A, uv.KIND_PHONE)
        with self.assertRaises(uv.VolumeError) as ctx:
            m.delete_volume(OWNER_A, uv.KIND_PHONE)
        self.assertEqual((ctx.exception.code, ctx.exception.status), ("lease_active", 409))
        self.assertTrue(os.path.exists(m.paths(OWNER_A, uv.KIND_PHONE)["image"]))
        self.assertTrue(self.host.is_mounted(m.paths(OWNER_A, uv.KIND_PHONE)["mount"]))

    def test_delete_closes_erases_the_header_and_removes_files(self):
        m = self.manager(lease_check=lambda token, kind: False)
        m.open_volume(OWNER_A, uv.KIND_PHONE)
        p = m.paths(OWNER_A, uv.KIND_PHONE)
        self.assertTrue(m.delete_volume(OWNER_A, uv.KIND_PHONE))
        self.assertIn(("cryptsetup", "luksErase"), [a[:2] for a in self.host.commands()])
        self.assertNotIn(p["image"], self.host.headers)
        self.assertFalse(os.path.lexists(p["image"]))
        self.assertFalse(os.path.lexists(p["mount"]))
        self.assertFalse(os.path.lexists(p["dir"]), "an empty owner directory goes too")
        self.assertFalse(m.delete_volume(OWNER_A, uv.KIND_PHONE))
        # A later open is a brand-new volume.
        self.assertTrue(m.open_volume(OWNER_A, uv.KIND_PHONE)["created"])

    def test_delete_without_lease_check_refuses(self):
        """Fail closed: a manager that cannot ask about leases does not delete."""
        m = self.manager()
        m.open_volume(OWNER_A, uv.KIND_PHONE)
        with self.assertRaises(uv.VolumeError) as ctx:
            m.delete_volume(OWNER_A, uv.KIND_PHONE)
        self.assertEqual(ctx.exception.code, "lease_active")

    def test_registry_records_volumes_without_owner_ids(self):
        store = {}
        m = self.manager(store_get=store.get, store_put=store.__setitem__, store_delete=lambda k: store.pop(k, None))
        m.open_volume(OWNER_A, uv.KIND_PHONE)
        key = f"{bp.owner_token(OWNER_A)}:phone-data"
        self.assertEqual(store[key]["size_bytes"], 8 * GIB)
        self.assertTrue(store[key]["open"])
        m.close_volume(OWNER_A, uv.KIND_PHONE)
        self.assertFalse(store[key]["open"])
        self.assertNotIn(OWNER_A, json.dumps(store).replace(bp.owner_token(OWNER_A), ""))


# ---------------------------------------------------------------------------
# Quotas and the host guard
# ---------------------------------------------------------------------------

class QuotaTests(Base):
    def test_defaults(self):
        m = self.manager()
        self.assertEqual(m.quota_bytes(OWNER_A, uv.KIND_PHONE), 8 * GIB)
        self.assertEqual(m.quota_bytes(OWNER_A, uv.KIND_BROWSER), 1 * GIB)
        m2 = self.manager(phone_quota_gb=2, browser_quota_gb=0.5)
        self.assertEqual(m2.quota_bytes(OWNER_A, uv.KIND_PHONE), 2 * GIB)
        self.assertEqual(m2.quota_bytes(OWNER_A, uv.KIND_BROWSER), GIB // 2)

    def test_per_owner_overrides_by_owner_id_or_token(self):
        qf = os.path.join(self.tmp, "quotas.json")
        with open(qf, "w") as fh:
            json.dump({OWNER_A: {"phone-data": 16},
                       bp.owner_token(OWNER_B): {"browser-profile": 2}}, fh)
        m = self.manager(quota_file=qf)
        self.assertEqual(m.quota_bytes(OWNER_A, uv.KIND_PHONE), 16 * GIB)
        self.assertEqual(m.quota_bytes(OWNER_A, uv.KIND_BROWSER), 1 * GIB)
        self.assertEqual(m.quota_bytes(OWNER_B, uv.KIND_BROWSER), 2 * GIB)
        self.assertEqual(m.quota_bytes(OWNER_B, uv.KIND_PHONE), 8 * GIB)
        m.open_volume(OWNER_A, uv.KIND_PHONE)
        self.assertEqual(os.path.getsize(m.paths(OWNER_A, uv.KIND_PHONE)["image"]), 16 * GIB)

    def test_broken_quota_file_falls_back_to_defaults_loudly(self):
        qf = os.path.join(self.tmp, "quotas.json")
        with open(qf, "w") as fh:
            fh.write("{not json")
        m = self.manager(quota_file=qf)
        with self.assertLogs(self.log, "WARNING"):
            self.assertEqual(m.quota_bytes(OWNER_A, uv.KIND_PHONE), 8 * GIB)
        with open(qf, "w") as fh:
            json.dump({OWNER_A: {"phone-data": -3}}, fh)
        with self.assertLogs(self.log, "WARNING"):
            self.assertEqual(m.quota_bytes(OWNER_A, uv.KIND_PHONE), 8 * GIB)

    def test_host_guard_refuses_when_free_space_would_drop_below_the_floor(self):
        self.host.host_free = 15 * GIB
        m = self.manager()
        with self.assertRaises(uv.VolumeError) as ctx:
            m.open_volume(OWNER_A, uv.KIND_PHONE)  # 15 - 8 = 7 < 10
        self.assertEqual((ctx.exception.code, ctx.exception.status), ("host_disk_low", 507))
        self.assertEqual(self.host.calls, [])
        self.assertFalse(os.path.exists(m.paths(OWNER_A, uv.KIND_PHONE)["image"]))
        m.open_volume(OWNER_A, uv.KIND_BROWSER)  # 15 - 1 = 14 >= 10

    def test_host_guard_counts_sparse_images_as_fully_allocated(self):
        self.host.host_free = 20 * GIB
        m = self.manager()
        m.open_volume(OWNER_A, uv.KIND_PHONE)  # 20 - 8 = 12: fine
        # The fake's free space did not move (the image is sparse), but the
        # 8 GiB A may still write is spoken for: 20 - 8 - 8 = 4 < 10.
        with self.assertRaises(uv.VolumeError) as ctx:
            m.open_volume(OWNER_B, uv.KIND_PHONE)
        self.assertEqual(ctx.exception.code, "host_disk_low")
        status = m.host_status()
        self.assertAlmostEqual(status["host_free_gb"], 20.0, places=1)
        self.assertAlmostEqual(status["host_uncommitted_gb"], 12.0, places=1)

    def test_existing_volume_reopens_even_when_the_host_is_low(self):
        m = self.manager()
        m.open_volume(OWNER_A, uv.KIND_PHONE)
        m.close_volume(OWNER_A, uv.KIND_PHONE)
        self.host.host_free = 1 * GIB
        m.open_volume(OWNER_A, uv.KIND_PHONE)  # already counted; no new allocation


# ---------------------------------------------------------------------------
# Usage and alerts
# ---------------------------------------------------------------------------

class UsageTests(Base):
    def set_pct(self, m, pct):
        p = m.paths(OWNER_A, uv.KIND_BROWSER)
        self.host.used[p["mount"]] = int(GIB * pct / 100)

    def test_usage_reports_used_and_limit(self):
        m = self.manager()
        m.open_volume(OWNER_A, uv.KIND_BROWSER)
        self.set_pct(m, 25)
        u = m.usage(OWNER_A, uv.KIND_BROWSER)
        self.assertTrue(u["open"])
        self.assertEqual(u["size_bytes"], GIB)
        self.assertAlmostEqual(u["used_pct"], 25.0, delta=0.1)
        self.assertEqual(u["kind"], uv.KIND_BROWSER)
        m.close_volume(OWNER_A, uv.KIND_BROWSER)
        closed = m.usage(OWNER_A, uv.KIND_BROWSER)
        self.assertFalse(closed["open"])
        self.assertAlmostEqual(closed["used_pct"], 25.0, delta=0.1, msg="last sample kept at close")
        self.assertFalse(m.usage(OWNER_B, uv.KIND_BROWSER)["exists"])

    def test_warns_once_at_80_and_once_at_95(self):
        m = self.manager()
        m.open_volume(OWNER_A, uv.KIND_BROWSER)
        warnings = []

        class Catch(logging.Handler):
            def emit(self, record):
                if record.levelno >= logging.WARNING:
                    warnings.append(record.getMessage())

        h = Catch()
        self.log.addHandler(h)
        try:
            for pct in (50, 79, 81, 85, 90, 96, 97, 99):
                self.set_pct(m, pct)
                m.usage(OWNER_A, uv.KIND_BROWSER)
            self.assertEqual(len(warnings), 2, warnings)
            self.assertIn("80%", warnings[0])
            self.assertIn("95%", warnings[1])
            # Logs name the owner by token (the audit log's convention), never by raw id.
            self.assertIn(f"token={bp.owner_token(OWNER_A)} ", warnings[0])
            # Dropping back under 80 re-arms the alert.
            self.set_pct(m, 40)
            m.usage(OWNER_A, uv.KIND_BROWSER)
            self.set_pct(m, 82)
            m.sample_open()
            self.assertEqual(len(warnings), 3)
        finally:
            self.log.removeHandler(h)


# ---------------------------------------------------------------------------
# Server wiring: flags, phones, browsers, endpoints
# ---------------------------------------------------------------------------

TOKEN = "orch-test-token-not-real"


class ServerBase(Base):
    def setUp(self):
        super().setUp()
        self.saved = {k: getattr(orch, k) for k in (
            "ORCH_DEPLOY_MODE", "ORCH_API_TOKEN", "ORCH_PROFILE_ROOT", "ORCH_PROFILE_ENCRYPTION",
            "ORCH_PHONE_DATA", "ORCH_BROWSER_PROFILE", "ORCH_VOLUME_ROOT", "ORCH_VOLUME_MASTER_KEY_FILE",
            "ORCH_MOCK_API_URL", "ORCH_MAX_INSTANCES", "ORCH_MAX_REDROID_INSTANCES", "ORCH_MAX_CHROME_INSTANCES")}
        orch.init_state(":memory:")
        self._clear()
        orch.ORCH_DEPLOY_MODE = "mock"
        orch.ORCH_API_TOKEN = TOKEN
        orch.ORCH_PROFILE_ROOT = os.path.join(self.tmp, "profiles")
        orch.ORCH_PROFILE_ENCRYPTION = "none"
        orch.ORCH_VOLUME_ROOT = self.root
        orch.ORCH_VOLUME_MASTER_KEY_FILE = self.keyfile
        orch.ORCH_MOCK_API_URL = "http://127.0.0.1:9"
        orch.ORCH_MAX_INSTANCES = 4
        orch.ORCH_MAX_REDROID_INSTANCES = 4
        orch.ORCH_MAX_CHROME_INSTANCES = 4
        orch.set_chrome_runtime(bp.MockChromeRuntime())
        self.vm = self.manager()
        orch.set_volume_manager(self.vm)
        self.client = orch.app.test_client()
        self.auth = {"Authorization": f"Bearer {TOKEN}"}

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(orch, k, v)
        orch.set_volume_manager(None)
        orch.set_chrome_runtime(None)
        self._clear()
        orch.init_state(":memory:")
        super().tearDown()

    @staticmethod
    def _clear():
        for d in (orch._instances, orch._leases, orch._instance_tenancy, orch._user_sessions,
                  orch._browser_sessions):
            d.clear()
        orch._tickets.clear()

    def phone(self, owner, ttl=600):
        return self.client.post("/sessions", json={"owner_user_id": owner, "ttl_seconds": ttl}, headers=self.auth)

    def browser(self, owner):
        return self.client.post("/sessions", json={"owner_user_id": owner, "purpose": "browser",
                                                   "ttl_seconds": 600}, headers=self.auth)

    def release(self, owner, purpose=None):
        q = "?purpose=browser" if purpose == "browser" else ""
        return self.client.delete(f"/sessions/{owner}{q}", headers=self.auth)


class FlagsOffTests(ServerBase):
    def test_flags_off_is_the_old_behaviour(self):
        orch.ORCH_PHONE_DATA = "instance"
        orch.ORCH_BROWSER_PROFILE = "dir"
        self.assertEqual(self.phone(OWNER_A).status_code, 201)
        b = self.browser(OWNER_A)
        self.assertEqual(b.status_code, 201)
        self.assertEqual(b.get_json()["session"]["profile_dir"],
                         os.path.join(orch.ORCH_PROFILE_ROOT, bp.owner_token(OWNER_A)))
        self.assertEqual(self.host.calls, [], "no volume command runs with the flags off")
        self.assertFalse(os.path.exists(self.root))
        inst = next(iter(orch._instances.values()))
        self.assertNotIn("data_source", inst)
        self.release(OWNER_A)
        r = self.phone(OWNER_B)
        self.assertEqual(r.status_code, 409, "the per-instance guard still refuses A's /data to B")
        self.assertIn("not clean", r.get_json()["error"])
        health = self.client.get("/health").get_json()
        self.assertEqual(health["volumes"]["enabled"], False)

    def test_config_refusal(self):
        orch.ORCH_PHONE_DATA, orch.ORCH_BROWSER_PROFILE = "instance", "dir"
        self.assertIsNone(orch.volume_config_refusal())
        orch.ORCH_PHONE_DATA = "per-user"
        self.assertIn("ORCH_PHONE_DATA", orch.volume_config_refusal())
        orch.ORCH_PHONE_DATA = "instance"
        orch.ORCH_BROWSER_PROFILE = "luks"
        self.assertIn("ORCH_BROWSER_PROFILE", orch.volume_config_refusal())
        orch.ORCH_BROWSER_PROFILE = "volume"
        self.assertIsNone(orch.volume_config_refusal())
        orch.ORCH_VOLUME_MASTER_KEY_FILE = os.path.join(self.tmp, "missing.key")
        self.assertIn("ORCH_VOLUME_MASTER_KEY_FILE", orch.volume_config_refusal())
        orch.ORCH_PHONE_DATA = "per_user"
        orch.ORCH_DEPLOY_MODE = "oci"
        orch.ORCH_VOLUME_MASTER_KEY_FILE = self.keyfile
        self.assertIn("oci", orch.volume_config_refusal())

    def test_containers_need_the_host_mount_namespace(self):
        """dockerd must see the mounted volume, or it binds the empty directory underneath."""
        orch.ORCH_PHONE_DATA, orch.ORCH_BROWSER_PROFILE = "per_user", "dir"
        orch.ORCH_DEPLOY_MODE = "redroid"
        real = orch._mount_namespace_check
        try:
            orch._mount_namespace_check = lambda: "private mount namespace"
            self.assertIn("private mount namespace", orch.volume_config_refusal())
            orch._mount_namespace_check = lambda: None
            self.assertIsNone(orch.volume_config_refusal())
            orch.ORCH_DEPLOY_MODE = "mock"   # no containers: nothing to check
            orch._mount_namespace_check = lambda: "private mount namespace"
            self.assertIsNone(orch.volume_config_refusal())
        finally:
            orch._mount_namespace_check = real
        same = {"/proc/self/ns/mnt": "mnt:[1]", "/proc/1/ns/mnt": "mnt:[1]"}
        self.assertIsNone(uv.mount_namespace_problem(readlink=same.__getitem__))
        other = {"/proc/self/ns/mnt": "mnt:[2]", "/proc/1/ns/mnt": "mnt:[1]"}
        self.assertIn("private mount namespace", uv.mount_namespace_problem(readlink=other.__getitem__))

        def denied(path):
            raise PermissionError(path)
        self.assertIn("root", uv.mount_namespace_problem(readlink=denied))


class PhonePerUserTests(ServerBase):
    def setUp(self):
        super().setUp()
        orch.ORCH_PHONE_DATA = "per_user"
        orch.ORCH_BROWSER_PROFILE = "dir"

    def inst_of(self, resp):
        return orch._instances[resp.get_json()["session"]["instance_id"]]

    def test_lease_mounts_the_owners_volume_as_data(self):
        r = self.phone(OWNER_A)
        self.assertEqual(r.status_code, 201, r.get_json())
        sess = r.get_json()["session"]
        self.assertEqual(sess["data"], {"source": "user_volume", "kind": "phone-data",
                                        "owner_token": bp.owner_token(OWNER_A)})
        inst = self.inst_of(r)
        self.assertEqual(inst["data_source"]["owner_token"], bp.owner_token(OWNER_A))
        self.assertTrue(inst["data_source"]["attached"])
        p = self.vm.paths(OWNER_A, uv.KIND_PHONE)
        self.assertEqual(self.host.mounted_image(p["mount"]), p["image"])

    def test_release_stops_and_closes_and_keeps_the_volume(self):
        r = self.phone(OWNER_A)
        self.release(OWNER_A)
        p = self.vm.paths(OWNER_A, uv.KIND_PHONE)
        self.assertFalse(self.host.is_mounted(p["mount"]))
        self.assertTrue(os.path.exists(p["image"]))
        self.assertFalse(self.inst_of(r)["data_source"]["attached"])
        again = self.phone(OWNER_A)
        self.assertEqual(again.status_code, 201)
        self.assertEqual(sum(1 for a in self.host.commands() if a[:2] == ("cryptsetup", "luksFormat")), 1)

    def test_next_owner_on_the_same_instance_gets_their_own_fresh_volume(self):
        orch.ORCH_MAX_REDROID_INSTANCES = 1
        orch.ORCH_MAX_INSTANCES = 1
        ra = self.phone(OWNER_A)
        self.release(OWNER_A)
        rb = self.phone(OWNER_B)
        self.assertEqual(rb.status_code, 201, rb.get_json())
        self.assertEqual(ra.get_json()["session"]["instance_id"], rb.get_json()["session"]["instance_id"])
        pa, pb = self.vm.paths(OWNER_A, uv.KIND_PHONE), self.vm.paths(OWNER_B, uv.KIND_PHONE)
        self.assertEqual(self.host.mounted_image(pb["mount"]), pb["image"])
        self.assertFalse(self.host.is_mounted(pa["mount"]))
        formats = [a[-1] for a in self.host.commands() if a[:2] == ("cryptsetup", "luksFormat")]
        self.assertEqual(formats, [pa["image"], pb["image"]], "B got a freshly formatted volume")
        tenancy = orch._instance_tenancy[rb.get_json()["session"]["instance_id"]]
        self.assertEqual(tenancy["last_owner"], OWNER_B)
        self.assertIn("reset_at", tenancy, "switching /data to another owner's volume is recorded")

    def test_busy_instance_still_refused_across_owners(self):
        orch.ORCH_MAX_REDROID_INSTANCES = 1
        orch.ORCH_MAX_INSTANCES = 1
        self.phone(OWNER_A)
        r = self.phone(OWNER_B)
        self.assertEqual(r.status_code, 409)
        self.assertFalse(os.path.exists(self.vm.paths(OWNER_B, uv.KIND_PHONE)["image"]),
                         "a lease refused for capacity allocates no volume")
        self.assertEqual(self.host.mounts.keys(), {self.vm.paths(OWNER_A, uv.KIND_PHONE)["mount"]})

    def test_guard_is_a_second_check_when_the_switch_did_not_happen(self):
        orch.ORCH_MAX_REDROID_INSTANCES = 1
        orch.ORCH_MAX_INSTANCES = 1
        self.phone(OWNER_A)
        self.release(OWNER_A)
        real = orch._attach_phone_data
        orch._attach_phone_data = lambda inst, token, mount: None  # pretend the swap silently failed
        try:
            r = self.phone(OWNER_B)
        finally:
            orch._attach_phone_data = real
        self.assertEqual(r.status_code, 409)
        self.assertIn("not clean", r.get_json()["error"])
        self.assertFalse(self.host.is_mounted(self.vm.paths(OWNER_B, uv.KIND_PHONE)["mount"]))

    def test_expired_lease_is_reaped_and_its_volume_closed(self):
        r = self.phone(OWNER_A, ttl=10)
        iid = r.get_json()["session"]["instance_id"]
        orch._leases[iid]["expires_at"] = time.time() - 1
        orch._volume_tick()
        self.assertFalse(self.host.is_mounted(self.vm.paths(OWNER_A, uv.KIND_PHONE)["mount"]))
        self.assertNotIn(OWNER_A, orch._user_sessions)
        self.assertFalse(orch._instances[iid]["data_source"]["attached"])

    def test_turning_the_flag_off_refuses_instances_that_carried_user_volumes(self):
        orch.ORCH_MAX_REDROID_INSTANCES = 1
        orch.ORCH_MAX_INSTANCES = 1
        self.phone(OWNER_A)
        self.release(OWNER_A)
        orch.ORCH_PHONE_DATA = "instance"
        r = self.phone(OWNER_A)
        self.assertEqual(r.status_code, 409)
        self.assertIn("reprovision", r.get_json()["error"])

    def test_raw_instance_lease_is_refused(self):
        r = self.phone(OWNER_A)
        iid = r.get_json()["session"]["instance_id"]
        self.release(OWNER_A)
        raw = self.client.post(f"/instances/{iid}/lease", json={"owner": OWNER_B, "ttl_seconds": 60},
                               headers=self.auth)
        self.assertEqual((raw.status_code, raw.get_json()["code"]), (409, "use_sessions"))

    def test_switching_purpose_detaches_the_volume(self):
        self.phone(OWNER_A)
        r = self.client.post("/sessions", json={"owner_user_id": OWNER_A, "purpose": "camera", "provision": False},
                             headers=self.auth)
        self.assertIn(r.status_code, (409, 500))  # no camera host in mock mode without provisioning
        self.assertFalse(self.host.is_mounted(self.vm.paths(OWNER_A, uv.KIND_PHONE)["mount"]))

    def test_host_disk_low_is_507(self):
        self.host.host_free = 12 * GIB
        r = self.phone(OWNER_A)
        self.assertEqual(r.status_code, 507)
        self.assertEqual(r.get_json()["code"], "host_disk_low")
        self.assertEqual(orch._user_sessions, {})
        self.assertEqual(orch._leases, {})

    def test_missing_key_refuses_the_lease(self):
        orch.set_volume_manager(self.manager(master_key_file=os.path.join(self.tmp, "gone.key")))
        r = self.phone(OWNER_A)
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.get_json()["code"], "volume_key_missing")

    def test_restart_keeps_the_mapping(self):
        db = os.path.join(self.tmp, "state.sqlite3")
        orch.init_state(db)
        ra, rb = self.phone(OWNER_A), self.phone(OWNER_B)
        self._clear()
        orch.init_state(db)
        n = len(self.host.calls)
        for owner, r in ((OWNER_A, ra), (OWNER_B, rb)):
            again = self.phone(owner)
            self.assertEqual(again.status_code, 200)
            self.assertEqual(again.get_json()["session"]["instance_id"], r.get_json()["session"]["instance_id"])
            self.assertEqual(again.get_json()["session"]["data"]["owner_token"], bp.owner_token(owner))
        self.assertEqual(len(self.host.calls), n, "a renewal after restart does not touch the volumes")

    def test_redroid_mode_binds_the_volume_as_data(self):
        orch.ORCH_DEPLOY_MODE = "redroid"
        orch.ORCH_MAX_REDROID_INSTANCES = 1
        orch.ORCH_MAX_INSTANCES = 1
        calls = []

        def fake_cmd(argv):
            calls.append(list(argv))
            name = argv[argv.index("--name") + 1]
            return json.dumps({"name": name, "adb_connect": "127.0.0.1:5555", "image": "img"}) + "\n"

        real = orch._phone_container_cmd
        orch._phone_container_cmd = fake_cmd
        try:
            self.assertEqual(self.phone(OWNER_A).status_code, 201)
            self.release(OWNER_A)
            self.assertEqual(self.phone(OWNER_B).status_code, 201)
        finally:
            orch._phone_container_cmd = real
        pa, pb = self.vm.paths(OWNER_A, uv.KIND_PHONE), self.vm.paths(OWNER_B, uv.KIND_PHONE)
        self.assertIn("--data-dir", calls[0])
        self.assertEqual(calls[0][calls[0].index("--data-dir") + 1], pa["mount"])
        self.assertIn("--down", calls[1], "release stops the container before the volume closes")
        up_b = calls[-1]
        self.assertEqual(up_b[up_b.index("--data-dir") + 1], pb["mount"])
        self.assertNotIn(pa["mount"], " ".join(" ".join(c) for c in calls[1:]))


class BrowserVolumeTests(ServerBase):
    def setUp(self):
        super().setUp()
        orch.ORCH_PHONE_DATA = "instance"
        orch.ORCH_BROWSER_PROFILE = "volume"

    def test_profile_is_the_owners_mounted_volume(self):
        r = self.browser(OWNER_A)
        self.assertEqual(r.status_code, 201, r.get_json())
        p = self.vm.paths(OWNER_A, uv.KIND_BROWSER)
        self.assertEqual(r.get_json()["session"]["profile_dir"], p["mount"])
        self.assertEqual(self.host.mounted_image(p["mount"]), p["image"])
        self.release(OWNER_A, "browser")
        self.assertFalse(self.host.is_mounted(p["mount"]))
        self.assertTrue(os.path.exists(p["image"]))

    def test_expiry_closes_the_volume(self):
        self.browser(OWNER_A)
        orch._browser_sessions[OWNER_A]["expires_at"] = time.time() - 1
        orch._volume_tick()
        self.assertFalse(self.host.is_mounted(self.vm.paths(OWNER_A, uv.KIND_BROWSER)["mount"]))

    def test_delete_refused_while_leased_then_allowed(self):
        self.browser(OWNER_A)
        r = self.client.delete(f"/volumes/{OWNER_A}/browser-profile", json={"confirm": OWNER_A},
                               headers=self.auth)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["code"], "lease_active")
        r = self.client.delete(f"/profiles/{OWNER_A}", json={"confirm": OWNER_A}, headers=self.auth)
        self.assertEqual(r.status_code, 409)
        self.release(OWNER_A, "browser")
        r = self.client.delete(f"/volumes/{OWNER_A}/browser-profile", json={"confirm": "someone-else"},
                               headers=self.auth)
        self.assertEqual(r.status_code, 400)
        r = self.client.delete(f"/profiles/{OWNER_A}", json={"confirm": OWNER_A}, headers=self.auth)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["deleted"])
        self.assertFalse(os.path.exists(self.vm.paths(OWNER_A, uv.KIND_BROWSER)["image"]))

    def test_host_disk_low_is_507(self):
        self.host.host_free = 10 * GIB
        r = self.browser(OWNER_A)
        self.assertEqual((r.status_code, r.get_json()["code"]), (507, "host_disk_low"))
        self.assertEqual(orch._browser_sessions, {})

    def test_failed_browser_start_closes_the_volume(self):
        class Boom(bp.MockChromeRuntime):
            def start(self, session, proxy_server=None):
                raise RuntimeError("chrome did not start")

        orch.set_chrome_runtime(Boom())
        r = self.browser(OWNER_A)
        self.assertEqual(r.status_code, 500)
        self.assertFalse(self.host.is_mounted(self.vm.paths(OWNER_A, uv.KIND_BROWSER)["mount"]))


class EndpointTests(ServerBase):
    def setUp(self):
        super().setUp()
        orch.ORCH_PHONE_DATA = "per_user"
        orch.ORCH_BROWSER_PROFILE = "volume"

    def test_usage_endpoint_is_owner_scoped_and_authenticated(self):
        self.phone(OWNER_A)
        self.browser(OWNER_A)
        self.browser(OWNER_B)
        self.host.used[self.vm.paths(OWNER_A, uv.KIND_BROWSER)["mount"]] = GIB // 2
        self.assertEqual(self.client.get(f"/volumes/{OWNER_A}").status_code, 401)
        body = self.client.get(f"/volumes/{OWNER_A}", headers=self.auth).get_json()
        self.assertEqual(body["owner_token"], bp.owner_token(OWNER_A))
        vols = body["volumes"]
        self.assertEqual(set(vols), {"phone-data", "browser-profile"})
        self.assertEqual(vols["phone-data"]["limit_bytes"], 8 * GIB)
        self.assertEqual(vols["browser-profile"]["limit_bytes"], GIB)
        self.assertAlmostEqual(vols["browser-profile"]["used_pct"], 50.0, delta=0.1)
        self.assertNotIn(bp.owner_token(OWNER_B), json.dumps(body))
        self.assertEqual(self.client.get("/volumes/", headers=self.auth).status_code, 404)

    def test_health_reports_volumes_without_owners(self):
        self.phone(OWNER_A)
        self.browser(OWNER_B)
        raw = self.client.get("/health").get_data(as_text=True)
        health = json.loads(raw)["volumes"]
        self.assertTrue(health["enabled"])
        self.assertEqual(health["root"], self.root)
        self.assertIsInstance(health["host_free_gb"], float)
        for secret_ish in (OWNER_A, OWNER_B, bp.owner_token(OWNER_A), bp.owner_token(OWNER_B),
                           FAKE_MASTER.hex(), self.keyfile):
            self.assertNotIn(secret_ish, raw)

    def test_unknown_kind_is_404(self):
        r = self.client.delete(f"/volumes/{OWNER_A}/swap", json={"confirm": OWNER_A}, headers=self.auth)
        self.assertEqual(r.status_code, 404)

    def test_phone_volume_delete_refused_while_leased(self):
        self.phone(OWNER_A)
        r = self.client.delete(f"/volumes/{OWNER_A}/phone-data", json={"confirm": OWNER_A}, headers=self.auth)
        self.assertEqual(r.status_code, 409)
        self.release(OWNER_A)
        r = self.client.delete(f"/volumes/{OWNER_A}/phone-data", json={"confirm": OWNER_A}, headers=self.auth)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["deleted"])


if __name__ == "__main__":
    unittest.main()
