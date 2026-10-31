#!/usr/bin/env python3
"""Control API rung M0: honest input results, safe text, a richer UI dump.

`run_adb` is patched (or `subprocess.run` under it), so no phone, no adb.
The Contacts editor fixture is hand-written in the shape of a uiautomator
dump; see the comment at its top.
"""

import base64
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "api"))

import server as control_api  # noqa: E402
import ui_control  # noqa: E402
from cloudphone_logging import clear_logs, recent_logs  # noqa: E402

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "ui", "contacts_editor.xml")
with open(FIXTURE, encoding="utf-8") as _fh:
    CONTACTS_XML = _fh.read()
# The Android 13 platform dumper may not write `hint`: same screen without it.
CONTACTS_XML_NO_HINT = re.sub(r' hint="[^"]*"', "", CONTACTS_XML)
TYPED = ("Avery", "Example", "avery@example.test")

WM_SIZE = "Physical size: 1080x2400"


def android_send_text(arg):
    """What Android's `input text` types for one argument (InputShellCommand)."""
    buff = list(arg)
    escape = False
    i = 0
    while i < len(buff):
        if escape:
            escape = False
            if buff[i] == "s":
                buff[i] = " "
                i -= 1
                del buff[i]
        if buff[i] == "%":
            escape = True
        i += 1
    return "".join(buff)


def typed_by(commands):
    """Device shell parse + `input text` decoding of a command list."""
    out = []
    for command in commands:
        argv = shlex.split(command)
        assert argv[:2] == ["input", "text"] and len(argv) == 3, command
        out.append(android_send_text(argv[2]))
    return "".join(out)


class FakeAdb:
    """Ordered (substring -> (ok, stdout, stderr)) rules, first match wins."""

    def __init__(self, rules=None):
        self.rules = list(rules or [])
        self.calls = []

    def __call__(self, *args, timeout=30):
        joined = " ".join(str(a) for a in args)
        self.calls.append(joined)
        for needle, outcome in self.rules:
            if needle in joined:
                return outcome
        if args and args[0] == "devices":
            return True, f"List of devices attached\n{control_api.current_adb_connect()}\tdevice", ""
        return True, "", ""

    def shell(self):
        return [c[len("shell "):] for c in self.calls if c.startswith("shell ")]


class Base(unittest.TestCase):
    def setUp(self):
        self.client = control_api.app.test_client()
        control_api.API_TOKEN = ""
        control_api._SCREEN_SIZE_CACHE.clear()
        control_api._UNICODE_PATH_CACHE.clear()

    def send(self, payload, rules=None):
        adb = FakeAdb(rules)
        with patch.object(control_api, "run_adb", adb):
            body = self.client.post("/device/input", json=payload).get_json()
        return body, adb.shell()


