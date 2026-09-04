#!/usr/bin/env python3
"""Control API: GApps reporting and per-request ADB routing.

`adb` is never actually invoked — `run_adb` is patched — so this runs with no
device and no android-tools installed.
"""

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "api"))

import server as control_api  # noqa: E402


def fake_adb(responses=None, record=None):
    """Return a run_adb stand-in driven by a {substring: (ok, stdout)} table."""
    table = responses or {}

    def run_adb(*args, timeout=30):
        joined = " ".join(str(a) for a in args)
        if record is not None:
            record.append((control_api.current_adb_connect(), joined))
        for needle, outcome in table.items():
            if needle in joined:
                return outcome
        if args and args[0] == "devices":
            return True, f"List of devices attached\n{control_api.current_adb_connect()}\tdevice", ""
        return True, "", ""

    return run_adb


class GappsReportingTests(unittest.TestCase):
    def setUp(self):
        self.client = control_api.app.test_client()
        control_api.API_TOKEN = ""

    def test_health_reports_gapps_ready(self):
        table = {
            "pm path com.google.android.gms": (True, "package:/system/priv-app/GmsCore.apk", ""),
            "pm path com.android.vending": (True, "package:/system/priv-app/Phonesky.apk", ""),
            "pm path com.google.android.gsf": (True, "package:/system/priv-app/GSF.apk", ""),
        }
        with patch.object(control_api, "run_adb", fake_adb(table)):
            body = self.client.get("/health").get_json()
        self.assertTrue(body["gapps"]["ready"])
        self.assertTrue(body["gapps"]["play_store"])
        self.assertEqual(body["status"], "healthy")

    def test_health_reports_gapps_missing(self):
        """The empty-gapps.zip lab failure must be visible from /health."""
        table = {"pm path": (True, "", "")}
        with patch.object(control_api, "run_adb", fake_adb(table)):
            body = self.client.get("/health").get_json()
        self.assertFalse(body["gapps"]["ready"])
        self.assertFalse(body["gapps"]["gms"])

    def test_health_when_adb_is_down_does_not_claim_gapps(self):
        def dead_adb(*args, timeout=30):
            return False, "", "device offline"
        with patch.object(control_api, "run_adb", dead_adb):
            body = self.client.get("/health").get_json()
        self.assertEqual(body["status"], "degraded")
        self.assertFalse(body["gapps"]["ready"])

    def test_status_includes_gapps(self):
        with patch.object(control_api, "run_adb", fake_adb({"pm path": (True, "package:x", "")})):
            body = self.client.get("/status").get_json()
        self.assertIn("gapps", body)
        self.assertTrue(body["gapps"]["ready"])


class AdbRoutingTests(unittest.TestCase):
    def setUp(self):
        self.client = control_api.app.test_client()
        control_api.API_TOKEN = ""

    def test_default_serial_without_header(self):
        calls = []
        with patch.object(control_api, "run_adb", fake_adb(record=calls)):
            self.client.get("/health")
        self.assertTrue(calls)
        self.assertEqual(calls[0][0], control_api.ADB_CONNECT)

    def test_header_routes_to_another_phone(self):
        """One Control API fronting several Redroid containers."""
        calls = []
        with patch.object(control_api, "run_adb", fake_adb(record=calls)):
            body = self.client.get(
                "/health", headers={"X-Cloud-Phone-Adb": "127.0.0.1:5561"}
            ).get_json()
        self.assertEqual(body["adb_target"], "127.0.0.1:5561")
        self.assertTrue(all(c[0] == "127.0.0.1:5561" for c in calls))

    def test_malformed_header_falls_back_to_default(self):
        """A serial is interpolated into an adb argv; refuse anything odd."""
        for bogus in ["; rm -rf /", "host with spaces", "x" * 200]:
            with self.subTest(bogus=bogus):
                calls = []
                with patch.object(control_api, "run_adb", fake_adb(record=calls)):
                    body = self.client.get(
                        "/health", headers={"X-Cloud-Phone-Adb": bogus}
                    ).get_json()
                self.assertEqual(body["adb_target"], control_api.ADB_CONNECT)

    def test_current_adb_connect_outside_a_request(self):
        self.assertEqual(control_api.current_adb_connect(), control_api.ADB_CONNECT)


