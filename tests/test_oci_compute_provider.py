#!/usr/bin/env python3
"""OciComputeProvider against a fake `oci` SDK: no network, no credentials.

Covers the launch payload (shape, image, subnet, tags, no secrets), both hard
caps, terminate, boot-time reconcile of orphans, the bounded health wait,
instance-principal vs config-file auth selection, honest OCI errors, and the
orchestrator wiring behind ORCH_PHONE_PROVIDER=oci (default off: untouched).
"""

import os
import tempfile
import types
import unittest

from orchestrator import server as orch
from orchestrator.providers import oci_compute as ocp

FAKE_IMAGE = "ocid1.image.oc1.phx.fakefakefake"
FAKE_SUBNET = "ocid1.subnet.oc1.phx.fakefakefake"
FAKE_COMPARTMENT = "ocid1.compartment.oc1..fakefakefake"
FAKE_SECRET = "not-a-real-token-but-must-never-appear"


def base_config(**over):
    cfg = {
        "image_id": FAKE_IMAGE, "subnet_id": FAKE_SUBNET, "compartment_id": FAKE_COMPARTMENT,
        "availability_domain": "fake:PHX-AD-1", "ocpus": 2.0, "memory_gb": 12.0,
        "max_instances": 2, "max_launches_per_hour": 4, "running_timeout": 60,
        "health_timeout": 60, "poll_seconds": 1, "orphan_ttl": 7200, "name_prefix": "oslo-phone",
        "auth": "auto", "config_file": "/nonexistent/oci/config", "profile": "DEFAULT", "api_port": 8080,
    }
    cfg.update(over)
    return cfg


class Clock:
    def __init__(self, start=1_700_000_000.0):
        self.now = start

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeServiceError(Exception):
    def __init__(self, status, code, message):
        super().__init__(f"{status} {code} {message}")
        self.status, self.code, self.message = status, code, message


