#!/usr/bin/env python3
"""R0/R1 contracts for the OBS RTMP -> clean pixel-copy bridge.

R0 (always): the bridge's --dry-run must resolve to an ffmpeg command that
copies pixels and strips everything else, and pick the right sink muxer for
each sink URI. No ffmpeg needed.

R1 (when ffmpeg is installed): drive the bridge from a local lossless FLV
file instead of nginx-rtmp and prove the pixel-copy contract on the output:
every frame byte-identical to the source pattern, contiguous, and the sink an
exact multiple of the frame size. Skipped when ffmpeg/ffprobe are missing.
"""

import os
import shlex
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "scripts" / "cuttlefish-rtmp-bridge.sh"
TEST_SCRIPT = ROOT / "scripts" / "test-cuttlefish-rtmp-bridge.sh"
OBS_CHECK = ROOT / "scripts" / "obs-e2e-check.sh"
UNIT = ROOT / "systemd" / "cuttlefish-rtmp-bridge.service"

SIZE = "320x180"
FPS = 25
FRAME_BYTES = 320 * 180 * 3 // 2


def run(cmd, timeout=30, **kw):
    return subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, **kw)


def dry_run(*args):
    with tempfile.TemporaryDirectory() as td:
        r = run(["bash", str(BRIDGE), "--dry-run", "--log-dir", td, *args])
    return r


def ffmpeg_argv(stdout):
    for line in stdout.splitlines():
        if line.strip().startswith("FFmpeg cmd:"):
            return shlex.split(line.split("FFmpeg cmd:", 1)[1])
    raise AssertionError("no 'FFmpeg cmd:' line in dry-run output:\n" + stdout)


def tee_spec(argv):
    return argv[argv.index("tee") + 1]


