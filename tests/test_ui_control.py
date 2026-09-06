#!/usr/bin/env python3
"""Unit tests for api.ui_control (pure UI commandlet logic)."""

import unittest

from api import ui_control as ui


class UIControlTests(unittest.TestCase):
    SIZE = (1280, 720)

    def test_select_backend(self):
        self.assertEqual(ui.select_backend(None), "adb")
        self.assertEqual(ui.select_backend("adb"), "adb")
        self.assertEqual(ui.select_backend("appium", appium_available=True), "appium")
        with self.assertRaises(ui.UIError):
            ui.select_backend("appium", appium_available=False)
        with self.assertRaises(ui.UIError):
            ui.select_backend("nonsense")

    def test_parse_wm_size(self):
        self.assertEqual(ui.parse_wm_size("Physical size: 1080x2400"), (1080, 2400))
        self.assertEqual(
            ui.parse_wm_size("Physical size: 1080x2400\nOverride size: 720x1280"),
            (720, 1280),
        )
        with self.assertRaises(ui.UIError):
            ui.parse_wm_size("no size here")

    def test_to_pixels_variants(self):
        self.assertEqual(ui.to_pixels(540, 1080), 540)
        self.assertEqual(ui.to_pixels("50%", 1280), 640)
        self.assertEqual(ui.to_pixels(0.5, 720), 360)
        self.assertEqual(ui.to_pixels("100", 1080), 100)

    def test_tap_pixels_and_percent(self):
        self.assertEqual(
            ui.build_adb_input({"action": "tap", "x": 100, "y": 200}, self.SIZE),
            ["input tap 100 200"],
        )
        self.assertEqual(
            ui.build_adb_input({"action": "tap", "xp": 50, "yp": 50}, self.SIZE),
            ["input tap 640 360"],
        )

    def test_swipe_percent(self):
        cmd = {"action": "swipe", "x1p": 50, "y1p": 80, "x2p": 50, "y2p": 20, "duration": 250}
        self.assertEqual(
            ui.build_adb_input(cmd, self.SIZE),
            ["input swipe 640 576 640 144 250"],
        )

    def test_text_and_key(self):
        self.assertEqual(
            ui.build_adb_input({"action": "text", "text": "hello world"}, self.SIZE),
            ["input text 'hello%sworld'"],
        )
        self.assertEqual(
            ui.build_adb_input({"action": "key", "key": "back"}, self.SIZE),
            ["input keyevent 4"],
        )

    def test_appium_w3c_tap_matches_pixels(self):
        payload = ui.build_appium_actions({"action": "tap", "xp": 50, "yp": 50}, self.SIZE)
        moves = [a for a in payload["actions"][0]["actions"] if a["type"] == "pointerMove"]
        self.assertEqual(moves[0]["x"], 640)
        self.assertEqual(moves[0]["y"], 360)

    def test_long_press(self):
        self.assertEqual(
            ui.build_adb_input({"action": "long_press", "x": 10, "y": 20, "duration": 900}, self.SIZE),
            ["input swipe 10 20 10 20 900"],
        )

    def test_errors(self):
        with self.assertRaises(ui.UIError):
            ui.build_adb_input({"action": "tap"}, self.SIZE)
        with self.assertRaises(ui.UIError):
            ui.build_adb_input({"action": "frobnicate"}, self.SIZE)
        with self.assertRaises(ui.UIError):
            ui.resolve_keycode("not_a_key")


if __name__ == "__main__":
    unittest.main()
