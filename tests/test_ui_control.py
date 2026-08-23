#!/usr/bin/env python3
"""Unit tests for api.ui_control (pure UI commandlet logic)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

import ui_control as ui


class UIControlTests(unittest.TestCase):
    SIZE = (1080, 2400)

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
        self.assertEqual(ui.to_pixels(540, 1080), 540)        # pixels
        self.assertEqual(ui.to_pixels("50%", 1080), 540)      # percent string
        self.assertEqual(ui.to_pixels(0.5, 2400), 1200)       # float fraction
        self.assertEqual(ui.to_pixels("100", 1080), 100)      # pixel string

    def test_tap_pixels_and_percent(self):
        self.assertEqual(ui.build_adb_input({"action": "tap", "x": 100, "y": 200}, self.SIZE),
                         ["input tap 100 200"])
        self.assertEqual(ui.build_adb_input({"action": "tap", "xp": 50, "yp": 50}, self.SIZE),
                         ["input tap 540 1200"])

    def test_swipe_percent(self):
        cmd = {"action": "swipe", "x1p": 50, "y1p": 80, "x2p": 50, "y2p": 20, "duration": 250}
        self.assertEqual(ui.build_adb_input(cmd, self.SIZE),
                         ["input swipe 540 1920 540 480 250"])

    def test_text_and_key(self):
        self.assertEqual(ui.build_adb_input({"action": "text", "text": "hello world"}, self.SIZE),
                         ["input text 'hello%sworld'"])
        self.assertEqual(ui.build_adb_input({"action": "key", "key": "back"}, self.SIZE),
                         ["input keyevent 4"])
        self.assertEqual(ui.build_adb_input({"action": "key", "keycode": 66}, self.SIZE),
                         ["input keyevent 66"])

    def test_long_press(self):
        self.assertEqual(ui.build_adb_input({"action": "long_press", "x": 10, "y": 20, "duration": 900}, self.SIZE),
                         ["input swipe 10 20 10 20 900"])

    def test_errors(self):
        with self.assertRaises(ui.UIError):
            ui.build_adb_input({"action": "tap"}, self.SIZE)  # no coords
        with self.assertRaises(ui.UIError):
            ui.build_adb_input({"action": "frobnicate"}, self.SIZE)
        with self.assertRaises(ui.UIError):
            ui.resolve_keycode("not_a_key")


# Shape captured from `uiautomator dump` on the live Redroid 11 arm64 lab phone
# (org.chromium.webview_shell, 1280x720).
UI_DUMP_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout"
        package="org.chromium.webview_shell" content-desc="" checkable="false"
        checked="false" clickable="false" enabled="true" focusable="false"
        focused="false" scrollable="false" long-clickable="false"
        password="false" selected="false" bounds="[0,0][1280,720]">
    <node index="0" text="WebView Browser Tester" resource-id="android:id/title"
          class="android.widget.TextView" package="org.chromium.webview_shell"
          content-desc="" checkable="false" checked="false" clickable="false"
          enabled="true" focusable="false" focused="false" scrollable="false"
          long-clickable="false" password="false" selected="false"
          bounds="[6,37][1274,72]" />
    <node index="1" text="osl-mobileio-live-proof"
          resource-id="org.chromium.webview_shell:id/url_field"
          class="android.widget.EditText" package="org.chromium.webview_shell"
          content-desc="" checkable="false" checked="false" clickable="true"
          enabled="true" focusable="true" focused="true" scrollable="false"
          long-clickable="true" password="false" selected="false"
          bounds="[0,74][1106,146]" />
    <node index="2" text="" resource-id="" class="android.widget.ImageButton"
          package="org.chromium.webview_shell" content-desc="Go"
          checkable="false" checked="false" clickable="true" enabled="true"
          focusable="true" focused="false" scrollable="false"
          long-clickable="false" password="false" selected="false"
          bounds="[1106,74][1193,146]" />
    <node index="3" text="" resource-id="" class="android.widget.LinearLayout"
          package="org.chromium.webview_shell" content-desc="" checkable="false"
          checked="false" clickable="false" enabled="true" focusable="false"
          focused="false" scrollable="false" long-clickable="false"
          password="false" selected="false" bounds="[0,0][0,0]" />
  </node>
</hierarchy>
"""


class UIHierarchyTests(unittest.TestCase):
    def test_parse_bounds(self):
        box, center = ui.parse_bounds("[0,74][1106,146]")
        self.assertEqual(box, {"x1": 0, "y1": 74, "x2": 1106, "y2": 146,
                               "width": 1106, "height": 72})
        self.assertEqual(center, {"x": 553, "y": 110})
        self.assertEqual(ui.parse_bounds("garbage"), (None, None))
        self.assertEqual(ui.parse_bounds(None), (None, None))

    def test_parse_hierarchy_returns_labeled_elements(self):
        parsed = ui.parse_ui_hierarchy(UI_DUMP_XML)
        by_id = {e["resource_id"]: e for e in parsed["elements"]}
        self.assertEqual(parsed["rotation"], "0")

        url = by_id["org.chromium.webview_shell:id/url_field"]
        self.assertEqual(url["text"], "osl-mobileio-live-proof")
        self.assertEqual(url["label"], "osl-mobileio-live-proof")
        self.assertEqual(url["center"], {"x": 553, "y": 110})
        self.assertTrue(url["editable"])
        self.assertTrue(url["clickable"])
        self.assertTrue(url["focused"])

        title = by_id["android:id/title"]
        self.assertEqual(title["label"], "WebView Browser Tester")
        self.assertFalse(title["clickable"])

    def test_parse_hierarchy_uses_content_desc_as_label(self):
        parsed = ui.parse_ui_hierarchy(UI_DUMP_XML)
        go = next(e for e in parsed["elements"] if e["class"].endswith("ImageButton"))
        self.assertEqual(go["label"], "Go")
        self.assertEqual(go["content_desc"], "Go")

    def test_parse_hierarchy_drops_noise(self):
        parsed = ui.parse_ui_hierarchy(UI_DUMP_XML)
        # Zero-area node and the unlabeled root FrameLayout carry no signal.
        self.assertTrue(all(e["bounds"]["width"] > 0 for e in parsed["elements"]))
        self.assertEqual(parsed["count"], len(parsed["elements"]))
        self.assertNotIn("android.widget.FrameLayout",
                         [e["class"] for e in parsed["elements"]])

    def test_interactive_only_filter(self):
        parsed = ui.parse_ui_hierarchy(UI_DUMP_XML, interactive_only=True)
        classes = sorted(e["class"] for e in parsed["elements"])
        self.assertEqual(classes, ["android.widget.EditText",
                                   "android.widget.ImageButton"])

    def test_parse_current_focus(self):
        dumpsys = (
            "  mFocusedApp=ActivityRecord{abc u0 org.chromium.webview_shell/.X}\n"
            "  mCurrentFocus=Window{34f1993 u0 org.chromium.webview_shell/"
            "org.chromium.webview_shell.WebViewBrowserActivity}\n"
        )
        self.assertIn("WebViewBrowserActivity", ui.parse_current_focus(dumpsys))
        self.assertEqual(ui.parse_current_focus(""), "")
        self.assertEqual(ui.parse_current_focus("nothing here"), "")


if __name__ == "__main__":
    unittest.main()