class HonestResultTests(Base):
    def test_failed_adb_is_reported_for_every_input_type(self):
        failing = [("input ", (False, "", "error: device offline"))]
        dump = [("uiautomator dump", (True, CONTACTS_XML, ""))]
        cases = [
            {"type": "tap", "x": 1, "y": 2},
            {"type": "swipe", "x1": 1, "y1": 2, "x2": 3, "y2": 4},
            {"type": "key", "keycode": 4},
            {"type": "text", "text": "hello"},
        ]
        for payload in cases:
            body, _ = self.send(payload, failing)
            self.assertFalse(body["success"], payload)
            self.assertEqual(body["error_code"], "adb_failed", payload)
            self.assertIn("device offline", body["error"])
        body, _ = self.send({"type": "tap_label", "label": "Save"}, dump + failing)
        self.assertFalse(body["success"])
        self.assertEqual(body["error_code"], "adb_failed")

    def test_exception_text_with_exit_zero_is_a_failure(self):
        # Old adb shell protocols report 0 whatever the device command did.
        rules = [("input tap", (True, "", "Exception occurred while executing 'tap':"))]
        body, _ = self.send({"type": "tap", "x": 5, "y": 5}, rules)
        self.assertFalse(body["success"])
        self.assertEqual(body["error_code"], "input_rejected")

    def test_timeout_has_its_own_code(self):
        body, _ = self.send({"type": "key", "keycode": 3},
                            [("input keyevent", (False, "", "Command timed out"))])
        self.assertEqual(body["error_code"], "adb_timeout")

    def test_success_is_still_reported(self):
        body, calls = self.send({"type": "tap", "x": 10, "y": 20})
        self.assertTrue(body["success"])
        self.assertEqual((body["x"], body["y"]), (10, 20))
        self.assertIn("input tap 10 20", calls)

    def test_unknown_type_is_not_a_success(self):
        body, calls = self.send({"type": "teleport"})
        self.assertFalse(body["success"])
        self.assertEqual(body["error_code"], "unsupported_type")
        self.assertFalse([c for c in calls if c.startswith("input")])

    def test_keycode_cannot_smuggle_a_shell_command(self):
        body, calls = self.send({"type": "key", "keycode": "4; reboot"})
        self.assertFalse(body["success"])
        self.assertEqual(body["error_code"], "bad_request")
        self.assertFalse(any("reboot" in c for c in calls))

    def test_named_keycodes_are_accepted(self):
        for value, sent in (("KEYCODE_ENTER", "KEYCODE_ENTER"), ("back", "4"), (66, "66")):
            body, calls = self.send({"type": "key", "keycode": value})
            self.assertTrue(body["success"], value)
            self.assertIn(f"input keyevent {sent}", calls)

    def test_coordinates_must_be_numbers(self):
        body, calls = self.send({"type": "tap", "x": "1;reboot", "y": 2})
        self.assertEqual(body["error_code"], "bad_request")
        self.assertFalse(any("reboot" in c for c in calls))
        body, _ = self.send({"type": "swipe", "x1": 1, "y1": 2, "x2": 3, "y2": 4,
                             "duration": "fast"})
        self.assertEqual(body["error_code"], "bad_request")

    def test_screen_action_reports_adb_failure(self):
        adb = FakeAdb([("input keyevent", (False, "", "device offline"))])
        with patch.object(control_api, "run_adb", adb):
            body = self.client.post("/device/screen", json={"action": "on"}).get_json()
        self.assertFalse(body["success"])
        self.assertEqual(body["error_code"], "adb_failed")


class PercentageTests(Base):
    def test_percentage_tap_maps_to_device_pixels(self):
        body, calls = self.send({"type": "tap", "x": 50, "y": 25, "percentage": True},
                                [("wm size", (True, WM_SIZE, ""))])
        self.assertTrue(body["success"])
        self.assertIn("input tap 540 600", calls)
        self.assertEqual((body["x"], body["y"]), (540, 600))
        self.assertEqual(body["screen"], {"width": 1080, "height": 2400})
        self.assertTrue(body["percentage"])

    def test_override_size_is_what_input_uses(self):
        rules = [("wm size", (True, WM_SIZE + "\nOverride size: 720x1600", ""))]
        _, calls = self.send({"type": "tap", "x": 100, "y": 100, "percentage": True}, rules)
        self.assertIn("input tap 719 1599", calls, "100% is the last pixel, not one past it")

    def test_screen_size_is_cached_per_phone(self):
        adb = FakeAdb([("wm size", (True, WM_SIZE, ""))])
        with patch.object(control_api, "run_adb", adb):
            for _ in range(3):
                self.client.post("/device/input",
                                 json={"type": "tap", "x": 10, "y": 10, "percentage": True})
        self.assertEqual(sum("wm size" in c for c in adb.calls), 1)

    def test_percentage_out_of_range_is_refused(self):
        body, calls = self.send({"type": "tap", "x": 150, "y": 10, "percentage": True},
                                [("wm size", (True, WM_SIZE, ""))])
        self.assertEqual(body["error_code"], "bad_request")
        self.assertFalse([c for c in calls if c.startswith("input")])

    def test_unknown_screen_size_is_not_guessed(self):
        body, calls = self.send({"type": "tap", "x": 50, "y": 50, "percentage": True},
                                [("wm size", (False, "", "device offline"))])
        self.assertEqual(body["error_code"], "screen_size_unavailable")
        self.assertFalse([c for c in calls if c.startswith("input")])

    def test_percentage_swipe(self):
        _, calls = self.send({"type": "swipe", "x1": 50, "y1": 80, "x2": 50, "y2": 20,
                              "duration": 200, "percentage": True},
                             [("wm size", (True, WM_SIZE, ""))])
        self.assertIn("input swipe 540 1920 540 480 200", calls)

    def test_pixel_tap_does_not_read_the_size(self):
        _, calls = self.send({"type": "tap", "x": 50, "y": 50})
        self.assertNotIn("wm size", calls)


