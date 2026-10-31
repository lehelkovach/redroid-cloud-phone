#!/usr/bin/env python3
"""Browser slots: one owner, N isolated browsers, each still owner-bound.

`POST /sessions {"purpose": "browser", "slot": "swarm-1"}` leases a second
(third, ...) browser for the SAME owner, with its own profile, DevTools port
and relay URL. The default slot `main` is the browser every caller had before
this change, and `ORCH_BROWSER_SLOTS_PER_OWNER` (default 1) keeps today's
behaviour until the founder raises it. Mock runtime; fake owners only.
"""

import json
import os
import shutil
import tempfile
import time
import unittest

from orchestrator import browser_pool as bp
from orchestrator import server as orch
from orchestrator import user_volumes as uv
from tests.fixtures.fake_volumes import FakeVolumeHost

TOKEN = "orch-test-token-not-real"
OWNER_A = "slack:U0FAKEA"
OWNER_B = "slack:U0FAKEB"

SAVED = ("ORCH_DEPLOY_MODE", "ORCH_API_TOKEN", "ORCH_PROFILE_ROOT", "ORCH_PROFILE_ENCRYPTION",
         "ORCH_BROWSER_PROFILE", "ORCH_PHONE_DATA", "ORCH_VOLUME_ROOT", "ORCH_VOLUME_MASTER_KEY_FILE",
         "ORCH_MAX_CHROME_INSTANCES", "ORCH_BROWSER_SLOTS_PER_OWNER", "ORCH_CHROME_IMAGE")


def _clear():
    for d in (orch._instances, orch._leases, orch._instance_tenancy, orch._user_sessions, orch._browser_sessions):
        d.clear()
    orch._tickets.clear()


class SlotBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="orch-slots-")
        self.saved = {k: getattr(orch, k) for k in SAVED}
        orch.init_state(":memory:")
        _clear()
        orch.ORCH_DEPLOY_MODE = "mock"
        orch.ORCH_API_TOKEN = TOKEN
        orch.ORCH_PROFILE_ROOT = os.path.join(self.tmp, "profiles")
        orch.ORCH_PROFILE_ENCRYPTION = "none"
        orch.ORCH_BROWSER_PROFILE = "dir"
        orch.ORCH_PHONE_DATA = "instance"
        orch.ORCH_MAX_CHROME_INSTANCES = 8
        orch.ORCH_BROWSER_SLOTS_PER_OWNER = 1
        # The fake DevTools URL names the isolation key, so two slots that
        # really are two runtimes show up as two different upstreams.
        self.runtime = bp.MockChromeRuntime(upstream_for=lambda s: f"ws://fake-cdp/{s['isolation_key']}")
        orch.set_chrome_runtime(self.runtime)
        self.client = orch.app.test_client()
        self.auth = {"Authorization": f"Bearer {TOKEN}"}

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(orch, k, v)
        orch.set_chrome_runtime(None)
        orch.set_volume_manager(None)
        _clear()
        orch.init_state(":memory:")
        shutil.rmtree(self.tmp, ignore_errors=True)

    def browser(self, owner, **extra):
        body = {"owner_user_id": owner, "purpose": "browser", "ttl_seconds": 600, **extra}
        return self.client.post("/sessions", json=body, headers=self.auth)

    def release(self, owner, **extra):
        return self.client.delete(f"/sessions/{owner}", json={"purpose": "browser", **extra}, headers=self.auth)

    def listed(self):
        return self.client.get("/sessions", headers=self.auth).get_json()["browser_sessions"]


