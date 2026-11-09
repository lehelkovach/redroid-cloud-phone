#!/usr/bin/env python3
"""Hibernate idle phone hosts and wake them on demand, against a fake OCI.

No network, no credentials, no real instance: the compute client, clock,
sleep, health probe and thread spawner are all fakes. Covers the brief:

- idle → stopped after N minutes (SOFTSTOP first, STOP on timeout);
- a host with an active lease is never stopped;
- a lease on a stopped host → START → RUNNING → healthy → ready, with a
  `waking` answer (ETA) meanwhile, also after an orchestrator restart;
- "Out of host capacity" → bounded retries → an honest failure;
- an empty allowlist or HIBERNATE=0 → nothing at all;
- protected names (personal projects, prod, the orchestrator) are never
  stopped or started, even when allowlisted;
- the orchestrator routes answer 503 `waking` with Retry-After.
"""

import types
import unittest

from orchestrator import hibernate as hib
from orchestrator import server as orch

PHONE = "ocid1.instance.oc1.phx.fakephonehost1"
PHONE2 = "ocid1.instance.oc1.phx.fakephonehost2"
ORCH_VM = "ocid1.instance.oc1.phx.fakeorchestrator"
PERSONAL = "ocid1.instance.oc1.phx.fakepersonal"
PROD = "ocid1.instance.oc1.phx.fakeprod"
COMPARTMENT = "ocid1.compartment.oc1..fakefakefake"
API = "http://10.0.1.250:8080"
MIN = 60.0


class Clock:
    def __init__(self, start=1_700_000_000.0):
        self.now = start
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class FakeServiceError(Exception):
    def __init__(self, status, code, message):
        super().__init__(f"{status} {code} {message}")
        self.status, self.code, self.message = status, code, message


def capacity_error():
    return FakeServiceError(500, "InternalError", "Out of host capacity.")


class FakeCompute:
    """Lifecycle per instance; actions move it one poll later, like OCI does."""

    def __init__(self, instances, clock):
        self.clock = clock
        self.inst = {o: types.SimpleNamespace(id=o, display_name=n, lifecycle_state=s)
                     for o, (n, s) in instances.items()}
        self.actions = []
        self.pending = {}          # ocid -> next lifecycle on the next get
        self.start_errors = []     # raised by START, one per call, in order
        self.softstop_hangs = False
        self.on_action = None

    def get_instance(self, ocid):
        i = self.inst[ocid]
        if ocid in self.pending:
            i.lifecycle_state = self.pending.pop(ocid)
        return types.SimpleNamespace(data=i)

    def instance_action(self, ocid, action):
        self.actions.append((ocid, action))
        if self.on_action:
            self.on_action(ocid, action)
        i = self.inst[ocid]
        if action == "START":
            if self.start_errors:
                raise self.start_errors.pop(0)
            i.lifecycle_state = "STARTING"
            self.pending[ocid] = "RUNNING"
        elif action == "SOFTSTOP":
            i.lifecycle_state = "STOPPING"
            if not self.softstop_hangs:
                self.pending[ocid] = "STOPPED"
        elif action == "STOP":
            i.lifecycle_state = "STOPPING"
            self.pending[ocid] = "STOPPED"
        else:  # TERMINATE or anything else must never be called
            raise AssertionError(f"unexpected action {action}")
        return types.SimpleNamespace(data=i)

    def list_instances(self, compartment_id, display_name=None):
        assert compartment_id == COMPARTMENT
        return types.SimpleNamespace(data=[i for i in self.inst.values()
                                           if display_name is None or i.display_name == display_name])

    def acted(self, ocid=None):
        return [a for o, a in self.actions if ocid is None or o == ocid]


class Deferred:
    """A thread spawner the test drives: nothing runs until run()."""

    def __init__(self):
        self.jobs = []

    def __call__(self, fn, name):
        self.jobs.append((name, fn))

    def run(self):
        jobs, self.jobs = self.jobs, []
        for _, fn in jobs:
            fn()
        return len(jobs)


def sync_spawn(fn, name):
    fn()


def config(**over):
    cfg = hib.config_from_env({})
    cfg.update({"allowlist": [PHONE], "_hostname": "test-runner-host"})
    cfg.update(over)
    return cfg