TRICKY = [
    "O'Brien", "hello world", "$HOME", "a&b", "x;y", "`id`", "$(reboot)",
    '"quoted"', "back\\slash", "pipe|<>(){}[]*?~#!", "100%sure", "50% off",
    "%s", "%%s", "% s", "  two leading", "trailing ", "a'b'c", "''",
]


class TextEncodingTests(Base):
    def test_encoding_round_trips_through_shell_and_input(self):
        for text in TRICKY:
            commands = ui_control.input_text_commands(text)
            self.assertEqual(typed_by(commands), text, text)

    @unittest.skipUnless(shutil.which("sh"), "needs a POSIX sh")
    def test_a_real_posix_shell_sees_one_literal_argument(self):
        for text in TRICKY:
            for command in ui_control.input_text_commands(text):
                arg = command[len("input text "):]
                out = subprocess.run(["sh", "-c", "printf '%s' " + arg],
                                     capture_output=True, text=True, check=True).stdout
                self.assertEqual(out, shlex.split(command)[2], text)

    def test_obrien_goes_out_quoted(self):
        body, calls = self.send({"type": "text", "text": "O'Brien"})
        self.assertTrue(body["success"])
        self.assertEqual(body["path"], "input_text")
        self.assertIn("input text 'O'\\''Brien'", calls)
        self.assertNotIn("O'Brien", json.dumps(body), "the reply never echoes the text")

    def test_spaces_and_metacharacters_from_the_endpoint(self):
        _, calls = self.send({"type": "text", "text": "Tom & Jerry; $5"})
        typed = [c for c in calls if c.startswith("input text")]
        self.assertEqual(typed_by(typed), "Tom & Jerry; $5")

    def test_empty_text_sends_nothing(self):
        body, calls = self.send({"type": "text", "text": ""})
        self.assertTrue(body["success"])
        self.assertFalse([c for c in calls if c.startswith("input")])

    def test_ui_text_commandlet_uses_the_same_encoder(self):
        self.assertEqual(ui_control.build_adb_input({"action": "text", "text": "O'Brien"}, (1, 1)),
                         ["input text 'O'\\''Brien'"])

    def test_typed_value_never_reaches_logs(self):
        clear_logs()
        secret = "Avery O'Example"

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, "", "Exception: " + secret)

        with patch.dict(os.environ, {"CLOUD_PHONE_VERBOSE": "1"}), \
                patch.object(control_api, "ensure_adb_connected", lambda: True), \
                patch.object(control_api.subprocess, "run", fake_run):
            body = self.client.post("/device/input", json={"type": "text", "text": secret}).get_json()
        self.assertFalse(body["success"])
        lines = " ".join(item["msg"] for item in recent_logs(n=500))
        self.assertIn("input text", lines)
        for fragment in ("Avery", "Example"):
            self.assertNotIn(fragment, lines)
            self.assertNotIn(fragment, json.dumps(body))
            self.assertNotIn(fragment, control_api._state["last_adb_error"])


def focused_field_dump(text, hint="First name", password=False):
    return (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?><hierarchy rotation=\"0\">"
        f'<node index="0" text="{text}" hint="{hint}" resource-id="app:id/first" '
        'class="android.widget.EditText" package="app" content-desc="" checkable="false" '
        'checked="false" clickable="true" enabled="true" focusable="true" focused="true" '
        f'scrollable="false" long-clickable="true" password="{str(password).lower()}" '
        'selected="false" bounds="[0,0][100,100]" /></hierarchy>'
    )


