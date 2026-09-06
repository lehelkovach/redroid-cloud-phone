"""Deterministic Gemini stand-in: login-form boxes on the fake viewport.

Never calls a network. Live Gemini is a skipped R4 rung
(`GEMINI_API_KEY` + `CLOUD_PHONE_LIVE=1`).
"""

# 1x1 PNG (same bytes MockMobileEnv uses). Agents screenshot via ADB, not VNC.
TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

# Default fake Control viewport is 1280x720 (`api/viewport.py`).
LOGIN_BOXES = {
    "concept_name": "LoginForm",
    "elements": [
        {"role": "email", "bbox": [200, 180, 1080, 260], "label": "Email"},
        {"role": "password", "bbox": [200, 280, 1080, 360], "label": "Password"},
        {"role": "submit", "bbox": [440, 420, 840, 500], "label": "Sign in"},
    ],
}

LABELED_DUMP = {
    "success": True,
    "count": 3,
    "elements": [
        {
            "label": "Email",
            "resource_id": "com.example:id/email",
            "clickable": True,
            "password": False,
            "bounds": [200, 180, 1080, 260],
            "x": 640,
            "y": 220,
        },
        {
            "label": "Password",
            "resource_id": "com.example:id/password",
            "clickable": True,
            "password": True,
            "bounds": [200, 280, 1080, 360],
            "x": 640,
            "y": 320,
        },
        {
            "label": "Sign in",
            "resource_id": "com.example:id/submit",
            "clickable": True,
            "password": False,
            "bounds": [440, 420, 840, 500],
            "x": 640,
            "y": 460,
        },
    ],
}

EMPTY_DUMP = {"success": True, "count": 0, "elements": []}


class FakeVlm:
    """Callable matching detect_form_boxes(vlm=...). Records the screenshot it saw."""

    def __init__(self, payload=None):
        self.payload = payload if payload is not None else LOGIN_BOXES
        self.calls = []

    def __call__(self, image_b64, screen=None):
        self.calls.append({"image_b64": image_b64, "screen": screen})
        return self.payload
