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

    def test_health_stays_open_for_probes(self):
        """systemd and Docker probe `/health` with no token; keep it 200."""
        with patch.object(control_api, "run_adb", fake_adb()):
            self.assertEqual(self.client.get("/health").status_code, 200)

    def test_health_refuses_to_call_itself_healthy_to_a_caller_it_will_401(self):
        """The masking bug: healthy phone, 401 everywhere, lab reads as dead."""
        with patch.object(control_api, "run_adb", fake_adb()):
            body = self.client.get("/health").get_json()
        self.assertEqual(body["status"], "unauthorized")
        self.assertFalse(body["usable"])
        self.assertTrue(body["adb_connected"], "the device itself is fine")
        self.assertEqual(body["auth"], {"required": True, "presented": False, "ok": False})

    def test_health_is_healthy_once_the_token_is_right(self):
        with patch.object(control_api, "run_adb", fake_adb()):
            body = self.client.get(
                "/health", headers={"Authorization": "Bearer s3cret"}
            ).get_json()
        self.assertEqual(body["status"], "healthy")
        self.assertTrue(body["usable"])
        self.assertTrue(body["auth"]["ok"])

    def test_health_separates_a_dead_device_from_a_bad_token(self):
        def dead_adb(*args, timeout=30):
            return False, "", "device offline"
        with patch.object(control_api, "run_adb", dead_adb):
            body = self.client.get(
                "/health", headers={"Authorization": "Bearer s3cret"}
            ).get_json()
        self.assertEqual(body["status"], "degraded")
        self.assertTrue(body["auth"]["ok"], "the token was fine; the phone was not")

    def test_401_is_machine_readable_and_distinguishes_missing_from_wrong(self):
        missing = self.client.get("/status")
        wrong = self.client.get("/status", headers={"Authorization": "Bearer nope"})
        self.assertEqual(missing.get_json()["code"], "auth_required")
        self.assertEqual(wrong.get_json()["code"], "auth_invalid")
        for resp in (missing, wrong):
            self.assertTrue(resp.get_json()["auth_required"])
            self.assertIs(resp.get_json()["success"], False)
            self.assertIn("Bearer", resp.headers.get("WWW-Authenticate", ""))

    def test_no_token_configured_means_open_and_healthy(self):
        control_api.API_TOKEN = ""
        with patch.object(control_api, "run_adb", fake_adb()):
            body = self.client.get("/health").get_json()
        self.assertEqual(body["status"], "healthy")
        self.assertEqual(body["auth"], {"required": False, "presented": False, "ok": True})


class AppLaunchTests(unittest.TestCase):
    """A launch that never happened must not report success.

    `cmd package resolve-activity` exits 0 and prints "No activity found" for a
    package that is not installed, so the old code answered
    `{"success": true, "activity": "No activity found"}` — which is how a phone
    with no Play Store looked like a phone that had just opened it.
    """

    def setUp(self):
        self.client = control_api.app.test_client()
        control_api.API_TOKEN = ""

    def test_resolved_activity_is_started(self):
        calls = []
        table = {
            "resolve-activity": (True, "com.example.app/.MainActivity", ""),
            "am start": (True, "Starting: Intent { ... }", ""),
        }
        with patch.object(control_api, "run_adb", fake_adb(table, record=calls)):
            body = self.client.post("/apps/com.example.app/start").get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["activity"], "com.example.app/.MainActivity")
        self.assertTrue(any("am start -n com.example.app/.MainActivity" in c[1] for c in calls))

    def test_missing_package_is_a_404_not_a_false_green(self):
        table = {
            "resolve-activity": (True, "No activity found", ""),
            "monkey": (True, "** No activities found to run, monkey aborted.", ""),
        }
        with patch.object(control_api, "run_adb", fake_adb(table)):
            resp = self.client.post("/apps/com.android.vending/start")
        body = resp.get_json()
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(body["success"])
        self.assertEqual(body["code"], "not_launchable")
        self.assertNotIn("activity", body, "do not report an activity that does not exist")

    def test_resolve_output_is_matched_against_the_package(self):
        """Any line not starting with `<package>/` is not an activity."""
        table = {"resolve-activity": (True, "No activity found", "")}
        with patch.object(control_api, "run_adb", fake_adb(table)):
            self.assertIsNone(control_api._resolve_launch_activity("com.example.app"))

    def test_am_start_error_is_reported(self):
        table = {
            "resolve-activity": (True, "com.example.app/.MainActivity", ""),
            "am start": (True, "", "Error: Activity not started"),
        }
        with patch.object(control_api, "run_adb", fake_adb(table)):
            resp = self.client.post("/apps/com.example.app/start")
        self.assertEqual(resp.status_code, 502)
        self.assertFalse(resp.get_json()["success"])

    def test_monkey_fallback_still_works(self):
        table = {
            "resolve-activity": (True, "No activity found", ""),
            "monkey": (True, "Events injected: 1", ""),
        }
        with patch.object(control_api, "run_adb", fake_adb(table)):
            body = self.client.post("/apps/com.example.app/start").get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["via"], "monkey")