def healthy_http(calls=None):
    def get(url, timeout):
        if calls is not None:
            calls.append(url)
        return 200, {"status": "ok"}
    return get


class Base(unittest.TestCase):
    def make(self, instances=None, records=None, busy=None, spawn=sync_spawn, http_get=None, **cfg):
        self.clock = Clock()
        self.compute = FakeCompute(instances or {PHONE: ("cloud-phone-gapps-a1", "RUNNING")}, self.clock)
        self.records = records if records is not None else []
        self.busy_ids = set()
        self.h = hib.Hibernator(
            config=config(**cfg), compute=self.compute, records=lambda: self.records,
            busy=busy or (lambda rid: rid in self.busy_ids), clock=self.clock,
            sleep=self.clock.sleep, http_get=http_get or healthy_http(), spawn=spawn,
        )
        return self.h

    def record(self, ocid=PHONE, name="cloud-phone-gapps-a1", rid="rec1", api_url=API):
        rec = {"id": rid, "name": name, "instance_ocid": ocid, "api_url": api_url,
               "last_used": self.clock.now}
        self.records.append(rec)
        return rec


class OffByDefault(Base):
    def test_empty_allowlist_is_a_no_op_with_no_oci_call(self):
        def explode():
            raise AssertionError("no OCI client may be built with an empty allowlist")

        h = hib.Hibernator(config=hib.config_from_env({}), compute_factory=explode,
                           records=lambda: [{"id": "r", "name": "x", "instance_ocid": PHONE}])
        self.assertFalse(h.active)
        self.assertEqual(h.tick(), [])
        self.assertIsNone(h.ensure_awake({"id": "r", "name": "x", "instance_ocid": PHONE}))
        self.assertEqual(h.wake_host(PHONE)["status"], "not_managed")
        self.assertFalse(h.snapshot()["active"])

    def test_hibernate_zero_turns_it_off_even_with_an_allowlist(self):
        for var in ("HIBERNATE", "ORCH_HIBERNATE"):
            cfg = hib.config_from_env({var: "0", "ORCH_HIBERNATE_ALLOWLIST": PHONE})
            self.assertFalse(cfg["enabled"])
            h = hib.Hibernator(config=cfg, compute_factory=lambda: (_ for _ in ()).throw(AssertionError()))
            self.assertFalse(h.active)
            self.assertEqual(h.tick(), [])
            self.assertEqual(h.snapshot(), {"enabled": False})

    def test_env_parsing(self):
        cfg = hib.config_from_env({
            "ORCH_HIBERNATE_ALLOWLIST": f" {PHONE} , cloud-phone-agent-6c58 ,,",
            "HIBERNATE_IDLE_MIN": "15",
            "ORCH_HIBERNATE_HEALTH_URLS": "cloud-phone-agent-6c58=http://10.0.1.251:8080/",
        })
        self.assertEqual(cfg["allowlist"], [PHONE, "cloud-phone-agent-6c58"])
        self.assertEqual(cfg["idle_seconds"], 15 * MIN)
        self.assertEqual(cfg["wake_timeout"], 240)
        self.assertEqual(cfg["health_urls"], {"cloud-phone-agent-6c58": "http://10.0.1.251:8080"})


class IdleStop(Base):
    def test_idle_host_is_soft_stopped_after_ten_minutes(self):
        self.make()
        self.assertEqual(self.h.tick(), [])          # first sight: a full idle window starts now
        self.clock.now += 9 * MIN
        self.assertEqual(self.h.tick(), [])
        self.assertEqual(self.compute.actions, [])
        self.clock.now += 1 * MIN
        self.assertEqual(self.h.tick(), [PHONE])
        self.assertEqual(self.compute.acted(PHONE), ["SOFTSTOP"])
        self.assertEqual(self.compute.inst[PHONE].lifecycle_state, "STOPPED")
        self.assertEqual(self.h.snapshot()["hosts"][0]["state"], "stopped")
        self.clock.now += 30 * MIN
        self.assertEqual(self.h.tick(), [])           # stopped hosts are left alone
        self.assertEqual(self.compute.acted(PHONE), ["SOFTSTOP"])

    def test_softstop_that_hangs_escalates_to_stop(self):
        self.make()
        self.compute.softstop_hangs = True
        self.h.tick()
        self.clock.now += 10 * MIN
        self.h.tick()
        self.assertEqual(self.compute.acted(PHONE), ["SOFTSTOP", "STOP"])
        self.assertEqual(self.compute.inst[PHONE].lifecycle_state, "STOPPED")

    def test_idle_min_is_configurable(self):
        self.make(idle_seconds=3 * MIN)
        self.h.tick()
        self.clock.now += 3 * MIN
        self.assertEqual(self.h.tick(), [PHONE])

    def test_record_activity_resets_the_idle_window(self):
        self.make()
        rec = self.record()
        self.h.tick()
        self.clock.now += 8 * MIN
        rec["last_used"] = self.clock.now
        self.clock.now += 5 * MIN
        self.assertEqual(self.h.tick(), [])
        self.h.touch(rec)
        self.clock.now += 9 * MIN
        self.assertEqual(self.h.tick(), [])
        self.assertEqual(self.compute.actions, [])


