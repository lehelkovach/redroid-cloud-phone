#!/usr/bin/env python3
"""Live view: a multipart frame stream straight from the Control API.

The Watch page polled `GET /device/screenshot` one PNG at a time through the
agent box (0.3–0.6 s per round trip, ~2 fps). This route pushes frames as
`multipart/x-mixed-replace` so a viewer (or OSLO's watch backend) holds one
connection and gets each screencap as soon as it exists. The screencap itself
is patched here, so the framing and bookkeeping are what is under test.
"""

import os
import re
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "api"))

import server as control_api  # noqa: E402
import device_control  # noqa: E402
import viewport  # noqa: E402

from tests.test_control_api import fake_adb  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class FramingTests(unittest.TestCase):
    def test_part_has_boundary_type_and_length(self):
        part = device_control.mjpeg_part(PNG, "image/png")
        self.assertTrue(part.startswith(b"--" + device_control.MJPEG_BOUNDARY.encode() + b"\r\n"))
        self.assertIn(b"Content-Type: image/png\r\n", part)
        self.assertIn(("Content-Length: %d\r\n" % len(PNG)).encode(), part)
        self.assertTrue(part.endswith(PNG + b"\r\n"))

    def test_fps_is_clamped(self):
        self.assertEqual(device_control.clamp_fps("100"), 10.0)
        self.assertEqual(device_control.clamp_fps("0"), 0.5)
        self.assertEqual(device_control.clamp_fps("abc"), 4.0)
        self.assertEqual(device_control.clamp_fps(None), 4.0)


class StreamRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = control_api.app.test_client()
        control_api.API_TOKEN = ""
        viewport.reset()

    def test_streams_n_frames_and_counts_them(self):
        grabs = []

        def fake_cap():
            grabs.append(1)
            return PNG

        with patch.object(control_api, "_screencap_png", fake_cap), patch.object(control_api, "run_adb", fake_adb()):
            res = self.client.get("/device/stream.mjpeg?fps=10&max_frames=3")
            body = res.get_data()
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.content_type.startswith("multipart/x-mixed-replace"))
        self.assertIn("boundary=" + device_control.MJPEG_BOUNDARY, res.content_type)
        self.assertEqual(len(grabs), 3)
        self.assertEqual(body.count(b"Content-Type: image/png"), 3)
        self.assertEqual(len(re.findall(rb"--%s\r\n" % device_control.MJPEG_BOUNDARY.encode(), body)), 3)
        self.assertTrue(body.endswith(b"--" + device_control.MJPEG_BOUNDARY.encode() + b"--\r\n"))
        snap = viewport.snapshot()
        self.assertEqual(snap["frames"], 3)
        self.assertEqual(snap["clients"], 0, "the viewer detached when the stream ended")

    def test_a_failed_capture_is_skipped_not_fatal(self):
        seq = [None, PNG, PNG]

        def flaky():
            return seq.pop(0)

        with patch.object(control_api, "_screencap_png", flaky), patch.object(control_api, "run_adb", fake_adb()):
            body = self.client.get("/device/stream.mjpeg?fps=10&max_frames=2").get_data()
        self.assertEqual(body.count(b"Content-Type: image/png"), 2)

    def test_requires_auth_like_every_device_route(self):
        control_api.API_TOKEN = "secret"
        try:
            with patch.object(control_api, "_screencap_png", lambda: PNG):
                res = self.client.get("/device/stream.mjpeg?max_frames=1")
            self.assertEqual(res.status_code, 401)
        finally:
            control_api.API_TOKEN = ""

    def test_health_advertises_the_stream(self):
        with patch.object(control_api, "run_adb", fake_adb()):
            body = self.client.get("/health").get_json()
        self.assertEqual(body["stream"]["path"], "/device/stream.mjpeg")
        self.assertEqual(body["stream"]["transport"], "multipart")


if __name__ == "__main__":
    unittest.main()
