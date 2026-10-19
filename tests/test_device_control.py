#!/usr/bin/env python3
"""Control API: device clock / timezone / settings, and container control.

OSLO's `mobile.*` tools need to set what an app sees as "where and when it is":
system time, timezone, GPS (already `/location`), Android settings, and the
device's egress proxy (already `/proxy`). None of these existed on the Control
API; a procedure that needed a timezone had to shell raw `adb` strings through
`/adb/shell`, which is exactly the "LLM-authored command line" this API is
meant to prevent. Every write here validates its input before it is
interpolated into an adb argv.

`adb` is never invoked — `run_adb` is patched — so this runs with no device.
"""

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "api"))

import server as control_api  # noqa: E402
import device_control  # noqa: E402

from tests.test_control_api import fake_adb  # noqa: E402


class TimeSpecTests(unittest.TestCase):
    def test_epoch_number_and_string(self):
        self.assertEqual(device_control.parse_time_spec({"epoch": 1792396800}), 1792396800)
        self.assertEqual(device_control.parse_time_spec({"epoch": "1792396800"}), 1792396800)

    def test_iso_with_zulu_and_offset(self):
        self.assertEqual(device_control.parse_time_spec({"iso": "2026-10-19T08:00:00Z"}), 1792396800)
        self.assertEqual(device_control.parse_time_spec({"iso": "2026-10-19T10:00:00+02:00"}), 1792396800)

    def test_garbage_is_none(self):
        for bad in [{}, {"epoch": "soon"}, {"iso": "yesterday"}, {"epoch": -5}]:
            self.assertIsNone(device_control.parse_time_spec(bad), bad)

    def test_toybox_set_string_is_utc(self):
        # toybox `date -u MMDDhhmm[[CC]YY][.ss]`
        self.assertEqual(device_control.epoch_to_toybox_set(1792396800), "101908002026.00")


class TimezoneValidationTests(unittest.TestCase):
    def test_olson_ids(self):
        for tz in ["UTC", "America/New_York", "Europe/Berlin", "Etc/GMT+5", "America/Argentina/Buenos_Aires"]:
            self.assertTrue(device_control.is_valid_timezone(tz), tz)

    def test_rejects_shell_shapes(self):
        for tz in ["", "America/New York", "UTC; reboot", "$(id)", "../etc", "a" * 100, None]:
            self.assertFalse(device_control.is_valid_timezone(tz), repr(tz))


class SettingValidationTests(unittest.TestCase):
    def test_namespace_and_key(self):
        self.assertTrue(device_control.is_valid_setting("global", "http_proxy"))
        self.assertTrue(device_control.is_valid_setting("secure", "mock_location"))
        self.assertFalse(device_control.is_valid_setting("kernel", "x"))
        self.assertFalse(device_control.is_valid_setting("global", "http proxy"))
        self.assertFalse(device_control.is_valid_setting("global", "x;id"))

    def test_value_is_quoted_not_trusted(self):
        self.assertEqual(device_control.quote_setting_value("a b"), "'a b'")
        self.assertEqual(device_control.quote_setting_value("x;id"), "'x;id'")
        with self.assertRaises(ValueError):
            device_control.quote_setting_value("bad\nline")
        with self.assertRaises(ValueError):
            device_control.quote_setting_value("y" * 600)


class ClockRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = control_api.app.test_client()
        control_api.API_TOKEN = ""

    def test_clock_reports_epoch_tz_and_auto_flags(self):
        table = {
            "date +%s": (True, "1792396800", ""),
            "getprop persist.sys.timezone": (True, "America/New_York", ""),
            # longest key first: fake_adb matches by substring in insertion order
            "settings get global auto_time_zone": (True, "1", ""),
            "settings get global auto_time": (True, "0", ""),
        }
        with patch.object(control_api, "run_adb", fake_adb(table)):
            body = self.client.get("/device/clock").get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["epoch"], 1792396800)
        self.assertEqual(body["iso"], "2026-10-19T08:00:00+00:00")
        self.assertEqual(body["timezone"], "America/New_York")
        self.assertFalse(body["auto_time"])
        self.assertTrue(body["auto_time_zone"])


class SetTimeRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = control_api.app.test_client()
        control_api.API_TOKEN = ""

    def test_sets_manual_time_with_epoch_form_first(self):
        calls = []
        table = {"date +%s": (True, "1792396801", "")}
        with patch.object(control_api, "run_adb", fake_adb(table, record=calls)):
            body = self.client.post("/device/time", json={"epoch": 1792396800}).get_json()
        joined = [c[1] for c in calls]
        self.assertTrue(body["success"], body)
        self.assertTrue(any("settings put global auto_time 0" in c for c in joined))
        self.assertTrue(any("date @1792396800" in c for c in joined), joined)
        self.assertTrue(any("android.intent.action.TIME_SET" in c for c in joined))
        self.assertEqual(body["epoch"], 1792396801)
        self.assertEqual(body["requested_epoch"], 1792396800)

    def test_falls_back_to_toybox_set_form(self):
        calls = []
        table = {
            "date @": (False, "", "date: bad date '@1792396800'"),
            "date +%s": (True, "1792396800", ""),
        }
        with patch.object(control_api, "run_adb", fake_adb(table, record=calls)):
            body = self.client.post("/device/time", json={"iso": "2026-10-19T08:00:00Z"}).get_json()
        self.assertTrue(body["success"], body)
        self.assertTrue(any("date -u 101908002026.00" in c[1] for c in calls))

    def test_drift_after_set_is_reported_as_failure(self):
        table = {"date +%s": (True, "1000", "")}
        with patch.object(control_api, "run_adb", fake_adb(table)):
            res = self.client.post("/device/time", json={"epoch": 1792396800})
        body = res.get_json()
        self.assertFalse(body["success"])
        self.assertEqual(body["code"], "time_not_applied")
        self.assertEqual(res.status_code, 502)

    def test_auto_restores_network_time(self):
        calls = []
        with patch.object(control_api, "run_adb", fake_adb(record=calls)):
            body = self.client.post("/device/time", json={"auto": True}).get_json()
        self.assertTrue(body["success"])
        self.assertTrue(any("settings put global auto_time 1" in c[1] for c in calls))
        self.assertFalse(any("date @" in c[1] for c in calls))

    def test_bad_spec_is_400(self):
        with patch.object(control_api, "run_adb", fake_adb()):
            res = self.client.post("/device/time", json={"epoch": "noon"})
        self.assertEqual(res.status_code, 400)


class SetTimezoneRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = control_api.app.test_client()
        control_api.API_TOKEN = ""

    def test_sets_timezone_and_reads_it_back(self):
        calls = []
        table = {"getprop persist.sys.timezone": (True, "Europe/Berlin", "")}
        with patch.object(control_api, "run_adb", fake_adb(table, record=calls)):
            body = self.client.post("/device/timezone", json={"tz": "Europe/Berlin"}).get_json()
        joined = [c[1] for c in calls]
        self.assertTrue(body["success"], body)
        self.assertEqual(body["timezone"], "Europe/Berlin")
        self.assertTrue(any("settings put global auto_time_zone 0" in c for c in joined))
        self.assertTrue(any("setprop persist.sys.timezone Europe/Berlin" in c for c in joined))
        self.assertTrue(any("TIMEZONE_CHANGED" in c for c in joined))

    def test_rejects_invalid_timezone_before_adb(self):
        calls = []
        with patch.object(control_api, "run_adb", fake_adb(record=calls)):
            res = self.client.post("/device/timezone", json={"tz": "UTC; reboot"})
        self.assertEqual(res.status_code, 400)
        self.assertFalse(any("setprop" in c[1] for c in calls))

    def test_readback_mismatch_is_failure(self):
        table = {"getprop persist.sys.timezone": (True, "UTC", "")}
        with patch.object(control_api, "run_adb", fake_adb(table)):
            res = self.client.post("/device/timezone", json={"tz": "Asia/Tokyo"})
        self.assertEqual(res.status_code, 502)
        self.assertEqual(res.get_json()["code"], "timezone_not_applied")


class SettingsRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = control_api.app.test_client()
        control_api.API_TOKEN = ""

    def test_get_setting(self):
        table = {"settings get global http_proxy": (True, "proxy.example:8080", "")}
        with patch.object(control_api, "run_adb", fake_adb(table)):
            body = self.client.get("/device/settings?namespace=global&key=http_proxy").get_json()
        self.assertEqual(body["value"], "proxy.example:8080")

    def test_put_setting_quotes_value_and_reads_back(self):
        calls = []
        table = {"settings get secure mock_location": (True, "1", "")}
        with patch.object(control_api, "run_adb", fake_adb(table, record=calls)):
            body = self.client.post(
                "/device/settings", json={"namespace": "secure", "key": "mock_location", "value": "1"}
            ).get_json()
        self.assertTrue(body["success"], body)
        # shlex leaves a safe token bare and quotes anything else
        self.assertTrue(any("settings put secure mock_location 1" in c[1] for c in calls), calls)
        self.assertEqual(body["value"], "1")

    def test_put_setting_quotes_values_with_shell_characters(self):
        calls = []
        with patch.object(control_api, "run_adb", fake_adb(record=calls)):
            self.client.post("/device/settings", json={"namespace": "global", "key": "http_proxy", "value": "host:8080; id"})
        self.assertTrue(any("settings put global http_proxy 'host:8080; id'" in c[1] for c in calls), calls)

    def test_rejects_bad_namespace_or_key(self):
        with patch.object(control_api, "run_adb", fake_adb()):
            self.assertEqual(
                self.client.post("/device/settings", json={"namespace": "kernel", "key": "x", "value": "1"}).status_code, 400
            )
            self.assertEqual(
                self.client.post("/device/settings", json={"namespace": "global", "key": "x;id", "value": "1"}).status_code, 400
            )

    def test_delete_setting(self):
        calls = []
        with patch.object(control_api, "run_adb", fake_adb(record=calls)):
            body = self.client.delete("/device/settings?namespace=global&key=http_proxy").get_json()
        self.assertTrue(body["success"])
        self.assertTrue(any("settings delete global http_proxy" in c[1] for c in calls))


class ContainerRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = control_api.app.test_client()
        control_api.API_TOKEN = ""

    def test_disabled_without_container_name(self):
        with patch.object(control_api, "CONTAINER_NAME", ""):
            res = self.client.get("/container")
        self.assertEqual(res.status_code, 501)
        self.assertEqual(res.get_json()["code"], "container_control_disabled")

    def test_status_uses_fixed_docker_argv(self):
        argvs = []

        def fake_docker(*args, timeout=60):
            argvs.append(list(args))
            if args[0] == "inspect":
                return True, "running", ""
            return True, "", ""

        with patch.object(control_api, "CONTAINER_NAME", "redroid"), patch.object(control_api, "run_docker", fake_docker):
            body = self.client.get("/container").get_json()
        self.assertEqual(body["container"], "redroid")
        self.assertEqual(body["state"], "running")
        self.assertEqual(argvs[0][:2], ["inspect", "-f"])
        self.assertEqual(argvs[0][-1], "redroid")

    def test_restart_is_allowed_and_rejects_other_actions(self):
        argvs = []

        def fake_docker(*args, timeout=60):
            argvs.append(list(args))
            if args[0] == "inspect":
                return True, "running", ""
            return True, "redroid", ""

        with patch.object(control_api, "CONTAINER_NAME", "redroid"), patch.object(control_api, "run_docker", fake_docker):
            ok = self.client.post("/container/restart").get_json()
            bad = self.client.post("/container/rm")
        self.assertTrue(ok["success"])
        self.assertEqual(argvs[0], ["restart", "redroid"])
        self.assertEqual(bad.status_code, 400)
        self.assertFalse(any(a[0] == "rm" for a in argvs))

    def test_container_name_is_never_user_controlled(self):
        """The name comes from the host env, not the request."""
        argvs = []

        def fake_docker(*args, timeout=60):
            argvs.append(list(args))
            return True, "running", ""

        with patch.object(control_api, "CONTAINER_NAME", "redroid"), patch.object(control_api, "run_docker", fake_docker):
            self.client.post("/container/restart", json={"name": "other; rm -rf /"})
        self.assertTrue(all("other" not in " ".join(a) for a in argvs))


if __name__ == "__main__":
    unittest.main()
