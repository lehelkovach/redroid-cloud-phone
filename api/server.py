#!/usr/bin/env python3
"""
Cloud Phone Control API

Enhanced API server for Cuttlefish cloud phone control including:
- ADB command interface
- Proxy configuration
- GPS/Location spoofing
- Device settings
- App management
- Screen capture
"""

import os
import json
import re
import subprocess
import shlex
import time
import base64
import contextlib
import math
import shutil
import tempfile
import threading
import uuid
from functools import wraps
from flask import Flask, request, jsonify, Response

try:
    from cloudphone_logging import configure as _configure_logging
    from cloudphone_logging import recent_logs, redact, truncate, verbose_enabled
    import ui_control
    import viewport
    import app_install
except ImportError:
    from api.cloudphone_logging import configure as _configure_logging
    from api.cloudphone_logging import recent_logs, redact, truncate, verbose_enabled
    from api import ui_control
    from api import viewport
    from api import app_install

app = Flask(__name__)

# Labeled logging — see docs/LOGGING.md. CLOUD_PHONE_VERBOSE=1 → DEBUG.
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logger = _configure_logging("control_api", log_type="API")
adb_logger = logger.bind("ADB")
cmd_logger = logger.bind("CMD")
apm_logger = logger.bind("APM")
vnc_logger = logger.bind("VNC")

# Configuration
ADB_CONNECT = os.environ.get("ADB_CONNECT", "127.0.0.1:5555")
API_TOKEN = os.environ.get("API_TOKEN", "")
UI_BACKEND = os.environ.get("UI_BACKEND", "adb").lower()
APPIUM_URL = os.environ.get("APPIUM_URL", "http://127.0.0.1:4723")
CONFIG_FILE = os.environ.get("CONFIG_FILE", "/etc/cloud-phone/config.json")
PROXY_SCRIPT = "/opt/cloud-phone-scripts/proxy-control.sh"
JOB_TTL_SECONDS = int(os.environ.get("JOB_TTL_SECONDS", "3600"))
JOB_MAX = int(os.environ.get("JOB_MAX", "500"))
# App install (docs/GAPPS-AND-APP-INSTALL.md). APP_INSTALL_DIRS is read per
# request so tests and operators can change it without a restart.
APP_INSTALL_MAX_BYTES = int(os.environ.get("APP_INSTALL_MAX_BYTES", str(app_install.DEFAULT_MAX_BYTES)))
APP_INSTALL_TIMEOUT = int(os.environ.get("APP_INSTALL_TIMEOUT", "300"))
APP_INSTALL_ALLOW_PRIVATE = os.environ.get("APP_INSTALL_ALLOW_PRIVATE", "").lower() in ("1", "true", "yes")
PLAY_INSTALL_TIMEOUT = int(os.environ.get("PLAY_INSTALL_TIMEOUT", "600"))
PLAY_UNCERTIFIED_URL = "https://www.google.com/android/uncertified"
PLAY_OWNER_ACTION = (
    "The phone's owner must sign in to Google Play on the phone themselves: take over "
    "the screen, open Play Store and sign in. If Play reports the device is not Play "
    "Protect certified, register the phone's GSF Android ID (GET /apps/play/status) at "
    + PLAY_UNCERTIFIED_URL + " first, then wait and retry. See docs/GAPPS-AND-APP-INSTALL.md."
)

# In-memory state
_state = {
    "proxy": {"enabled": False, "type": None, "host": None, "port": None},
    "location": {"enabled": False, "latitude": 0, "longitude": 0},
    "connected": False,
    "last_adb_error": "",
    "ui_backend": UI_BACKEND,
}

# In-memory job queue
_jobs = {}
_jobs_lock = threading.Lock()

# =============================================================================
# Helpers
# =============================================================================

_ADB_TARGET_RE = re.compile(r"^[A-Za-z0-9._:\[\]-]+$")


# A job runs on its own thread, outside the request that queued it; this
# carries that request's phone so the job does not drift to ADB_CONNECT.
_job_target = threading.local()


def current_adb_connect():
    """Per-request serial (orchestrator multi-phone) or process default."""
    hdr = ""
    try:
        hdr = (request.headers.get("X-Cloud-Phone-Adb") or "").strip()
    except RuntimeError:
        hdr = getattr(_job_target, "serial", "") or ""
    if hdr and _ADB_TARGET_RE.match(hdr) and len(hdr) < 128:
        return hdr
    return ADB_CONNECT


# Commands that carry what the user typed (or a screen full of it) are logged
# by a label, never by their arguments or output. See `_sensitive_adb`.
_adb_quiet = threading.local()


@contextlib.contextmanager
def _sensitive_adb(label, secret=""):
    """Within this block, run_adb logs `label` instead of argv and hides output."""
    previous = (getattr(_adb_quiet, "label", None), getattr(_adb_quiet, "secret", ""))
    _adb_quiet.label, _adb_quiet.secret = label, secret
    try:
        yield
    finally:
        _adb_quiet.label, _adb_quiet.secret = previous


def _scrub(text, secret):
    """Remove `secret` (and each word of it) from an error message."""
    text = "" if text is None else str(text)
    if not secret:
        return text
    pieces = {secret, secret.replace(" ", "%s")}
    pieces.update(word for word in re.split(r"[\s%]+", secret) if len(word) >= 3)
    for piece in sorted(pieces, key=len, reverse=True):
        if piece:
            text = text.replace(piece, "<text>")
    return text


def run_adb(*args, timeout=30):
    """Run ADB command and return (success, stdout, stderr)"""
    target = current_adb_connect()
    cmd = ["adb", "-s", target] + list(args)
    quiet = getattr(_adb_quiet, "label", None)
    secret = getattr(_adb_quiet, "secret", "") if quiet else ""
    shown = f"adb -s {target} {quiet}" if quiet else " ".join(cmd)
    started = time.time()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        success = result.returncode == 0
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        elapsed_ms = int((time.time() - started) * 1000)
        if not success:
            _state["last_adb_error"] = _scrub(stderr or stdout or "adb command failed", secret)
            adb_logger.warning(
                "failed rc=%s ms=%s cmd=%s stderr=%s",
                result.returncode, elapsed_ms, shown, truncate(_scrub(stderr, secret)),
            )
        else:
            hide = quiet or "screencap" in " ".join(args)
            out_note = f"{len(stdout)} bytes" if hide else truncate(stdout)
            adb_logger.debug("ok ms=%s cmd=%s stdout=%s", elapsed_ms, shown, out_note)
            if verbose_enabled():
                adb_logger.info("ok ms=%s cmd=%s stdout=%s", elapsed_ms, shown, out_note)
        return success, stdout, stderr
    except subprocess.TimeoutExpired:
        _state["last_adb_error"] = "adb command timed out"
        adb_logger.warning("timed out: %s", shown)
        return False, "", "Command timed out"
    except Exception as e:
        _state["last_adb_error"] = _scrub(str(e), secret)
        adb_logger.warning("exception: %s: %s", shown, _scrub(str(e), secret))
        return False, "", _scrub(str(e), secret)

def run_adb_shell(command, timeout=30):
    """Run ADB shell command"""
    return run_adb("shell", command, timeout=timeout)

def ensure_adb_connected():
    """Ensure ADB is connected to device"""
    target = current_adb_connect()
    success, out, _ = run_adb("devices")
    if target in out and "device" in out:
        _state["connected"] = True
        return True
    
    # Try to connect
    success, out, err = run_adb("connect", target)
    if success and "connected" in out.lower():
        _state["connected"] = True
        return True
    
    _state["connected"] = False
    if err or out:
        _state["last_adb_error"] = err or out
        adb_logger.warning("connect failed: %s", _state["last_adb_error"])
    return False


@app.before_request
def _log_request():
    path = request.path or "/"
    if path in ("/health", "/logs"):
        logger.debug("%s %s", request.method, path)
    else:
        logger.info("%s %s", request.method, path)

def presented_token():
    """Bearer token on the current request, or "" when none was sent."""
    try:
        header = request.headers.get("Authorization", "")
    except RuntimeError:  # outside a request context
        return ""
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return header.strip()


def auth_state():
    """Whether this caller can drive the device.

    `/health` is deliberately unauthenticated so systemd and Docker probes keep
    working, which used to mean a phone reported `healthy` while every device
    call answered 401. Reporting this alongside the ADB state is what stops an
    auth failure from reading as a dead device.
    """
    required = bool(API_TOKEN)
    token = presented_token()
    return {
        "required": required,
        "presented": bool(token),
        "ok": (not required) or token == API_TOKEN,
    }