class DefaultSlotUnchanged(SlotBase):
    def test_no_slot_is_main_and_renews_as_before(self):
        r = self.browser(OWNER_A)
        self.assertEqual(r.status_code, 201, r.get_json())
        sess = r.get_json()["session"]
        self.assertEqual(sess["slot"], "main")
        self.assertEqual(sess["isolation_key"], f"{bp.owner_token(OWNER_A)}/main")
        self.assertEqual(sess["profile_dir"], os.path.join(orch.ORCH_PROFILE_ROOT, bp.owner_token(OWNER_A)))
        # The in-memory and persisted key of the main slot is still the owner id.
        self.assertIn(OWNER_A, orch._browser_sessions)
        self.assertEqual(orch._store.get("browser_sessions", OWNER_A)["lease_id"], sess["lease_id"])
        again = self.browser(OWNER_A, ttl_seconds=900)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.get_json()["session"]["lease_id"], sess["lease_id"])
        self.assertEqual(again.get_json()["session"]["renewals"], 1)
        # An explicit "main" is the same browser, not a second one.
        same = self.browser(OWNER_A, slot="main")
        self.assertEqual(same.status_code, 200)
        self.assertEqual(same.get_json()["session"]["lease_id"], sess["lease_id"])
        self.assertEqual(len(self.runtime.started), 1)
        self.assertEqual(self.release(OWNER_A).status_code, 200)
        self.assertEqual(orch._browser_sessions, {})

    def test_bad_slot_is_400(self):
        for bad in ("Main", "a b", "x" * 33, "../x", ""):
            r = self.browser(OWNER_A, slot=bad)
            self.assertEqual((r.status_code, r.get_json()["code"]), (400, "bad_slot"), bad)
        self.assertEqual(orch._browser_sessions, {})

    def test_old_persisted_rows_load_as_slot_main(self):
        db = os.path.join(self.tmp, "state.sqlite3")
        orch.init_state(db)
        token = bp.owner_token(OWNER_A)
        old_row = {  # the shape written before slots existed: no slot, no isolation_key
            "lease_id": "bl-oldrow0000000001", "owner_user_id": OWNER_A, "owner_token": token,
            "purpose": "browser", "runtime": "chrome", "chrome_runtime": "mock",
            "profile_dir": os.path.join(orch.ORCH_PROFILE_ROOT, token), "cdp_port": 19222,
            "proxy_id": "direct", "proxy_upstream": None, "egress": "direct",
            "acquired_at": time.time(), "expires_at": time.time() + 600, "ttl_seconds": 600, "renewals": 0,
            "cdp_ws_internal": "ws://fake-cdp/old",
        }
        orch._store.put("browser_sessions", OWNER_A, old_row)
        _clear()
        orch.init_state(db)
        loaded = orch._browser_sessions[OWNER_A]
        self.assertEqual(loaded["slot"], "main")
        self.assertEqual(loaded["isolation_key"], f"{token}/main")
        got = self.client.get(f"/sessions/{OWNER_A}?purpose=browser", headers=self.auth)
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.get_json()["lease_id"], "bl-oldrow0000000001")
        self.assertEqual(got.get_json()["slot"], "main")
        # Renewing the old row keeps its lease; a ticket for it still mints.
        again = self.browser(OWNER_A)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.get_json()["session"]["lease_id"], "bl-oldrow0000000001")
        t = self.client.post("/tickets", json={"owner_user_id": OWNER_A, "purpose": "browser"}, headers=self.auth)
        self.assertEqual(t.status_code, 201)
        self.assertTrue(t.get_json()["relay"]["cdp_ws_url"].endswith("/relay/cdp/bl-oldrow0000000001"))