class NeverStopInUse(Base):
    def test_active_lease_is_never_stopped(self):
        self.make()
        self.record()
        self.busy_ids.add("rec1")
        for _ in range(12):
            self.clock.now += 10 * MIN
            self.assertEqual(self.h.tick(), [])
        self.assertEqual(self.compute.actions, [])
        self.busy_ids.clear()                        # lease released: the window starts then
        self.clock.now += 9 * MIN
        self.assertEqual(self.h.tick(), [])
        self.clock.now += 2 * MIN
        self.assertEqual(self.h.tick(), [PHONE])

    def test_lease_checked_again_right_before_softstop(self):
        deferred = Deferred()
        self.make(spawn=deferred)
        self.record()
        self.h.tick()
        self.clock.now += 11 * MIN
        self.assertEqual(self.h.tick(), [PHONE])     # decided to stop ...
        self.busy_ids.add("rec1")                    # ... a lease lands before the stop runs
        deferred.run()
        self.assertEqual(self.compute.actions, [])
        self.assertEqual(self.compute.inst[PHONE].lifecycle_state, "RUNNING")

    def test_lease_during_softstop_cancels_the_escalation(self):
        self.make()
        self.record()
        self.compute.softstop_hangs = True
        self.compute.on_action = lambda o, a: a == "SOFTSTOP" and self.busy_ids.add("rec1")
        self.h.tick()
        self.clock.now += 10 * MIN
        self.h.tick()
        self.assertEqual(self.compute.acted(PHONE), ["SOFTSTOP"])  # no forced STOP

    def test_hold_from_wake_host_keeps_it_up_until_released(self):
        self.make()
        self.assertEqual(self.h.wake_host("cloud-phone-gapps-a1", hold_seconds=3600)["status"], "ready")
        self.clock.now += 50 * MIN
        self.assertEqual(self.h.tick(), [])
        self.assertTrue(self.h.release_hold(PHONE))
        self.clock.now += 10 * MIN
        self.assertEqual(self.h.tick(), [PHONE])

    def test_nothing_is_ever_terminated(self):
        self.make()
        self.h.tick()
        self.clock.now += 10 * MIN
        self.h.tick()
        self.assertNotIn("TERMINATE", self.compute.acted())