HIERARCHY = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
    <node index="1" text="Email" resource-id="com.example:id/email_label" clickable="false" bounds="[100,500][400,560]" />
    <node index="2" text="" resource-id="com.example:id/email" clickable="true" bounds="[100,580][980,680]" />
    <node index="3" text="Password" resource-id="com.example:id/password" clickable="true" password="true" bounds="[100,700][980,800]" />
    <node index="4" text="Sign up" resource-id="com.example:id/submit" clickable="true" bounds="[100,900][980,1000]" />
  </node>
</hierarchy>"""


class UiTreeTests(unittest.TestCase):
    def setUp(self):
        self.client = control_api.app.test_client()
        control_api.API_TOKEN = ""

    def test_parses_labels_and_centres(self):
        elements = control_api._parse_ui_elements(HIERARCHY)
        labels = [e["label"] for e in elements if e["label"]]
        self.assertIn("Sign up", labels)
        submit = control_api._find_by_label(elements, "Sign up")
        self.assertEqual(submit["x"], 540)
        self.assertEqual(submit["y"], 950, "tap the centre, not a corner")

    def test_password_flag_survives(self):
        elements = control_api._parse_ui_elements(HIERARCHY)
        self.assertTrue(control_api._find_by_label(elements, "Password")["password"])

    def test_malformed_xml_yields_no_elements(self):
        self.assertEqual(control_api._parse_ui_elements("<hierarchy"), [])

    def test_exact_label_beats_substring(self):
        elements = [
            {"label": "Sign up with Google", "resource_id": "", "clickable": True,
             "x": 1, "y": 1, "bounds": [], "focused": False, "password": False},
            {"label": "Sign up", "resource_id": "", "clickable": True,
             "x": 2, "y": 2, "bounds": [], "focused": False, "password": False},
        ]
        self.assertEqual(control_api._find_by_label(elements, "Sign up")["x"], 2)

    def test_clickable_wins_over_decorative_text(self):
        elements = control_api._parse_ui_elements(HIERARCHY)
        match = control_api._find_by_label(elements, "email")
        self.assertTrue(match["clickable"], "the label node is not the tappable field")

    def test_unknown_label_returns_none(self):
        self.assertIsNone(control_api._find_by_label([], "nope"))

    def test_ui_endpoint_returns_elements(self):
        table = {"uiautomator dump": (True, HIERARCHY, "")}
        with patch.object(control_api, "run_adb", fake_adb(table)):
            body = self.client.get("/device/ui").get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["count"], 4)

    def test_ui_endpoint_reports_dump_failure(self):
        with patch.object(control_api, "run_adb", fake_adb({"uiautomator": (False, "", "no dump")})):
            resp = self.client.get("/device/ui")
        self.assertEqual(resp.status_code, 500)
        self.assertFalse(resp.get_json()["success"])

    def test_tap_label_taps_the_centre(self):
        calls = []
        table = {"uiautomator dump": (True, HIERARCHY, "")}
        with patch.object(control_api, "run_adb", fake_adb(table, record=calls)):
            body = self.client.post(
                "/device/input", json={"type": "tap_label", "label": "Sign up"}
            ).get_json()
        self.assertTrue(body["success"])
        self.assertEqual((body["x"], body["y"]), (540, 950))
        self.assertTrue(any("input tap 540 950" in c[1] for c in calls))

    def test_tap_label_missing_lists_what_was_there(self):
        table = {"uiautomator dump": (True, HIERARCHY, "")}
        with patch.object(control_api, "run_adb", fake_adb(table)):
            body = self.client.post(
                "/device/input", json={"type": "tap_label", "label": "Checkout"}
            ).get_json()
        self.assertFalse(body["success"])
        self.assertIn("Sign up", body["available"])


class DeviceInputTests(unittest.TestCase):
    def setUp(self):
        self.client = control_api.app.test_client()
        control_api.API_TOKEN = ""

    def _send(self, payload):
        calls = []
        with patch.object(control_api, "run_adb", fake_adb(record=calls)):
            body = self.client.post("/device/input", json=payload).get_json()
        return body, [c[1] for c in calls]

    def test_tap(self):
        body, calls = self._send({"type": "tap", "x": 10, "y": 20})
        self.assertTrue(body["success"])
        self.assertTrue(any("input tap 10 20" in c for c in calls))

    def test_swipe_carries_duration(self):
        body, calls = self._send({
            "type": "swipe", "x1": 1, "y1": 2, "x2": 3, "y2": 4, "duration": 250
        })
        self.assertTrue(body["success"])
        self.assertTrue(any("input swipe 1 2 3 4 250" in c for c in calls))

    def test_key(self):
        _, calls = self._send({"type": "key", "keycode": 66})
        self.assertTrue(any("input keyevent 66" in c for c in calls))

    def test_text_spaces_are_escaped_for_adb(self):
        _, calls = self._send({"type": "text", "text": "hello world"})
        self.assertTrue(any("hello%sworld" in c for c in calls),
                        "adb input text splits on raw spaces")

    def test_app_start_and_stop(self):
        with patch.object(control_api, "run_adb", fake_adb()):
            started = self.client.post("/apps/com.example.app/start").get_json()
            stopped = self.client.post("/apps/com.example.app/stop").get_json()
        self.assertTrue(started["success"])
        self.assertTrue(stopped["success"])

    def test_adb_shell_endpoint(self):
        with patch.object(control_api, "run_adb", fake_adb({"echo hi": (True, "hi", "")})):
            body = self.client.post("/adb/shell", json={"command": "echo hi"}).get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["stdout"], "hi")

    def test_adb_shell_requires_a_command(self):
        with patch.object(control_api, "run_adb", fake_adb()):
            resp = self.client.post("/adb/shell", json={})
        self.assertEqual(resp.status_code, 400)


class JobTests(unittest.TestCase):
    def setUp(self):
        self.client = control_api.app.test_client()
        control_api.API_TOKEN = ""
        control_api._jobs.clear()

    def test_job_runs_and_is_pollable(self):
        import time
        with patch.object(control_api, "run_adb", fake_adb({"echo ok": (True, "ok", "")})):
            created = self.client.post(
                "/jobs", json={"type": "adb_shell", "payload": {"command": "echo ok"}}
            )
            self.assertEqual(created.status_code, 202)
            job_id = created.get_json()["job_id"]
            for _ in range(50):
                body = self.client.get(f"/jobs/{job_id}").get_json()
                if body["status"] in {"done", "failed"}:
                    break
                time.sleep(0.05)
        self.assertEqual(body["status"], "done")

    def test_unknown_job(self):
        self.assertEqual(self.client.get("/jobs/nope").status_code, 404)

    def test_expired_jobs_are_pruned(self):
        control_api._jobs["old"] = {
            "id": "old", "status": "done",
            "created_at": 0, "updated_at": 0,
        }
        control_api._prune_jobs()
        self.assertNotIn("old", control_api._jobs)


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.client = control_api.app.test_client()
        control_api.API_TOKEN = "s3cret"
        self.addCleanup(setattr, control_api, "API_TOKEN", "")

    def test_protected_endpoint_rejects_missing_token(self):
        self.assertEqual(self.client.get("/status").status_code, 401)

    def test_protected_endpoint_accepts_token(self):
        with patch.object(control_api, "run_adb", fake_adb()):
            resp = self.client.get("/status", headers={"Authorization": "Bearer s3cret"})
        self.assertEqual(resp.status_code, 200)

    def test_health_stays_open_but_that_can_mask_auth_failures(self):
        """`/health` is unauthenticated on purpose; PR #10 found this masking 401s."""
        with patch.object(control_api, "run_adb", fake_adb()):
            self.assertEqual(self.client.get("/health").status_code, 200)


if __name__ == "__main__":
    unittest.main()