def require_auth(f):
    """Decorator for API authentication"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if API_TOKEN:
            state = auth_state()
            if not state["ok"]:
                code = "auth_invalid" if state["presented"] else "auth_required"
                body = {
                    "success": False,
                    "error": "Unauthorized",
                    "code": code,
                    "auth_required": True,
                }
                response = jsonify(body)
                response.headers["WWW-Authenticate"] = 'Bearer realm="cloud-phone"'
                return response, 401
        return f(*args, **kwargs)
    return decorated

def load_config():
    """Load configuration from file"""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {}

def save_config(config):
    """Save configuration to file"""
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def _prune_jobs(now=None):
    now = now or time.time()
    with _jobs_lock:
        expired = [job_id for job_id, job in _jobs.items()
                   if now - job["created_at"] > JOB_TTL_SECONDS]
        for job_id in expired:
            _jobs.pop(job_id, None)
        if len(_jobs) > JOB_MAX:
            oldest = sorted(_jobs.items(), key=lambda item: item[1]["created_at"])
            for job_id, _ in oldest[: max(0, len(_jobs) - JOB_MAX)]:
                _jobs.pop(job_id, None)


def _update_job(job_id, **updates):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job.update(updates)
        job["updated_at"] = time.time()


# -----------------------------------------------------------------------------
# Input: every command's adb result is checked (rung M0, honest actuation).
# -----------------------------------------------------------------------------

# `input` and `am`/`cmd` print these on failure. Old adb shell protocols exit 0
# whatever the device command did, so the output is checked as well.
_INPUT_FAILURE_MARKERS = (
    "Exception occurred",
    "Error:",
    "java.lang.",
    "Usage: input",
    "Unknown command",
    "inaccessible or not found",
    "No shell command implementation",
    "Can't find service",
    "SecurityException",
)
KEYCODE_MOVE_END = "KEYCODE_MOVE_END"
KEYCODE_DEL = 67
KEYCODE_FORWARD_DEL = 112
KEYCODE_PASTE = 279
CLEAR_MAX_CHARS = 4096
KEYEVENT_BATCH = 32
SCREEN_SIZE_TTL = 300
UNICODE_PATH_TTL = 300
_SCREEN_SIZE_CACHE = {}    # adb serial -> (width, height, fetched_at)
_UNICODE_PATH_CACHE = {}   # adb serial -> (path or None, tried, checked_at)
_KEYCODE_NAME_RE = re.compile(r"^KEYCODE_[A-Z0-9_]+$")


class InputError(Exception):
    """An input that did not happen, with a machine-readable code."""

    def __init__(self, code, message="", **extra):
        super().__init__(message or code)
        self.code = code
        self.message = message
        self.extra = extra


def _input_failure(input_type, code, message="", **extra):
    body = {
        "success": False,
        "type": input_type,
        "error_code": code,
        "error": f"{code}: {truncate(message, 200)}" if message else code,
    }
    body.update(extra)
    return body


def _input_step(command, label=None, secret=""):
    """Run one input shell command. Raises InputError unless it really worked."""
    if label:
        with _sensitive_adb(label, secret):
            ok, stdout, stderr = run_adb_shell(command)
    else:
        ok, stdout, stderr = run_adb_shell(command)
    detail = _scrub((stderr or stdout or "").strip(), secret)
    if not ok:
        code = "adb_timeout" if "timed out" in detail.lower() else "adb_failed"
        raise InputError(code, detail or "adb command failed")
    combined = f"{stdout or ''}\n{stderr or ''}"
    if any(marker in combined for marker in _INPUT_FAILURE_MARKERS):
        raise InputError("input_rejected", detail)
    return stdout or ""


def _number(data, key, default=None):
    value = data.get(key, default)
    if isinstance(value, bool) or value is None:
        raise InputError("bad_request", f"{key} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise InputError("bad_request", f"{key} must be a number")
    if not math.isfinite(number):
        raise InputError("bad_request", f"{key} must be a number")
    return number


def _device_screen_size():
    """The phone's (width, height) from `wm size`, cached per phone; None if unknown."""
    serial = current_adb_connect()
    hit = _SCREEN_SIZE_CACHE.get(serial)
    if hit and time.time() - hit[2] < SCREEN_SIZE_TTL:
        return hit[0], hit[1]
    ok, out, _ = run_adb_shell("wm size")
    if not ok or not out:
        return None
    try:
        width, height = ui_control.parse_wm_size(out)
    except ui_control.UIError:
        return None
    _SCREEN_SIZE_CACHE[serial] = (width, height, time.time())
    return width, height


def _resolve_points(data, keys, default):
    """Pixels for each (x, y) pair; with `percentage: true` they are 0-100 of the screen."""
    raw = [(_number(data, xk, default), _number(data, yk, default)) for xk, yk in keys]
    if not data.get("percentage"):
        points = []
        for x, y in raw:
            if x < 0 or y < 0:
                raise InputError("bad_request", "coordinates must not be negative")
            points.append((int(round(x)), int(round(y))))
        return points, None
    for x, y in raw:
        if not (0 <= x <= 100 and 0 <= y <= 100):
            raise InputError("bad_request", "percentage coordinates must be within 0-100")
    size = _device_screen_size()
    if not size:
        raise InputError("screen_size_unavailable", "could not read the screen size (wm size)")
    width, height = size
    points = [
        (min(int(round(x / 100.0 * width)), width - 1),
         min(int(round(y / 100.0 * height)), height - 1))
        for x, y in raw
    ]
    return points, {"width": width, "height": height}


def _keycode(value):
    if isinstance(value, bool):
        raise InputError("bad_request", "keycode must be a number or KEYCODE_ name")
    if isinstance(value, int):
        if 0 <= value <= 1000:
            return str(value)
        raise InputError("bad_request", "keycode out of range")
    text = str(value or "").strip()
    if text.isdigit() and int(text) <= 1000:
        return text
    if _KEYCODE_NAME_RE.match(text):
        return text
    try:
        return str(ui_control.resolve_keycode(text))
    except ui_control.UIError:
        raise InputError("bad_request", "keycode must be a number or KEYCODE_ name")


def _key_batches(keycode, count):
    """`input keyevent` commands pressing `keycode` `count` times, in batches."""
    commands = []
    while count > 0:
        n = min(count, KEYEVENT_BATCH)
        commands.append("input keyevent " + " ".join([str(keycode)] * n))
        count -= n
    return commands


def _do_screen_action(action):
    sequences = {
        "on": ["input keyevent KEYCODE_WAKEUP"],
        "off": ["input keyevent KEYCODE_SLEEP"],
        "toggle": ["input keyevent KEYCODE_POWER"],
        "unlock": ["input keyevent KEYCODE_WAKEUP", None, "input swipe 500 1500 500 500"],
    }
    if action not in sequences:
        return _input_failure("screen", "unsupported_action", f"unknown screen action: {action}",
                              action=action)
    try:
        for command in sequences[action]:
            if command is None:
                time.sleep(0.5)
            else:
                _input_step(command)
    except InputError as exc:
        return _input_failure("screen", exc.code, exc.message, action=action)
    return {"success": True, "action": action}


def _loggable_input(data):
    payload = redact(data)
    if str(payload.get("type") or payload.get("action") or "").lower() in {"text", "type"}:
        raw = data.get("text", "")
        payload = dict(payload)
        payload["text"] = f"<{len(str(raw))} chars>"
    return payload


_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
# Class names of text inputs: EditText and its subclasses, plus the
# TextInput family some toolkits report.
_EDITABLE_CLASS_MARKERS = ("EditText", "TextInput", "AutoCompleteTextView")


def _flag(attrib, key):
    return attrib.get(key) == "true"


def _text_state(raw_text, hint, editable_or_password):
    """What an input's `text` holds: "empty", "hint", "value" or "unknown".

    Android reports an empty field's hint as its text. Only a dump that also
    carries `hint` can tell the two apart; without one a non-empty field is
    "unknown" and treated as a value.
    """
    if not editable_or_password:
        return None
    if not raw_text.strip():
        return "empty"
    if hint is None:
        return "unknown"
    return "hint" if raw_text.strip() == hint else "value"