class TwoSlots(SlotBase):
    def setUp(self):
        super().setUp()
        orch.ORCH_BROWSER_SLOTS_PER_OWNER = 3

    def test_two_slots_are_two_runtimes_with_distinct_isolation_keys(self):
        main = self.browser(OWNER_A).get_json()["session"]
        r = self.browser(OWNER_A, slot="swarm-1")
        self.assertEqual(r.status_code, 201, r.get_json())
        s1 = r.get_json()["session"]
        s2 = self.browser(OWNER_A, slot="swarm-2").get_json()["session"]
        keys = {main["isolation_key"], s1["isolation_key"], s2["isolation_key"]}
        self.assertEqual(len(keys), 3)
        self.assertEqual(s1["isolation_key"], f"{bp.owner_token(OWNER_A)}/swarm-1")
        self.assertEqual(len({main["lease_id"], s1["lease_id"], s2["lease_id"]}), 3)
        self.assertEqual(len({main["profile_dir"], s1["profile_dir"], s2["profile_dir"]}), 3)
        for s in (s1, s2):
            self.assertEqual(s["owner_user_id"], OWNER_A)
            self.assertEqual(s["owner_token"], main["owner_token"], "a slot is still the owner's")
            self.assertEqual(os.path.dirname(s["profile_dir"]), orch.ORCH_PROFILE_ROOT)
            self.assertNotIn(main["profile_dir"] + os.sep, s["profile_dir"], "a slot profile is not inside main's")
            self.assertEqual(oct(os.stat(s["profile_dir"]).st_mode & 0o777), "0o700")
        # Three browser starts, three internal DevTools ports, three upstreams.
        self.assertEqual(len(self.runtime.started), 3)
        live = orch._browser_sessions.values()
        self.assertEqual(len({s["cdp_port"] for s in live}), 3)
        self.assertEqual(len({s["cdp_ws_internal"] for s in live}), 3)
        # Listing shows the slots; the pool says what the cap is.
        self.assertEqual(sorted(s["slot"] for s in self.listed()), ["main", "swarm-1", "swarm-2"])
        pool = self.client.get("/pool", headers=self.auth).get_json()["pool"]["browser"]
        self.assertEqual(pool["leased"], 3)
        self.assertEqual(pool["slots_per_owner"], 3)
        # Each slot renews on its own.
        again = self.browser(OWNER_A, slot="swarm-1")
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.get_json()["session"]["lease_id"], s1["lease_id"])
        self.assertEqual(again.get_json()["session"]["renewals"], 1)
        self.assertEqual(orch._browser_sessions[OWNER_A]["renewals"], 0, "renewing a slot never touches main")

    def test_release_one_slot_keeps_the_others(self):
        main = self.browser(OWNER_A).get_json()["session"]
        s1 = self.browser(OWNER_A, slot="swarm-1").get_json()["session"]
        s2 = self.browser(OWNER_A, slot="swarm-2").get_json()["session"]
        r = self.release(OWNER_A, slot="swarm-1")
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["released"]["lease_id"], s1["lease_id"])
        self.assertEqual(self.runtime.stopped, [s1["lease_id"]])
        self.assertEqual(sorted(s["lease_id"] for s in self.listed()), sorted([main["lease_id"], s2["lease_id"]]))
        self.assertIsNone(orch._store.get("browser_sessions", orch._browser_key(OWNER_A, "swarm-1")))
        self.assertIsNotNone(orch._store.get("browser_sessions", OWNER_A))
        # Release by lease id (what OSLO's swarm does), and by query string.
        r = self.client.delete(f"/sessions/{OWNER_A}?purpose=browser&lease_id={s2['lease_id']}", headers=self.auth)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual([s["lease_id"] for s in self.listed()], [main["lease_id"]])
        # Releasing a slot that is not held is a 404, and main is still there.
        self.assertEqual(self.release(OWNER_A, slot="swarm-1").status_code, 404)
        self.assertEqual(self.release(OWNER_A).status_code, 200)
        self.assertEqual(self.listed(), [])
        self.assertEqual(sorted(self.runtime.stopped), sorted([main["lease_id"], s1["lease_id"], s2["lease_id"]]))

    def test_expiry_and_kill_are_per_slot(self):
        main = self.browser(OWNER_A).get_json()["session"]
        s1 = self.browser(OWNER_A, slot="swarm-1").get_json()["session"]
        orch._browser_sessions[orch._browser_key(OWNER_A, "swarm-1")]["expires_at"] = time.time() - 1
        orch._reap_browser_sessions()
        self.assertEqual([s["lease_id"] for s in self.listed()], [main["lease_id"]])
        self.assertEqual(self.runtime.stopped, [s1["lease_id"]])
        s3 = self.browser(OWNER_A, slot="swarm-3").get_json()["session"]
        k = self.client.post(f"/sessions/{OWNER_A}/kill", json={"purpose": "browser", "slot": "swarm-3"}, headers=self.auth)
        self.assertEqual(k.status_code, 200)
        self.assertEqual(k.get_json()["lease_id"], s3["lease_id"])
        k = self.client.post(f"/sessions/{OWNER_A}/kill", json={"purpose": "browser"}, headers=self.auth)
        self.assertEqual(k.get_json()["lease_id"], main["lease_id"])
        got = self.client.get(f"/sessions/{OWNER_A}?purpose=browser&slot=swarm-3", headers=self.auth).get_json()
        self.assertEqual(got["lease_id"], s3["lease_id"])
        self.assertEqual(self.client.get(f"/sessions/{OWNER_A}?purpose=browser&slot=swarm-1", headers=self.auth).status_code, 404)

    def test_tickets_name_the_slot_or_the_lease(self):
        main = self.browser(OWNER_A).get_json()["session"]
        s1 = self.browser(OWNER_A, slot="swarm-1").get_json()["session"]
        by_slot = self.client.post("/tickets", json={"owner_user_id": OWNER_A, "purpose": "browser", "slot": "swarm-1"},
                                   headers=self.auth).get_json()
        self.assertTrue(by_slot["relay"]["cdp_ws_url"].endswith(f"/relay/cdp/{s1['lease_id']}"))
        by_lease = self.client.post("/tickets", json={"owner_user_id": OWNER_A, "purpose": "browser",
                                                      "lease_id": s1["lease_id"]}, headers=self.auth).get_json()
        self.assertTrue(by_lease["relay"]["cdp_ws_url"].endswith(f"/relay/cdp/{s1['lease_id']}"))
        default = self.client.post("/tickets", json={"owner_user_id": OWNER_A, "purpose": "browser"},
                                   headers=self.auth).get_json()
        self.assertTrue(default["relay"]["cdp_ws_url"].endswith(f"/relay/cdp/{main['lease_id']}"))
        # A ticket is bound to its lease: slot-1's ticket does not check out for main's relay.
        ok, _ = orch._lease_check({"kind": "browser", "owner": OWNER_A, "lease_id": s1["lease_id"]})
        self.assertTrue(ok)
        self.release(OWNER_A, slot="swarm-1")
        ok, why = orch._lease_check({"kind": "browser", "owner": OWNER_A, "lease_id": s1["lease_id"]})
        self.assertFalse(ok)
        self.assertIn("released", why)
        ok, _ = orch._lease_check({"kind": "browser", "owner": OWNER_A, "lease_id": main["lease_id"]})
        self.assertTrue(ok)

    def test_delete_profile_refused_while_any_slot_is_leased_then_removes_slot_profiles(self):
        main = self.browser(OWNER_A).get_json()["session"]
        s1 = self.browser(OWNER_A, slot="swarm-1").get_json()["session"]
        self.release(OWNER_A)
        r = self.client.delete(f"/profiles/{OWNER_A}", json={"confirm": OWNER_A}, headers=self.auth)
        self.assertEqual((r.status_code, r.get_json()["code"]), (409, "lease_active"))
        self.release(OWNER_A, slot="swarm-1")
        r = self.client.delete(f"/profiles/{OWNER_A}", json={"confirm": OWNER_A}, headers=self.auth)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(os.path.exists(main["profile_dir"]))
        self.assertFalse(os.path.exists(s1["profile_dir"]))


