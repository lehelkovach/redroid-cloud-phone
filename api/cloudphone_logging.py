"""Labeled logging shared by the Control API, orchestrator, and shell scripts.

Every line carries the subsystem that produced it:

    2026-09-06 13:30:12.430 [CMD] [INFO ] commandlet tap backend=adb input tap 640 360
    2026-09-06 13:30:12.431 [APM] [INFO ] session create requested url=http://127.0.0.1:4723
    2026-09-06 13:30:12.432 [VNC] [INFO ] viewport 1280x720 :5900 clients=0

`LOG_FORMAT=json` emits one JSON object per line. Labels stay in sync with
`scripts/lib/log.sh`; `tests/test_logging.py` fails if the two drift.

`CLOUD_PHONE_VERBOSE=1` promotes the process to DEBUG so ADB argv, Appium W3C
payloads, and VNC frame ticks all show up.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from collections import deque

# Subsystem labels. Three letters so text logs stay column-aligned.
TYPES = {
    "SYS": "system / CLI",
    "API": "control API",
    "ORC": "orchestrator",
    "ADB": "adb commanders",
    "CMD": "UI commandlets (tap/swipe/text/key)",
    "APM": "Appium / W3C actions",
    "VNC": "VNC / RFB viewports",
    "RDR": "redroid container",
    "CVD": "cuttlefish launch",
    "GAP": "gapps install",
    "NGX": "nginx-rtmp",
    "FFM": "ffmpeg bridge",
    "DKR": "docker",
    "LCT": "android logcat",
    "TST": "test harness",
}

DEFAULT_TYPE = "SYS"
_TS_FORMAT = "%Y-%m-%d %H:%M:%S"
_SECRET_KEYS = {
    "password", "passwd", "token", "api_token", "auth_token",
    "authorization", "secret", "api_key",
}

_RING = deque(maxlen=int(os.environ.get("LOG_RING_SIZE", "4000")))
_RING_LOCK = threading.Lock()


def normalize_type(value):
    """Unknown labels degrade to SYS rather than corrupting the column."""
    label = str(value or "").strip().upper()
    return label if label in TYPES else DEFAULT_TYPE


def verbose_enabled():
    flag = os.environ.get("CLOUD_PHONE_VERBOSE", "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return True
    return (os.environ.get("LOG_LEVEL") or "").strip().upper() == "DEBUG"


def resolved_level(level=None):
    if level:
        return str(level).strip().upper()
    if os.environ.get("CLOUD_PHONE_VERBOSE", "").strip().lower() in {"1", "true", "yes", "on"}:
        return "DEBUG"
    return (os.environ.get("LOG_LEVEL", "INFO")).strip().upper()


def _timestamp(created):
    return f"{time.strftime(_TS_FORMAT, time.localtime(created))}.{int(created % 1 * 1000):03d}"


def redact(value):
    """Drop secrets from verbose log payloads. Never print passwords or tokens."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in _SECRET_KEYS or "password" in lowered:
                out[key] = "***"
            else:
                out[key] = redact(item)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


def truncate(text, limit=240):
    text = "" if text is None else str(text).replace("\n", "\\n")
    if len(text) <= limit:
        return text
    return text[:limit] + f"...({len(text)} chars)"


class LabeledFormatter(logging.Formatter):
    """TIMESTAMP [TYPE] [LEVEL] MESSAGE"""

    def __init__(self, default_type=DEFAULT_TYPE):
        super().__init__()
        self.default_type = normalize_type(default_type)

    def record_type(self, record):
        return normalize_type(getattr(record, "log_type", self.default_type))

    def format(self, record):
        line = "{ts} [{type}] [{level:<5}] {msg}".format(
            ts=_timestamp(record.created),
            type=self.record_type(record),
            level=record.levelname,
            msg=record.getMessage(),
        )
        if record.exc_info:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        return line


class JsonFormatter(LabeledFormatter):
    """One JSON object per line, same fields as the text form."""

    def format(self, record):
        payload = {
            "ts": _timestamp(record.created),
            "type": self.record_type(record),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in getattr(record, "log_fields", {}).items():
            payload.setdefault(key, value)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class RingHandler(logging.Handler):
    """In-process ring so GET /logs and tests can read the same verbose stream."""

    def emit(self, record):
        item = {
            "ts": _timestamp(record.created),
            "type": normalize_type(getattr(record, "log_type", DEFAULT_TYPE)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        with _RING_LOCK:
            _RING.append(item)


def recent_logs(log_type=None, n=200):
    with _RING_LOCK:
        items = list(_RING)
    if log_type:
        wanted = {normalize_type(part) for part in str(log_type).split(",") if part.strip()}
        items = [item for item in items if item["type"] in wanted]
    return items[-int(n):]


def clear_logs():
    with _RING_LOCK:
        _RING.clear()


class TypeAdapter(logging.LoggerAdapter):
    """Stamps every call from one subsystem with its label."""

    def process(self, msg, kwargs):
        extra = dict(kwargs.get("extra") or {})
        extra.setdefault("log_type", self.extra.get("log_type", DEFAULT_TYPE))
        kwargs["extra"] = extra
        return msg, kwargs

    def bind(self, log_type):
        return TypeAdapter(self.logger, {"log_type": normalize_type(log_type)})


def build_formatter(log_format=None, default_type=DEFAULT_TYPE):
    fmt = (log_format or os.environ.get("LOG_FORMAT", "text")).strip().lower()
    if fmt == "json":
        return JsonFormatter(default_type)
    return LabeledFormatter(default_type)


def configure(name, log_type=DEFAULT_TYPE, level=None, stream=None, log_file=None):
    """Configure and return a label-stamped logger for one subsystem."""
    log_type = normalize_type(log_type)
    level_name = resolved_level(level)
    resolved = getattr(logging, level_name, logging.INFO)

    logger = logging.getLogger(name)
    logger.setLevel(resolved)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    formatter = build_formatter(default_type=log_type)
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    ring = RingHandler()
    ring.setFormatter(formatter)
    logger.addHandler(ring)

    path = log_file or os.environ.get("LOG_FILE", "")
    if path:
        try:
            directory = os.path.dirname(path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            file_handler = logging.FileHandler(path)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except OSError as exc:
            logger.warning("cannot open LOG_FILE %s: %s", path, exc)

    return TypeAdapter(logger, {"log_type": log_type})