class Wake(Base):
    def stopped(self, **kw):
        self.make(instances={PHONE: ("cloud-phone-gapps-a1", "STOPPED")}, **kw)

    def test_lease_on_a_stopped_host_starts_waits_and_reports_waking(self):
        calls = []
        deferred = Deferred()
        statuses = iter([(503, None), (200, {"status": "booting"}), (200, {"status": "ok"})])

        def http_get(url, timeout):
            calls.append(url)
            return next(statuses)

        self.stopped(spawn=deferred, http_get=http_get)
        rec = self.record()
        first = self.h.ensure_awake(rec)
        self.assertEqual(first["status"], "waking")
        self.assertEqual(first["code"], "waking")
        self.assertEqual(first["http_status"], 503)
        self.assertEqual(first["eta_seconds"], 150)
        self.assertGreaterEqual(first["retry_after"], 5)
        self.assertEqual(first["message"], "Waking the cloud phone, about 2 min")
        self.assertEqual(self.h.ensure_awake(rec)["status"], "waking")   # one wake, not two
        self.assertEqual(deferred.run(), 1)
        self.assertEqual(self.compute.acted(PHONE), ["START"])
        self.assertEqual(calls, [f"{API}/health"] * 3)
        self.assertIsNone(self.h.ensure_awake(rec))                      # ready
        self.assertEqual(self.h.snapshot()["hosts"][0]["state"], "running")

    def test_wake_after_orchestrator_restart_reads_oci_lifecycle(self):
        self.stopped()
        rec = self.record()
        # A brand-new Hibernator (empty memory) on the same fake OCI.
        h2 = hib.Hibernator(config=config(), compute=self.compute, records=lambda: [rec],
                            busy=lambda rid: False, clock=self.clock, sleep=self.clock.sleep,
                            http_get=healthy_http(), spawn=sync_spawn)
        self.assertIsNone(h2.ensure_awake(rec))
        self.assertEqual(self.compute.acted(PHONE), ["START"])
        self.assertEqual(self.compute.inst[PHONE].lifecycle_state, "RUNNING")

    def test_running_host_needs_no_wake_and_no_action(self):
        self.make()
        rec = self.record()
        self.assertIsNone(self.h.ensure_awake(rec))
        self.assertEqual(self.compute.actions, [])

    def test_lease_while_stopping_waits_then_starts(self):
        self.make(instances={PHONE: ("cloud-phone-gapps-a1", "STOPPING")})
        self.compute.pending[PHONE] = "STOPPED"
        rec = self.record()
        self.assertIsNone(self.h.ensure_awake(rec))
        self.assertEqual(self.compute.acted(PHONE), ["START"])

    def test_unhealthy_after_wake_is_honest_and_still_stopped_later(self):
        self.stopped(http_get=lambda url, timeout: (503, None))
        rec = self.record()
        res = self.h.ensure_awake(rec)
        self.assertEqual(res["status"], "wake_failed")
        self.assertEqual(res["code"], "oci_unhealthy")
        self.assertIn("did not report healthy", res["message"])
        # Running but unusable still bills: the idle loop must stop it later.
        self.clock.now += 11 * MIN
        self.assertEqual(self.h.tick(), [PHONE])
        self.assertEqual(self.compute.acted(PHONE), ["START", "SOFTSTOP"])

    def test_wake_host_by_name_resolves_through_oci(self):
        self.make(instances={PHONE: ("cloud-phone-gapps-a1", "STOPPED"),
                             PHONE2: ("cloud-phone-agent-6c58", "RUNNING")},
                  allowlist=["cloud-phone-agent-6c58", "cloud-phone-gapps-a1"],
                  compartment_id=COMPARTMENT,
                  health_urls={"cloud-phone-gapps-a1": API})
        res = self.h.wake_host("cloud-phone-gapps-a1", hold_seconds=600, owner="avery")
        self.assertEqual(res["status"], "ready")
        self.assertEqual(self.compute.acted(PHONE), ["START"])
        self.assertEqual(self.compute.acted(PHONE2), [])
        self.assertEqual(self.h.wake_host("not-allowlisted")["status"], "not_managed")


class Capacity(Base):
    def test_capacity_retries_then_honest_failure(self):
        self.make(instances={PHONE: ("cloud-phone-gapps-a1", "STOPPED")})
        self.compute.start_errors = [capacity_error() for _ in range(4)]
        rec = self.record()
        res = self.h.ensure_awake(rec)
        self.assertEqual(res["status"], "wake_failed")
        self.assertEqual(res["code"], "oci_capacity")
        self.assertEqual(res["http_status"], 503)
        self.assertIn("no free capacity", res["message"])
        self.assertIn("still stopped", res["message"])
        self.assertIn("tried 4 times", res["message"])
        self.assertEqual(self.compute.acted(PHONE), ["START"] * 4)        # 1 + 3 retries, bounded
        self.assertEqual([s for s in self.clock.sleeps if s >= 20], [20, 40, 80])
        # Said once; the next request tries again (and this time OCI has room).
        self.assertIsNone(self.h.ensure_awake(rec))
        self.assertEqual(self.compute.acted(PHONE), ["START"] * 5)

    def test_capacity_then_room_succeeds_within_the_retries(self):
        self.make(instances={PHONE: ("cloud-phone-gapps-a1", "STOPPED")})
        self.compute.start_errors = [capacity_error(), capacity_error()]
        self.assertIsNone(self.h.ensure_awake(self.record()))
        self.assertEqual(self.compute.acted(PHONE), ["START"] * 3)

    def test_other_oci_errors_are_not_retried(self):
        self.make(instances={PHONE: ("cloud-phone-gapps-a1", "STOPPED")})
        self.compute.start_errors = [FakeServiceError(404, "NotAuthorizedOrNotFound", "no such instance")]
        res = self.h.ensure_awake(self.record())
        self.assertEqual(res["code"], "oci_error")
        self.assertIn("NotAuthorizedOrNotFound", res["message"])
        self.assertEqual(self.compute.acted(PHONE), ["START"])