def _parse_full_elements(xml_text):
    """Every useful node, values included. Internal: callers get `_public_element`."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    elements = []
    for node in root.iter("node"):
        attrib = node.attrib
        raw_text = attrib.get("text") or ""
        content_desc = (attrib.get("content-desc") or "").strip()
        label = raw_text.strip() or content_desc
        resource_id = attrib.get("resource-id", "")
        cls = attrib.get("class", "")
        editable = any(marker in cls for marker in _EDITABLE_CLASS_MARKERS)
        password = _flag(attrib, "password")
        if not label and not resource_id and not editable:
            continue
        match = _BOUNDS_RE.match(attrib.get("bounds", ""))
        if not match:
            continue
        left, top, right, bottom = (int(g) for g in match.groups())
        hint = (attrib.get("hint") or "").strip() or None
        state = _text_state(raw_text, hint, editable or password)
        elements.append({
            "label": label,
            "resource_id": resource_id,
            "clickable": _flag(attrib, "clickable"),
            "focused": _flag(attrib, "focused"),
            "password": password,
            "x": (left + right) // 2,
            "y": (top + bottom) // 2,
            "bounds": [left, top, right, bottom],
            "class": cls,
            "package": attrib.get("package", ""),
            "text": raw_text,
            "content_desc": content_desc,
            "hint": hint,
            "checkable": _flag(attrib, "checkable"),
            "checked": _flag(attrib, "checked"),
            "enabled": _flag(attrib, "enabled"),
            "focusable": _flag(attrib, "focusable"),
            "editable": editable,
            "long_clickable": _flag(attrib, "long-clickable"),
            "scrollable": _flag(attrib, "scrollable"),
            "selected": _flag(attrib, "selected"),
            "text_state": state,
            "text_len": 0 if state in ("empty", "hint") else len(raw_text),
            "value_redacted": False,
        })
    return elements


def _public_element(element, include_values=False):
    """A copy safe to return: typed values are replaced by their length.

    Passwords stay hidden even with `include_values`.
    """
    out = dict(element)
    holds_value = element.get("text_state") in ("value", "unknown")
    if holds_value and (not include_values or element.get("password")):
        out["text"] = None
        out["label"] = element.get("content_desc") or element.get("hint") or ""
        out["value_redacted"] = True
    return out


def _parse_ui_elements(xml_text, include_values=False):
    """Flatten a `uiautomator dump` hierarchy into labeled, tappable elements.

    Coordinates come back as the centre of the node's bounds, because tapping a
    corner lands outside rounded or padded widgets. Text an input holds is
    redacted (`value_redacted`, `text_len`) unless `include_values`.
    """
    return [_public_element(e, include_values) for e in _parse_full_elements(xml_text)]


UI_DUMP_PATH = "/data/local/tmp/cloudphone_ui.xml"


def _read_ui_tree(include_values=False, raw=False):
    """`raw=True` returns unredacted elements, for matching inside this process only."""
    ensure_adb_connected()
    # Dump to a file and cat it in the same shell. `uiautomator dump /dev/tty`
    # prints only "UI hierchary dumped to: /dev/tty" on Android 13 Redroid
    # (adb shell without a pty), so /device/ui failed on every GApps phone.
    with _sensitive_adb(f"shell uiautomator dump {UI_DUMP_PATH} && cat"):
        ok, stdout, stderr = run_adb_shell(
            f"uiautomator dump {UI_DUMP_PATH} >/dev/null 2>&1 && cat {UI_DUMP_PATH}"
        )
    if not ok:
        return {"success": False, "error": stderr or "uiautomator dump failed"}
    start = stdout.find("<?xml")
    if start == -1:
        start = stdout.find("<hierarchy")
    if start == -1:
        return {"success": False, "error": "no UI hierarchy in dump output"}
    xml_text = stdout[start:]
    full = _parse_full_elements(xml_text)
    elements = full if raw else [_public_element(e, include_values) for e in full]
    return {
        "success": True,
        "count": len(elements),
        "elements": elements,
        "values_included": bool(include_values or raw),
        # False on dumpers that do not write `hint`: then an input showing its
        # hint cannot be told from one holding a value.
        "hints_exposed": ' hint="' in xml_text,
    }


def _find_by_label(elements, label):
    """Pick the element a human would mean by `label`.

    Clickability outranks exactness: a form's caption ("Email") often matches
    the query more exactly than the input beside it, but tapping the caption
    does nothing. Among equally tappable candidates, the exact label wins so
    "Sign up" does not select "Sign up with Google".
    """
    wanted = str(label or "").strip().lower()
    if not wanted:
        return None

    candidates = []
    for element in elements:
        text = element["label"].lower()
        resource_id = element["resource_id"].lower()
        exact = text == wanted
        if not (exact or wanted in text or wanted in resource_id):
            continue
        candidates.append((element["clickable"], exact, element))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][2]


def current_focus():
    """Focused window line from `dumpsys window`, or "" when unavailable.

    The filtering happens here rather than in `adb shell "... | grep ..."`: a
    pipe inside the guest shell closes early and kills the upstream dumpsys, so
    the piped form returns an empty string and reads as a failed read.
    """
    ok, stdout, _ = run_adb_shell("dumpsys window")
    if not ok:
        return ""
    for line in (stdout or "").splitlines():
        if "mCurrentFocus" in line:
            return line.strip()
    return ""


def _do_tap_label(label, include_values=False):
    tree = _read_ui_tree(raw=True)
    if not tree.get("success"):
        return _input_failure("tap_label", "ui_dump_failed", tree.get("error", ""))
    element = _find_by_label(tree["elements"], label)
    if not element:
        available = [e["label"] for e in (_public_element(x) for x in tree["elements"])
                     if e["label"]][:12]
        return {"success": False, "type": "tap_label", "error_code": "no_match",
                "error": f"no element labeled '{label}'", "available": available}
    _input_step(f"input tap {element['x']} {element['y']}")
    viewport.frame(vnc_logger)
    return {"success": True, "type": "tap_label", "label": label,
            "x": element["x"], "y": element["y"],
            "matched": _public_element(element, include_values)}


def _do_tap(data):
    (point,), screen = _resolve_points(data, [("x", "y")], 500)
    _input_step(f"input tap {point[0]} {point[1]}")
    viewport.frame(vnc_logger)
    body = {"success": True, "type": "tap", "x": point[0], "y": point[1]}
    if screen:
        body.update({"percentage": True, "screen": screen})
    return body


def _do_swipe(data):
    defaults = {"x1": 100, "y1": 100, "x2": 500, "y2": 500}
    merged = {**defaults, **{k: v for k, v in data.items() if v is not None}}
    (start, end), screen = _resolve_points(merged, [("x1", "y1"), ("x2", "y2")], None)
    duration = _number(data, "duration", 300)
    if duration < 0:
        raise InputError("bad_request", "duration must not be negative")
    _input_step(f"input swipe {start[0]} {start[1]} {end[0]} {end[1]} {int(duration)}")
    viewport.frame(vnc_logger)
    body = {"success": True, "type": "swipe", "from": list(start), "to": list(end),
            "duration": int(duration)}
    if screen:
        body.update({"percentage": True, "screen": screen})
    return body


def _do_key(data):
    keycode = _keycode(data.get("keycode", 4))
    _input_step(f"input keyevent {keycode}")
    return {"success": True, "type": "key", "keycode": keycode}


def _clear_focused_field():
    """Empty the focused input: MOVE_END, then forward-delete and delete.

    The count comes from the current text length in a fresh dump, so the
    deletes are bounded; forward-deletes cover text after the cursor on a
    multi-line field, where MOVE_END only reaches the end of the line.
    """
    tree = _read_ui_tree(raw=True)
    if not tree.get("success"):
        raise InputError("ui_dump_failed", tree.get("error", ""))
    field = next((e for e in tree["elements"]
                  if e["focused"] and (e["editable"] or e["password"])), None)
    if field is None:
        raise InputError("no_focused_field", "clear needs a focused text field; tap it first")
    count = min(field["text_len"], CLEAR_MAX_CHARS)
    if count:
        _input_step(f"input keyevent {KEYCODE_MOVE_END}")
        for command in _key_batches(KEYCODE_FORWARD_DEL, count) + _key_batches(KEYCODE_DEL, count):
            _input_step(command)
    return {"chars": count, "method": "move_end+forward_del+del",
            "length_from": field["text_state"]}


def _probe_adbkeyboard():
    ok, out, _ = run_adb_shell("settings get secure default_input_method")
    return ok and "com.android.adbkeyboard" in (out or "")


def _probe_clipper():
    ok, out, _ = run_adb_shell("pm path ca.zgrs.clipper")
    return ok and "package:" in (out or "")


class _PathUnavailable(Exception):
    """This phone lacks a Unicode text path; try the next one."""


def _set_clipboard_cmd(text, label):
    try:
        _input_step("cmd clipboard set-text " + ui_control.shell_quote(text), label, text)
    except InputError as exc:
        raise _PathUnavailable(exc.code)


def _set_clipboard_clipper(text, label):
    out = _input_step(
        "am broadcast -n ca.zgrs.clipper/.ClipperReceiver -a clipper.set -e text "
        + ui_control.shell_quote(text), label, text)
    if "result=-1" not in out:  # Activity.RESULT_OK: Clipper copied it
        raise InputError("input_rejected", "clipper did not confirm the copy")


def _type_adbkeyboard(text, label):
    b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
    _input_step(f"am broadcast -a ADB_INPUT_B64 --es msg {b64}", label, text)
    return {}


def _type_via_clipboard(setter, text, label):
    setter(text, label)  # may raise _PathUnavailable: nothing was typed yet
    _input_step(f"input keyevent {KEYCODE_PASTE}")
    # Leave nothing personal on the clipboard for other apps to read.
    try:
        setter("", "clipboard clear")
        cleared = True
    except (InputError, _PathUnavailable):
        cleared = False
    return {"clipboard_cleared": cleared}


# (name, availability probe or None to just try, typer). Tried in order.
_UNICODE_PATHS = (
    ("adbkeyboard", _probe_adbkeyboard, _type_adbkeyboard),
    ("cmd_clipboard", None,
     lambda text, label: _type_via_clipboard(_set_clipboard_cmd, text, label)),
    ("clipper_broadcast", _probe_clipper,
     lambda text, label: _type_via_clipboard(_set_clipboard_clipper, text, label)),
)


def _type_unicode(text, label):
    """Type text `input text` cannot, by the first path this phone supports."""
    serial = current_adb_connect()
    paths = {name: (probe, typer) for name, probe, typer in _UNICODE_PATHS}
    cached = _UNICODE_PATH_CACHE.get(serial)
    if cached and time.time() - cached[2] < UNICODE_PATH_TTL:
        name, tried = cached[0], cached[1]
        if name is None:
            raise InputError("unsupported_unicode", "no Unicode text path on this phone",
                             tried=tried)
        try:
            return name, paths[name][1](text, label)
        except _PathUnavailable as exc:
            _UNICODE_PATH_CACHE.pop(serial, None)
            raise InputError("unsupported_unicode", f"{name} stopped working ({exc})",
                             tried=[name])
        except InputError:
            _UNICODE_PATH_CACHE.pop(serial, None)
            raise
    tried = []
    for name, probe, typer in _UNICODE_PATHS:
        tried.append(name)
        if probe is not None and not probe():
            continue
        try:
            extra = typer(text, label)
        except _PathUnavailable:
            continue  # e.g. this build has no `cmd clipboard`
        _UNICODE_PATH_CACHE[serial] = (name, list(tried), time.time())
        return name, extra
    _UNICODE_PATH_CACHE[serial] = (None, list(tried), time.time())
    raise InputError("unsupported_unicode",
                     "no Unicode text path on this phone (install ADBKeyboard or Clipper)",
                     tried=tried)


def _do_text(data):
    text = data.get("text", "")
    text = "" if text is None else str(text)
    body = {"success": True, "type": "text", "text_len": len(text)}
    if data.get("clear"):
        body["cleared"] = _clear_focused_field()
    if not text:
        body["path"] = "none"
        return body
    label = f"shell input text <{len(text)} chars>"
    if ui_control.is_plain_text(text):
        for command in ui_control.input_text_commands(text):
            _input_step(command, label, text)
        body["path"] = "input_text"
        return body
    path, extra = _type_unicode(text, f"shell <unicode text, {len(text)} chars>")
    body["path"] = path
    body.update(extra)
    return body


_INPUT_HANDLERS = {
    "tap": _do_tap,
    "swipe": _do_swipe,
    "text": _do_text,
    "key": _do_key,
}


def _do_device_input(data):
    """One input; `success` is true only when adb and the device accepted it."""
    input_type = str(data.get("type") or "tap").lower()
    cmd_logger.info("commandlet %s payload=%s", input_type, _loggable_input(data))
    try:
        if input_type == "tap_label":
            return _do_tap_label(data.get("label"), _truthy(data.get("include_values")))
        handler = _INPUT_HANDLERS.get(input_type)
        if handler is None:
            raise InputError("unsupported_type", f"unsupported input type: {input_type}")
        return handler(data)
    except InputError as exc:
        cmd_logger.warning("commandlet %s failed code=%s", input_type, exc.code)
        return _input_failure(input_type, exc.code, exc.message, **exc.extra)


def _truthy(value):
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _appium_available():
    if not APPIUM_URL:
        return False
    try:
        import appium  # noqa: F401
        return True
    except ImportError:
        return False


def _appium_status():
    ready = _appium_available()
    status = {
        "url": APPIUM_URL,
        "ready": ready,
        "backend": _state.get("ui_backend") or UI_BACKEND,
    }
    apm_logger.info(
        "status url=%s ready=%s backend=%s",
        status["url"], status["ready"], status["backend"],
    )
    return status


def _screen_size():
    if not _state.get("connected"):
        return viewport.default_size()
    ok, out, _ = run_adb_shell("wm size")
    if ok and out:
        try:
            return ui_control.parse_wm_size(out)
        except ui_control.UIError:
            pass
    return viewport.default_size()


def _execute_ui_command(cmd):
    backend_pref = cmd.get("backend") or _state.get("ui_backend") or UI_BACKEND
    size = _screen_size()
    w3c = None
    try:
        w3c = ui_control.build_appium_actions(cmd, size)
        apm_logger.info(
            "w3c action=%s size=%sx%s payload=%s",
            cmd.get("action") or cmd.get("type"), size[0], size[1], w3c,
        )
    except ui_control.UIError:
        pass

    if str(backend_pref).lower() == "appium" and not _appium_available():
        apm_logger.warning(
            "backend=appium unavailable url=%s (install appium-python-client, set APPIUM_URL)",
            APPIUM_URL,
        )
        return {
            "success": False,
            "error": "appium backend selected but not available; install appium-python-client and set APPIUM_URL",
            "backend": "appium",
            "appium_url": APPIUM_URL,
            "w3c": w3c,
        }, 501

    backend = ui_control.select_backend(backend_pref, appium_available=_appium_available())
    if backend == "adb":
        shell_cmds = ui_control.build_adb_input(cmd, size)
        is_text = str(cmd.get("action") or cmd.get("type") or "").lower() in ("text", "type")
        secret = str(cmd.get("text", "")) if is_text else ""
        shown = [f"input text <{len(secret)} chars>"] * len(shell_cmds) if is_text else shell_cmds
        cmd_logger.info(
            "commandlet action=%s backend=adb size=%sx%s cmds=%s",
            cmd.get("action") or cmd.get("type"), size[0], size[1], shown,
        )
        results = []
        for shell, label in zip(shell_cmds, shown):
            with _sensitive_adb(f"shell {label}", secret) if is_text else contextlib.nullcontext():
                ok, stdout, stderr = run_adb_shell(shell)
            results.append({"cmd": label, "ok": ok, "stdout": stdout,
                            "stderr": _scrub(stderr, secret)})
        viewport.frame(vnc_logger)
        return {
            "success": all(item["ok"] for item in results) if results else True,
            "backend": "adb",
            "size": {"width": size[0], "height": size[1]},
            "commands": shown,
            "results": results,
            "w3c": w3c,
        }, 200

    apm_logger.warning("appium selected but not wired on this instance url=%s", APPIUM_URL)
    return {
        "success": False,
        "error": "appium backend selected but not wired on this instance",
        "backend": "appium",
        "w3c": w3c,
    }, 501


def _ui_response(cmd):
    try:
        body, status = _execute_ui_command(cmd)
        return jsonify(body), status
    except ui_control.UIError as exc:
        cmd_logger.warning("commandlet rejected: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 400


def _do_screenshot_base64():
    result = subprocess.run(
        ["adb", "-s", current_adb_connect(), "exec-out", "screencap", "-p"],
        capture_output=True, timeout=30
    )
    if result.returncode == 0 and result.stdout:
        return {"success": True, "image_base64": base64.b64encode(result.stdout).decode()}
    return {"success": False, "error": "Failed to capture screenshot"}


def _handle_job(job_type, payload):
    ensure_adb_connected()
    if job_type == "adb_shell":
        command = payload.get("command", "")
        timeout = payload.get("timeout", 30)
        if not command:
            return {"success": False, "error": "command required"}
        success, stdout, stderr = run_adb_shell(command, timeout=timeout)
        return {"success": success, "stdout": stdout, "stderr": stderr}
    if job_type == "device_input":
        return _do_device_input(payload or {})
    if job_type == "screen":
        return _do_screen_action(payload.get("action", "toggle"))
    if job_type == "screenshot":
        return _do_screenshot_base64()
    if job_type == "app_start":
        package = payload.get("package", "")
        if not package:
            return {"success": False, "error": "package required"}
        success, out, err = run_adb_shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
        return {"success": success, "message": out or err}
    if job_type == "app_stop":
        package = payload.get("package", "")
        if not package:
            return {"success": False, "error": "package required"}
        success, out, err = run_adb_shell(f"am force-stop {package}")
        return {"success": success, "message": out or err}
    if job_type == "app_clear":
        package = payload.get("package", "")
        if not package:
            return {"success": False, "error": "package required"}
        success, out, err = run_adb_shell(f"pm clear {package}")
        return {"success": success, "message": out or err}
    if job_type == "app_uninstall":
        package = payload.get("package", "")
        if not package:
            return {"success": False, "error": "package required"}
        success, out, err = run_adb_shell(f"pm uninstall {package}")
        return {"success": success, "message": out or err}
    if job_type == "play_install":
        return _play_install(payload or {})
    return {"success": False, "error": f"unsupported job type: {job_type}"}


def _run_job(job_id, job_type, payload, adb_target=None):
    _job_target.serial = adb_target or ""
    _update_job(job_id, status="running")
    try:
        result = _handle_job(job_type, payload or {})
        _update_job(job_id, status="done", result=result)
    except Exception as exc:
        logger.exception("Job failed: %s", job_id)
        _update_job(job_id, status="failed", error=str(exc))
    finally:
        _job_target.serial = ""
    _prune_jobs()

# =============================================================================
# Health & Status Endpoints
# =============================================================================

def _gapps_status():
    """Play/GMS packages. Spoof props do not count."""
    mapping = {
        "gms": "com.google.android.gms",
        "play_store": "com.android.vending",
        "gsf": "com.google.android.gsf",
    }
    found = {}
    for key, pkg in mapping.items():
        ok, stdout, _ = run_adb_shell(f"pm path {pkg}")
        found[key] = bool(ok and "package:" in (stdout or ""))
    found["ready"] = bool(found.get("gms") and found.get("play_store"))
    return found


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint.

    Stays open and always answers 200 so probes keep working, but reports
    whether the caller could actually drive the device: `status` is
    `unauthorized` for a caller whose token every device endpoint will reject,
    because "healthy" plus 401 everywhere is how a working phone reads as dead.
    """
    connected = ensure_adb_connected()
    gapps = _gapps_status() if connected else {
        "gms": False, "play_store": False, "gsf": False, "ready": False,
    }
    auth = auth_state()
    if not auth["ok"]:
        status = "unauthorized"
    elif connected:
        status = "healthy"
    else:
        status = "degraded"
    vnc = viewport.snapshot()
    appium = {
        "url": APPIUM_URL,
        "ready": _appium_available(),
        "backend": _state.get("ui_backend") or UI_BACKEND,
    }
    return jsonify({
        "status": status,
        "adb_connected": connected,
        "adb_target": current_adb_connect(),
        "auth": auth,
        "usable": bool(connected and auth["ok"]),
        "runtime": os.environ.get("CLOUD_PHONE_RUNTIME", "unknown"),
        "gapps": gapps,
        "appium": appium,
        "vnc": vnc,
        "state": _state
    })