class SlotCap(SlotBase):
    def test_default_cap_one_refuses_a_second_slot_with_the_cap_in_the_body(self):
        main = self.browser(OWNER_A).get_json()["session"]
        r = self.browser(OWNER_A, slot="swarm-1")
        self.assertEqual(r.status_code, 429, r.get_json())
        body = r.get_json()
        self.assertEqual(body["code"], "slots_exhausted")
        self.assertEqual(body["cap"], 1)
        self.assertEqual(body["slots"], ["main"])
        self.assertEqual(len(self.runtime.started), 1, "a refused slot starts nothing")
        self.assertEqual(self.browser(OWNER_A).get_json()["session"]["lease_id"], main["lease_id"])
        # Another owner is not affected by A's cap.
        self.assertEqual(self.browser(OWNER_B).status_code, 201)

    def test_cap_two_allows_two_and_refuses_the_third_until_one_is_released(self):
        orch.ORCH_BROWSER_SLOTS_PER_OWNER = 2
        self.assertEqual(self.browser(OWNER_A, slot="swarm-1").status_code, 201)
        self.assertEqual(self.browser(OWNER_A, slot="swarm-2").status_code, 201)
        r = self.browser(OWNER_A, slot="swarm-3")
        self.assertEqual((r.status_code, r.get_json()["code"], r.get_json()["cap"]), (429, "slots_exhausted", 2))
        self.assertEqual(sorted(r.get_json()["slots"]), ["swarm-1", "swarm-2"])
        # A held slot still renews under the cap.
        self.assertEqual(self.browser(OWNER_A, slot="swarm-1").status_code, 200)
        self.release(OWNER_A, slot="swarm-1")
        self.assertEqual(self.browser(OWNER_A, slot="swarm-3").status_code, 201)

    def test_global_pool_cap_still_applies(self):
        orch.ORCH_BROWSER_SLOTS_PER_OWNER = 4
        orch.ORCH_MAX_CHROME_INSTANCES = 2
        self.assertEqual(self.browser(OWNER_A, slot="swarm-1").status_code, 201)
        self.assertEqual(self.browser(OWNER_A, slot="swarm-2").status_code, 201)
        r = self.browser(OWNER_A, slot="swarm-3")
        self.assertEqual((r.status_code, r.get_json()["code"]), (409, "at_capacity"))


