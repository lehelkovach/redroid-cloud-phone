#!/usr/bin/env python3
"""scripts/real-app-smoke.py: offline checks, plus a live run on a real phone.

The live class runs only with CLOUD_PHONE_REAL_DEVICE=1 and a reachable
Control API (CLOUD_PHONE_API_URL, token in CLOUD_PHONE_API_TOKEN). Offline
tests never open a socket to a phone.
"""

import importlib.util
import os
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "real_app_smoke", os.path.join(ROOT, "scripts", "real-app-smoke.py"))
smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke)

REAL = os.environ.get("CLOUD_PHONE_REAL_DEVICE", "") in {"1", "true", "yes"}


def el(rid="", label="", x=10, y=20, clickable=True):
    return {"resource_id": "pkg:id/" + rid if rid else "", "label": label,
            "x": x, "y": y, "clickable": clickable}


class FakeApi:
    def __init__(self, screens=None, shell_out=""):
        self.screens = list(screens or [])
        self.shell_out = shell_out
        self.calls = []

    def ui(self):
        return {"success": True, "elements": self.screens[0] if self.screens else []}

    def tap(self, x, y):
        self.calls.append(("tap", x, y))
        if len(self.screens) > 1:
            self.screens.pop(0)
        return {"success": True}

    def key(self, code):
        self.calls.append(("key", code))
        return {"success": True}

    def shell(self, command):
        self.calls.append(("shell", command))
        return {"success": True, "stdout": self.shell_out}


class OfflineTests(unittest.TestCase):
    def test_pins_are_https_f_droid_with_full_sha256(self):
        for name, app in smoke.APPS.items():
            self.assertTrue(app["url"].startswith("https://f-droid.org/repo/"), name)
            self.assertRegex(app["sha256"], r"^[0-9a-f]{64}$", name)
            self.assertIn(app["package"], app["url"], name)

    def test_find_by_short_id_label_and_substring(self):
        els = [el("result", "42"), el("text_note_view", "hello smoke123")]
        self.assertEqual(smoke.find(els, rid="result", label="42")["label"], "42")
        self.assertIsNone(smoke.find(els, rid="result", label="41"))
        self.assertIsNotNone(smoke.find(els, contains="smoke123"))

    def test_permission_prompt_for_the_app_is_allowed(self):
        api = FakeApi([[el("permission_message", "Allow Contacts to access your contacts?"),
                        el("permission_allow_button", "ALLOW", 1, 2),
                        el("permission_deny_button", "DON'T ALLOW", 3, 4)], []])
        with mock.patch.object(smoke.time, "sleep"):
            out = smoke.answer_permission_prompt(api, "Allow Contacts")
        self.assertTrue(out.startswith("allowed"))
        self.assertIn(("tap", 1, 2), api.calls)

    def test_permission_prompt_for_another_app_is_denied(self):
        # The AOSP keyboard asks for contacts the first time it opens there.
        api = FakeApi([[el("permission_message", "Allow Android Keyboard (AOSP) to access your contacts?"),
                        el("permission_allow_button", "ALLOW", 1, 2),
                        el("permission_deny_button", "DON'T ALLOW", 3, 4)], []])
        with mock.patch.object(smoke.time, "sleep"):
            out = smoke.answer_permission_prompt(api, "Allow Contacts")
        self.assertTrue(out.startswith("denied"))
        self.assertIn(("tap", 3, 4), api.calls)

    def test_no_prompt_means_no_tap(self):
        api = FakeApi([[el("result", "0")]])
        self.assertIsNone(smoke.answer_permission_prompt(api, "Allow Contacts"))
        self.assertEqual(api.calls, [])

    def test_back_only_while_the_keyboard_is_up(self):
        down = FakeApi(shell_out="mInputShown=false")
        with mock.patch.object(smoke.time, "sleep"):
            self.assertFalse(smoke.hide_keyboard(down))
        self.assertNotIn(("key", smoke.KEY_BACK), down.calls)
        up = FakeApi(shell_out="  mShowRequested=true mInputShown=true")
        with mock.patch.object(smoke.time, "sleep"):
            self.assertTrue(smoke.hide_keyboard(up))
        self.assertIn(("key", smoke.KEY_BACK), up.calls)

    def test_skips_without_a_url(self):
        with mock.patch.dict(os.environ, {"CLOUD_PHONE_API_URL": ""}):
            self.assertEqual(smoke.main(["--api-url", ""]), smoke.EXIT_SKIP)

    def test_skips_when_nothing_answers(self):
        with mock.patch.object(smoke.Api, "health", side_effect=OSError("refused")):
            self.assertEqual(smoke.main(["--api-url", "http://phone.invalid:8080"]), smoke.EXIT_SKIP)

    def test_skips_an_unusable_phone(self):
        with mock.patch.object(smoke.Api, "health", return_value={"status": "unhealthy", "usable": False}):
            self.assertEqual(smoke.main(["--api-url", "http://phone.invalid:8080"]), smoke.EXIT_SKIP)

    def test_unknown_app_is_a_usage_error(self):
        self.assertEqual(smoke.main(["--api-url", "http://x", "--apps", "tinder"]), 64)

    def test_token_from_file_then_env(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write("file-token\n")
        try:
            self.assertEqual(smoke.read_token(fh.name), "file-token")
        finally:
            os.unlink(fh.name)
        with mock.patch.dict(os.environ, {"CLOUD_PHONE_API_TOKEN": "env-token"}):
            self.assertEqual(smoke.read_token(None), "env-token")


@unittest.skipUnless(REAL, "set CLOUD_PHONE_REAL_DEVICE=1 (and CLOUD_PHONE_API_URL) to drive a real phone")
class RealDeviceTests(unittest.TestCase):
    def test_three_apps_end_to_end(self):
        out = tempfile.mkdtemp(prefix="real-app-smoke-")
        rc = smoke.main(["--out", out])
        if rc == smoke.EXIT_SKIP:
            self.skipTest("no usable phone at CLOUD_PHONE_API_URL")
        self.assertEqual(rc, 0, "a step failed; screenshots in " + out)


if __name__ == "__main__":
    unittest.main()