@app.route("/status", methods=["GET"])
@require_auth
def status():
    """Detailed device status"""
    ensure_adb_connected()
    
    # Get device properties
    success, model, _ = run_adb_shell("getprop ro.product.model")
    success, android_version, _ = run_adb_shell("getprop ro.build.version.release")
    success, sdk, _ = run_adb_shell("getprop ro.build.version.sdk")
    success, battery, _ = run_adb_shell("dumpsys battery | grep level")
    success, screen, _ = run_adb_shell("dumpsys display | grep mScreenState")
    
    return jsonify({
        "connected": _state["connected"],
        "device": {
            "model": model or "unknown",
            "android_version": android_version or "unknown",
            "sdk_version": sdk or "unknown"
        },
        "battery": battery.split(":")[-1].strip() if battery else "unknown",
        "screen_state": "on" if "ON" in screen else "off" if screen else "unknown",
        "proxy": _state["proxy"],
        "location": _state["location"],
        "gapps": _gapps_status(),
    })

# =============================================================================
# Proxy Control Endpoints
# =============================================================================

@app.route("/proxy", methods=["GET"])
@require_auth
def get_proxy():
    """Get current proxy configuration"""
    return jsonify(_state["proxy"])

@app.route("/proxy", methods=["POST"])
@require_auth
def set_proxy():
    """
    Set proxy configuration
    
    Body:
    {
        "enabled": true,
        "type": "socks5",  // http, socks5, transparent
        "host": "proxy.example.com",
        "port": 1080,
        "username": "",  // optional
        "password": ""   // optional
    }
    """
    data = request.get_json() or {}
    
    enabled = data.get("enabled", False)
    proxy_type = data.get("type", "socks5")
    host = data.get("host", "")
    port = data.get("port", 0)
    username = data.get("username", "")
    password = data.get("password", "")
    
    if enabled and (not host or not port):
        return jsonify({"error": "host and port required when enabled"}), 400
    
    success = False
    message = ""
    
    if enabled:
        if proxy_type == "http":
            # Set Android global HTTP proxy
            success, _, err = run_adb_shell(
                f"settings put global http_proxy {host}:{port}"
            )
            if success:
                message = f"HTTP proxy set to {host}:{port}"
        
        elif proxy_type == "socks5":
            # Use iptables + redsocks/tun2socks for SOCKS5
            # This requires the proxy-control.sh script
            if os.path.exists(PROXY_SCRIPT):
                result = subprocess.run(
                    [PROXY_SCRIPT, "enable", proxy_type, host, str(port), username, password],
                    capture_output=True, text=True
                )
                success = result.returncode == 0
                message = result.stdout or result.stderr
            else:
                # Fallback: set via ADB
                success, _, err = run_adb_shell(
                    f"setprop persist.proxy.socks5.host {host} && "
                    f"setprop persist.proxy.socks5.port {port}"
                )
                message = f"SOCKS5 proxy set to {host}:{port} (app-level)"
        
        elif proxy_type == "transparent":
            # Transparent proxy via iptables
            if os.path.exists(PROXY_SCRIPT):
                result = subprocess.run(
                    [PROXY_SCRIPT, "enable", "transparent", host, str(port)],
                    capture_output=True, text=True
                )
                success = result.returncode == 0
                message = result.stdout or result.stderr
            else:
                return jsonify({"error": "Transparent proxy requires proxy-control.sh"}), 500
    else:
        # Disable proxy
        run_adb_shell("settings put global http_proxy :0")
        if os.path.exists(PROXY_SCRIPT):
            subprocess.run([PROXY_SCRIPT, "disable"], capture_output=True)
        success = True
        message = "Proxy disabled"
    
    if success:
        _state["proxy"] = {
            "enabled": enabled,
            "type": proxy_type if enabled else None,
            "host": host if enabled else None,
            "port": port if enabled else None
        }
    
    return jsonify({
        "success": success,
        "message": message,
        "proxy": _state["proxy"]
    })

