"""VNC / RFB viewport metadata and verbose frame logs.

Cuttlefish's operator UI is WebRTC; the classic Android emulator / scrcpy-style
path is RFB on :5900. This module does not start a VNC server — it records the
viewport the Control API advertises (size, port, clients) so logs and tests can
prove a human or agent is looking at the same rectangle the commandlets tap.
"""

from __future__ import annotations

import os
import threading

_LOCK = threading.Lock()
_STATE = {
    "clients": 0,
    "frames": 0,
    "last_attach": None,
}


def default_size():
    width = int(os.environ.get("VNC_WIDTH", os.environ.get("REDROID_WIDTH", "1280")))
    height = int(os.environ.get("VNC_HEIGHT", os.environ.get("REDROID_HEIGHT", "720")))
    return width, height


def port():
    return int(os.environ.get("VNC_PORT", "5900"))


def fps():
    return int(os.environ.get("VNC_FPS", os.environ.get("REDROID_FPS", "30")))


def snapshot(runtime=None, size=None):
    width, height = size or default_size()
    with _LOCK:
        clients = _STATE["clients"]
        frames = _STATE["frames"]
    return {
        "protocol": "rfb",
        "port": port(),
        "width": width,
        "height": height,
        "fps": fps(),
        "clients": clients,
        "frames": frames,
        "runtime": runtime or os.environ.get("CLOUD_PHONE_RUNTIME", "unknown"),
    }


def attach(logger=None, runtime=None, size=None):
    with _LOCK:
        _STATE["clients"] += 1
        clients = _STATE["clients"]
    status = snapshot(runtime=runtime, size=size)
    status["clients"] = clients
    if logger is not None:
        logger.info(
            "viewport attach %sx%s :%s clients=%s fps=%s runtime=%s",
            status["width"], status["height"], status["port"],
            status["clients"], status["fps"], status["runtime"],
        )
    return status


def detach(logger=None):
    with _LOCK:
        _STATE["clients"] = max(0, _STATE["clients"] - 1)
        clients = _STATE["clients"]
    if logger is not None:
        logger.info("viewport detach clients=%s", clients)
    return clients


def frame(logger=None, nbytes=0, size=None):
    width, height = size or default_size()
    with _LOCK:
        _STATE["frames"] += 1
        seq = _STATE["frames"]
        clients = _STATE["clients"]
    if logger is not None:
        logger.debug(
            "viewport frame seq=%s %sx%s bytes=%s clients=%s",
            seq, width, height, nbytes, clients,
        )
    return seq


def reset():
    with _LOCK:
        _STATE["clients"] = 0
        _STATE["frames"] = 0
        _STATE["last_attach"] = None
