"""
A loaded pool: the phones the lab already has are leased first, mode "static"
never provisions past them, and leases survive a restart.
"""
import importlib
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

POOL = [
    {"id": "redroid-lab-1", "name": "cloud-phone-agent-6c58", "runtime": "redroid", "purpose": "automation", "api_url": "http://10.0.1.127:8080", "adb_connect": "127.0.0.1:5555", "public_ip": "129.146.55.133"},
    {"id": "cuttlefish-lab-1", "name": "cloud-phone-gapps-test", "runtime": "cuttlefish", "purpose": "camera", "api_url": "http://10.0.1.242:8080"},
    {"id": "off-lab", "name": "stopped box", "runtime": "redroid", "api_url": "http://10.0.1.9:8080", "enabled": False},
    {"id": "broken", "name": "no url", "runtime": "redroid"},
]


def fresh_server(env):
    """Import the orchestrator module under `env` (module-level config), isolated per test."""
    for k in list(os.environ):
        if k.startswith("ORCH_"):
            os.environ.pop(k)
    os.environ.update(env)
    sys.modules.pop("orchestrator.server", None)
    import orchestrator.server as orch  # noqa: E402
    importlib.reload(orch)
    return orch


class StaticPoolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # These tests re-import the orchestrator under their own ORCH_* env;
        # give the other suites back the environment and module they expect.
        cls._env = dict(os.environ)
        cls._mod = sys.modules.get("orchestrator.server")

    @classmethod
    def tearDownClass(cls):
        os.environ.clear()
        os.environ.update(cls._env)
        if cls._mod is not None:
            sys.modules["orchestrator.server"] = cls._mod
        else:
            sys.modules.pop("orchestrator.server", None)

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.pool_path = os.path.join(self.tmp, "pool.json")
        self.state_path = os.path.join(self.tmp, "state.json")
        with open(self.pool_path, "w") as fh:
            json.dump(POOL, fh)

    def _orch(self, mode="static"):
        return fresh_server({"ORCH_DEPLOY_MODE": mode, "ORCH_POOL_FILE": self.pool_path, "ORCH_STATE_FILE": self.state_path, "ORCH_API_TOKEN": ""})

    def test_pool_file_loads_one_redroid_and_one_cuttlefish_and_skips_disabled_or_broken_entries(self):
        orch = self._orch()
        client = orch.app.test_client()
        pool = client.get("/pool").get_json()["pool"]
        self.assertEqual(pool["deploy_mode"], "static")
        self.assertEqual((pool["automation"]["total"], pool["automation"]["idle"], pool["automation"]["static"]), (1, 1, 1))
        self.assertEqual((pool["camera"]["total"], pool["camera"]["idle"]), (1, 1))
        ids = {m["id"] for p in ("automation", "camera") for m in pool[p]["members"]}
        self.assertEqual(ids, {"redroid-lab-1", "cuttlefish-lab-1"})
        member = pool["automation"]["members"][0]
        self.assertEqual(member["host"], "10.0.1.127:8080")
        self.assertNotIn("token", json.dumps(pool).lower())
        health = client.get("/health").get_json()
        self.assertEqual(health["instances"], 2)
        self.assertEqual(health["deploy_mode"], "static")

    def test_one_user_leases_the_static_redroid_phone_and_a_second_user_is_refused_without_provisioning(self):
        orch = self._orch()
        client = orch.app.test_client()
        with patch.object(orch, "_control_get", return_value={"status": "healthy", "usable": True, "auth": {"required": False}}):
            r = client.post("/sessions", json={"owner_user_id": "lehel", "ttl_seconds": 600})
            self.assertEqual(r.status_code, 201, r.get_json())
            sess = r.get_json()["session"]
            self.assertEqual(sess["instance_id"], "redroid-lab-1")
            self.assertEqual(sess["api_url"], "http://10.0.1.127:8080")
            pool = client.get("/pool").get_json()["pool"]
            self.assertEqual((pool["automation"]["leased"], pool["automation"]["idle"]), (1, 0))
            self.assertEqual(pool["automation"]["members"][0]["lease_owner"], "lehel")
            # Renewal by the same owner keeps the phone.
            again = client.post("/sessions", json={"owner_user_id": "lehel", "ttl_seconds": 600})
            self.assertEqual(again.status_code, 200)
            # Another person: the pool is exhausted and static mode does not provision.
            other = client.post("/sessions", json={"owner_user_id": "bob", "ttl_seconds": 600})
            self.assertEqual(other.status_code, 409, other.get_json())
            self.assertIn("exhausted", other.get_json()["error"])
            # The camera phone is a separate pool, still free.
            cam = client.post("/sessions", json={"owner_user_id": "bob", "ttl_seconds": 600, "purpose": "camera"})
            self.assertEqual(cam.status_code, 201, cam.get_json())
            self.assertEqual(cam.get_json()["session"]["instance_id"], "cuttlefish-lab-1")

    def test_leases_and_sessions_survive_a_restart(self):
        orch = self._orch()
        client = orch.app.test_client()
        with patch.object(orch, "_control_get", return_value={"status": "healthy", "usable": True, "auth": {"required": False}}):
            self.assertEqual(client.post("/sessions", json={"owner_user_id": "lehel", "ttl_seconds": 600}).status_code, 201)
        self.assertTrue(os.path.exists(self.state_path), "state file written on lease")
        saved = json.load(open(self.state_path))
        self.assertIn("redroid-lab-1", saved["leases"])
        self.assertEqual(saved["sessions"]["lehel"]["instance_id"], "redroid-lab-1")

        # Restart: a fresh module, same pool file and state file.
        orch2 = self._orch()
        client2 = orch2.app.test_client()
        pool = client2.get("/pool").get_json()["pool"]
        self.assertEqual(pool["automation"]["members"][0]["lease_owner"], "lehel", "lease restored")
        got = client2.get("/sessions/lehel")
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.get_json()["instance_id"], "redroid-lab-1")
        with patch.object(orch2, "_control_get", return_value={"status": "healthy", "usable": True, "auth": {"required": False}}):
            self.assertEqual(client2.post("/sessions", json={"owner_user_id": "bob", "ttl_seconds": 60}).status_code, 409, "still exhausted after restart")
        # Release, and the slot frees.
        self.assertEqual(client2.delete("/sessions/lehel").status_code, 200)
        self.assertEqual(client2.get("/pool").get_json()["pool"]["automation"]["idle"], 1)

    def test_mock_mode_still_leases_the_static_pool_first_then_provisions(self):
        orch = self._orch(mode="mock")
        client = orch.app.test_client()
        with patch.object(orch, "_control_get", return_value={"status": "healthy", "usable": True, "auth": {"required": False}}):
            first = client.post("/sessions", json={"owner_user_id": "lehel", "ttl_seconds": 600}).get_json()["session"]
            self.assertEqual(first["instance_id"], "redroid-lab-1", "the loaded phone goes first")
            second = client.post("/sessions", json={"owner_user_id": "bob", "ttl_seconds": 600})
            self.assertEqual(second.status_code, 201, second.get_json())
            self.assertNotEqual(second.get_json()["session"]["instance_id"], "redroid-lab-1", "then the pool scales up")

    def test_no_pool_file_means_the_old_behaviour(self):
        orch = fresh_server({"ORCH_DEPLOY_MODE": "mock", "ORCH_API_TOKEN": ""})
        self.assertEqual(orch.app.test_client().get("/health").get_json()["instances"], 0)


if __name__ == "__main__":
    unittest.main()
