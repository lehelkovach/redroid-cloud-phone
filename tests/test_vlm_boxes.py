#!/usr/bin/env python3
"""R0: VLM box parse → CPMS roles → tap-centre fill plan (no network)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from orchestrator import vlm_boxes
from fixtures.fake_vlm import FakeVlm, LABELED_DUMP, EMPTY_DUMP, LOGIN_BOXES


class ParseBboxTests(unittest.TestCase):
    def test_list_form(self):
        self.assertEqual(vlm_boxes.parse_bbox([10, 20, 110, 80]), [10, 20, 110, 80])

    def test_xywh(self):
        self.assertEqual(
            vlm_boxes.parse_bbox({"x": 10, "y": 20, "w": 100, "h": 60}),
            [10, 20, 110, 80],
        )

    def test_clamps_to_screen(self):
        box = vlm_boxes.parse_bbox([-10, -10, 9999, 9999], {"width": 100, "height": 50})
        self.assertEqual(box, [0, 0, 100, 50])

    def test_center(self):
        self.assertEqual(vlm_boxes.bbox_center([200, 180, 1080, 260]), (640, 220))


class RoleAndDumpTests(unittest.TestCase):
    def test_email_aliases_username(self):
        self.assertEqual(vlm_boxes.normalize_role("email"), "username")
        self.assertEqual(vlm_boxes.normalize_role("Password"), "password")

    def test_labeled_dump_is_enough(self):
        fields = vlm_boxes.fields_from_dump(LABELED_DUMP)
        roles = {f["role"] for f in fields}
        self.assertEqual(roles, {"username", "password", "submit"})
        self.assertFalse(vlm_boxes.needs_vision_fallback(fields))

    def test_empty_dump_needs_vlm(self):
        self.assertTrue(vlm_boxes.needs_vision_fallback(vlm_boxes.fields_from_dump(EMPTY_DUMP)))

    def test_half_form_needs_vlm(self):
        fields = [{"role": "username", "type": "username"}]
        self.assertTrue(vlm_boxes.needs_vision_fallback(fields))

    def test_captcha_is_not_vlm_solved(self):
        fields = [{"role": "captcha", "refused": True}]
        self.assertFalse(vlm_boxes.needs_vision_fallback(fields))


class DetectAndPlanTests(unittest.TestCase):
    def test_vlm_fallback_on_empty_dump(self):
        vlm = FakeVlm()
        det = vlm_boxes.detect_form_boxes(
            image_b64="AAAA",
            screen={"width": 1280, "height": 720},
            ui_dump=EMPTY_DUMP,
            vlm=vlm,
        )
        self.assertTrue(det["ok"])
        self.assertEqual(det["source"], "gemini-vision")
        self.assertEqual(len(vlm.calls), 1)
        roles = {f["role"] for f in det["fields"]}
        self.assertEqual(roles, {"username", "password", "submit"})

    def test_dump_skips_vlm(self):
        vlm = FakeVlm()
        det = vlm_boxes.detect_form_boxes(ui_dump=LABELED_DUMP, vlm=vlm)
        self.assertEqual(det["source"], "uiautomator")
        self.assertEqual(vlm.calls, [])

    def test_missing_vlm_reports_needs_vision(self):
        det = vlm_boxes.detect_form_boxes(ui_dump=EMPTY_DUMP, vlm=None)
        self.assertFalse(det["ok"])
        self.assertTrue(det["needs_vision"])

    def test_fill_plan_taps_centres_and_skips_submit(self):
        fields = vlm_boxes.fields_from_vlm(LOGIN_BOXES, {"width": 1280, "height": 720})
        plan = vlm_boxes.plan_fill_steps(
            fields,
            {"username": "bs@example.com", "password": "fake-password-not-real"},
            include_submit=False,
        )
        self.assertTrue(plan["ok"])
        self.assertTrue(plan["approvalRequired"])
        actions = [s["action"] for s in plan["steps"]]
        self.assertEqual(actions, ["tap", "type", "tap", "type"])
        self.assertNotIn("submit", actions)
        taps = [s for s in plan["steps"] if s["action"] == "tap"]
        self.assertEqual((taps[0]["x"], taps[0]["y"]), (640, 220))
        self.assertEqual((taps[1]["x"], taps[1]["y"]), (640, 320))

    def test_submit_is_gated_action(self):
        fields = vlm_boxes.fields_from_vlm(LOGIN_BOXES)
        plan = vlm_boxes.plan_fill_steps(
            fields, {"email": "a@b.c", "password": "x"}, include_submit=True,
        )
        self.assertEqual(plan["steps"][-1]["action"], "submit")

    def test_refused_captcha_box_is_dropped_from_plan(self):
        payload = {
            "elements": [
                {"role": "email", "bbox": [0, 0, 10, 10]},
                {"role": "password", "bbox": [0, 20, 10, 30]},
                {"role": "captcha", "bbox": [0, 40, 10, 50]},
            ]
        }
        fields = vlm_boxes.fields_from_vlm(payload)
        refused = [f for f in fields if f.get("refused")]
        self.assertEqual(len(refused), 1)
        plan = vlm_boxes.plan_fill_steps(
            fields, {"username": "u", "password": "p"}, include_submit=False,
        )
        self.assertTrue(all(s.get("role") != "captcha" for s in plan["steps"]))


if __name__ == "__main__":
    unittest.main()