class AnotherOwner(SlotBase):
    def setUp(self):
        super().setUp()
        orch.ORCH_BROWSER_SLOTS_PER_OWNER = 2

    def test_cannot_release_renew_ticket_or_kill_a_slot_by_naming_it(self):
        a1 = self.browser(OWNER_A, slot="swarm-1").get_json()["session"]
        # B naming the same slot gets B's own slot, never A's.
        rb = self.browser(OWNER_B, slot="swarm-1")
        self.assertEqual(rb.status_code, 201)
        b1 = rb.get_json()["session"]
        self.assertNotEqual(b1["lease_id"], a1["lease_id"])
        self.assertNotEqual(b1["isolation_key"], a1["isolation_key"])
        self.assertNotEqual(b1["profile_dir"], a1["profile_dir"])
        # B renewing A's lease id: refused, not reassigned, holder not named.
        steal = self.browser(OWNER_B, lease_id=a1["lease_id"])
        self.assertEqual(steal.status_code, 403)
        self.assertNotIn(OWNER_A, steal.get_data(as_text=True))
        # B releasing A's lease id, or A's slot by name under B's own id.
        r = self.release(OWNER_B, lease_id=a1["lease_id"])
        self.assertEqual(r.status_code, 403, r.get_json())
        self.assertNotIn(OWNER_A, r.get_data(as_text=True))
        self.release(OWNER_B, slot="swarm-1")  # B's own slot goes; A's stays
        self.assertEqual(self.release(OWNER_B, slot="swarm-1").status_code, 404)
        self.assertIn(orch._browser_key(OWNER_A, "swarm-1"), orch._browser_sessions)
        # Tickets and the kill switch.
        t = self.client.post("/tickets", json={"owner_user_id": OWNER_B, "purpose": "browser", "lease_id": a1["lease_id"]},
                             headers=self.auth)
        self.assertEqual(t.status_code, 403)
        t = self.client.post("/tickets", json={"owner_user_id": OWNER_B, "purpose": "browser", "slot": "swarm-1"},
                             headers=self.auth)
        self.assertEqual(t.status_code, 404)
        k = self.client.post(f"/sessions/{OWNER_B}/kill", json={"purpose": "browser", "lease_id": a1["lease_id"]},
                             headers=self.auth)
        self.assertIn(k.status_code, (403, 404))
        self.assertEqual(self.runtime.stopped, [b1["lease_id"]])
        ok, _ = orch._lease_check({"kind": "browser", "owner": OWNER_B, "lease_id": a1["lease_id"]})
        self.assertFalse(ok, "B's ticket record cannot check out against A's slot")


