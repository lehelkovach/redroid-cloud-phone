"""Device / OS / container control helpers for the Control API.

Pure functions only: time-spec parsing, timezone and settings validation, APK
inspection and multipart frame framing. Everything that touches adb, docker or
the network lives in server.py behind a patchable seam, so these can be tested
with no device and the routes can be tested with the seams patched.

Every value that reaches an `adb shell` argv passes through one of the
validators here. The Control API exists so OSLO never has to author a raw
command line; a validator that let a `;` through would undo that.
"""

from __future__ import annotations

import hashlib
import re
import shlex
import zipfile
from datetime import datetime, timezone

MJPEG_BOUNDARY = "cloudphoneframe"
DEFAULT_STREAM_FPS = 4.0
MIN_STREAM_FPS = 0.5
MAX_STREAM_FPS = 10.0

# Olson ids: "UTC", "Etc/GMT+5", "America/Argentina/Buenos_Aires". No spaces,
# no shell metacharacters, at most four path segments.
_TZ_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_+\-]{0,31}(/[A-Za-z0-9_+\-]{1,32}){0,3}$")
SETTING_NAMESPACES = frozenset({"system", "secure", "global"})
_SETTING_KEY_RE = re.compile(r"^[a-z0-9_.]{1,80}$")
_PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")
MAX_SETTING_VALUE = 512


def parse_time_spec(data):
    """Epoch seconds from `{epoch}` or `{iso}`; None when neither parses."""
    data = data or {}
    epoch = data.get("epoch")
    if epoch is not None and epoch != "":
        try:
            value = int(float(epoch))
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None
    iso = data.get("iso")
    if isinstance(iso, str) and iso.strip():
        text = iso.strip()
        if text.endswith("Z") or text.endswith("z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        value = int(parsed.timestamp())
        return value if value > 0 else None
    return None


def epoch_to_toybox_set(epoch):
    """toybox `date -u MMDDhhmm[[CC]YY][.ss]` form, in UTC."""
    dt = datetime.fromtimestamp(int(epoch), tz=timezone.utc)
    return dt.strftime("%m%d%H%M%Y.%S")


def epoch_to_iso(epoch):
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat()


def is_valid_timezone(tz):
    return isinstance(tz, str) and bool(_TZ_RE.match(tz))


def is_valid_setting(namespace, key):
    return (
        isinstance(namespace, str)
        and namespace in SETTING_NAMESPACES
        and isinstance(key, str)
        and bool(_SETTING_KEY_RE.match(key))
    )


def quote_setting_value(value):
    """Shell-quote a settings value; refuse control characters and oversized input."""
    text = "" if value is None else str(value)
    if len(text) > MAX_SETTING_VALUE:
        raise ValueError("setting value too long")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise ValueError("setting value contains control characters")
    return shlex.quote(text)


def is_valid_package(pkg):
    return isinstance(pkg, str) and bool(_PACKAGE_RE.match(pkg)) and len(pkg) <= 200


def sha256_of_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def inspect_apk_file(path):
    """Classify a downloaded file: `apk`, `bundle` (xapk/apks/zip of apks) or `invalid`.

    A 404 page saved as `app.apk` is the lab failure this guards: the old flow
    would have handed `adb install` an HTML file and reported its stderr.
    """
    info = {"kind": "invalid", "apks": [], "sha256": None, "bytes": 0}
    try:
        with open(path, "rb") as f:
            head = f.read(4)
        if not head.startswith(b"PK\x03\x04"):
            return info
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
    except (OSError, zipfile.BadZipFile):
        return info
    info["sha256"] = sha256_of_file(path)
    import os

    info["bytes"] = os.path.getsize(path)
    if "AndroidManifest.xml" in names:
        info["kind"] = "apk"
        return info
    apks = [n for n in names if n.lower().endswith(".apk") and "/" not in n.strip("/")]
    if apks:
        info["kind"] = "bundle"
        info["apks"] = apks
    return info


def parse_version_from_dumpsys(text):
    """versionName / versionCode out of `dumpsys package <pkg>`."""
    name = None
    code = None
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("versionName="):
            name = line.split("=", 1)[1].strip()
        m = re.match(r"versionCode=(\d+)", line)
        if m and code is None:
            code = int(m.group(1))
    return name, code


def clamp_fps(raw):
    try:
        fps = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_STREAM_FPS
    if fps != fps:  # NaN
        return DEFAULT_STREAM_FPS
    return max(MIN_STREAM_FPS, min(MAX_STREAM_FPS, fps))


def mjpeg_part(frame, content_type="image/png"):
    """One `multipart/x-mixed-replace` part with a boundary line and length."""
    header = (
        f"--{MJPEG_BOUNDARY}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(frame)}\r\n\r\n"
    ).encode("ascii")
    return header + frame + b"\r\n"


def mjpeg_close():
    return f"--{MJPEG_BOUNDARY}--\r\n".encode("ascii")
