#!/usr/bin/env python3
"""Cuttlefish ingest probe: a known A/V signal in, the same signal measured out.

The RTMP bridge test (`scripts/test-cuttlefish-rtmp-bridge.sh`) proved that
bytes flow and that the sinks decode. It did not prove that what the guest's
camera and mic receive is the stream that was published: a bridge that
duplicated the wrong input, dropped audio, or froze on the first frame still
"had a video stream". This module makes the signal measurable end to end.

  make_test_clip()   a looping clip whose video is a sequence of solid colour
                     blocks (one per second) and whose audio is a pure tone,
                     so any sample of either can be matched back to the source
  frame_color()      mean RGB of the centre of a frame at time t in any file
                     ffmpeg can decode (a sink .ts, a screencap .png, …)
  classify_color()   nearest palette colour and how sure
  dominant_tone()    Goertzel power over candidate frequencies in any audio
                     ffmpeg can decode (a sink .ts, a guest tinycap .wav, …)
  verify_sinks()     the whole judgement for the bridge's three sinks

Pure Python + ffmpeg/ffprobe on PATH. No numpy: the VM does not have it.
CLI: `python3 tools/ingest_probe.py clip|frame-color|tone|verify …`.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile

# Saturated primaries + secondaries: far apart in RGB space, so even a
# heavily compressed, chroma-subsampled, colour-shifted copy still classifies.
PALETTE = {
    "red": (255, 0, 0),
    "lime": (0, 255, 0),
    "blue": (0, 0, 255),
    "yellow": (255, 255, 0),
    "magenta": (255, 0, 255),
    "cyan": (0, 255, 255),
}
DEFAULT_SEQUENCE = ("red", "lime", "blue", "yellow", "magenta", "cyan")
DEFAULT_TONE_HZ = 1000
CANDIDATE_TONES = (440, 1000, 1500, 2500)
SAMPLE_RATE = 48000


def have_ffmpeg():
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _run(cmd, timeout=120):
    return subprocess.run(cmd, capture_output=True, timeout=timeout)


# ---------------------------------------------------------------- generation


def make_test_clip(path, sequence=DEFAULT_SEQUENCE, block_seconds=1.0, size=(640, 360),
                   fps=15, tone_hz=DEFAULT_TONE_HZ, sample_rate=SAMPLE_RATE):
    """Write an H.264 + AAC mp4: colour blocks in `sequence`, one tone throughout."""
    if not have_ffmpeg():
        raise RuntimeError("ffmpeg/ffprobe not on PATH")
    w, h = size
    total = block_seconds * len(sequence)
    inputs = []
    for name in sequence:
        r, g, b = PALETTE[name]
        inputs += ["-f", "lavfi", "-i", f"color=c=0x{r:02x}{g:02x}{b:02x}:s={w}x{h}:r={fps}:d={block_seconds}"]
    inputs += ["-f", "lavfi", "-i", f"sine=frequency={tone_hz}:sample_rate={sample_rate}:duration={total}"]
    n = len(sequence)
    concat = "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]"
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs,
        "-filter_complex", concat,
        "-map", "[v]", "-map", f"{n}:a",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-g", str(fps),
        "-c:a", "aac", "-ar", str(sample_rate), "-b:a", "128k",
        "-shortest", path,
    ]
    res = _run(cmd)
    if res.returncode != 0 or not os.path.exists(path):
        raise RuntimeError(f"ffmpeg failed: {res.stderr.decode(errors='replace')[-400:]}")
    return {
        "path": path,
        "sequence": list(sequence),
        "block_seconds": block_seconds,
        "duration": total,
        "tone_hz": tone_hz,
        "size": [w, h],
        "fps": fps,
    }


def stream_command(clip, rtmp_url, loop=True, seconds=None, video_bitrate="1500k"):
    """The ffmpeg argv that publishes `clip` to `rtmp_url` in real time, looping."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin", "-re"]
    if loop:
        cmd += ["-stream_loop", "-1"]
    cmd += ["-i", clip]
    if seconds:
        cmd += ["-t", str(seconds)]
    cmd += [
        "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-pix_fmt", "yuv420p",
        "-b:v", video_bitrate, "-g", "15",
        "-c:a", "aac", "-ar", str(SAMPLE_RATE), "-b:a", "128k",
        "-f", "flv", rtmp_url,
    ]
    return cmd


# ---------------------------------------------------------------- video side