class Protected(Base):
    def test_protected_names_are_never_stopped_even_when_allowlisted(self):
        instances = {
            ORCH_VM: ("cloud-phone-orch-6c58", "RUNNING"),
            PERSONAL: ("omnistream-relay", "RUNNING"),
            PROD: ("ksg-main-eb91", "RUNNING"),
            PHONE: ("cloud-phone-gapps-a1", "RUNNING"),
        }
        self.make(instances=instances, allowlist=list(instances), compartment_id=COMPARTMENT)
        self.h.tick()
        self.clock.now += 60 * MIN
        self.h.tick()
        self.assertEqual(self.compute.acted(), ["SOFTSTOP"])
        self.assertEqual(self.compute.acted(PHONE), ["SOFTSTOP"])
        for ocid in (ORCH_VM, PERSONAL, PROD):
            self.assertEqual(self.compute.inst[ocid].lifecycle_state, "RUNNING")
        names = [h["name"] for h in self.h.snapshot()["hosts"]]
        self.assertEqual(names, ["cloud-phone-gapps-a1"])

    def test_protected_names_are_never_started(self):
        self.make(instances={PERSONAL: ("OmniStream-box", "STOPPED")}, allowlist=[PERSONAL])
        rec = self.record(ocid=PERSONAL, name="OmniStream-box")
        self.assertIsNone(self.h.ensure_awake(rec))
        self.assertEqual(self.compute.actions, [])

    def test_live_display_name_is_checked_before_acting(self):
        # The record and allowlist say a phone; OCI says it is prod now.
        self.make(instances={PROD: ("ksg-main-eb91", "RUNNING")}, allowlist=[PROD])
        self.h._host(PROD, "cloud-phone-old")
        self.clock.now += 20 * MIN
        self.h.tick()
        self.assertEqual(self.compute.actions, [])

    def test_self_ocid_and_hostname_are_protected(self):
        self.make(instances={ORCH_VM: ("orch-box", "RUNNING"), PHONE: ("my-host", "RUNNING")},
                  allowlist=[ORCH_VM, PHONE], self_ocid=ORCH_VM, _hostname="my-host")
        self.h.tick()
        self.clock.now += 60 * MIN
        self.h.tick()
        self.assertEqual(self.compute.actions, [])

    def test_denylist_is_in_code(self):
        for name in ("omnistream", "my-omnistream-2", "ksg-main-eb91", "cloud-phone-orch-6c58"):
            self.assertTrue(hib.is_protected(name), name)
        self.assertFalse(hib.is_protected("cloud-phone-gapps-a1"))
        self.assertFalse(hib.is_protected("cloud-phone-agent-6c58"))