class ClearBeforeTypeTests(Base):
    def test_clear_deletes_the_current_length_then_types(self):
        rules = [("uiautomator dump", (True, focused_field_dump("Avery"), ""))]
        body, calls = self.send({"type": "text", "text": "Blake", "clear": True}, rules)
        self.assertTrue(body["success"])
        inputs = [c for c in calls if c.startswith("input")]
        self.assertEqual(inputs[0], "input keyevent KEYCODE_MOVE_END")
        keys = " ".join(inputs[1:-1])
        self.assertEqual(keys.count("112"), 5, "forward-delete covers text after the cursor")
        self.assertEqual(keys.count("67"), 5, "delete bounded by the current length")
        self.assertTrue(inputs[-1].startswith("input text"), "typing comes after the clear")
        self.assertEqual(body["cleared"]["chars"], 5)

    def test_a_field_showing_its_hint_is_already_empty(self):
        rules = [("uiautomator dump", (True, focused_field_dump("First name"), ""))]
        body, calls = self.send({"type": "text", "text": "Blake", "clear": True}, rules)
        self.assertEqual(body["cleared"]["chars"], 0)
        self.assertFalse([c for c in calls if " 67" in c])

    def test_clear_without_a_focused_field_types_nothing(self):
        rules = [("uiautomator dump", (True, CONTACTS_XML.replace('focused="true"', 'focused="false"'), ""))]
        body, calls = self.send({"type": "text", "text": "Blake", "clear": True}, rules)
        self.assertFalse(body["success"])
        self.assertEqual(body["error_code"], "no_focused_field")
        self.assertFalse([c for c in calls if c.startswith("input text")])

    def test_failed_clear_stops_before_typing(self):
        rules = [("uiautomator dump", (True, focused_field_dump("Avery"), "")),
                 ("input keyevent", (False, "", "device offline"))]
        body, calls = self.send({"type": "text", "text": "Blake", "clear": True}, rules)
        self.assertFalse(body["success"])
        self.assertFalse([c for c in calls if c.startswith("input text")])

    def test_long_text_is_cleared_in_bounded_batches(self):
        rules = [("uiautomator dump", (True, focused_field_dump("x" * 100), ""))]
        _, calls = self.send({"type": "text", "text": "y", "clear": True}, rules)
        batches = [c for c in calls if c.startswith("input keyevent 67")]
        self.assertGreater(len(batches), 1)
        self.assertEqual(sum(c.split().count("67") for c in batches), 100)