@app.route("/proxy", methods=["DELETE"])
@require_auth
def disable_proxy():
    """Disable proxy"""
    return set_proxy()  # Will call with empty body = disabled

# =============================================================================
# GPS/Location Spoofing Endpoints
# =============================================================================

@app.route("/location", methods=["GET"])
@require_auth
def get_location():
    """Get current spoofed location"""
    return jsonify(_state["location"])

@app.route("/location", methods=["POST"])
@require_auth
def set_location():
    """
    Set GPS location (mock location)
    
    Body:
    {
        "enabled": true,
        "latitude": 37.7749,
        "longitude": -122.4194,
        "altitude": 0,
        "accuracy": 10
    }
    """
    data = request.get_json() or {}
    
    enabled = data.get("enabled", False)
    latitude = data.get("latitude", 0)
    longitude = data.get("longitude", 0)
    altitude = data.get("altitude", 0)
    accuracy = data.get("accuracy", 10)
    
    if enabled:
        # Enable mock locations in developer settings
        run_adb_shell("settings put secure mock_location 1")
        
        # Method 1: Use geo fix command via adb
        # Format: geo fix <longitude> <latitude> [altitude] [satellites] [velocity]
        success, out, err = run_adb_shell(
            f"am broadcast -a android.intent.action.MOCK_LOCATION "
            f"--ef latitude {latitude} --ef longitude {longitude} "
            f"--ef altitude {altitude} --ef accuracy {accuracy}"
        )
        
        # Method 2: Use appops to allow mock location for shell
        run_adb_shell("appops set com.android.shell android:mock_location allow")
        
        # Method 3: Direct geo command (telnet-style, may not work on all)
        geo_cmd = f"geo fix {longitude} {latitude} {altitude}"
        run_adb("emu", geo_cmd)
        
        # Alternative: Use location mock app if installed
        run_adb_shell(
            f"am start -a android.intent.action.VIEW "
            f"-d 'geo:{latitude},{longitude}'"
        )
        
        _state["location"] = {
            "enabled": True,
            "latitude": latitude,
            "longitude": longitude,
            "altitude": altitude,
            "accuracy": accuracy
        }
        
        return jsonify({
            "success": True,
            "message": f"Location set to {latitude}, {longitude}",
            "location": _state["location"]
        })
    else:
        run_adb_shell("settings put secure mock_location 0")
        _state["location"] = {"enabled": False, "latitude": 0, "longitude": 0}
        
        return jsonify({
            "success": True,
            "message": "Mock location disabled",
            "location": _state["location"]
        })

@app.route("/location", methods=["DELETE"])
@require_auth
def disable_location():
    """Disable mock location"""
    return set_location()  # Empty body = disabled

# =============================================================================
# ADB Command Interface
# =============================================================================

@app.route("/adb/shell", methods=["POST"])
@require_auth
def adb_shell():
    """
    Execute ADB shell command
    
    Body:
    {
        "command": "ls /sdcard",
        "timeout": 30
    }
    """
    data = request.get_json() or {}
    command = data.get("command", "")
    timeout = data.get("timeout", 30)
    
    if not command:
        return jsonify({"error": "command required"}), 400
    
    ensure_adb_connected()
    success, stdout, stderr = run_adb_shell(command, timeout=timeout)
    
    return jsonify({
        "success": success,
        "stdout": stdout,
        "stderr": stderr
    })

@app.route("/adb/push", methods=["POST"])
@require_auth
def adb_push():
    """
    Push file to device
    
    Body (multipart/form-data):
    - file: The file to push
    - path: Destination path on device (e.g., /sdcard/myfile.txt)
    """
    if "file" not in request.files:
        return jsonify({"error": "file required"}), 400
    
    file = request.files["file"]
    dest_path = request.form.get("path", f"/sdcard/{file.filename}")
    
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        file.save(tmp.name)
        success, out, err = run_adb("push", tmp.name, dest_path)
        os.unlink(tmp.name)
    
    return jsonify({
        "success": success,
        "path": dest_path,
        "message": out or err
    })

@app.route("/adb/pull", methods=["POST"])
@require_auth
def adb_pull():
    """
    Pull file from device
    
    Body:
    {
        "path": "/sdcard/myfile.txt"
    }
    
    Returns: File content as base64
    """
    data = request.get_json() or {}
    path = data.get("path", "")
    
    if not path:
        return jsonify({"error": "path required"}), 400
    
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        success, out, err = run_adb("pull", path, tmp.name)
        if success and os.path.exists(tmp.name):
            with open(tmp.name, "rb") as f:
                content = base64.b64encode(f.read()).decode()
            os.unlink(tmp.name)
            return jsonify({
                "success": True,
                "path": path,
                "content_base64": content
            })
        os.unlink(tmp.name)
    
    return jsonify({"success": False, "error": err}), 404

@app.route("/adb/install", methods=["POST"])
@require_auth
def adb_install():
    """
    Install APK on device
    
    Body (multipart/form-data):
    - file: APK file
    - options: Additional options (e.g., "-r" for reinstall)
    """
    if "file" not in request.files:
        return jsonify({"error": "APK file required"}), 400
    
    file = request.files["file"]
    options = request.form.get("options", "-r").split()
    
    with tempfile.NamedTemporaryFile(suffix=".apk", delete=False) as tmp:
        file.save(tmp.name)
        success, out, err = run_adb("install", *options, tmp.name, timeout=120)
        os.unlink(tmp.name)
    
    return jsonify({
        "success": success,
        "message": out or err
    })

# =============================================================================
# Device Control Endpoints
# =============================================================================

@app.route("/device/screen", methods=["POST"])
@require_auth
def control_screen():
    """
    Control screen state
    
    Body:
    {
        "action": "on" | "off" | "toggle" | "unlock"
    }
    """
    data = request.get_json() or {}
    action = data.get("action", "toggle")
    return jsonify(_do_screen_action(action))

@app.route("/device/input", methods=["POST"])
@require_auth
def device_input():
    """
    Send input to device. `success` is true only when adb and the device
    accepted every command; otherwise `success: false` with `error_code`
    (adb_failed, adb_timeout, input_rejected, bad_request, screen_size_unavailable,
    no_focused_field, ui_dump_failed, unsupported_unicode, unsupported_type, no_match).

    Body:
    {
        "type": "tap" | "tap_label" | "swipe" | "text" | "key",
        "x": 500, "y": 500,          // tap; with "percentage": true, 0-100 of the screen
        "label": "Save",             // tap_label
        "x1": 100, "y1": 100,        // swipe (percentage applies too)
        "x2": 500, "y2": 500,
        "duration": 300,             // swipe (ms)
        "text": "hello",             // text; any characters, see `path` in the reply
        "clear": false,              // text: empty the focused field first
        "keycode": 4                 // key: number or KEYCODE_ name (BACK=4, HOME=3)
    }
    """
    data = request.get_json() or {}
    return jsonify(_do_device_input(data))


@app.route("/ui/command", methods=["POST"])
@require_auth
def ui_command():
    """Backend-agnostic UI commandlet. Body: action plus coords, optional backend=adb|appium."""
    return _ui_response(request.get_json() or {})


@app.route("/ui/tap", methods=["POST"])
@require_auth
def ui_tap():
    cmd = request.get_json() or {}
    cmd.setdefault("action", "tap")
    return _ui_response(cmd)


@app.route("/ui/swipe", methods=["POST"])
@require_auth
def ui_swipe():
    cmd = request.get_json() or {}
    cmd.setdefault("action", "swipe")
    return _ui_response(cmd)


@app.route("/ui/text", methods=["POST"])
@require_auth
def ui_text():
    cmd = request.get_json() or {}
    cmd.setdefault("action", "text")
    return _ui_response(cmd)


@app.route("/ui/key", methods=["POST"])
@require_auth
def ui_key():
    cmd = request.get_json() or {}
    cmd.setdefault("action", "key")
    return _ui_response(cmd)


@app.route("/ui/screen", methods=["GET"])
@require_auth
def ui_screen():
    size = _screen_size()
    cmd_logger.info("getScreen %sx%s", size[0], size[1])
    shot = _do_screenshot_base64()
    viewport.frame(vnc_logger, nbytes=len(shot.get("image_base64") or ""))
    shot.update({"width": size[0], "height": size[1], "backend": _state.get("ui_backend") or UI_BACKEND})
    return jsonify(shot)


@app.route("/appium/status", methods=["GET"])
def appium_status():
    return jsonify(_appium_status())


@app.route("/appium/session", methods=["POST"])
@require_auth
def appium_session():
    body = request.get_json() or {}
    apm_logger.info("session create requested url=%s caps=%s", APPIUM_URL, redact(body))
    if not _appium_available():
        apm_logger.warning("session refused: appium client missing url=%s", APPIUM_URL)
        return jsonify({
            "success": False,
            "error": "appium not available",
            "url": APPIUM_URL,
        }), 501
    return jsonify({"success": False, "error": "appium session not wired", "url": APPIUM_URL}), 501


@app.route("/vnc/status", methods=["GET"])
def vnc_status():
    status = viewport.snapshot()
    vnc_logger.info(
        "viewport %sx%s :%s clients=%s frames=%s runtime=%s",
        status["width"], status["height"], status["port"],
        status["clients"], status["frames"], status["runtime"],
    )
    return jsonify(status)


@app.route("/vnc/attach", methods=["POST"])
@require_auth
def vnc_attach():
    return jsonify(viewport.attach(vnc_logger, size=_screen_size())), 201