class ServerWiring(unittest.TestCase):
    """The orchestrator routes: 503 `waking` + Retry-After, then ready; /health; /hosts."""

    def setUp(self):
        orch.ORCH_DEPLOY_MODE = "mock"
        orch.ORCH_MAX_INSTANCES = 2
        orch.ORCH_API_TOKEN = ""
        orch._instances.clear()
        orch._leases.clear()
        orch._user_sessions.clear()
        self.clock = Clock()
        self.compute = FakeCompute({PHONE: ("cloud-phone-gapps-a1", "STOPPED")}, self.clock)
        self.deferred = Deferred()
        self.h = hib.Hibernator(
            config=config(), compute=self.compute, records=orch._hibernate_records,
            busy=orch._hibernate_busy, clock=self.clock, sleep=self.clock.sleep,
            http_get=healthy_http(), spawn=self.deferred,
        )
        orch.set_hibernator(self.h)
        self.rec = orch._create_instance_record(API, "cloud-phone-gapps-a1", extra={"instance_ocid": PHONE})
        self.client = orch.app.test_client()

    def tearDown(self):
        orch.set_hibernator(None)
        orch._instances.clear()
        orch._leases.clear()
        orch._user_sessions.clear()

    def test_session_on_a_hibernated_host_answers_waking_then_ready(self):
        r = self.client.post("/sessions", json={"owner_user_id": "avery", "ttl_seconds": 600})
        self.assertEqual(r.status_code, 503, r.get_data(as_text=True))
        body = r.get_json()
        self.assertEqual(body["status"], "waking")
        self.assertEqual(body["code"], "waking")
        self.assertEqual(body["instance_id"], self.rec["id"])
        self.assertIn("about", body["error"])
        self.assertEqual(r.headers["Retry-After"], str(body["retry_after"]))
        self.assertNotIn("instance_ocid", body)
        self.assertTrue(orch._is_lease_valid(self.rec["id"], owner="avery"))  # held while waking
        self.deferred.run()
        r2 = self.client.post("/sessions", json={"owner_user_id": "avery", "ttl_seconds": 600})
        self.assertEqual(r2.status_code, 200, r2.get_data(as_text=True))
        self.assertEqual(r2.get_json()["session"]["instance_id"], self.rec["id"])

    def test_capacity_failure_is_said_plainly(self):
        self.compute.start_errors = [capacity_error() for _ in range(4)]
        self.client.post("/sessions", json={"owner_user_id": "avery"})
        self.deferred.run()
        r = self.client.post("/sessions", json={"owner_user_id": "avery"})
        self.assertEqual(r.status_code, 503)
        body = r.get_json()
        self.assertEqual(body["code"], "oci_capacity")
        self.assertIn("no free capacity", body["error"])

    def test_raw_lease_waits_too_and_the_same_owner_may_poll(self):
        r = self.client.post(f"/instances/{self.rec['id']}/lease", json={"owner": "avery", "ttl_seconds": 60})
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.get_json()["status"], "waking")
        other = self.client.post(f"/instances/{self.rec['id']}/lease", json={"owner": "jordan", "ttl_seconds": 60})
        self.assertEqual(other.status_code, 409)
        self.deferred.run()
        again = self.client.post(f"/instances/{self.rec['id']}/lease", json={"owner": "avery", "ttl_seconds": 60})
        self.assertEqual(again.status_code, 200, again.get_data(as_text=True))

    def test_phone_routes_wake_a_sleeping_host(self):
        r = self.client.get(f"/phones/{self.rec['id']}/status")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.get_json()["status"], "waking")

    def test_active_lease_blocks_the_idle_loop(self):
        self.compute.inst[PHONE].lifecycle_state = "RUNNING"
        orch._set_lease(self.rec["id"], "avery", 7200)
        self.h.tick()
        self.clock.now += 60 * MIN
        self.assertEqual(self.h.tick(), [])
        self.assertEqual(self.compute.actions, [])

    def test_health_and_hosts_report_state_without_ocids(self):
        self.client.post("/sessions", json={"owner_user_id": "avery"})
        health = self.client.get("/health").get_json()
        self.assertTrue(health["hibernate"]["active"])
        self.assertEqual(health["hibernate"]["hosts"][0]["state"], "waking")
        self.assertNotIn(PHONE, str(health))
        r = self.client.post("/hosts/cloud-phone-gapps-a1/wake", json={"hold_seconds": 600})
        self.assertEqual(r.status_code, 503)
        self.deferred.run()
        r = self.client.post("/hosts/cloud-phone-gapps-a1/wake", json={"hold_seconds": 600})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["status"], "ready")
        self.assertEqual(self.client.delete("/hosts/cloud-phone-gapps-a1/hold").status_code, 200)
        self.assertEqual(self.client.post("/hosts/omnistream/wake").status_code, 404)

    def test_default_server_hibernator_is_inactive(self):
        orch.set_hibernator(None)
        self.assertFalse(orch.hibernator().active)
        r = self.client.post("/sessions", json={"owner_user_id": "avery"})
        self.assertIn(r.status_code, (200, 201), r.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