class SlotProfilesAndContainers(unittest.TestCase):
    def setUp(self):
        self.root = os.path.join(tempfile.mkdtemp(prefix="orch-slotprof-"), "profiles")

    def test_slot_profile_is_a_sibling_0700_dir_and_deleted_with_the_owner(self):
        main = bp.ensure_profile_dir(self.root, "owner-a")
        s1 = bp.ensure_profile_dir(self.root, "owner-a", slot="swarm-1")
        other = bp.ensure_profile_dir(self.root, "owner-b", slot="swarm-1")
        self.assertEqual(main, bp.ensure_profile_dir(self.root, "owner-a", slot="main"))
        self.assertEqual(os.path.dirname(s1), self.root)
        self.assertNotEqual(s1, main)
        self.assertNotEqual(s1, other)
        self.assertEqual(oct(os.stat(s1).st_mode & 0o777), "0o700")
        with self.assertRaises(bp.LeaseError) as ctx:
            bp.profile_dir_for(self.root, "owner-a", slot="Bad Slot")
        self.assertEqual(ctx.exception.code, "bad_slot")
        self.assertTrue(bp.delete_profile_dir(self.root, "owner-a"))
        self.assertFalse(os.path.exists(main))
        self.assertFalse(os.path.exists(s1))
        self.assertTrue(os.path.exists(other), "deleting A's profiles never touches B's slot")

    def test_docker_container_name_is_per_slot_and_unchanged_for_main(self):
        sess = {"owner_token": "owner-a-1234abcd", "cdp_port": 19222, "lease_id": "bl-1",
                "profile_dir": "/var/lib/x/profiles/owner-a-1234abcd"}
        argv = bp.docker_run_argv(sess, image="chrome:fake", network="browsers")
        self.assertEqual(argv[argv.index("--name") + 1], "orch-chrome-owner-a-1234abcd")
        argv = bp.docker_run_argv({**sess, "slot": "main"}, image="chrome:fake", network="browsers")
        self.assertEqual(argv[argv.index("--name") + 1], "orch-chrome-owner-a-1234abcd")
        argv = bp.docker_run_argv({**sess, "slot": "swarm-1", "profile_dir": "/var/lib/x/profiles/owner-a-1234abcd.slot-swarm-1"},
                                  image="chrome:fake", network="browsers")
        self.assertEqual(argv[argv.index("--name") + 1], "orch-chrome-owner-a-1234abcd.swarm-1")
        self.assertIn("/var/lib/x/profiles/owner-a-1234abcd.slot-swarm-1:/data/profile:rw", argv)


class SlotsOnPerOwnerVolumes(SlotBase):
    """ORCH_BROWSER_PROFILE=volume: every slot of an owner lives on that owner's one volume."""

    def setUp(self):
        super().setUp()
        self.host = FakeVolumeHost()
        self.vol_root = os.path.join(self.tmp, "volumes")
        self.keyfile = os.path.join(self.tmp, "master.key")
        with open(self.keyfile, "wb") as fh:
            fh.write(os.urandom(32))  # fake, per run
        os.chmod(self.keyfile, 0o600)
        orch.ORCH_BROWSER_PROFILE = "volume"
        orch.ORCH_VOLUME_ROOT = self.vol_root
        orch.ORCH_VOLUME_MASTER_KEY_FILE = self.keyfile
        orch.ORCH_BROWSER_SLOTS_PER_OWNER = 3
        self.vm = uv.VolumeManager(self.vol_root, self.keyfile, phone_quota_gb=8, browser_quota_gb=1,
                                   host_min_free_gb=10, **self.host.hooks())
        orch.set_volume_manager(self.vm)

    def test_slots_share_the_owners_volume_and_the_last_release_closes_it(self):
        p = self.vm.paths(OWNER_A, uv.KIND_BROWSER)
        main = self.browser(OWNER_A).get_json()["session"]
        r = self.browser(OWNER_A, slot="swarm-1")
        self.assertEqual(r.status_code, 201, r.get_json())
        s1 = r.get_json()["session"]
        self.assertEqual(main["profile_dir"], p["mount"])
        self.assertTrue(s1["profile_dir"].startswith(p["mount"] + os.sep), "a slot's profile is on the owner's volume")
        self.assertNotEqual(s1["profile_dir"], main["profile_dir"])
        self.assertTrue(s1["profile_volume"])
        self.assertEqual(self.host.mounted_image(p["mount"]), p["image"])
        opens = sum(1 for v in self.host.verbs() if v == "cryptsetup open")
        self.assertEqual(self.release(OWNER_A).status_code, 200)
        self.assertTrue(self.host.is_mounted(p["mount"]), "releasing main keeps the volume open for the slot")
        self.assertEqual(self.release(OWNER_A, slot="swarm-1").status_code, 200)
        self.assertFalse(self.host.is_mounted(p["mount"]), "the last slot closes the volume")
        self.assertTrue(os.path.exists(p["image"]))
        self.assertEqual(sum(1 for v in self.host.verbs() if v == "cryptsetup open"), opens, "one open, not one per slot")
        # Delete refused while any slot is leased; allowed afterwards.
        self.browser(OWNER_A, slot="swarm-2")
        r = self.client.delete(f"/volumes/{OWNER_A}/browser-profile", json={"confirm": OWNER_A}, headers=self.auth)
        self.assertEqual(r.status_code, 409)
        self.release(OWNER_A, slot="swarm-2")
        r = self.client.delete(f"/volumes/{OWNER_A}/browser-profile", json={"confirm": OWNER_A}, headers=self.auth)
        self.assertEqual(r.status_code, 200)

    def test_another_owners_slot_opens_another_volume(self):
        self.browser(OWNER_A, slot="swarm-1")
        self.browser(OWNER_B, slot="swarm-1")
        pa, pb = self.vm.paths(OWNER_A, uv.KIND_BROWSER), self.vm.paths(OWNER_B, uv.KIND_BROWSER)
        self.assertEqual(self.host.mounted_image(pa["mount"]), pa["image"])
        self.assertEqual(self.host.mounted_image(pb["mount"]), pb["image"])
        self.assertNotEqual(pa["image"], pb["image"])