@app.route("/vnc/detach", methods=["POST"])
@require_auth
def vnc_detach():
    clients = viewport.detach(vnc_logger)
    return jsonify({"success": True, "clients": clients})


@app.route("/logs", methods=["GET"])
def get_logs():
    """Recent labeled lines (ADB commanders, Appium, VNC viewports, …)."""
    log_type = request.args.get("type")
    try:
        n = int(request.args.get("n", "200"))
    except ValueError:
        n = 200
    items = recent_logs(log_type=log_type, n=n)
    return jsonify({"count": len(items), "logs": items})


@app.route("/device/ui", methods=["GET", "POST"])
@require_auth
def device_ui():
    """Labeled elements with tap centres, so procedures address by label.

    Text typed into inputs is redacted (`value_redacted`, `text_len`) unless
    the caller passes `include_values=1` (query, or JSON body on POST), e.g.
    to read a form back after filling it. Passwords are never returned.
    """
    body = request.get_json(silent=True) if request.method == "POST" else None
    include = _truthy(request.args.get("include_values")) or _truthy(
        (body or {}).get("include_values") if isinstance(body, dict) else None)
    result = _read_ui_tree(include_values=include)
    return jsonify(result), (200 if result.get("success") else 500)

@app.route("/device/focus", methods=["GET"])
@require_auth
def device_focus():
    """Focused window/activity, read without a pipe inside `adb shell`."""
    focus = current_focus()
    return jsonify({"success": bool(focus), "focus": focus})

@app.route("/device/screenshot", methods=["GET"])
@require_auth
def screenshot():
    """Take screenshot and return as PNG"""
    ensure_adb_connected()
    result = subprocess.run(
        ["adb", "-s", current_adb_connect(), "exec-out", "screencap", "-p"],
        capture_output=True, timeout=30
    )
    if result.returncode == 0 and result.stdout:
        return Response(result.stdout, mimetype="image/png")
    return jsonify({"error": "Failed to capture screenshot"}), 500

@app.route("/device/screenshot/base64", methods=["GET"])
@require_auth
def screenshot_base64():
    """Take screenshot and return as base64 JSON"""
    result = _do_screenshot_base64()
    status = 200 if result.get("success") else 500
    return jsonify(result), status

# =============================================================================
# App Management Endpoints
# =============================================================================

_PM_SCOPES = {"third_party": "-3", "system": "-s", "all": ""}


@app.route("/apps", methods=["GET"])
@require_auth
def list_apps():
    """List installed packages. `?scope=third_party` (default), `system` or `all`."""
    scope = request.args.get("scope", "third_party")
    if scope not in _PM_SCOPES:
        return jsonify({
            "success": False, "code": "invalid_scope",
            "error": f"scope must be one of {sorted(_PM_SCOPES)}",
        }), 400
    flag = _PM_SCOPES[scope]
    success, out, err = run_adb_shell(f"pm list packages {flag}".strip())
    packages = app_install.parse_packages(out) if success else []
    body = {"success": success, "scope": scope, "packages": packages, "count": len(packages)}
    if not success:
        body["error"] = err or out or "pm list packages failed"
        return jsonify(body), 502
    return jsonify(body)

def _resolve_launch_activity(package):
    """Launcher component for `package`, or None when it has none.

    `cmd package resolve-activity` exits 0 and prints "No activity found" for a
    package that is absent or has no launcher entry, so the output has to be
    matched against the package name rather than trusted.
    """
    success, out, _ = run_adb_shell(f"cmd package resolve-activity --brief {package}")
    if not success:
        return None
    for line in reversed((out or "").splitlines()):
        candidate = line.strip()
        if candidate.startswith(f"{package}/"):
            return candidate
    return None


@app.route("/apps/<package>/start", methods=["POST"])
@require_auth
def start_app(package):
    """Start an app by package name.

    Reports failure when the package has no launchable activity — launching a
    Play Store that GApps never installed used to answer `success: true` with
    `"activity": "No activity found"`, which is what hid the missing GMS.
    """
    activity = _resolve_launch_activity(package)
    if activity:
        success, out, err = run_adb_shell(f"am start -n {activity}")
        combined = f"{out}\n{err}"
        if success and "Error" not in combined and "Exception" not in combined:
            return jsonify({"success": True, "activity": activity})
        return jsonify({
            "success": False,
            "activity": activity,
            "error": (err or out or "am start failed").strip(),
        }), 502

    success, out, err = run_adb_shell(
        f"monkey -p {package} -c android.intent.category.LAUNCHER 1"
    )
    combined = f"{out}\n{err}"
    if success and "No activities found" not in combined and "Error" not in combined:
        return jsonify({"success": True, "package": package, "via": "monkey"})
    return jsonify({
        "success": False,
        "package": package,
        "code": "not_launchable",
        "error": f"no launchable activity for {package} (not installed?)",
    }), 404

@app.route("/apps/<package>/stop", methods=["POST"])
@require_auth
def stop_app(package):
    """Force stop an app"""
    success, out, err = run_adb_shell(f"am force-stop {package}")
    return jsonify({"success": success, "message": out or err})

@app.route("/apps/<package>/uninstall", methods=["DELETE"])
@require_auth
def uninstall_app(package):
    """Uninstall an app"""
    success, out, err = run_adb("uninstall", package)
    return jsonify({"success": success, "message": out or err})

@app.route("/apps/<package>/clear", methods=["POST"])
@require_auth
def clear_app_data(package):
    """Clear app data"""
    success, out, err = run_adb_shell(f"pm clear {package}")
    return jsonify({"success": success, "message": out or err})


# =============================================================================
# App install: APK / split set / bundle by host path or URL+sha256, and Play
# by package name once the owner has signed in. docs/GAPPS-AND-APP-INSTALL.md
# =============================================================================

app_logger = logger.bind("ADB")
_FAILURE_RE = re.compile(r"Failure \[([A-Z0-9_]+)")
# Patched by tests so polling loops run instantly.
_sleep = time.sleep
_now = time.time


def _package_installed(package):
    ok, out, _ = run_adb_shell(f"pm path {package}")
    return bool(ok and "package:" in (out or ""))


def _installed_packages():
    ok, out, _ = run_adb_shell("pm list packages")
    return set(app_install.parse_packages(out)) if ok else None


def _install_apks(apks, options, expected_package=None):
    """Run `adb install` / `install-multiple` and report what changed."""
    before = _installed_packages()
    args = app_install.adb_install_args(apks, options)
    success, out, err = run_adb(*args, timeout=APP_INSTALL_TIMEOUT)
    message = "\n".join(x for x in (out, err) if x).strip()
    failure = _FAILURE_RE.search(message)
    # Older adb exits 0 and prints "Failure [INSTALL_FAILED_...]".
    success = bool(success and not failure and "Success" in message) or bool(
        success and not failure and not message
    )
    result = {
        "success": success,
        "files": [os.path.basename(a) for a in apks],
        "method": args[0],
        "message": truncate(message),
    }
    if failure:
        result["install_error"] = failure.group(1)
    if success:
        after = _installed_packages()
        if before is not None and after is not None:
            result["new_packages"] = sorted(after - before)
        if expected_package:
            result["package"] = expected_package
            result["verified"] = _package_installed(expected_package)
            if not result["verified"]:
                result["success"] = False
                result["code"] = "package_not_found_after_install"
                result["error"] = f"install reported success but {expected_package} is not installed"
    else:
        result["code"] = "install_failed"
        result["error"] = result.get("install_error") or "adb install failed"
    return result


def _describe_source(src):
    if "url" in src:
        return app_install.safe_url_for_log(src["url"])
    return os.path.basename(str(src.get("path", "")))


@app.route("/apps/install", methods=["POST"])
@require_auth
def install_app():
    """Install an APK, a split set, or an .apks/.xapk bundle.

    Body: exactly one of `path`, `paths`, `url` (+ `sha256`, required) or
    `urls` (list of {url, sha256}); optional `replace` (default true),
    `grant_permissions`, `downgrade`, `expected_package`.
    """
    try:
        req = app_install.parse_install_request(request.get_json(silent=True))
    except app_install.InstallError as exc:
        app_logger.warning("install refused code=%s", exc.code)
        return jsonify(exc.as_dict()), exc.status

    described = [_describe_source(s) for s in req["sources"]]
    app_logger.info("install requested kind=%s sources=%s", req["kind"], described)
    workdir = app_install.new_temp_dir()
    try:
        files = []
        for index, src in enumerate(req["sources"]):
            if "path" in src:
                real = app_install.resolve_host_path(src["path"], app_install.install_dirs())
                if src.get("sha256") and app_install.sha256_file(real) != src["sha256"]:
                    raise app_install.InstallError(
                        "checksum_mismatch",
                        "sha256 of the file does not match; nothing was installed",
                        status=422, path=os.path.basename(real),
                    )
                files.append(real)
            else:
                dest = os.path.join(workdir, f"download-{index}.apk")
                app_install.download(
                    src["url"], dest, src["sha256"],
                    max_bytes=APP_INSTALL_MAX_BYTES,
                    allow_private=APP_INSTALL_ALLOW_PRIVATE,
                )
                files.append(dest)

        apks, obb = [], []
        for path in files:
            if app_install.classify_archive(path) == "apk":
                apks.append(path)
                continue
            if len(files) > 1:
                raise app_install.InstallError(
                    "invalid_body", "install a bundle (.apks/.xapk/.apkm) on its own, not with other files"
                )
            extracted, obb = app_install.extract_bundle(path, workdir)
            apks.extend(extracted)

        ensure_adb_connected()
        result = _install_apks(apks, req["options"], req["expected_package"])
        result["sources"] = described
        if obb:
            result["obb_not_installed"] = obb
        app_logger.info(
            "install %s method=%s files=%d new=%s",
            "ok" if result["success"] else "failed",
            result["method"], len(apks), result.get("new_packages"),
        )
        return jsonify(result), (200 if result["success"] else 502)
    except app_install.InstallError as exc:
        app_logger.warning("install refused code=%s sources=%s", exc.code, described)
        return jsonify(exc.as_dict()), exc.status
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@app.route("/apps/<package>", methods=["GET"])
@require_auth
def app_info(package):
    """Whether `package` is installed, with its version."""
    if not app_install.is_valid_package(package):
        return jsonify({"success": False, "code": "invalid_package",
                        "error": "not a valid Android package name"}), 400
    if not _package_installed(package):
        return jsonify({"success": True, "package": package, "installed": False}), 404
    ok, out, _ = run_adb_shell(f"dumpsys package {package}", timeout=30)
    info = app_install.parse_package_info(out if ok else "", package)
    info.update({"success": True, "installed": True})
    return jsonify(info)


