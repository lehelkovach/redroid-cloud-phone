#!/usr/bin/env python3
"""End-to-end mobile automation against the fake phone.

The ladder a real dating-app run would follow, with none of the real parts:
configure a residential proxy, launch the app, sign up with fake credentials,
swipe within a budget, then follow up on a match an hour later — where "an hour"
is a virtual clock, and the send is approval-gated.

The app is `com.example.mockdating`, a fabricated fixture. There is no real
account, no real person, and no unattended outreach. Swiping a live dating
service and messaging strangers is out of scope on purpose.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orchestrator import procedures as proc
from orchestrator import rules
from tests.fixtures.fake_phone import DEMO_APP, FakePhone, FakePhoneServer

import requests

PROXY = {"host": "geo.example-residential.net", "port": 12321,
         "username": "user", "password": "secret"}


def control_post(base_url):
    def post(path, payload=None, instance=None):
        resp = requests.post(f"{base_url}{path}", json=payload or {}, timeout=5)
        resp.raise_for_status()
        return resp.json()
    return post


def control_get(base_url):
    def get(path, instance=None):
        resp = requests.get(f"{base_url}{path}", timeout=5)
        resp.raise_for_status()
        return resp.json()
    return get


class MobileScenarioTests(unittest.TestCase):
    def setUp(self):
        self.server = FakePhoneServer(FakePhone())
        self.server.__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)
        self.base = self.server.base_url
        self.phone = self.server.phone
        self.post = control_post(self.base)
        self.get = control_get(self.base)
        self.adapters = {
            proc.MOBILE: proc.MobileAdapter(
                control_post=self.post, control_get=self.get, instance={"api_url": self.base}
            )
        }

    # -- proxy -------------------------------------------------------------
    def test_app_refuses_to_load_without_proxy(self):
        """The scenario is only meaningful if egress actually gates the app."""
        outcome = proc.run_procedure(
            [{"action": "open", "package": DEMO_APP}], self.adapters, sleep=lambda s: None
        )
        self.assertEqual(outcome["status"], "failed")
        self.assertIn("proxy", outcome["error"])

    def test_proxy_changes_egress_and_is_not_logged_in_clear(self):
        result = self.post("/proxy", {"enabled": True, **PROXY})
        self.assertTrue(result["success"])
        self.assertEqual(self.get("/status")["egress_ip"], "203.0.113.7")
        # The fixture records what the orchestrator sent; assert we never put
        # the password on a path that gets echoed back into logs.
        proxy_calls = [c for c in self.phone.calls if c["endpoint"] == "/proxy"]
        self.assertEqual(self.get("/proxy")["host"], PROXY["host"])
        self.assertNotIn("secret", str(self.get("/proxy")))
        self.assertTrue(proxy_calls)

    # -- signup ------------------------------------------------------------
    def _configure_proxy(self):
        self.post("/proxy", {"enabled": True, **PROXY})

    def test_signup_procedure_creates_account(self):
        self._configure_proxy()
        steps = proc.login_procedure(
            DEMO_APP, "bs@example.com", "fake-password-not-real",
            surface=proc.MOBILE, password_label="Password",
        )
        outcome = proc.run_procedure(steps, self.adapters, sleep=lambda s: None)
        self.assertEqual(outcome["status"], "done", outcome.get("error"))
        self.assertEqual(self.phone.account, {"email": "bs@example.com"})
        self.assertEqual(self.phone.screen, "deck")

    def test_typing_before_the_app_opens_fails_loudly(self):
        """A step that lands on the wrong screen must fail, not silently pass."""
        outcome = proc.run_procedure(
            [{"action": "type", "text": "hello"}], self.adapters, sleep=lambda s: None
        )
        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(outcome["failed_index"], 0)

    # -- swiping -----------------------------------------------------------
    def _signup(self):
        self._configure_proxy()
        proc.run_procedure(
            proc.login_procedure(DEMO_APP, "bs@example.com", "fake-password-not-real",
                                 password_label="Password"),
            self.adapters, sleep=lambda s: None,
        )

    def test_autoswipe_respects_budget(self):
        self._signup()
        budget = rules.SwipeBudget(max_swipes=3, max_likes=2)
        performed = []
        while not budget.exhausted and self.phone.deck:
            liked = True
            outcome = proc.run_procedure(
                [{"action": "swipe", "x1": 200, "y1": 1000, "x2": 900, "y2": 1000}],
                self.adapters, sleep=lambda s: None,
            )
            self.assertEqual(outcome["status"], "done", outcome.get("error"))
            budget.record(liked)
            performed.append(outcome["steps"][0]["result"])

        self.assertEqual(len(performed), 2, "likes cap should bite before swipe cap")
        self.assertTrue(budget.exhausted)
        self.assertEqual(budget.remaining()["likes"], 0)
        self.assertEqual(len(self.phone.swipes), 2)

    def test_swiping_produces_a_match(self):
        self._signup()
        for _ in range(2):
            proc.run_procedure(
                [{"action": "swipe", "x1": 200, "y1": 1000, "x2": 900, "y2": 1000}],
                self.adapters, sleep=lambda s: None,
            )
        self.assertEqual(self.phone.matches, ["grace"])

    # -- the hour rule -----------------------------------------------------
    def test_no_followup_before_the_delay(self):
        now = 1_000_000.0
        matches = [{"id": "grace", "name": "Grace", "matched_at": now - 600}]
        self.assertEqual(rules.due_followups(matches, now, delay_s=3600), [])

    def test_followup_due_after_an_hour_and_needs_approval(self):
        now = 1_000_000.0
        matches = [{"id": "grace", "name": "Grace", "matched_at": now - 3601}]
        intents = rules.plan_followups(matches, now, "hey {name}, how's your week?")
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0]["to"], "grace")
        self.assertTrue(intents[0]["needs_approval"])
        self.assertGreaterEqual(intents[0]["waited_s"], 3600)

    def test_one_message_per_match_ever(self):
        now = 1_000_000.0
        matches = [{"id": "grace", "name": "Grace", "matched_at": now - 7200}]
        self.assertEqual(
            rules.plan_followups(matches, now, "hi {name}", already_messaged=["grace"]), []
        )

    def test_followups_are_capped_per_run(self):
        now = 1_000_000.0
        matches = [
            {"id": f"m{i}", "name": f"M{i}", "matched_at": now - 7200 - i}
            for i in range(10)
        ]
        intents = rules.plan_followups(matches, now, "hi {name}", max_per_run=3)
        self.assertEqual(len(intents), 3)
        self.assertEqual(intents[0]["to"], "m9", "oldest match first")

    def test_template_must_personalize(self):
        with self.assertRaises(ValueError):
            rules.plan_followups([], 0, "hey there")

    def test_approved_followup_reaches_the_phone(self):
        self._signup()
        for _ in range(2):
            proc.run_procedure(
                [{"action": "swipe", "x1": 200, "y1": 1000, "x2": 900, "y2": 1000}],
                self.adapters, sleep=lambda s: None,
            )
        match = self.phone.matches[0]
        now = 1_000_000.0
        intents = rules.plan_followups(
            [{"id": match, "name": match.title(), "matched_at": now - 3601}],
            now, "hey {name}, how's your week?", approve=True,
        )
        self.assertFalse(intents[0]["needs_approval"])

        outcome = proc.run_procedure(
            [{"action": "shell", "command": f"message {intents[0]['to']} {intents[0]['text']}"}],
            self.adapters, sleep=lambda s: None,
        )
        self.assertEqual(outcome["status"], "done", outcome.get("error"))
        self.assertEqual(self.phone.messages, [{"to": match, "text": intents[0]["text"]}])

    def test_message_to_a_non_match_is_refused(self):
        self._signup()
        outcome = proc.run_procedure(
            [{"action": "shell", "command": "message stranger hello"}],
            self.adapters, sleep=lambda s: None,
        )
        self.assertEqual(outcome["status"], "failed")
        self.assertIn("not a match", outcome["error"])

    # -- full ladder -------------------------------------------------------
    def test_full_ladder_proxy_signup_swipe_match_followup(self):
        self._configure_proxy()
        self.assertEqual(self.get("/status")["egress_ip"], "203.0.113.7")

        ladder = proc.login_procedure(
            DEMO_APP, "bs@example.com", "fake-password-not-real", password_label="Password"
        ) + [
            {"action": "swipe", "x1": 200, "y1": 1000, "x2": 900, "y2": 1000},
            {"action": "swipe", "x1": 200, "y1": 1000, "x2": 900, "y2": 1000},
            {"action": "screenshot"},
        ]
        outcome = proc.run_procedure(ladder, self.adapters, sleep=lambda s: None)

        self.assertEqual(outcome["status"], "done", outcome.get("error"))
        self.assertEqual(len(outcome["steps"]), len(ladder))
        self.assertTrue(all(s["status"] == "ok" for s in outcome["steps"]))
        self.assertEqual(self.phone.matches, ["grace"])

        now = 1_000_000.0
        intents = rules.plan_followups(
            [{"id": "grace", "name": "Grace", "matched_at": now - 3601}],
            now, "hey {name}, good match!",
        )
        self.assertTrue(intents[0]["needs_approval"])
        self.assertEqual(self.phone.messages, [], "nothing sends without approval")


if __name__ == "__main__":
    unittest.main()
