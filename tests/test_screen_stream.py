#!/usr/bin/env python3
"""R0: the stream generators behind /device/stream.mjpeg and /device/stream.h264.

No device, no adb, no sleeping — capture and recorder callables are fakes and
the pacing clock is patched.
"""

import io
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "api"))

import screen_stream  # noqa: E402

PNG_1x1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
    b"\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82"
)


class FakeClock:
    """Monotonic clock that only moves when a test says so."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def parts_of(parts):
    return [p for p in parts]


class EncodeTests(unittest.TestCase):
    def test_png_becomes_jpeg_when_pillow_is_present(self):
        if not screen_stream.pillow_available():
            self.skipTest("Pillow not installed")
        body, ctype = screen_stream.encode_frame(PNG_1x1, quality=50)
        self.assertEqual(ctype, "image/jpeg")
        self.assertTrue(body.startswith(b"\xff\xd8"), "JPEG SOI marker")

    def test_without_pillow_the_png_is_passed_through(self):
        with patch.object(screen_stream, "_PILImage", None):
            body, ctype = screen_stream.encode_frame(PNG_1x1)
        self.assertEqual((body, ctype), (PNG_1x1, "image/png"))

    def test_garbage_capture_is_shipped_rather_than_stalling(self):
        body, ctype = screen_stream.encode_frame(b"not a png")
        self.assertEqual((body, ctype), (b"not a png", "image/png"))

    def test_multipart_part_shape(self):
        part = screen_stream.multipart_part(b"abc", "image/jpeg")
        self.assertTrue(part.startswith(b"--" + screen_stream.BOUNDARY.encode() + b"\r\n"))
        self.assertIn(b"Content-Type: image/jpeg\r\n", part)
        self.assertIn(b"Content-Length: 3\r\n\r\nabc\r\n", part)


class MjpegPartsTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.addCleanup(patch.stopall)
        patch.object(screen_stream, "_CLOCK", self.clock).start()
        patch.object(screen_stream, "_SLEEP", self.clock.sleep).start()
        self.identity = lambda png, q: (png, "image/png")

    def test_yields_one_part_per_capture_at_the_fps_cap(self):
        parts = parts_of(screen_stream.mjpeg_parts(
            capture=lambda: b"frame", fps=4, max_frames=3, encoder=self.identity,
        ))
        self.assertEqual(len(parts), 3)
        self.assertTrue(all(b"\r\n\r\nframe\r\n" in p for p in parts))
        # capture is instant on the fake clock, so each gap is a full interval
        self.assertEqual(self.clock.sleeps, [0.25, 0.25])

    def test_a_slow_capture_eats_into_the_sleep(self):
        def slow_capture():
            self.clock.now += 0.2
            return b"f"
        parts_of(screen_stream.mjpeg_parts(
            capture=slow_capture, fps=4, max_frames=2, encoder=self.identity,
        ))
        self.assertAlmostEqual(self.clock.sleeps[0], 0.05)

    def test_capture_slower_than_the_interval_never_sleeps(self):
        def very_slow():
            self.clock.now += 1.0
            return b"f"
        parts_of(screen_stream.mjpeg_parts(
            capture=very_slow, fps=4, max_frames=2, encoder=self.identity,
        ))
        self.assertEqual(self.clock.sleeps, [])

    def test_on_frame_and_on_stop_report_sizes_not_bytes(self):
        seen, stops = [], []
        parts_of(screen_stream.mjpeg_parts(
            capture=lambda: b"abcdef", fps=10, max_frames=2, encoder=self.identity,
            on_frame=seen.append, on_stop=lambda n, why: stops.append((n, why)),
        ))
        self.assertEqual(seen, [6, 6])
        self.assertEqual(stops, [(2, "frame_limit")])

    def test_consecutive_failures_end_the_stream(self):
        stops = []
        parts = parts_of(screen_stream.mjpeg_parts(
            capture=lambda: None, fps=10, max_failures=3, encoder=self.identity,
            on_stop=lambda n, why: stops.append((n, why)),
        ))
        self.assertEqual(parts, [])
        self.assertEqual(stops, [(0, "capture_failed")])
        self.assertEqual(len(self.clock.sleeps), 2, "backs off between retries")

    def test_a_good_frame_resets_the_failure_count(self):
        script = iter([None, None, b"ok", None, None, b"ok"])
        parts = parts_of(screen_stream.mjpeg_parts(
            capture=lambda: next(script), fps=10, max_frames=2, max_failures=3,
            encoder=self.identity,
        ))
        self.assertEqual(len(parts), 2)

    def test_closing_the_generator_runs_on_stop(self):
        """A disconnecting HTTP client closes the generator mid-yield."""
        stops = []
        gen = screen_stream.mjpeg_parts(
            capture=lambda: b"f", fps=10, encoder=self.identity,
            on_stop=lambda n, why: stops.append((n, why)),
        )
        next(gen)
        next(gen)
        gen.close()
        self.assertEqual(stops, [(2, "closed")])


class FakeRecorder:
    """Stands in for a `screenrecord` Popen: emits scripted bytes then EOF."""

    def __init__(self, payloads, spawned, killed):
        self.stdout = io.BytesIO(b"".join(payloads))
        self._returncode = None
        self.spawned = spawned
        self.killed = killed

    def poll(self):
        return self._returncode

    def kill(self):
        self._returncode = -9
        self.killed.append(self)

    def wait(self, timeout=None):
        self._returncode = self._returncode if self._returncode is not None else 0
        return self._returncode


def recorder_factory(runs, spawned=None, killed=None):
    spawned = [] if spawned is None else spawned
    killed = [] if killed is None else killed
    script = iter(runs)

    def popen(argv):
        spawned.append(argv)
        proc = FakeRecorder(next(script, [b""]), spawned, killed)
        return proc

    return popen, spawned, killed


class H264ChunksTests(unittest.TestCase):
    def test_argv_targets_stdout_with_annexb(self):
        argv = screen_stream.screenrecord_argv("127.0.0.1:5555", size=(640, 360), bitrate=2_000_000, time_limit=180)
        self.assertEqual(argv[:4], ["adb", "-s", "127.0.0.1:5555", "exec-out"])
        self.assertIn("screenrecord", argv)
        self.assertIn("--output-format=h264", argv)
        self.assertIn("--size=640x360", argv)
        self.assertIn("--bit-rate=2000000", argv)
        self.assertIn("--time-limit=180", argv)
        self.assertEqual(argv[-1], "-")

    def test_runs_are_chained_into_one_stream(self):
        popen, spawned, _ = recorder_factory([[b"\x00\x00\x00\x01run1"], [b"\x00\x00\x00\x01run2"]])
        stops = []
        chunks = list(screen_stream.h264_chunks(
            ["fake"], popen=popen, max_runs=2, on_stop=lambda r, t, why: stops.append((r, t, why)),
        ))
        self.assertEqual(b"".join(chunks), b"\x00\x00\x00\x01run1\x00\x00\x00\x01run2")
        self.assertEqual(len(spawned), 2)
        self.assertEqual(stops, [(2, 16, "run_limit")])

    def test_recorder_that_never_produces_ends_the_stream(self):
        popen, spawned, _ = recorder_factory([[b""], [b""], [b""], [b""]])
        stops = []
        chunks = list(screen_stream.h264_chunks(
            ["fake"], popen=popen, max_restarts=2, on_stop=lambda r, t, why: stops.append(why),
        ))
        self.assertEqual(chunks, [])
        self.assertEqual(len(spawned), 2)
        self.assertEqual(stops, ["recorder_failed"])

    def test_client_disconnect_kills_the_recorder(self):
        popen, _, killed = recorder_factory([[b"a" * 10, b"b" * 10]])
        gen = screen_stream.h264_chunks(["fake"], popen=popen, chunk_size=10)
        first = next(gen)
        self.assertEqual(first, b"a" * 10)
        gen.close()
        self.assertEqual(len(killed), 1, "screenrecord must not outlive its watcher")


if __name__ == "__main__":
    unittest.main()