def _google_account_present():
    ok, out, _ = run_adb_shell("dumpsys account")
    return bool(ok and app_install.has_google_account(out))


def _gsf_android_id():
    """GSF Android ID for Google's uncertified-device form, or None.

    Read from gservices.db as root. Redroid's adbd runs as root; `su 0` is a
    fallback for images where it does not. Needs sqlite3 in the image.
    """
    query = "select value from main where name='android_id';"
    db = "/data/data/com.google.android.gsf/databases/gservices.db"
    for prefix in ("", "su 0 "):
        ok, out, _ = run_adb_shell(f'{prefix}sqlite3 {db} "{query}"')
        value = (out or "").strip()
        if ok and value.isdigit():
            return value
    return None


@app.route("/apps/play/status", methods=["GET"])
@require_auth
def play_status():
    """What stands between this phone and installing from Play.

    Reports presence only: whether a Google account exists, never its name.
    """
    ensure_adb_connected()
    gapps = _gapps_status()
    signed_in = _google_account_present() if gapps.get("ready") else False
    gsf_id = _gsf_android_id() if gapps.get("gsf") else None
    ready = bool(gapps.get("ready") and signed_in)
    body = {
        "success": True,
        "gapps": gapps,
        "google_account": signed_in,
        "gsf_android_id": gsf_id,
        "uncertified_registration_url": PLAY_UNCERTIFIED_URL,
        "ready": ready,
    }
    if not ready:
        body["owner_action"] = PLAY_OWNER_ACTION
    return jsonify(body)


def _play_install(payload):
    """Drive the Play Store details page to install `package`.

    Never buys: a price on the button stops the run. Never signs in: a sign-in
    prompt stops the run and names the owner action.
    """
    package = payload.get("package", "")
    if not app_install.is_valid_package(package):
        return {"success": False, "code": "invalid_package", "error": "invalid package"}
    timeout = int(payload.get("timeout") or PLAY_INSTALL_TIMEOUT)
    started = _now()
    if _package_installed(package):
        return {"success": True, "package": package, "already_installed": True, "via": "play_store"}

    uri = f"market://details?id={package}"
    ok, out, err = run_adb_shell(
        f"am start -a android.intent.action.VIEW -d '{uri}' com.android.vending"
    )
    if not ok or "Error" in f"{out}{err}":
        return {"success": False, "package": package, "code": "play_not_launchable",
                "error": (err or out or "am start failed").strip()}

    ui_deadline = started + min(90, timeout)
    tapped = False
    last_state = "unknown"
    while _now() < ui_deadline:
        tree = _read_ui_tree()
        screen = app_install.play_screen_state(tree.get("elements") if tree.get("success") else [])
        last_state = screen["state"]
        if last_state == "install":
            target = screen["target"]
            run_adb_shell(f"input tap {target['x']} {target['y']}")
            tapped = True
            app_logger.info("play install tapped package=%s", package)
            break
        if last_state == "installed" and _package_installed(package):
            return {"success": True, "package": package, "already_installed": True, "via": "play_store"}
        if last_state == "paid":
            return {"success": False, "package": package, "code": "paid_app",
                    "error": "this app has a price; installing from Play here never buys anything"}
        if last_state == "sign_in":
            return {"success": False, "package": package, "code": "play_sign_in_required",
                    "error": "Play is asking for a Google sign-in", "owner_action": PLAY_OWNER_ACTION}
        if last_state == "unavailable":
            return {"success": False, "package": package, "code": "not_available",
                    "error": "Play says this app is not available for this device or account"}
        _sleep(2)

    if not tapped:
        return {"success": False, "package": package, "code": "install_button_not_found",
                "error": f"no Install button on the Play page (last screen: {last_state})"}

    while _now() < started + timeout:
        if _package_installed(package):
            return {"success": True, "package": package, "via": "play_store",
                    "elapsed_s": int(_now() - started)}
        _sleep(3)
    return {"success": False, "package": package, "code": "install_timeout",
            "error": f"{package} was not installed within {timeout}s (still downloading?)"}


@app.route("/apps/play/install", methods=["POST"])
@require_auth
def play_install():
    """Install from Google Play by package name. Requires the owner's sign-in.

    Body: {"package": "com.example.app", "wait"?: false, "timeout"?: seconds}.
    Answers 409 with `owner_action` until GApps are present and a Google
    account is signed in on the phone; otherwise queues a job (202) or, with
    `wait: true`, runs it in the request.
    """
    data = request.get_json(silent=True) or {}
    package = data.get("package")
    if not app_install.is_valid_package(package):
        return jsonify({"success": False, "code": "invalid_package",
                        "error": f"{package!r} is not a valid Android package name"}), 400
    timeout = data.get("timeout", PLAY_INSTALL_TIMEOUT)
    if not isinstance(timeout, int) or not 10 <= timeout <= 3600:
        return jsonify({"success": False, "code": "invalid_timeout",
                        "error": "timeout must be an integer between 10 and 3600 seconds"}), 400

    ensure_adb_connected()
    gapps = _gapps_status()
    if not gapps.get("ready"):
        return jsonify({"success": False, "code": "gapps_missing", "gapps": gapps,
                        "error": "Play Store / Play services are not installed on this phone",
                        "owner_action": "Run a GApps image (scripts/build-gapps-image.sh) first."}), 409
    if not _google_account_present():
        return jsonify({"success": False, "code": "play_sign_in_required",
                        "error": "no Google account is signed in on this phone",
                        "owner_action": PLAY_OWNER_ACTION}), 409

    payload = {"package": package, "timeout": timeout}
    app_logger.info("play install requested package=%s wait=%s", package, bool(data.get("wait")))
    if data.get("wait") is True:
        result = _play_install(payload)
        return jsonify(result), (200 if result.get("success") else 502)
    job_id = _start_job("play_install", payload)
    return jsonify({"job_id": job_id, "status": "queued", "type": "play_install",
                    "package": package}), 202

# =============================================================================
# Settings Endpoints
# =============================================================================

@app.route("/settings/<namespace>/<key>", methods=["GET"])
@require_auth
def get_setting(namespace, key):
    """Get Android setting (namespace: system, secure, global)"""
    if namespace not in ["system", "secure", "global"]:
        return jsonify({"error": "Invalid namespace"}), 400
    
    success, value, _ = run_adb_shell(f"settings get {namespace} {key}")
    return jsonify({"namespace": namespace, "key": key, "value": value})

@app.route("/settings/<namespace>/<key>", methods=["PUT"])
@require_auth
def set_setting(namespace, key):
    """Set Android setting"""
    if namespace not in ["system", "secure", "global"]:
        return jsonify({"error": "Invalid namespace"}), 400
    
    data = request.get_json() or {}
    value = data.get("value", "")
    
    success, out, err = run_adb_shell(f"settings put {namespace} {key} {value}")
    return jsonify({"success": success, "message": out or err})

# =============================================================================
# Configuration Endpoints
# =============================================================================

@app.route("/config", methods=["GET"])
@require_auth
def get_config():
    """Get current configuration"""
    config = load_config()
    config["runtime_state"] = _state
    return jsonify(config)

@app.route("/config", methods=["POST"])
@require_auth
def update_config():
    """Update configuration"""
    data = request.get_json() or {}
    config = load_config()
    config.update(data)
    save_config(config)
    
    # Apply relevant settings
    if "proxy" in data:
        # Trigger proxy update
        pass
    
    if "location" in data:
        # Trigger location update
        pass
    
    return jsonify({"success": True, "config": config})

# =============================================================================
# Device Spoofing / Anti-Detection Endpoints
# =============================================================================

# Device profiles directory
PROFILES_DIR = os.environ.get("PROFILES_DIR", "/opt/cloud-phone/config/device-profiles")
ANTIDETECT_SCRIPT = "/opt/cloud-phone-scripts/anti-detection.sh"

# In-memory device state
_device_state = {
    "profile": None,
    "hardware_ids": {},
    "antidetect_enabled": False
}

def generate_imei():
    """Generate valid IMEI with Luhn checksum"""
    import random
    tac = f"35{random.randint(1000, 9999)}0"
    snr = f"{random.randint(0, 999999):06d}"
    imei_base = tac + snr
    
    # Luhn checksum
    total = 0
    for i, digit in enumerate(imei_base):
        d = int(digit)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    check = (10 - (total % 10)) % 10
    return imei_base + str(check)

def generate_serial():
    """Generate realistic serial number"""
    import random
    import string
    chars = string.digits + string.ascii_uppercase.replace('I', '').replace('O', '')
    return ''.join(random.choice(chars) for _ in range(11))

def generate_mac():
    """Generate random MAC address"""
    import random
    vendors = ["00:1A:2B", "00:1E:C9", "00:26:BB", "D8:FC:93", "F4:F5:D8"]
    prefix = random.choice(vendors)
    suffix = ":".join(f"{random.randint(0, 255):02X}" for _ in range(3))
    return f"{prefix}:{suffix}"

def generate_android_id():
    """Generate 16-char hex Android ID"""
    import random
    return ''.join(random.choice('0123456789abcdef') for _ in range(16))

@app.route("/device/identity", methods=["GET"])
@require_auth
def get_device_identity():
    """Get current device identity/spoofing status"""
    ensure_adb_connected()
    
    # Get current values
    _, model, _ = run_adb_shell("getprop ro.product.model")
    _, brand, _ = run_adb_shell("getprop ro.product.brand")
    _, fingerprint, _ = run_adb_shell("getprop ro.build.fingerprint")
    _, android_id, _ = run_adb_shell("settings get secure android_id")
    _, serial, _ = run_adb_shell("getprop ro.serialno")
    _, debuggable, _ = run_adb_shell("getprop ro.debuggable")
    _, qemu, _ = run_adb_shell("getprop ro.kernel.qemu")
    
    return jsonify({
        "current": {
            "model": model,
            "brand": brand,
            "fingerprint": fingerprint,
            "android_id": android_id,
            "serial": serial,
            "debuggable": debuggable,
            "qemu_detected": qemu == "1"
        },
        "spoofing": _device_state
    })