class FocusTests(unittest.TestCase):
    """`adb shell "dumpsys window | grep x"` returns "" — the pipe kills dumpsys."""

    def setUp(self):
        self.client = control_api.app.test_client()
        control_api.API_TOKEN = ""

    def test_focus_is_read_without_a_pipe(self):
        dump = (
            "  mCurrentFocus=Window{ab12 u0 com.example.app/.MainActivity}\n"
            "  mFocusedApp=AppWindowToken{...}\n"
        )
        calls = []
        with patch.object(control_api, "run_adb", fake_adb({"dumpsys window": (True, dump, "")},
                                                           record=calls)):
            body = self.client.get("/device/focus").get_json()
        self.assertTrue(body["success"])
        self.assertIn("com.example.app/.MainActivity", body["focus"])
        self.assertFalse(any("|" in c[1] for c in calls), "no pipe inside adb shell")

    def test_focus_reports_failure_rather_than_an_empty_string(self):
        with patch.object(control_api, "run_adb", lambda *a, timeout=30: (False, "", "offline")):
            body = self.client.get("/device/focus").get_json()
        self.assertFalse(body["success"])
        self.assertEqual(body["focus"], "")


import cloudphone_logging as cpl  # noqa: E402
import viewport  # noqa: E402


class CommandletAndViewportTests(unittest.TestCase):
    def setUp(self):
        control_api.API_TOKEN = ""
        viewport.reset()
        cpl.clear_logs()
        self.client = control_api.app.test_client()

    def test_health_includes_appium_and_vnc(self):
        with patch.object(control_api, "run_adb", fake_adb()):
            body = self.client.get("/health").get_json()
        self.assertIn("appium", body)
        self.assertIn("vnc", body)
        self.assertEqual(body["vnc"]["port"], 5900)

    def test_commandlet_tap_percent_logs_cmd(self):
        with patch.object(control_api, "run_adb_shell", return_value=(True, "Physical size: 1280x720", "")):
            resp = self.client.post("/ui/command", json={"action": "tap", "xp": 50, "yp": 50})
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        body = resp.get_json()
        self.assertEqual(body["backend"], "adb")
        self.assertEqual(body["commands"], ["input tap 640 360"])
        cmd_lines = cpl.recent_logs(log_type="CMD")
        self.assertTrue(any("commandlet" in item["msg"] for item in cmd_lines), cmd_lines)

    def test_appium_backend_unavailable_logs_apm(self):
        with patch.object(control_api, "run_adb_shell", return_value=(True, "Physical size: 1280x720", "")):
            resp = self.client.post(
                "/ui/command",
                json={"action": "tap", "x": 1, "y": 2, "backend": "appium"},
            )
        self.assertEqual(resp.status_code, 501)
        body = resp.get_json()
        self.assertEqual(body["backend"], "appium")
        self.assertIn("w3c", body)
        apm_lines = cpl.recent_logs(log_type="APM")
        self.assertTrue(
            any("unavailable" in item["msg"] or "w3c" in item["msg"] for item in apm_lines),
            apm_lines,
        )

    def test_appium_status_endpoint(self):
        body = self.client.get("/appium/status").get_json()
        self.assertIn("url", body)
        self.assertIn("ready", body)
        self.assertFalse(body["ready"])
        self.assertTrue(cpl.recent_logs(log_type="APM"))

    def test_vnc_viewport_logs(self):
        body = self.client.get("/vnc/status").get_json()
        self.assertEqual(body["protocol"], "rfb")
        self.assertEqual(body["port"], 5900)
        attached = self.client.post("/vnc/attach")
        self.assertEqual(attached.status_code, 201)
        self.assertEqual(attached.get_json()["clients"], 1)
        vnc_lines = cpl.recent_logs(log_type="VNC")
        self.assertTrue(any("viewport" in item["msg"] for item in vnc_lines), vnc_lines)

    def test_logs_endpoint_filters_types(self):
        with patch.object(control_api, "run_adb_shell", return_value=(True, "Physical size: 1280x720", "")):
            self.client.post("/ui/command", json={"action": "key", "key": "back"})
        self.client.get("/vnc/status")
        self.client.get("/appium/status")
        filtered = self.client.get("/logs?type=CMD,APM,VNC").get_json()
        types = {item["type"] for item in filtered["logs"]}
        self.assertTrue({"CMD", "APM", "VNC"} <= types, types)


if __name__ == "__main__":
    unittest.main()
