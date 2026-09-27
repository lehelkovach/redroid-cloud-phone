"""Live screen streams for the Control API (route A: MJPEG; route B: H.264).

Nothing in this repo speaks RFB. `viewport.py` is bookkeeping — it records the
rectangle the Control API advertises so `[VNC]` logs can prove a human and the
agent looked at the same pixels. These generators are what actually moves
frames to a watcher:

* ``mjpeg_parts`` — loops a screencap callable, encodes each PNG as JPEG and
  yields ``multipart/x-mixed-replace`` parts at a capped frame rate. Browsers
  render it with a plain ``<img src=".../device/stream.mjpeg">``.
* ``h264_chunks`` — relays the raw Annex-B byte stream of
  ``adb exec-out screenrecord --output-format=h264 -`` and restarts the recorder
  at its hard time limit, so a client sees one continuous stream.

Both are pure generators over injected callables so the offline ladder can
drive them with a fake screencap / fake recorder and no device.

Frame bytes are never logged; only sizes and sequence numbers are.
"""

from __future__ import annotations

import io
import os
import subprocess
import time

BOUNDARY = "cloudphoneframe"
MJPEG_CONTENT_TYPE = f"multipart/x-mixed-replace; boundary={BOUNDARY}"
H264_CONTENT_TYPE = "video/h264"

# Patched by tests so pacing is asserted without sleeping.
_SLEEP = time.sleep
_CLOCK = time.monotonic

try:  # Pillow is in api/requirements.txt; the stream degrades to PNG without it.
    from PIL import Image as _PILImage
except ImportError:  # pragma: no cover - exercised via encoder=None in tests
    _PILImage = None


def pillow_available():
    return _PILImage is not None


def default_quality():
    return _clamp(int(os.environ.get("STREAM_JPEG_QUALITY", "70")), 10, 95)


def default_max_failures():
    return max(1, int(os.environ.get("STREAM_MAX_FAILURES", "10")))


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def encode_frame(png_bytes, quality=None):
    """Return ``(bytes, content_type)`` for one frame.

    JPEG through Pillow when it is importable, otherwise the PNG untouched:
    browsers sniff each part, so an ``<img>`` still renders it — it is just a
    larger part per frame.
    """
    if _PILImage is None or not png_bytes:
        return png_bytes, "image/png"
    quality = default_quality() if quality is None else _clamp(int(quality), 10, 95)
    try:
        with _PILImage.open(io.BytesIO(png_bytes)) as img:
            rgb = img.convert("RGB")
            out = io.BytesIO()
            rgb.save(out, format="JPEG", quality=quality, optimize=False)
            return out.getvalue(), "image/jpeg"
    except Exception:  # undecodable capture: ship what we have rather than stall
        return png_bytes, "image/png"


def multipart_part(body, content_type):
    """One ``multipart/x-mixed-replace`` part, boundary first (Werkzeug/Flask
    generator responses never terminate the stream, so the boundary leads)."""
    head = (
        f"--{BOUNDARY}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\n\r\n"
    ).encode("ascii")
    return head + body + b"\r\n"


def mjpeg_parts(capture, fps, quality=None, max_frames=0, max_failures=None,
                on_frame=None, on_stop=None, encoder=encode_frame):
    """Yield multipart parts from ``capture()`` at up to ``fps`` frames/s.

    ``capture`` returns PNG bytes or ``None`` on a failed screencap. After
    ``max_failures`` consecutive failures the stream ends (a rebooting phone
    should drop the watcher, not spin the CPU forever). ``max_frames`` of 0
    means unbounded — the client disconnecting closes the generator, which is
    what runs ``on_stop``. ``on_frame(nbytes)`` is the ``viewport.frame`` hook
    so ``[VNC]`` counters stay truthful.
    """
    fps = max(1, int(fps))
    interval = 1.0 / fps
    max_failures = default_max_failures() if max_failures is None else max_failures
    sent = 0
    failures = 0
    reason = "closed"
    try:
        while True:
            started = _CLOCK()
            png = capture()
            if not png:
                failures += 1
                if failures >= max_failures:
                    reason = "capture_failed"
                    return
                _SLEEP(interval)
                continue
            failures = 0
            body, ctype = encoder(png, quality)
            if on_frame is not None:
                on_frame(len(body))
            sent += 1
            yield multipart_part(body, ctype)
            if max_frames and sent >= max_frames:
                reason = "frame_limit"
                return
            remaining = interval - (_CLOCK() - started)
            if remaining > 0:
                _SLEEP(remaining)
    finally:
        if on_stop is not None:
            on_stop(sent, reason)


# ---------------------------------------------------------------------------
# Route B: raw H.264 from `screenrecord`
# ---------------------------------------------------------------------------

def screenrecord_argv(adb_target, size=None, bitrate=None, time_limit=None):
    """argv for one recorder run. ``-`` sends the Annex-B stream to stdout."""
    argv = ["adb", "-s", adb_target, "exec-out", "screenrecord", "--output-format=h264"]
    if size:
        argv.append(f"--size={size[0]}x{size[1]}")
    if bitrate:
        argv.append(f"--bit-rate={int(bitrate)}")
    if time_limit:
        argv.append(f"--time-limit={int(time_limit)}")
    argv.append("-")
    return argv


def default_h264_time_limit():
    # Android's screenrecord refuses more than 180 s per run; the generator
    # chains runs so the client never notices.
    return _clamp(int(os.environ.get("STREAM_H264_TIME_LIMIT", "180")), 1, 180)


def default_h264_bitrate():
    return _clamp(int(os.environ.get("STREAM_H264_BITRATE", "4000000")), 100_000, 50_000_000)


def _popen(argv):
    return subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


def h264_chunks(argv, popen=None, chunk_size=64 * 1024, max_runs=0, max_restarts=None,
                on_chunk=None, on_stop=None):
    """Yield raw H.264 bytes from repeated ``screenrecord`` runs.

    Each run ends at its ``--time-limit``; the next run starts immediately and
    begins with fresh SPS/PPS, which every Annex-B decoder resynchronises on. A
    run that produces no bytes at all counts as a failure; ``max_restarts``
    consecutive empty runs end the stream. ``max_runs`` of 0 means unbounded.
    The recorder is killed when the client goes away.
    """
    popen = _popen if popen is None else popen  # late-bound so tests can patch _popen
    max_restarts = default_max_failures() if max_restarts is None else max_restarts
    runs = 0
    total = 0
    empty_runs = 0
    reason = "closed"
    proc = None
    try:
        while True:
            runs += 1
            produced = 0
            try:
                proc = popen(argv)
            except OSError:  # no adb on PATH, fork failure: a failed run, not a traceback
                proc = None
            if proc is not None:
                read = getattr(proc.stdout, "read1", proc.stdout.read)  # short reads: latency, not fill
                try:
                    while True:
                        chunk = read(chunk_size)
                        if not chunk:
                            break
                        produced += len(chunk)
                        total += len(chunk)
                        if on_chunk is not None:
                            on_chunk(len(chunk))
                        yield chunk
                finally:
                    _reap(proc)
                    proc = None
            if produced == 0:
                empty_runs += 1
                if empty_runs >= max_restarts:
                    reason = "recorder_failed"
                    return
            else:
                empty_runs = 0
            if max_runs and runs >= max_runs:
                reason = "run_limit"
                return
    finally:
        if proc is not None:
            _reap(proc)
        if on_stop is not None:
            on_stop(runs, total, reason)


def _reap(proc):
    try:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)
    except Exception:
        pass
    try:
        proc.stdout.close()
    except Exception:
        pass