def frame_color(path, at_seconds=0.0, size=(640, 360), centre_fraction=0.5):
    """Mean (r, g, b) of the centre `centre_fraction` of the frame at `at_seconds`."""
    w, h = size
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{at_seconds:.3f}", "-i", path,
        "-frames:v", "1", "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ]
    res = _run(cmd)
    if res.returncode != 0 or len(res.stdout) < w * h * 3:
        raise RuntimeError(f"no frame at {at_seconds}s in {path}: {res.stderr.decode(errors='replace')[-200:]}")
    return mean_rgb(res.stdout, w, h, centre_fraction)


def mean_rgb(rgb_bytes, w, h, centre_fraction=0.5):
    cw, ch = max(1, int(w * centre_fraction)), max(1, int(h * centre_fraction))
    x0, y0 = (w - cw) // 2, (h - ch) // 2
    rs = gs = bs = 0
    n = 0
    step = max(1, cw // 64)  # sample, do not walk every pixel of a 1280x720 frame
    for y in range(y0, y0 + ch, step):
        row = y * w * 3
        for x in range(x0, x0 + cw, step):
            i = row + x * 3
            rs += rgb_bytes[i]
            gs += rgb_bytes[i + 1]
            bs += rgb_bytes[i + 2]
            n += 1
    return (rs // n, gs // n, bs // n)


def classify_color(rgb, palette=PALETTE, max_distance=120.0):
    """Nearest palette entry by RGB distance; `None` when nothing is close enough."""
    best, best_d = None, float("inf")
    for name, ref in palette.items():
        d = math.dist(rgb, ref)
        if d < best_d:
            best, best_d = name, d
    if best_d > max_distance:
        return None, best_d
    return best, best_d


def expected_color_at(t, sequence=DEFAULT_SEQUENCE, block_seconds=1.0):
    total = block_seconds * len(sequence)
    idx = int((t % total) // block_seconds)
    return sequence[idx]


# ---------------------------------------------------------------- audio side


def decode_pcm(path, seconds=2.0, start=0.0, sample_rate=SAMPLE_RATE):
    """Mono 16-bit PCM samples from any file ffmpeg can decode."""
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", path,
        "-t", f"{seconds:.3f}", "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "pipe:1",
    ]
    res = _run(cmd)
    if res.returncode != 0 or len(res.stdout) < 2 * sample_rate // 10:
        raise RuntimeError(f"no audio in {path}: {res.stderr.decode(errors='replace')[-200:]}")
    count = len(res.stdout) // 2
    return struct.unpack(f"<{count}h", res.stdout[: count * 2])


def goertzel(samples, freq, sample_rate=SAMPLE_RATE):
    """Power at `freq` (classic Goertzel, O(n), no numpy)."""
    n = len(samples)
    if n == 0:
        return 0.0
    k = int(0.5 + n * freq / sample_rate)
    omega = 2.0 * math.pi * k / n
    coeff = 2.0 * math.cos(omega)
    s_prev = s_prev2 = 0.0
    for x in samples:
        s = x + coeff * s_prev - s_prev2
        s_prev2, s_prev = s_prev, s
    return s_prev2 * s_prev2 + s_prev * s_prev - coeff * s_prev * s_prev2


def dominant_tone(path_or_samples, candidates=CANDIDATE_TONES, seconds=2.0, start=0.0, sample_rate=SAMPLE_RATE):
    """Which candidate frequency carries the most power, and how dominant it is.

    `ratio` is best / second-best power; a pure tone gives ratios in the
    hundreds, speech or silence stays near 1. `rms` tells silence from signal.
    """
    samples = path_or_samples if isinstance(path_or_samples, (tuple, list)) else decode_pcm(path_or_samples, seconds, start, sample_rate)
    if not samples:
        return {"tone_hz": None, "ratio": 0.0, "rms": 0.0, "powers": {}}
    rms = math.sqrt(sum(x * x for x in samples) / len(samples))
    powers = {f: goertzel(samples, f, sample_rate) for f in candidates}
    ranked = sorted(powers.items(), key=lambda kv: kv[1], reverse=True)
    best, best_p = ranked[0]
    second_p = ranked[1][1] if len(ranked) > 1 else 0.0
    ratio = best_p / second_p if second_p > 0 else float("inf")
    return {"tone_hz": best if rms > 50 else None, "ratio": ratio, "rms": rms, "powers": {str(k): v for k, v in powers.items()}}


# ---------------------------------------------------------------- judgement


def verify_video(path, sequence=DEFAULT_SEQUENCE, block_seconds=1.0, samples=4, size=(640, 360)):
    """Sample frames across the file; pass when most classify as palette colours
    and at least two distinct palette colours appear (a frozen frame fails)."""
    duration = media_duration(path)
    if not duration or duration <= 0:
        return {"ok": False, "error": "no decodable video", "path": path}
    hits, seen, rows = 0, set(), []
    for i in range(samples):
        t = min(duration * (i + 0.5) / samples, max(0.0, duration - 0.05))
        try:
            rgb = frame_color(path, t, size=size)
        except RuntimeError as e:
            rows.append({"t": t, "error": str(e)})
            continue
        name, dist = classify_color(rgb)
        rows.append({"t": round(t, 2), "rgb": list(rgb), "color": name, "distance": round(dist, 1)})
        if name:
            hits += 1
            seen.add(name)
    ok = hits >= max(1, (samples * 3) // 4) and len(seen) >= 2
    return {"ok": ok, "path": path, "duration": duration, "samples": rows, "colors_seen": sorted(seen), "hits": hits}


def verify_audio(path, tone_hz=DEFAULT_TONE_HZ, seconds=2.0, min_ratio=20.0):
    try:
        res = dominant_tone(path, seconds=seconds)
    except RuntimeError as e:
        return {"ok": False, "error": str(e), "path": path}
    ok = res["tone_hz"] == tone_hz and res["ratio"] >= min_ratio
    return {"ok": ok, "path": path, "expected_hz": tone_hz, **res}


def verify_sinks(front, back, mic, sequence=DEFAULT_SEQUENCE, tone_hz=DEFAULT_TONE_HZ, size=(640, 360)):
    report = {
        "front": verify_video(front, sequence, size=size),
        "back": verify_video(back, sequence, size=size),
        "mic": verify_audio(mic, tone_hz),
    }
    report["ok"] = all(part.get("ok") for part in report.values())
    return report


def media_duration(path):
    res = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path])
    try:
        return float(res.stdout.decode().strip().splitlines()[0])
    except (ValueError, IndexError):
        return None


# ---------------------------------------------------------------- CLI


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("clip", help="write the test clip")
    c.add_argument("path")
    c.add_argument("--tone", type=int, default=DEFAULT_TONE_HZ)
    c.add_argument("--block-seconds", type=float, default=1.0)
    f = sub.add_parser("frame-color", help="classify the centre colour of a frame")
    f.add_argument("path")
    f.add_argument("--at", type=float, default=0.0)
    t = sub.add_parser("tone", help="dominant tone of an audio file")
    t.add_argument("path")
    t.add_argument("--seconds", type=float, default=2.0)
    v = sub.add_parser("verify", help="judge the three bridge sinks")
    v.add_argument("--front", required=True)
    v.add_argument("--back", required=True)
    v.add_argument("--mic", required=True)
    v.add_argument("--tone", type=int, default=DEFAULT_TONE_HZ)
    vv = sub.add_parser("verify-video", help="judge one video file")
    vv.add_argument("path")
    va = sub.add_parser("verify-audio", help="judge one audio file")
    va.add_argument("path")
    va.add_argument("--tone", type=int, default=DEFAULT_TONE_HZ)
    s = sub.add_parser("stream-cmd", help="print the publish argv")
    s.add_argument("clip")
    s.add_argument("rtmp_url")
    s.add_argument("--seconds", type=float, default=None)
    args = ap.parse_args(argv)

    if args.cmd == "clip":
        out = make_test_clip(args.path, tone_hz=args.tone, block_seconds=args.block_seconds)
    elif args.cmd == "frame-color":
        rgb = frame_color(args.path, args.at)
        name, dist = classify_color(rgb)
        out = {"rgb": list(rgb), "color": name, "distance": dist}
    elif args.cmd == "tone":
        out = dominant_tone(args.path, seconds=args.seconds)
    elif args.cmd == "verify":
        out = verify_sinks(args.front, args.back, args.mic, tone_hz=args.tone)
    elif args.cmd == "verify-video":
        out = verify_video(args.path)
    elif args.cmd == "verify-audio":
        out = verify_audio(args.path, tone_hz=args.tone)
    else:
        out = {"argv": stream_command(args.clip, args.rtmp_url, seconds=args.seconds)}
    print(json.dumps(out, indent=2, default=str))
    if isinstance(out, dict) and out.get("ok") is False:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
