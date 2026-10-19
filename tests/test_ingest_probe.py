#!/usr/bin/env python3
"""Ingest probe: the signal we publish is the signal we measure (R0–R2, offline).

R0  pure analysis: palette classification, expected colour at time t, the
    Goertzel detector on synthesized samples, the ffmpeg publish argv.
R1  with ffmpeg on PATH: a generated clip classifies frame-by-frame and its
    tone is found; a frozen frame and silence are rejected.
R2  loopback: ffmpeg itself listens on a local RTMP port (stand-in for
    nginx-rtmp), the clip is published to it in real time, and the received
    transport stream passes the same judgement the live rung applies to the
    bridge sinks and the guest captures. No Cuttlefish, no OCI.
"""

import math
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools import ingest_probe as probe  # noqa: E402

HAVE_FFMPEG = probe.have_ffmpeg()


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _sine(freq, seconds=1.0, rate=probe.SAMPLE_RATE, amp=12000):
    n = int(seconds * rate)
    return [int(amp * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)]


class AnalysisTests(unittest.TestCase):
    def test_classify_exact_and_degraded_colours(self):
        self.assertEqual(probe.classify_color((255, 0, 0))[0], "red")
        self.assertEqual(probe.classify_color((230, 20, 30))[0], "red", "compression drift still classifies")
        self.assertEqual(probe.classify_color((10, 240, 250))[0], "cyan")
        self.assertIsNone(probe.classify_color((128, 128, 128))[0], "grey is nothing in the palette")
        self.assertIsNone(probe.classify_color((0, 0, 0))[0], "black (a dead feed) is nothing")

    def test_expected_colour_wraps_with_the_loop(self):
        seq = ("red", "lime", "blue")
        self.assertEqual(probe.expected_color_at(0.2, seq), "red")
        self.assertEqual(probe.expected_color_at(1.5, seq), "lime")
        self.assertEqual(probe.expected_color_at(3.1, seq), "red", "looping clip wraps")

    def test_goertzel_finds_the_tone_and_rejects_silence(self):
        res = probe.dominant_tone(_sine(1000))
        self.assertEqual(res["tone_hz"], 1000)
        self.assertGreater(res["ratio"], 20)
        other = probe.dominant_tone(_sine(440))
        self.assertEqual(other["tone_hz"], 440)
        silence = probe.dominant_tone([0] * 48000)
        self.assertIsNone(silence["tone_hz"])

    def test_mean_rgb_reads_the_centre_not_the_border(self):
        w, h = 8, 8
        buf = bytearray(w * h * 3)
        for y in range(h):
            for x in range(w):
                i = (y * w + x) * 3
                centre = 2 <= x < 6 and 2 <= y < 6
                buf[i:i + 3] = (0, 255, 0) if centre else (255, 0, 0)
        self.assertEqual(probe.mean_rgb(bytes(buf), w, h, 0.5), (0, 255, 0))

    def test_stream_command_loops_in_real_time_to_flv(self):
        argv = probe.stream_command("/tmp/clip.mp4", "rtmp://127.0.0.1/live/cam", seconds=12)
        self.assertIn("-re", argv)
        self.assertIn("-stream_loop", argv)
        self.assertEqual(argv[argv.index("-stream_loop") + 1], "-1")
        self.assertEqual(argv[-2:], ["flv", "rtmp://127.0.0.1/live/cam"])
        self.assertIn("-t", argv)


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg/ffprobe not on PATH")
class ClipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="ingest-probe-")
        cls.clip = os.path.join(cls.dir, "clip.mp4")
        cls.meta = probe.make_test_clip(cls.clip, sequence=("red", "lime", "blue"), block_seconds=1.0, size=(320, 180), fps=15)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_clip_has_video_and_audio(self):
        self.assertAlmostEqual(probe.media_duration(self.clip), 3.0, delta=0.3)

    def test_frames_classify_in_sequence(self):
        for t, want in [(0.3, "red"), (1.5, "lime"), (2.6, "blue")]:
            rgb = probe.frame_color(self.clip, t, size=(320, 180))
            name, dist = probe.classify_color(rgb)
            self.assertEqual(name, want, f"t={t} rgb={rgb} d={dist}")

    def test_tone_is_found(self):
        res = probe.verify_audio(self.clip, tone_hz=probe.DEFAULT_TONE_HZ)
        self.assertTrue(res["ok"], res)

    def test_wrong_tone_fails(self):
        res = probe.verify_audio(self.clip, tone_hz=440)
        self.assertFalse(res["ok"])

    def test_verify_video_passes_and_a_frozen_frame_fails(self):
        good = probe.verify_video(self.clip, size=(320, 180))
        self.assertTrue(good["ok"], good)
        self.assertGreaterEqual(len(good["colors_seen"]), 2)
        frozen = os.path.join(self.dir, "frozen.mp4")
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
            "-i", "color=c=red:s=320x180:r=15:d=3", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", frozen,
        ], check=True)
        bad = probe.verify_video(frozen, size=(320, 180))
        self.assertFalse(bad["ok"], "one colour for the whole file is a frozen feed")
        self.assertEqual(bad["colors_seen"], ["red"])

    def test_silence_fails_audio(self):
        silent = os.path.join(self.dir, "silent.mp4")
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
            "-t", "2", "-c:a", "aac", silent,
        ], check=True)
        self.assertFalse(probe.verify_audio(silent)["ok"])


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg/ffprobe not on PATH")
class RtmpLoopbackTests(unittest.TestCase):
    """ffmpeg as the RTMP server: what nginx-rtmp does on the Cuttlefish host."""

    def test_published_loop_arrives_intact(self):
        d = tempfile.mkdtemp(prefix="ingest-loop-")
        try:
            clip = os.path.join(d, "clip.mp4")
            probe.make_test_clip(clip, sequence=("red", "lime", "blue"), block_seconds=1.0, size=(320, 180), fps=15)
            port = _free_port()
            url = f"rtmp://127.0.0.1:{port}/live/cam"
            recv = os.path.join(d, "recv.ts")
            listener = subprocess.Popen(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-listen", "1", "-i", url, "-c", "copy", "-f", "mpegts", recv],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            time.sleep(1.0)
            pub = subprocess.run(probe.stream_command(clip, url, seconds=5, video_bitrate="800k"), capture_output=True, timeout=60)
            self.assertEqual(pub.returncode, 0, pub.stderr.decode(errors="replace")[-300:])
            try:
                listener.wait(timeout=15)
            except subprocess.TimeoutExpired:
                listener.kill()
            self.assertTrue(os.path.exists(recv) and os.path.getsize(recv) > 10000, "nothing arrived over RTMP")
            report = probe.verify_sinks(recv, recv, recv, size=(320, 180))
            self.assertTrue(report["ok"], report)
            self.assertGreaterEqual(len(report["front"]["colors_seen"]), 2, "the loop should cycle colours")
            self.assertEqual(report["mic"]["tone_hz"], probe.DEFAULT_TONE_HZ)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class ScriptContractTests(unittest.TestCase):
    def test_e2e_script_help_and_dry_run(self):
        script = os.path.join(ROOT, "scripts", "cuttlefish-ingest-e2e.sh")
        r = subprocess.run(["bash", script, "--help"], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        for flag in ["--duration", "--skip-guest", "--vm", "--rtmp-url", "--dry-run", "--tone"]:
            self.assertIn(flag, r.stdout)
        dry = subprocess.run(["bash", script, "--local", "--dry-run"], capture_output=True, text=True, timeout=20)
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertIn("ingest_probe.py clip", dry.stdout)
        self.assertIn("cuttlefish-rtmp-bridge.sh", dry.stdout)
        self.assertIn("tinycap", dry.stdout, "the guest mic rung samples with tinycap")
        self.assertIn("screencap", dry.stdout, "the guest camera rung samples the screen")


if __name__ == "__main__":
    unittest.main()