class UnicodeTextTests(Base):
    def test_adbkeyboard_when_it_is_the_active_ime(self):
        rules = [("default_input_method", (True, "com.android.adbkeyboard/.AdbIME", ""))]
        body, calls = self.send({"type": "text", "text": "Zoë 李"}, rules)
        self.assertTrue(body["success"])
        self.assertEqual(body["path"], "adbkeyboard")
        b64 = base64.b64encode("Zoë 李".encode("utf-8")).decode()
        self.assertIn(f"am broadcast -a ADB_INPUT_B64 --es msg {b64}", calls)
        self.assertFalse([c for c in calls if c.startswith("input text")])

    def test_clipboard_and_paste_when_cmd_clipboard_works(self):
        rules = [("default_input_method", (True, "com.android.inputmethod.latin/.LatinIME", ""))]
        body, calls = self.send({"type": "text", "text": "Zoë"}, rules)
        self.assertTrue(body["success"])
        self.assertEqual(body["path"], "cmd_clipboard")
        set_idx = next(i for i, c in enumerate(calls) if c.startswith("cmd clipboard set-text 'Zoë'"))
        paste_idx = calls.index("input keyevent 279")
        self.assertLess(set_idx, paste_idx)
        self.assertTrue(body["clipboard_cleared"])
        self.assertIn("cmd clipboard set-text ''", calls[paste_idx:])

    def test_clipper_broadcast_when_installed(self):
        rules = [
            ("default_input_method", (True, "com.android.inputmethod.latin/.LatinIME", "")),
            ("cmd clipboard", (False, "", "No shell command implementation.")),
            ("pm path ca.zgrs.clipper", (True, "package:/data/app/clipper.apk", "")),
            ("clipper.set", (True, 'Broadcast completed: result=-1, data="Text is copied into clipboard."', "")),
        ]
        body, calls = self.send({"type": "text", "text": "Zoë"}, rules)
        self.assertTrue(body["success"])
        self.assertEqual(body["path"], "clipper_broadcast")
        self.assertIn("input keyevent 279", calls)

    def test_no_path_is_reported_honestly(self):
        rules = [
            ("default_input_method", (True, "com.android.inputmethod.latin/.LatinIME", "")),
            ("cmd clipboard", (True, "No shell command implementation.", "")),
            ("pm path ca.zgrs.clipper", (False, "", "")),
        ]
        body, calls = self.send({"type": "text", "text": "Zoë"}, rules)
        self.assertFalse(body["success"])
        self.assertEqual(body["error_code"], "unsupported_unicode")
        self.assertEqual(body["tried"], ["adbkeyboard", "cmd_clipboard", "clipper_broadcast"])
        self.assertFalse([c for c in calls if c.startswith("input")])
        self.assertNotIn("Zo", json.dumps(body))

    def test_working_path_is_remembered(self):
        adb = FakeAdb([("default_input_method", (True, "com.android.adbkeyboard/.AdbIME", ""))])
        with patch.object(control_api, "run_adb", adb):
            for _ in range(2):
                self.client.post("/device/input", json={"type": "text", "text": "ñ"})
        self.assertEqual(sum("default_input_method" in c for c in adb.calls), 1)

    def test_ascii_never_touches_the_clipboard(self):
        _, calls = self.send({"type": "text", "text": "plain ascii"})
        self.assertFalse([c for c in calls if "clipboard" in c or "broadcast" in c])