class BridgeDryRunContract(unittest.TestCase):
    def test_default_mode_is_clean_pixel_copy(self):
        r = dry_run()
        self.assertEqual(r.returncode, 0, r.stderr)
        argv = ffmpeg_argv(r.stdout)
        self.assertIn("rawvideo", argv)
        self.assertNotIn("libx264", argv)
        # no re-timing, no metadata, no chapters, no data/subtitle streams
        self.assertIn("passthrough", argv)
        self.assertIn("-map_metadata", argv)
        self.assertEqual(argv[argv.index("-map_metadata") + 1], "-1")
        self.assertIn("-map_chapters", argv)
        self.assertIn("-bitexact", argv)
        for flag in ("-sn", "-dn"):
            self.assertIn(flag, argv)
        # no scaling / fps filter unless asked
        vf = argv[argv.index("-filter:v") + 1]
        self.assertEqual(vf, "format=yuv420p")
        # audio is raw PCM
        self.assertIn("pcm_s16le", argv)

    def test_no_http_only_reconnect_flags_on_rtmp_input(self):
        # -reconnect* are HTTP protocol options; ffmpeg refuses to open an
        # RTMP input with them ("Option reconnect not found").
        argv = ffmpeg_argv(dry_run().stdout)
        self.assertFalse(any(a.startswith("-reconnect") for a in argv), argv)

    def test_frames_flushed_per_packet_on_every_sink(self):
        argv = ffmpeg_argv(dry_run().stdout)
        spec = tee_spec(argv)
        self.assertEqual(spec.count("flush_packets=1"), 2, spec)
        self.assertIn("-flush_packets", argv)

    def test_sink_uri_picks_muxer(self):
        r = dry_run(
            "--front-sink", "/dev/video10",
            "--back-sink", "pipe:/tmp/back.yuv",
            "--mic-sink", "file:/tmp/mic.pcm",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        argv = ffmpeg_argv(r.stdout)
        spec = tee_spec(argv)
        self.assertIn("[f=v4l2:onfail=ignore:flush_packets=1]/dev/video10", spec)
        self.assertIn("[f=rawvideo:onfail=ignore:flush_packets=1]/tmp/back.yuv", spec)
        self.assertEqual(argv[argv.index("-f", argv.index("tee") + 1) + 1], "s16le")

    def test_socket_sink_uses_headerless_y4m(self):
        argv = ffmpeg_argv(dry_run().stdout)
        spec = tee_spec(argv)
        self.assertIn("[f=yuv4mpegpipe:onfail=ignore:flush_packets=1]udp://127.0.0.1:23000", spec)
        self.assertNotIn("nut", spec)

    def test_video_format_override(self):
        argv = ffmpeg_argv(dry_run("--video-format", "nut").stdout)
        self.assertIn("[f=nut:", tee_spec(argv))

    def test_explicit_geometry_enables_scale_and_cfr(self):
        argv = ffmpeg_argv(dry_run("--video-width", "1280", "--video-height", "720", "--video-fps", "30").stdout)
        vf = argv[argv.index("-filter:v") + 1]
        self.assertTrue(vf.startswith("scale=1280:720"), vf)
        self.assertTrue(vf.endswith("fps=30"), vf)
        self.assertIn("cfr", argv)

    def test_width_without_height_rejected(self):
        r = dry_run("--video-width", "1280")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("together", r.stderr + r.stdout)

    def test_unknown_mode_rejected(self):
        r = dry_run("--mode", "toaster")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Unsupported", r.stderr + r.stdout)

    def test_encode_mode_is_legacy_x264_with_sei_stripped(self):
        argv = ffmpeg_argv(dry_run("--mode", "encode").stdout)
        self.assertIn("libx264", argv)
        self.assertIn("filter_units=remove_types=6", argv)
        self.assertIn("[f=mpegts:", tee_spec(argv))
        self.assertNotIn("rawvideo", argv)
        vf = argv[argv.index("-filter:v") + 1]
        self.assertTrue(vf.startswith("scale=1280:720"), vf)

    def test_no_mic_drops_audio_output(self):
        argv = ffmpeg_argv(dry_run("--no-mic").stdout)
        self.assertNotIn("pcm_s16le", argv)
        self.assertNotIn("0:a:0?", argv)

    def test_help_lists_modes_and_sink_kinds(self):
        r = run(["bash", str(BRIDGE), "--help"])
        self.assertEqual(r.returncode, 0)
        for token in ("--mode clean|encode", "--video-format", "--idle-timeout", "--stall-timeout"):
            self.assertIn(token, r.stdout)


class ScriptWiringContract(unittest.TestCase):
    def test_systemd_unit_passes_bridge_mode(self):
        text = UNIT.read_text()
        self.assertIn("--mode", text)
        self.assertIn("BRIDGE_MODE:-clean", text)

    def test_installer_and_env_example_carry_bridge_mode(self):
        installer = (ROOT / "scripts" / "install-cuttlefish-cloud-phone.sh").read_text()
        env = (ROOT / ".env.example").read_text()
        self.assertIn("BRIDGE_MODE", installer)
        self.assertIn("BRIDGE_MODE", env)

    def test_cli_exposes_obs_check(self):
        text = (ROOT / "cloud-phone").read_text()
        self.assertIn("obs-check", text)
        self.assertIn("obs-e2e-check.sh", text)

    def test_scripts_parse(self):
        for script in (BRIDGE, TEST_SCRIPT, OBS_CHECK):
            r = run(["bash", "-n", str(script)])
            self.assertEqual(r.returncode, 0, f"{script}: {r.stderr}")

    def test_test_script_help_mentions_pixel_exact_modes(self):
        r = run(["bash", str(TEST_SCRIPT), "--help"])
        self.assertEqual(r.returncode, 0)
        self.assertIn("--mode clean|encode", r.stdout)
        r = run(["bash", str(OBS_CHECK), "--help"])
        self.assertEqual(r.returncode, 0)
        self.assertIn("--wait", r.stdout)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe not installed")
class BridgePixelCopyLocal(unittest.TestCase):
    """Runs the real bridge against a local lossless FLV (no nginx needed)."""

    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.mkdtemp(prefix="bridge-r1-")
        cls.src = os.path.join(cls.td, "src.flv")
        r = run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=size={SIZE}:rate={FPS}",
            "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000",
            "-t", "3",
            "-metadata", "encoder=obs-output module (libobs version 30.2.3)",
            "-metadata", "title=junk-title",
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-qp", "0",
            "-pix_fmt", "yuv420p", "-g", str(FPS),
            "-c:a", "aac", "-ar", "48000", "-ac", "1", "-b:a", "96k",
            "-f", "flv", cls.src,
        ], timeout=120)
        if r.returncode != 0:
            raise unittest.SkipTest("could not build lossless source: " + r.stderr)
        ref = run([
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc2=size={SIZE}:rate={FPS}", "-t", "3",
            "-pix_fmt", "yuv420p", "-f", "framemd5", "-",
        ], timeout=120)
        cls.ref = [ln.split()[-1] for ln in ref.stdout.splitlines() if ln[:1].isdigit()]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.td, ignore_errors=True)

    def _frame_hashes(self, path):
        r = run([
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "rawvideo", "-video_size", SIZE, "-pix_fmt", "yuv420p", "-i", path,
            "-f", "framemd5", "-",
        ], timeout=120)
        return [ln.split()[-1] for ln in r.stdout.splitlines() if ln[:1].isdigit()]

    def test_bridge_output_is_pixel_exact_and_contiguous(self):
        front = os.path.join(self.td, "front.yuv")
        back = os.path.join(self.td, "back.y4m")
        mic = os.path.join(self.td, "mic.pcm")
        logdir = os.path.join(self.td, "log")
        proc = subprocess.Popen([
            "bash", str(BRIDGE), "--rtmp-url", self.src,
            "--front-sink", f"file:{front}", "--back-sink", f"file:{back}",
            "--video-format", "auto", "--mic-sink", f"file:{mic}",
            "--log-dir", logdir, "--probe-interval", "1", "--retry-delay", "1",
            "--stat-url", "http://127.0.0.1:1/nope",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.time() + 60
            while time.time() < deadline:
                if os.path.exists(front) and os.path.getsize(front) >= FRAME_BYTES * FPS * 3:
                    break
                time.sleep(0.5)
            # the file source hits EOF -> worker ends -> bridge loops; give it a moment to settle
            time.sleep(1.5)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()

        size = os.path.getsize(front)
        self.assertGreater(size, 0, "front sink empty")
        self.assertEqual(size % FRAME_BYTES, 0, f"front sink {size} bytes is not a whole number of frames")
        out = self._frame_hashes(front)
        self.assertEqual(len(out), FPS * 3, "frame count differs from the source")
        self.assertEqual(out, self.ref, "output frames are not byte-identical / contiguous with the source pattern")

        self.assertTrue(os.path.getsize(mic) > 48000 * 2 * 2, "mic PCM shorter than 2s")
        with open(mic, "rb") as fh:
            blob = fh.read()
        for marker in (b"obs-output", b"junk-title", b"Lavf", b"Lavc"):
            self.assertNotIn(marker, blob)
        with open(front, "rb") as fh:
            blob = fh.read()
        for marker in (b"obs-output", b"junk-title", b"Lavf", b"Lavc", b"x264"):
            self.assertNotIn(marker, blob)

        log = Path(logdir, "bridge.log").read_text()
        self.assertIn("Stream detected", log)
        self.assertNotIn("ERROR", log)


if __name__ == "__main__":
    unittest.main()