class _Model:
    """Keyword-only model like the SDK's: attributes are the fields."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeInstance(_Model):
    pass


class FakeCompute:
    def __init__(self, world):
        self.world = world
        self.launches = []
        self.terminates = []
        self.get_calls = 0

    def launch_instance(self, details):
        self.launches.append(details)
        if self.world.get("launch_error"):
            raise self.world["launch_error"]
        ocid = f"ocid1.instance.oc1.phx.fake{len(self.launches)}"
        self.world.setdefault("instances", {})[ocid] = FakeInstance(
            id=ocid, display_name=details.display_name, lifecycle_state="PROVISIONING",
            freeform_tags=dict(details.freeform_tags),
        )
        self.world.setdefault("state_plan", {})[ocid] = list(self.world.get("plan", ["PROVISIONING", "RUNNING"]))
        return types.SimpleNamespace(data=self.world["instances"][ocid])

    def get_instance(self, ocid):
        self.get_calls += 1
        inst = self.world["instances"][ocid]
        plan = self.world["state_plan"].get(ocid) or []
        if plan:
            inst.lifecycle_state = plan.pop(0)
        return types.SimpleNamespace(data=inst)

    def list_vnic_attachments(self, compartment_id, instance_id):
        assert compartment_id == FAKE_COMPARTMENT
        return types.SimpleNamespace(data=[_Model(vnic_id=f"vnic-{instance_id}", lifecycle_state="ATTACHED")])

    def terminate_instance(self, ocid, preserve_boot_volume=None):
        self.terminates.append((ocid, preserve_boot_volume))
        if self.world.get("terminate_error"):
            raise self.world["terminate_error"]
        inst = self.world.get("instances", {}).get(ocid)
        if inst is not None:
            inst.lifecycle_state = "TERMINATING"

    def list_instances(self, compartment_id):
        assert compartment_id == FAKE_COMPARTMENT
        return types.SimpleNamespace(data=list(self.world.get("instances", {}).values()))


class FakeVnet:
    def __init__(self, world):
        self.world = world

    def get_vnic(self, vnic_id):
        ip = self.world.get("private_ip", "10.0.1.200")
        return types.SimpleNamespace(data=_Model(id=vnic_id, private_ip=ip))


class FakeIdentity:
    def list_availability_domains(self, compartment_id):
        return types.SimpleNamespace(data=[_Model(name="fake:PHX-AD-1")])


def make_sdk(world, instance_principal_ok=True):
    """A module-shaped fake with the pieces the provider touches."""
    sdk = types.SimpleNamespace()
    sdk.world = world
    sdk.exceptions = types.SimpleNamespace(ServiceError=FakeServiceError)
    sdk.core = types.SimpleNamespace()
    sdk.core.models = types.SimpleNamespace(
        LaunchInstanceDetails=_Model, LaunchInstanceShapeConfigDetails=_Model,
        InstanceSourceViaImageDetails=_Model, CreateVnicDetails=_Model,
    )
    world["clients"] = []

    def compute(cfg, signer=None):
        c = FakeCompute(world)
        world["clients"].append(("compute", cfg, signer))
        world["compute"] = c
        return c

    sdk.core.ComputeClient = compute
    sdk.core.VirtualNetworkClient = lambda cfg, signer=None: FakeVnet(world)
    sdk.identity = types.SimpleNamespace(IdentityClient=lambda cfg, signer=None: FakeIdentity())

    class Signer:
        region = "us-phoenix-1"

        def __init__(self):
            if not instance_principal_ok:
                raise RuntimeError("no metadata service here")

    sdk.auth = types.SimpleNamespace(signers=types.SimpleNamespace(InstancePrincipalsSecurityTokenSigner=Signer))
    sdk.config = types.SimpleNamespace(
        from_file=lambda file_location, profile_name: {"region": "us-phoenix-1", "profile": profile_name,
                                                        "key_file": FAKE_SECRET},
    )
    return sdk


def healthy(url, timeout):
    return 200, {"status": "ok"}


def make_provider(world=None, config=None, http_get=healthy, clock=None, **sdk_kw):
    world = world if world is not None else {}
    clock = clock or Clock()
    sdk = make_sdk(world, **sdk_kw)
    p = ocp.OciComputeProvider(config=config or base_config(), sdk=sdk, clock=clock, sleep=clock.sleep,
                               http_get=http_get)
    return p, world, clock


class LaunchPayloadTests(unittest.TestCase):
    def test_launch_payload_shape_image_subnet_tags(self):
        p, world, clock = make_provider()
        rec = p.launch("alice@example.test")
        details = world["compute"].launches[0]
        self.assertEqual(details.shape, "VM.Standard.A1.Flex")
        self.assertEqual(details.shape_config.ocpus, 2.0)
        self.assertEqual(details.shape_config.memory_in_gbs, 12.0)
        self.assertEqual(details.source_details.image_id, FAKE_IMAGE)
        self.assertEqual(details.source_details.source_type, "image")
        self.assertEqual(details.create_vnic_details.subnet_id, FAKE_SUBNET)
        self.assertFalse(details.create_vnic_details.assign_public_ip)
        self.assertEqual(details.compartment_id, FAKE_COMPARTMENT)
        self.assertEqual(details.availability_domain, "fake:PHX-AD-1")
        self.assertEqual(details.freeform_tags["oslo-role"], "phone")
        self.assertEqual(details.freeform_tags["oslo-owner"], "alice-example.test")
        self.assertRegex(details.freeform_tags["oslo-created"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        # The session shape the orchestrator stores: private IP, port 8080.
        self.assertEqual(rec["api_url"], "http://10.0.1.200:8080")
        self.assertEqual(rec["private_ip"], "10.0.1.200")
        self.assertEqual(rec["instance_ocid"], "ocid1.instance.oc1.phx.fake1")
        self.assertTrue(rec["name"].startswith("oslo-phone-alice-example.test-"))
        self.assertEqual(rec["auth_mode"], "instance_principal")
        self.assertEqual(p.caps()["live"], 1)
        self.assertEqual(p.caps()["launches_last_hour"], 1)

    def test_payload_and_record_carry_no_secret(self):
        p, world, _ = make_provider(instance_principal_ok=False, config=base_config(config_file=self._cfg()))
        rec = p.launch("bob")
        blob = repr(vars(world["compute"].launches[0])) + repr(rec)
        self.assertNotIn(FAKE_SECRET, blob)
        self.assertNotIn("key_file", blob)

    def test_custom_ocpus_memory(self):
        p, world, _ = make_provider(config=base_config(ocpus=4, memory_gb=24))
        p.launch("c")
        d = world["compute"].launches[0]
        self.assertEqual((d.shape_config.ocpus, d.shape_config.memory_in_gbs), (4, 24))

    def test_availability_domain_looked_up_when_unset(self):
        p, world, _ = make_provider(config=base_config(availability_domain=""))
        p.launch("d")
        self.assertEqual(world["compute"].launches[0].availability_domain, "fake:PHX-AD-1")

    def _cfg(self):
        f = tempfile.NamedTemporaryFile("w", suffix=".cfg", delete=False)
        f.write("[DEFAULT]\n")
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name


class CapTests(unittest.TestCase):
    def test_max_instances_refused_before_any_call(self):
        p, world, _ = make_provider(config=base_config(max_instances=1))
        p.launch("a")
        with self.assertRaises(ocp.ProviderError) as ctx:
            p.launch("b")
        self.assertEqual(ctx.exception.code, "oci_cap")
        self.assertEqual(ctx.exception.status, 429)
        self.assertEqual(ctx.exception.extra["live"], 1)
        self.assertEqual(len(world["compute"].launches), 1, "no second API call")

    def test_launch_rate_cap_counts_attempts_including_failures(self):
        world = {"launch_error": FakeServiceError(500, "InternalError", "boom")}
        p, world, clock = make_provider(world=world, config=base_config(max_launches_per_hour=2, max_instances=5))
        for _ in range(2):
            with self.assertRaises(ocp.ProviderError) as ctx:
                p.launch("a")
            self.assertEqual(ctx.exception.code, "oci_error")
        with self.assertRaises(ocp.ProviderError) as ctx:
            p.launch("a")
        self.assertEqual(ctx.exception.code, "oci_cap")
        self.assertEqual(len(world["compute"].launches), 2, "the cap stopped the third call")
        # An hour later the budget is back.
        clock.now += 3601
        world["launch_error"] = None
        p.launch("a")
        self.assertEqual(len(world["compute"].launches), 3)

    def test_terminate_frees_a_slot(self):
        p, world, _ = make_provider(config=base_config(max_instances=1, max_launches_per_hour=10))
        rec = p.launch("a")
        p.terminate(rec["instance_ocid"])
        self.assertEqual(world["compute"].terminates, [(rec["instance_ocid"], False)])
        self.assertEqual(p.caps()["live"], 0)
        p.launch("b")

    def test_caps_before_sdk_is_even_imported(self):
        p = ocp.OciComputeProvider(config=base_config(max_instances=1), sdk=None, clock=Clock(), sleep=lambda s: None)
        p._live["x"] = {}
        with self.assertRaises(ocp.ProviderError) as ctx:
            p.launch("a")
        self.assertEqual(ctx.exception.code, "oci_cap")
        self.assertIsNone(p._sdk)

    def test_config_refusals(self):
        self.assertIn("ORCH_OCI_IMAGE_ID", ocp.validate(base_config(image_id="")))
        self.assertIn("ORCH_OCI_SUBNET_ID", ocp.validate(base_config(subnet_id="")))
        self.assertIn("ORCH_OCI_COMPARTMENT_ID", ocp.validate(base_config(compartment_id="")))
        self.assertIn("MAX_INSTANCES", ocp.validate(base_config(max_instances=0)))
        self.assertIn("MAX_LAUNCHES", ocp.validate(base_config(max_launches_per_hour=0)))
        self.assertIsNone(ocp.validate(base_config()))
        with self.assertRaises(ocp.ProviderError) as ctx:
            ocp.OciComputeProvider(config=base_config(image_id=""), sdk=make_sdk({}))
        self.assertEqual(ctx.exception.code, "oci_config")

    def test_config_from_env_defaults(self):
        env = {"ORCH_OCI_IMAGE_ID": FAKE_IMAGE, "ORCH_OCI_SUBNET_ID": FAKE_SUBNET,
               "ORCH_OCI_COMPARTMENT_ID": FAKE_COMPARTMENT}
        cfg = ocp.config_from_env(env)
        self.assertEqual((cfg["ocpus"], cfg["memory_gb"]), (2.0, 12.0))
        self.assertEqual((cfg["max_instances"], cfg["max_launches_per_hour"]), (2, 4))
        self.assertEqual(cfg["auth"], "auto")
        with self.assertRaises(ocp.ProviderError):
            ocp.config_from_env({**env, "ORCH_OCI_MAX_INSTANCES": "two"})


class WaitTests(unittest.TestCase):
    def test_health_wait_gives_up_and_terminates(self):
        calls = []

        def never_healthy(url, timeout):
            calls.append(url)
            raise ConnectionError("refused")

        p, world, clock = make_provider(http_get=never_healthy, config=base_config(health_timeout=10, poll_seconds=5))
        with self.assertRaises(ocp.ProviderError) as ctx:
            p.launch("a")
        exc = ctx.exception
        self.assertEqual(exc.code, "oci_unhealthy")
        self.assertEqual(exc.status, 504)
        self.assertTrue(exc.extra["terminated"])
        self.assertEqual(exc.extra["instance_ocid"], "ocid1.instance.oc1.phx.fake1")
        self.assertIn("terminated", str(exc))
        self.assertEqual(world["compute"].terminates, [("ocid1.instance.oc1.phx.fake1", False)])
        self.assertEqual(p.caps()["live"], 0)
        self.assertTrue(all(u == "http://10.0.1.200:8080/health" for u in calls))
        self.assertLessEqual(len(calls), 4, "bounded, not a loop")

    def test_health_wait_reports_unhealthy_status_body(self):
        def degraded(url, timeout):
            return 200, {"status": "degraded"}

        p, _, _ = make_provider(http_get=degraded, config=base_config(health_timeout=3, poll_seconds=2))
        with self.assertRaises(ocp.ProviderError) as ctx:
            p.launch("a")
        self.assertIn("degraded", str(ctx.exception))

    def test_running_wait_gives_up(self):
        world = {"plan": ["PROVISIONING"] * 50}
        p, world, _ = make_provider(world=world, config=base_config(running_timeout=10, poll_seconds=5))
        with self.assertRaises(ocp.ProviderError) as ctx:
            p.launch("a")
        self.assertEqual(ctx.exception.code, "oci_timeout")
        self.assertEqual(ctx.exception.extra["lifecycle_state"], "PROVISIONING")
        self.assertTrue(ctx.exception.extra["terminated"])

    def test_instance_dying_during_boot_is_an_error_not_a_wait(self):
        world = {"plan": ["PROVISIONING", "TERMINATED"]}
        p, world, _ = make_provider(world=world)
        with self.assertRaises(ocp.ProviderError) as ctx:
            p.launch("a")
        self.assertEqual(ctx.exception.code, "oci_error")
        self.assertEqual(ctx.exception.extra["lifecycle_state"], "TERMINATED")

    def test_failed_termination_after_failed_launch_is_reported(self):
        def never(url, timeout):
            raise ConnectionError("refused")

        world = {"terminate_error": FakeServiceError(409, "IncorrectState", "busy")}
        p, world, _ = make_provider(world=world, http_get=never, config=base_config(health_timeout=1, poll_seconds=1))
        with self.assertRaises(ocp.ProviderError) as ctx:
            p.launch("a")
        self.assertFalse(ctx.exception.extra["terminated"])
        self.assertIn("NOT terminated", str(ctx.exception))


class ErrorTests(unittest.TestCase):
    def test_service_error_surfaces_code_and_is_not_retried(self):
        world = {"launch_error": FakeServiceError(429, "TooManyRequests", "slow down")}
        p, world, _ = make_provider(world=world)
        with self.assertRaises(ocp.ProviderError) as ctx:
            p.launch("a")
        exc = ctx.exception
        self.assertEqual(exc.code, "oci_error")
        self.assertEqual(exc.status, 502)
        self.assertEqual(exc.extra["service_code"], "TooManyRequests")
        self.assertEqual(exc.extra["service_status"], 429)
        self.assertEqual(len(world["compute"].launches), 1)
        self.assertEqual(p.caps()["live"], 0)

    def test_terminate_error_surfaces(self):
        world = {"terminate_error": FakeServiceError(404, "NotAuthorizedOrNotFound", "gone")}
        p, world, _ = make_provider(world=world)
        with self.assertRaises(ocp.ProviderError) as ctx:
            p.terminate("ocid1.instance.oc1.phx.nothere")
        self.assertEqual(ctx.exception.extra["service_code"], "NotAuthorizedOrNotFound")
        with self.assertRaises(ocp.ProviderError):
            p.terminate("")

    def test_missing_sdk_is_a_config_error(self):
        import builtins
        real_import = builtins.__import__

        def no_oci(name, *a, **k):
            if name == "oci":
                raise ImportError("no module named oci")
            return real_import(name, *a, **k)

        p = ocp.OciComputeProvider(config=base_config(), sdk=None, clock=Clock(), sleep=lambda s: None)
        builtins.__import__ = no_oci
        try:
            with self.assertRaises(ocp.ProviderError) as ctx:
                p.launch("a")
        finally:
            builtins.__import__ = real_import
        self.assertEqual(ctx.exception.code, "oci_sdk_missing")


class AuthSelectionTests(unittest.TestCase):
    def test_instance_principal_preferred(self):
        p, world, _ = make_provider()
        p.clients()
        self.assertEqual(p.auth_mode, "instance_principal")
        kind, cfg, signer = world["clients"][0]
        self.assertIsNotNone(signer)
        self.assertNotIn("key_file", cfg)

    def test_falls_back_to_config_file_on_a_dev_box(self):
        f = tempfile.NamedTemporaryFile("w", suffix=".cfg", delete=False)
        f.write("[DEFAULT]\n")
        f.close()
        self.addCleanup(os.unlink, f.name)
        p, world, _ = make_provider(instance_principal_ok=False, config=base_config(config_file=f.name, profile="DEV"))
        p.clients()
        self.assertEqual(p.auth_mode, "config")
        kind, cfg, signer = world["clients"][0]
        self.assertIsNone(signer)
        self.assertEqual(cfg["profile"], "DEV")

    def test_no_principal_and_no_config_file_is_honest(self):
        p, _, _ = make_provider(instance_principal_ok=False)
        with self.assertRaises(ocp.ProviderError) as ctx:
            p.clients()
        self.assertEqual(ctx.exception.code, "oci_auth")
        self.assertIn("instance principal also failed", str(ctx.exception))

    def test_forced_instance_principal_does_not_fall_back(self):
        p, _, _ = make_provider(instance_principal_ok=False, config=base_config(auth="instance_principal"))
        with self.assertRaises(ocp.ProviderError) as ctx:
            p.clients()
        self.assertEqual(ctx.exception.code, "oci_auth")
        self.assertIn("instance principal unavailable", str(ctx.exception))


class ReconcileTests(unittest.TestCase):
    def _world_with(self, clock, *phones):
        world = {"instances": {}}
        for ocid, name, age, tags in phones:
            created = ocp._iso_now(lambda: clock.now - age) if age is not None else None
            ft = dict(tags)
            if created:
                ft["oslo-created"] = created
            world["instances"][ocid] = FakeInstance(id=ocid, display_name=name, lifecycle_state="RUNNING",
                                                    freeform_tags=ft)
        return world

    def test_orphans_past_ttl_terminated_known_adopted_young_kept(self):
        clock = Clock()
        phone = {"oslo-role": "phone", "oslo-owner": "x"}
        world = self._world_with(
            clock,
            ("ocid1.instance.oc1.phx.known", "known", 10_000, phone),
            ("ocid1.instance.oc1.phx.old", "orphan-old", 10_000, phone),
            ("ocid1.instance.oc1.phx.young", "orphan-young", 60, phone),
            ("ocid1.instance.oc1.phx.notours", "ksg-main", 10_000, {}),
            ("ocid1.instance.oc1.phx.undated", "orphan-undated", None, phone),
        )
        p, world, clock = make_provider(world=world, clock=clock)
        res = p.reconcile(["ocid1.instance.oc1.phx.known"])
        self.assertEqual(res["adopted"], ["ocid1.instance.oc1.phx.known"])
        self.assertEqual(sorted(res["terminated"]), ["ocid1.instance.oc1.phx.old", "ocid1.instance.oc1.phx.undated"])
        self.assertEqual(res["kept"], ["ocid1.instance.oc1.phx.young"])
        terminated = [t[0] for t in world["compute"].terminates]
        self.assertNotIn("ocid1.instance.oc1.phx.notours", terminated, "untagged instances are never touched")
        self.assertEqual(p.caps()["live"], 2, "adopted + kept count against the cap")

    def test_list_filters_terminated_and_untagged(self):
        clock = Clock()
        world = self._world_with(clock, ("ocid1.instance.oc1.phx.a", "a", 5, {"oslo-role": "phone"}),
                                 ("ocid1.instance.oc1.phx.b", "b", 5, {}))
        world["instances"]["ocid1.instance.oc1.phx.a"].lifecycle_state = "TERMINATED"
        p, world, _ = make_provider(world=world, clock=clock)
        self.assertEqual(p.list(), [])


class OrchestratorWiringTests(unittest.TestCase):
    """ORCH_PHONE_PROVIDER=oci behind the existing /sessions and /instances routes."""

    def setUp(self):
        orch._instances.clear()
        orch._leases.clear()
        orch._user_sessions.clear()
        orch._ops.clear()
        orch._instance_tenancy.clear()
        orch.ORCH_DEPLOY_MODE = "mock"
        orch.ORCH_API_TOKEN = ""
        orch.ORCH_MAX_INSTANCES = 5
        orch.ORCH_MAX_REDROID_INSTANCES = 3
        orch.ORCH_PHONE_DATA = "instance"
        orch.ORCH_PHONE_PROVIDER = "oci"
        self.provider, self.world, self.clock = make_provider(config=base_config(max_instances=1))
        orch.set_oci_provider(self.provider)
        self.client = orch.app.test_client()

    def tearDown(self):
        orch.ORCH_PHONE_PROVIDER = ""
        orch.set_oci_provider(None)
        orch._instances.clear()
        orch._leases.clear()
        orch._user_sessions.clear()

    def test_default_off_changes_nothing(self):
        orch.ORCH_PHONE_PROVIDER = ""
        r = self.client.post("/sessions", json={"owner_user_id": "alice"})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["session"]["api_url"], orch.ORCH_MOCK_API_URL)
        self.assertEqual(self.world.get("compute"), None, "no OCI client was ever built")
        self.assertEqual(self.client.get("/health").get_json()["phone_provider"], {"name": "builtin"})
        self.assertIsNone(orch.provider_config_refusal())

    def test_session_leases_an_oci_phone_with_owner_tag(self):
        r = self.client.post("/sessions", json={"owner_user_id": "alice", "ttl_seconds": 60})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        sess = r.get_json()["session"]
        self.assertEqual(sess["api_url"], "http://10.0.1.200:8080")
        self.assertEqual(sess["runtime"], "redroid")
        inst = orch._instances[sess["instance_id"]]
        self.assertEqual(inst["provider"], "oci")
        self.assertEqual(inst["instance_ocid"], "ocid1.instance.oc1.phx.fake1")
        self.assertEqual(self.world["compute"].launches[0].freeform_tags["oslo-owner"], "alice")
        health = self.client.get("/health").get_json()
        self.assertEqual(health["phone_provider"]["name"], "oci")
        self.assertEqual(health["phone_provider"]["live"], 1)
        self.assertEqual(health["phone_provider"]["auth"], "instance_principal")
        self.assertEqual(health["pool"]["automation"]["max"], 1, "the tighter OCI cap is what /pool reports")

    def test_pool_cap_follows_the_oci_cap_and_launches_nothing_more(self):
        # The orchestrator's own pool check sees the tighter OCI cap first: 409, as every pool-full is.
        self.assertEqual(self.client.post("/sessions", json={"owner_user_id": "alice"}).status_code, 201)
        r = self.client.post("/sessions", json={"owner_user_id": "bob"})
        self.assertEqual(r.status_code, 409, r.get_data(as_text=True))
        self.assertIn("pool full (1/1)", r.get_json()["error"])
        self.assertEqual(len(self.world["compute"].launches), 1)

    def test_provider_cap_is_429_oci_cap_when_it_knows_phones_the_pool_does_not(self):
        # An adopted or still-young orphan counts against the OCI cap but is not in the pool.
        self.provider._live["ocid1.instance.oc1.phx.orphan"] = {}
        r = self.client.post("/sessions", json={"owner_user_id": "bob"})
        self.assertEqual(r.status_code, 429, r.get_data(as_text=True))
        body = r.get_json()
        self.assertEqual(body["code"], "oci_cap")
        self.assertEqual(body["max_instances"], 1)
        self.assertEqual(body["live"], 1)
        self.assertNotIn("compute", self.world, "refused before any OCI call")

    def test_oci_error_is_502_with_service_code(self):
        self.world["launch_error"] = FakeServiceError(404, "NotAuthorizedOrNotFound", "image gone")
        r = self.client.post("/sessions", json={"owner_user_id": "alice"})
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.get_json()["code"], "oci_error")
        self.assertEqual(r.get_json()["service_code"], "NotAuthorizedOrNotFound")
        self.assertEqual(orch._instances, {}, "no record for a phone that was never launched")

    def test_delete_instance_terminates_without_preserving_boot_volume(self):
        r = self.client.post("/instances", json={"purpose": "automation", "owner": "carol"})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        inst = r.get_json()
        self.assertEqual(self.world["compute"].launches[0].freeform_tags["oslo-owner"], "carol")
        d = self.client.delete(f"/instances/{inst['id']}")
        self.assertEqual(d.status_code, 200, d.get_data(as_text=True))
        self.assertEqual(self.world["compute"].terminates, [(inst["instance_ocid"], False)])
        self.assertNotIn(inst["id"], orch._instances)

    def test_delete_instance_surfaces_terminate_failure(self):
        r = self.client.post("/instances", json={"purpose": "automation"})
        inst = r.get_json()
        self.world["terminate_error"] = FakeServiceError(409, "IncorrectState", "busy")
        d = self.client.delete(f"/instances/{inst['id']}")
        self.assertEqual(d.status_code, 502)
        self.assertEqual(d.get_json()["service_code"], "IncorrectState")
        self.assertIn(inst["id"], orch._instances, "the record stays until OCI confirms")

    def test_boot_reconcile_adopts_known_and_terminates_orphans(self):
        # One phone this orchestrator knows (persisted record), one orphan from an older run.
        r = self.client.post("/instances", json={"purpose": "automation"})
        known = r.get_json()["instance_ocid"]
        old = ocp._iso_now(lambda: self.clock.now - 100_000)
        self.world["instances"]["ocid1.instance.oc1.phx.orphan"] = FakeInstance(
            id="ocid1.instance.oc1.phx.orphan", display_name="orphan", lifecycle_state="RUNNING",
            freeform_tags={"oslo-role": "phone", "oslo-created": old})
        res = orch.reconcile_oci_phones()
        self.assertEqual(res["adopted"], [known])
        self.assertEqual(res["terminated"], ["ocid1.instance.oc1.phx.orphan"])

    def test_boot_reconcile_failure_is_logged_not_fatal(self):
        self.world["launch_error"] = None
        orig = FakeCompute.list_instances
        FakeCompute.list_instances = lambda self, compartment_id: (_ for _ in ()).throw(
            FakeServiceError(401, "NotAuthenticated", "no principal"))
        try:
            self.provider.clients()
            self.assertIsNone(orch.reconcile_oci_phones())
        finally:
            FakeCompute.list_instances = orig

    def test_startup_refusals(self):
        orch.ORCH_PHONE_PROVIDER = "aws"
        self.assertIn("must be unset or 'oci'", orch.provider_config_refusal())
        orch.ORCH_PHONE_PROVIDER = "oci"
        orch.ORCH_PHONE_DATA = "per_user"
        self.assertIn("per_user", orch.provider_config_refusal())
        orch.ORCH_PHONE_DATA = "instance"
        saved = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("ORCH_OCI_")}
        try:
            self.assertIn("ORCH_OCI_IMAGE_ID", orch.provider_config_refusal())
            os.environ.update({"ORCH_OCI_IMAGE_ID": FAKE_IMAGE, "ORCH_OCI_SUBNET_ID": FAKE_SUBNET,
                               "ORCH_OCI_COMPARTMENT_ID": FAKE_COMPARTMENT})
            self.assertIsNone(orch.provider_config_refusal())
        finally:
            for k in ("ORCH_OCI_IMAGE_ID", "ORCH_OCI_SUBNET_ID", "ORCH_OCI_COMPARTMENT_ID"):
                os.environ.pop(k, None)
            os.environ.update(saved)

    def test_camera_purpose_still_uses_the_old_path(self):
        orch.ORCH_MOCK_CAMERA_API_URL = ""
        r = self.client.post("/instances", json={"purpose": "camera"})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["runtime"], "cuttlefish")
        self.assertNotIn("compute", self.world)


if __name__ == "__main__":
    unittest.main()