class RicherDumpTests(Base):
    def elements(self, xml=CONTACTS_XML, include_values=False):
        return {e["resource_id"].split("/")[-1]: e
                for e in control_api._parse_ui_elements(xml, include_values=include_values)
                if e["resource_id"]}

    def test_new_attributes(self):
        els = self.elements()
        first = els["contact_first_name"]
        self.assertEqual(first["class"], "android.widget.EditText")
        self.assertEqual(first["package"], "org.fossify.contacts")
        self.assertTrue(first["editable"])
        self.assertTrue(first["focusable"])
        self.assertTrue(first["long_clickable"])
        self.assertTrue(first["enabled"])
        self.assertEqual(first["hint"], "First name")
        starred = els["contact_starred"]
        self.assertTrue(starred["checkable"])
        self.assertTrue(starred["checked"])
        self.assertFalse(starred["editable"])
        self.assertTrue(els["contact_scrollview"]["scrollable"])
        self.assertFalse(els["contact_ringtone"]["enabled"])
        self.assertIsNone(els["contact_number_type"]["hint"])
        self.assertTrue(els["contact_surname"]["focused"])

    def test_old_keys_are_kept(self):
        save = self.elements()["save"]
        for key in ("label", "resource_id", "clickable", "focused", "password", "x", "y", "bounds"):
            self.assertIn(key, save)
        self.assertEqual(save["label"], "Save")
        self.assertEqual((save["x"], save["y"]), (1017, 136))

    def test_typed_values_are_redacted_by_default(self):
        els = self.elements()
        first = els["contact_first_name"]
        self.assertTrue(first["value_redacted"])
        self.assertIsNone(first["text"])
        self.assertEqual(first["text_len"], 5)
        self.assertEqual(first["label"], "First name", "the hint stands in for the value")
        self.assertEqual(els["contact_email"]["text_len"], len("avery@example.test"))
        blob = json.dumps(control_api._parse_ui_elements(CONTACTS_XML))
        for value in TYPED:
            self.assertNotIn(value, blob)

    def test_a_field_showing_its_hint_is_not_a_value(self):
        middle = self.elements()["contact_middle_name"]
        self.assertFalse(middle["value_redacted"])
        self.assertEqual(middle["text_state"], "hint")
        self.assertEqual(middle["label"], "Middle name")

    def test_non_editable_text_is_untouched(self):
        mobile = self.elements()["contact_number_type"]
        self.assertEqual(mobile["text"], "Mobile")
        self.assertFalse(mobile["value_redacted"])

    def test_include_values_returns_them(self):
        els = self.elements(include_values=True)
        self.assertEqual(els["contact_first_name"]["text"], "Avery")
        self.assertEqual(els["contact_first_name"]["label"], "Avery")
        self.assertFalse(els["contact_first_name"]["value_redacted"])

    def test_without_hint_attribute_nonempty_inputs_stay_redacted(self):
        els = self.elements(CONTACTS_XML_NO_HINT)
        for rid in ("contact_first_name", "contact_middle_name", "contact_email"):
            self.assertIsNone(els[rid]["hint"], rid)
            self.assertEqual(els[rid]["text_state"], "unknown", rid)
            self.assertTrue(els[rid]["value_redacted"], rid)
        blob = json.dumps(control_api._parse_ui_elements(CONTACTS_XML_NO_HINT))
        for value in TYPED:
            self.assertNotIn(value, blob)

    def test_password_text_is_redacted_even_with_include_values(self):
        xml = focused_field_dump("••••••", hint="Password", password=True)
        el = control_api._parse_ui_elements(xml, include_values=True)[0]
        self.assertTrue(el["value_redacted"])
        self.assertIsNone(el["text"])
        self.assertEqual(el["text_len"], 6)

    def test_endpoint_redacts_unless_asked(self):
        adb = FakeAdb([("uiautomator dump", (True, CONTACTS_XML, ""))])
        with patch.object(control_api, "run_adb", adb):
            plain = self.client.get("/device/ui").get_json()
            full = self.client.get("/device/ui?include_values=1").get_json()
            posted = self.client.post("/device/ui", json={"include_values": True}).get_json()
        for value in TYPED:
            self.assertNotIn(value, json.dumps(plain))
        self.assertFalse(plain["values_included"])
        self.assertTrue(plain["hints_exposed"])
        self.assertTrue(full["values_included"])
        self.assertIn("avery@example.test", json.dumps(full))
        self.assertIn("avery@example.test", json.dumps(posted))

    def test_hints_exposed_is_false_without_hint_attributes(self):
        adb = FakeAdb([("uiautomator dump", (True, CONTACTS_XML_NO_HINT, ""))])
        with patch.object(control_api, "run_adb", adb):
            body = self.client.get("/device/ui").get_json()
        self.assertFalse(body["hints_exposed"])

    def test_tap_label_matches_raw_text_but_replies_redacted(self):
        rules = [("uiautomator dump", (True, CONTACTS_XML, ""))]
        body, calls = self.send({"type": "tap_label", "label": "surname"}, rules)
        self.assertTrue(body["success"])
        self.assertIn("input tap 613 1249", calls)
        self.assertTrue(body["matched"]["value_redacted"])
        self.assertNotIn("Example", json.dumps(body))

    def test_tap_label_by_hint_text_still_works(self):
        rules = [("uiautomator dump", (True, CONTACTS_XML_NO_HINT, ""))]
        body, _ = self.send({"type": "tap_label", "label": "Middle name"}, rules)
        self.assertTrue(body["success"])
        self.assertEqual(body["matched"]["resource_id"], "org.fossify.contacts:id/contact_middle_name")

    def test_tap_label_miss_lists_no_typed_values(self):
        rules = [("uiautomator dump", (True, CONTACTS_XML, ""))]
        body, _ = self.send({"type": "tap_label", "label": "Checkout"}, rules)
        self.assertFalse(body["success"])
        self.assertIn("Save", body["available"])
        for value in TYPED:
            self.assertNotIn(value, json.dumps(body))


if __name__ == "__main__":
    unittest.main()