@app.route("/device/identity", methods=["POST"])
@require_auth
def set_device_identity():
    """
    Set device identity (spoof device properties)
    
    Body:
    {
        "profile": "samsung-galaxy-s21",  // or "google-pixel-6", "oneplus-9-pro", "random"
        "generate_ids": true,  // Generate new hardware IDs
        "custom": {  // Optional custom properties
            "model": "SM-G991B",
            "brand": "samsung",
            "android_id": "abc123...",
            "serial": "R5CW12345"
        }
    }
    """
    data = request.get_json() or {}
    profile = data.get("profile", "samsung-galaxy-s21")
    generate_ids = data.get("generate_ids", True)
    custom = data.get("custom", {})
    
    ensure_adb_connected()
    
    # Generate hardware IDs if requested
    if generate_ids:
        _device_state["hardware_ids"] = {
            "imei": generate_imei(),
            "imei2": generate_imei(),
            "serial": generate_serial(),
            "mac_wifi": generate_mac(),
            "mac_bt": generate_mac(),
            "android_id": generate_android_id()
        }
    
    # Apply custom values if provided
    if custom:
        _device_state["hardware_ids"].update(custom)
    
    # Load and apply profile
    profile_file = None
    profiles = {
        "samsung-galaxy-s21": "samsung-galaxy-s21.prop",
        "google-pixel-6": "google-pixel-6.prop",
        "oneplus-9-pro": "oneplus-9-pro.prop"
    }
    
    if profile == "random":
        import random
        profile = random.choice(list(profiles.keys()))
    
    if profile in profiles:
        profile_file = os.path.join(PROFILES_DIR, profiles[profile])
    
    # Apply profile properties
    applied_props = []
    if profile_file and os.path.exists(profile_file):
        with open(profile_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    key, value = line.split('=', 1)
                    run_adb_shell(f"setprop {key} '{value}'")
                    applied_props.append(key)
    
    # Apply hardware IDs
    ids = _device_state["hardware_ids"]
    if "android_id" in ids:
        run_adb_shell(f"settings put secure android_id '{ids['android_id']}'")
    if "serial" in ids:
        run_adb_shell(f"setprop ro.serialno '{ids['serial']}'")
        run_adb_shell(f"setprop ro.boot.serialno '{ids['serial']}'")
    if "imei" in ids:
        run_adb_shell(f"setprop gsm.sim.imei '{ids['imei']}'")
    
    _device_state["profile"] = profile
    _device_state["antidetect_enabled"] = True
    
    return jsonify({
        "success": True,
        "profile": profile,
        "hardware_ids": _device_state["hardware_ids"],
        "properties_applied": len(applied_props)
    })

@app.route("/device/identity/profiles", methods=["GET"])
@require_auth
def list_profiles():
    """List available device profiles"""
    profiles = []
    if os.path.exists(PROFILES_DIR):
        for f in os.listdir(PROFILES_DIR):
            if f.endswith('.prop'):
                profiles.append(f.replace('.prop', ''))
    
    return jsonify({
        "profiles": profiles,
        "current": _device_state.get("profile")
    })

@app.route("/device/identity/generate", methods=["POST"])
@require_auth
def generate_new_ids():
    """Generate new random hardware identifiers"""
    _device_state["hardware_ids"] = {
        "imei": generate_imei(),
        "imei2": generate_imei(),
        "serial": generate_serial(),
        "mac_wifi": generate_mac(),
        "mac_bt": generate_mac(),
        "android_id": generate_android_id(),
        "advertising_id": str(__import__('uuid').uuid4())
    }
    
    return jsonify({
        "success": True,
        "hardware_ids": _device_state["hardware_ids"]
    })

@app.route("/device/antidetect", methods=["POST"])
@require_auth
def apply_antidetect():
    """
    Apply full anti-detection configuration
    
    Body:
    {
        "profile": "samsung-galaxy-s21",
        "hide_root": true,
        "hide_emulator": true,
        "spoof_battery": true
    }
    """
    data = request.get_json() or {}
    profile = data.get("profile", "samsung-galaxy-s21")
    hide_root = data.get("hide_root", True)
    hide_emulator = data.get("hide_emulator", True)
    spoof_battery = data.get("spoof_battery", True)
    
    ensure_adb_connected()
    results = []
    
    # Apply device profile
    if os.path.exists(ANTIDETECT_SCRIPT):
        result = subprocess.run(
            [ANTIDETECT_SCRIPT, "apply", profile],
            capture_output=True, text=True, timeout=60
        )
        results.append({"action": "profile", "success": result.returncode == 0})
    else:
        # Manual application
        # Set profile
        set_result = set_device_identity()
        results.append({"action": "profile", "success": True})
    
    # Hide emulator artifacts
    if hide_emulator:
        emulator_props = [
            ("ro.kernel.qemu", "0"),
            ("ro.hardware.virtual_device", "0"),
            ("ro.boot.qemu", "0"),
            ("qemu.hw.mainkeys", "0")
        ]
        for key, value in emulator_props:
            run_adb_shell(f"setprop {key} '{value}'")
        results.append({"action": "hide_emulator", "success": True})
    
    # Hide root/debug
    if hide_root:
        run_adb_shell("setprop ro.debuggable 0")
        run_adb_shell("setprop ro.secure 1")
        run_adb_shell("setprop ro.boot.verifiedbootstate green")
        run_adb_shell("setprop ro.boot.flash.locked 1")
        results.append({"action": "hide_root", "success": True})
    
    # Spoof battery
    if spoof_battery:
        import random
        level = random.randint(45, 85)
        run_adb_shell(f"dumpsys battery set level {level}")
        run_adb_shell("dumpsys battery set status 3")  # Not charging
        run_adb_shell("dumpsys battery set ac 0")
        run_adb_shell("dumpsys battery set usb 0")
        results.append({"action": "spoof_battery", "success": True, "level": level})
    
    _device_state["antidetect_enabled"] = True
    
    return jsonify({
        "success": True,
        "profile": profile,
        "results": results
    })

@app.route("/device/antidetect/status", methods=["GET"])
@require_auth
def antidetect_status():
    """Check anti-detection status"""
    ensure_adb_connected()
    
    checks = {}
    
    # Check debuggable
    _, val, _ = run_adb_shell("getprop ro.debuggable")
    checks["debuggable"] = val != "1"
    
    # Check qemu
    _, val, _ = run_adb_shell("getprop ro.kernel.qemu")
    checks["qemu_hidden"] = val != "1"
    
    # Check boot state
    _, val, _ = run_adb_shell("getprop ro.boot.verifiedbootstate")
    checks["boot_state_green"] = val == "green"
    
    # Check build type
    _, val, _ = run_adb_shell("getprop ro.build.type")
    checks["user_build"] = val == "user"
    
    # Overall score
    passed = sum(1 for v in checks.values() if v)
    total = len(checks)
    
    return jsonify({
        "enabled": _device_state.get("antidetect_enabled", False),
        "profile": _device_state.get("profile"),
        "checks": checks,
        "score": f"{passed}/{total}",
        "detection_risk": "low" if passed == total else "medium" if passed >= total/2 else "high"
    })

@app.route("/device/antidetect/reset", methods=["POST"])
@require_auth  
def reset_antidetect():
    """Reset to default (detectable) state"""
    ensure_adb_connected()
    
    # Reset battery
    run_adb_shell("dumpsys battery reset")
    
    # Re-enable debug
    run_adb_shell("setprop ro.debuggable 1")
    
    _device_state["antidetect_enabled"] = False
    _device_state["profile"] = None
    
    return jsonify({
        "success": True,
        "message": "Anti-detection reset. Restart device runtime for full reset."
    })

# =============================================================================
# Job Queue Endpoints
# =============================================================================

def _start_job(job_type, payload):
    _prune_jobs()
    job_id = uuid.uuid4().hex
    now = time.time()
    job = {
        "id": job_id,
        "type": job_type,
        "status": "queued",
        "created_at": now,
        "updated_at": now,
        "result": None,
        "error": None
    }
    with _jobs_lock:
        _jobs[job_id] = job

    thread = threading.Thread(
        target=_run_job, args=(job_id, job_type, payload, current_adb_connect()), daemon=True
    )
    thread.start()
    return job_id


@app.route("/jobs", methods=["POST"])
@require_auth
def create_job():
    """
    Create an async job and poll for result.

    Body:
    {
        "type": "adb_shell" | "device_input" | "screen" | "screenshot"
                | "app_start" | "app_stop" | "app_clear" | "app_uninstall"
                | "play_install",
        "payload": { ... }
    }
    """
    data = request.get_json() or {}
    job_type = data.get("type", "")
    payload = data.get("payload", {})
    if not job_type:
        return jsonify({"error": "type required"}), 400

    job_id = _start_job(job_type, payload)
    return jsonify({
        "job_id": job_id,
        "status": "queued",
        "type": job_type
    }), 202


@app.route("/jobs/<job_id>", methods=["GET"])
@require_auth
def get_job(job_id):
    _prune_jobs()
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    return jsonify(job)

# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    host = os.environ.get("API_HOST", "127.0.0.1")
    port = int(os.environ.get("API_PORT", "8080"))
    debug = os.environ.get("API_DEBUG", "false").lower() == "true"
    
    print(f"Starting Cloud Phone Control API on {host}:{port}")
    print(f"ADB target: {ADB_CONNECT}")
    print(f"UI backend: {UI_BACKEND}  Appium: {APPIUM_URL}  VNC: :{viewport.port()}")
    logger.info(
        "listen %s:%s adb=%s ui_backend=%s appium=%s vnc=:%s verbose=%s",
        host, port, ADB_CONNECT, UI_BACKEND, APPIUM_URL, viewport.port(), verbose_enabled(),
    )
    
    app.run(host=host, port=port, debug=debug, threaded=True)
