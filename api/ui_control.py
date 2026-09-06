#!/usr/bin/env python3
"""ADB | Appium UI commandlets for cloud phones.

The Control API exposes a small, stable set of UI commands (tap, swipe, text,
key, screenshot) that map onto either **ADB** (`input ...`) or **Appium** (W3C
actions). Which backend is used is chosen per-instance (`ui_backend`, default
`adb`).

Coordinates accept pixels (`x`/`y`) or a percentage of the screen (`xp`/`yp`,
`"50%"`, or floats in 0–1).
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

VALID_BACKENDS = ("adb", "appium")

KEYCODES = {
    "back": 4, "home": 3, "menu": 82, "recents": 187, "app_switch": 187,
    "enter": 66, "tab": 61, "space": 62, "del": 67, "delete": 67,
    "escape": 111, "up": 19, "down": 20, "left": 21, "right": 22,
    "power": 26, "volume_up": 24, "volume_down": 25, "search": 84,
}


class UIError(Exception):
    """Raised for invalid UI commands or unavailable backends."""


def select_backend(requested: Optional[str], appium_available: bool = False) -> str:
    backend = (requested or "adb").lower()
    if backend not in VALID_BACKENDS:
        raise UIError(f"unsupported ui_backend: {backend} (valid: {', '.join(VALID_BACKENDS)})")
    if backend == "appium" and not appium_available:
        raise UIError(
            "appium backend selected but not available "
            "(set APPIUM_URL and install appium-python-client)"
        )
    return backend


def parse_wm_size(output: str) -> Tuple[int, int]:
    override = re.search(r"Override size:\s*(\d+)x(\d+)", output)
    physical = re.search(r"Physical size:\s*(\d+)x(\d+)", output)
    match = override or physical or re.search(r"(\d+)x(\d+)", output)
    if not match:
        raise UIError(f"could not parse screen size from: {output!r}")
    return int(match.group(1)), int(match.group(2))


def to_pixels(value, dimension: int) -> int:
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("%"):
            return round(float(text[:-1]) / 100.0 * dimension)
        value = float(text) if "." in text else int(text)
    if isinstance(value, float):
        if 0.0 <= value <= 1.0:
            return round(value * dimension)
        return round(value)
    if isinstance(value, int):
        return value
    raise UIError(f"invalid coordinate: {value!r}")


def resolve_xy(cmd: Dict, size: Tuple[int, int], prefix: str = "") -> Tuple[int, int]:
    width, height = size
    xk, yk, xpk, ypk = f"{prefix}x", f"{prefix}y", f"{prefix}xp", f"{prefix}yp"
    if xpk in cmd or ypk in cmd:
        if xpk not in cmd or ypk not in cmd:
            raise UIError(f"both {xpk} and {ypk} are required for percent coordinates")
        return to_pixels(f"{cmd[xpk]}%", width), to_pixels(f"{cmd[ypk]}%", height)
    if xk in cmd or yk in cmd:
        if xk not in cmd or yk not in cmd:
            raise UIError(f"both {xk} and {yk} are required")
        return to_pixels(cmd[xk], width), to_pixels(cmd[yk], height)
    raise UIError(f"missing coordinates ({xk}/{yk} or {xpk}/{ypk})")


def _escape_text(text: str) -> str:
    return text.replace(" ", "%s").replace("'", "\\'")


def resolve_keycode(value) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        if value.isdigit():
            return int(value)
        key = value.lower()
        if key in KEYCODES:
            return KEYCODES[key]
    raise UIError(f"unknown key: {value!r}")


def _resolve_named(cmd, size, xk, yk, xpk, ypk):
    width, height = size
    if xpk in cmd or ypk in cmd:
        if xpk not in cmd or ypk not in cmd:
            raise UIError(f"both {xpk} and {ypk} required")
        return to_pixels(f"{cmd[xpk]}%", width), to_pixels(f"{cmd[ypk]}%", height)
    if xk not in cmd or yk not in cmd:
        raise UIError(f"both {xk} and {yk} required (or {xpk}/{ypk})")
    return to_pixels(cmd[xk], width), to_pixels(cmd[yk], height)


def build_adb_input(cmd: Dict, size: Tuple[int, int]) -> List[str]:
    """Translate a UI command into one or more `adb shell` argument strings."""
    action = (cmd.get("action") or cmd.get("type") or "").lower()
    if action == "tap":
        x, y = resolve_xy(cmd, size)
        return [f"input tap {x} {y}"]
    if action == "long_press":
        x, y = resolve_xy(cmd, size)
        duration = int(cmd.get("duration", 800))
        return [f"input swipe {x} {y} {x} {y} {duration}"]
    if action == "swipe":
        x1, y1 = _resolve_named(cmd, size, "x1", "y1", "x1p", "y1p")
        x2, y2 = _resolve_named(cmd, size, "x2", "y2", "x2p", "y2p")
        duration = int(cmd.get("duration", 300))
        return [f"input swipe {x1} {y1} {x2} {y2} {duration}"]
    if action in ("text", "type"):
        return [f"input text '{_escape_text(cmd.get('text', ''))}'"]
    if action == "key":
        return [f"input keyevent {resolve_keycode(cmd.get('keycode', cmd.get('key', 4)))}"]
    raise UIError(f"unsupported UI action: {action!r}")


def _pointer_move(x, y, duration=0):
    return {"type": "pointerMove", "duration": duration, "x": x, "y": y}


def build_appium_actions(cmd: Dict, size: Tuple[int, int]) -> Dict:
    """W3C actions payload for the same commandlet, logged even when Appium is down."""
    action = (cmd.get("action") or cmd.get("type") or "").lower()
    finger = []
    if action == "tap":
        x, y = resolve_xy(cmd, size)
        finger = [_pointer_move(x, y), {"type": "pointerDown", "button": 0}, {"type": "pointerUp", "button": 0}]
    elif action == "long_press":
        x, y = resolve_xy(cmd, size)
        duration = int(cmd.get("duration", 800))
        finger = [
            _pointer_move(x, y),
            {"type": "pointerDown", "button": 0},
            {"type": "pause", "duration": duration},
            {"type": "pointerUp", "button": 0},
        ]
    elif action == "swipe":
        x1, y1 = _resolve_named(cmd, size, "x1", "y1", "x1p", "y1p")
        x2, y2 = _resolve_named(cmd, size, "x2", "y2", "x2p", "y2p")
        duration = int(cmd.get("duration", 300))
        finger = [
            _pointer_move(x1, y1),
            {"type": "pointerDown", "button": 0},
            _pointer_move(x2, y2, duration),
            {"type": "pointerUp", "button": 0},
        ]
    elif action in ("text", "type"):
        return {"actions": [{"type": "key", "id": "keyboard", "actions": [
            {"type": "keyDown", "value": cmd.get("text", "")},
            {"type": "keyUp", "value": cmd.get("text", "")},
        ]}]}
    elif action == "key":
        code = str(resolve_keycode(cmd.get("keycode", cmd.get("key", 4))))
        return {"actions": [{"type": "key", "id": "keyboard", "actions": [
            {"type": "keyDown", "value": code},
            {"type": "keyUp", "value": code},
        ]}]}
    else:
        raise UIError(f"unsupported UI action: {action!r}")
    return {"actions": [{"type": "pointer", "id": "finger", "parameters": {"pointerType": "touch"}, "actions": finger}]}