class TwoSlotsThroughTheRelay(unittest.TestCase):
    """Live orchestrator (tests/fixtures/isolation_harness.py): a cookie set in slot 1 is not in slot 2."""

    def setUp(self):
        from tests.fixtures.isolation_harness import Harness, cdp_call  # noqa: F401 - imported for the fixtures
        self.h = Harness()
        orch.ORCH_BROWSER_SLOTS_PER_OWNER = 3
        cdp_ws = self.h.cdp.ws_url
        # One fake browser per isolation key (the harness default keys them per owner).
        orch.set_chrome_runtime(bp.MockChromeRuntime(
            upstream_for=lambda sess: f"{cdp_ws}/devtools/browser/{sess['isolation_key'].replace('/', '.')}"))
        self._open = []

    def tearDown(self):
        for ws in self._open:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass
        orch.ORCH_BROWSER_SLOTS_PER_OWNER = 1
        self.h.close()

    def connect(self, lease):
        ws = self.h.cdp_connect(lease["relay"]["cdp_ws_url"], lease["ticket"])
        self._open.append(ws)
        return ws

    def test_slot_one_logs_in_and_slot_two_is_not_logged_in(self):
        from tests.fixtures.isolation_harness import OWNER_A as A, cdp_call
        main = self.h.browser(A).json()
        s1 = self.h.browser(A, slot="swarm-1").json()
        s2 = self.h.browser(A, slot="swarm-2").json()
        self.assertEqual(len({main["session"]["lease_id"], s1["session"]["lease_id"], s2["session"]["lease_id"]}), 3)
        self.assertNotEqual(s1["relay"]["cdp_ws_url"], s2["relay"]["cdp_ws_url"])
        w1, w2, wm = self.connect(s1), self.connect(s2), self.connect(main)
        cdp_call(w1, 1, "Network.setCookie", {"name": "session", "value": "sess-slot1-fake-0000"})
        names = lambda ws: {c["name"] for c in cdp_call(ws, 2, "Network.getAllCookies")["result"].get("cookies", [])}  # noqa: E731
        self.assertIn("session", names(w1))
        self.assertEqual(names(w2), set(), "slot 2 carries slot 1's session: crossover")
        self.assertEqual(names(wm), set(), "the main browser carries a slot's session: crossover")
        self.assertEqual(cdp_call(w1, 3, "Browser.getVersion")["result"]["browser"],
                         s1["session"]["isolation_key"].replace("/", "."))
        # Slot 1's ticket does not open slot 2's relay.
        with self.assertRaises(Exception):
            self._open.append(self.h.cdp_connect(s2["relay"]["cdp_ws_url"], s1["ticket"]))
        # Releasing slot 1 drops its stream and leaves slot 2 and main connected.
        import requests
        r = requests.delete(f"{self.h.base}/sessions/{A}?purpose=browser&slot=swarm-1", headers=self.h.headers(), timeout=5)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(cdp_call(w2, 4, "Browser.getVersion")["result"]["browser"],
                         s2["session"]["isolation_key"].replace("/", "."))
        listed = requests.get(f"{self.h.base}/sessions", headers=self.h.headers(), timeout=5).json()["browser_sessions"]
        self.assertEqual(sorted(s["slot"] for s in listed), ["main", "swarm-2"])
        text = json.dumps(listed)
        self.assertNotIn("cdp_port", text)
        self.assertNotIn("cdp_ws_internal", text)


if __name__ == "__main__":
    unittest.main()
