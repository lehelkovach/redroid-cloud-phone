"""Labeled logging shared by the Control API, orchestrator, and shell scripts.

Every line carries the subsystem that produced it, so one unified log can be
filtered by origin instead of guessed at from message wording:

    2026-09-04 08:53:12.430 [ORC] [INFO] Acquired session owner=alice

`LOG_FORMAT=json` emits one JSON object per line for shipping.

Labels are shared with `scripts/lib/log.sh`; keep the two in sync.
"""

import json
import logging
import os
import sys
import time

# Subsystem labels. Fixed width so text logs stay column-aligned.
TYPES = {
    "SYS": "system / CLI",
    "API": "control API",
    "ORC": "orchestrator",
    "ADB": "adb commands",
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


def normalize_type(value):
    """Unknown labels degrade to SYS rather than corrupting the column."""
    label = str(value or "").strip().upper()
    return label if label in TYPES else DEFAULT_TYPE


def _timestamp(created):
    return f"{time.strftime(_TS_FORMAT, time.localtime(created))}.{int(created % 1 * 1000):03d}"


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
    level_name = (level or os.environ.get("LOG_LEVEL", "INFO")).strip().upper()
    resolved = getattr(logging, level_name, logging.INFO)

    logger = logging.getLogger(name)
    logger.setLevel(resolved)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(build_formatter(default_type=log_type))
    logger.addHandler(handler)

    path = log_file or os.environ.get("LOG_FILE", "")
    if path:
        try:
            directory = os.path.dirname(path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            file_handler = logging.FileHandler(path)
            file_handler.setFormatter(build_formatter(default_type=log_type))
            logger.addHandler(file_handler)
        except OSError as exc:
            # A missing log directory must not take the service down.
            logger.warning("cannot open LOG_FILE %s: %s", path, exc)

    return TypeAdapter(logger, {"log_type": log_type})
