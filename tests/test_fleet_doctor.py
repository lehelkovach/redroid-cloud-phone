"""The fleet doctor's diagnosis is pure: give it facts, read the findings."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import fleet_doctor as fd  # noqa: E402

OPEN_RULES = [
    {"source": "0.0.0.0/0", "protocol": "6", "min": 22, "max": 22},
    {"source": "0.0.0.0/0", "protocol": "6", "min": 8080, "max": 8080},
    {"source": "0.0.0.0/0", "protocol": "6", "min": 5555, "max": 5555},
]


def facts(phone_state="RUNNING", orch_state="RUNNING", rules=None, probes=None, runcmd=True):
    return {
        "from": "test",
        "vms": {
            "phone": {"name": "cloud-phone-agent-6c58", "state": phone_state, "public_ip": "129.146.55.133", "private_ip": "10.0.1.127", "run_command_plugin": runcmd},
            "orchestrator": {"name": "cloud-phone-orch-6c58", "state": orch_state, "public_ip": "129.146.105.26", "private_ip": "10.0.1.123", "run_command_plugin": runcmd},
        },
        "ingress": OPEN_RULES if rules is None else rules,
        "probes": probes or {},
    }


class FleetDoctorTests(unittest.TestCase):
    def test_stopped_vms_are_the_blocker_the_lab_had_on_2026_10_19(self):
        r = fd.diagnose(facts(phone_state="STOPPED", orch_state="STOPPED"))
        self.assertFalse(r["ok"])
        whats = " ".join(f["what"] for f in r["findings"])
        self.assertIn("cloud-phone-agent-6c58 is STOPPED", whats)
        self.assertIn("cloud-phone-orch-6c58 is STOPPED", whats)
        self.assertTrue(all("START" in f["fix"] for f in r["findings"] if f["severity"] == "blocker" and f["where"] != "network"))

    def test_running_but_services_down_points_at_the_units(self):
        r = fd.diagnose(facts(probes={"phone:ssh": True, "phone:control_api": False, "phone:adb": False, "orchestrator:ssh": True, "orchestrator:orchestrator": False}))
        self.assertFalse(r["ok"])
        fixes = " ".join(f["fix"] for f in r["findings"])
        self.assertIn("control-api-redroid.service", fixes)
        self.assertIn("cloud-phone-orchestrator.service", fixes)
        self.assertIn("redroid-container.service", fixes)

    def test_missing_8090_rule_is_named_even_when_everything_else_is_fine(self):
        r = fd.diagnose(facts(probes={"phone:ssh": True, "phone:control_api": True, "phone:adb": True, "orchestrator:ssh": True, "orchestrator:orchestrator": True}))
        self.assertFalse(r["ok"])
        self.assertEqual([f for f in r["findings"] if f["severity"] == "blocker"][0]["where"], "network")
        self.assertIn("8090", r["findings"][0]["what"])

    def test_vcn_rule_for_8090_satisfies_prod_and_the_lab_is_ok(self):
        rules = OPEN_RULES + [{"source": "10.0.0.0/16", "protocol": "6", "min": 8090, "max": 8090}]
        r = fd.diagnose(facts(rules=rules, probes={"phone:ssh": True, "phone:control_api": True, "phone:adb": True, "orchestrator:ssh": True, "orchestrator:orchestrator": True}))
        self.assertTrue(r["ok"], r["findings"])
        self.assertTrue(fd.ingress_allows(rules, 8090, "10.0.1.0/24"))
        self.assertFalse(fd.ingress_allows(rules, 8090, "0.0.0.0/0"))

    def test_run_command_off_is_information_not_a_blocker(self):
        rules = OPEN_RULES + [{"source": "10.0.0.0/16", "protocol": "6", "min": 8090, "max": 8090}]
        r = fd.diagnose(facts(rules=rules, runcmd=False, probes={"phone:ssh": True, "phone:control_api": True, "phone:adb": True, "orchestrator:ssh": True, "orchestrator:orchestrator": True}))
        self.assertTrue(r["ok"])
        self.assertEqual({f["severity"] for f in r["findings"]}, {"info"})

    def test_render_has_a_verdict_line(self):
        f = facts(phone_state="STOPPED")
        out = fd.render(f, fd.diagnose(f))
        self.assertIn("VERDICT: blocked", out)
        self.assertIn("BLOCKER", out)


if __name__ == "__main__":
    unittest.main()
