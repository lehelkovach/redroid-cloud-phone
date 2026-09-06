"""Map orchestrator purpose to the guest Android runtime.

Automation / Play / mobile IO uses Redroid with GApps.
Streamed camera / mic ingest uses Cuttlefish with nginx-rtmp.

Do not bake Play into Cuttlefish, and do not attach virtual cameras to Redroid.
"""

PURPOSE_AUTOMATION = "automation"
PURPOSE_CAMERA = "camera"
RUNTIME_REDROID = "redroid"
RUNTIME_CUTTLEFISH = "cuttlefish"

AUTOMATION_ALIASES = {
    None,
    "",
    "automation",
    "play",
    "mobile",
    "phone",
    "gapps",
    "redroid",
    "default",
}
CAMERA_ALIASES = {
    "camera",
    "ingest",
    "stream",
    "rtmp",
    "webrtc",
    "cuttlefish",
    "video",
    "mic",
}


def _norm(value):
    if value is None:
        return None
    return str(value).strip().lower()


def resolve_purpose(purpose=None, runtime=None):
    """Return 'automation' or 'camera'. Runtime wins if both are set."""
    runtime_key = _norm(runtime)
    if runtime_key:
        if runtime_key in {RUNTIME_REDROID, "mock-redroid"}:
            return PURPOSE_AUTOMATION
        if runtime_key in {RUNTIME_CUTTLEFISH, "oci", "mock-cuttlefish"}:
            return PURPOSE_CAMERA
        raise ValueError(f"Unsupported runtime: {runtime}")

    purpose_key = _norm(purpose)
    if purpose_key in AUTOMATION_ALIASES:
        return PURPOSE_AUTOMATION
    if purpose_key in CAMERA_ALIASES:
        return PURPOSE_CAMERA
    raise ValueError(
        f"Unsupported purpose: {purpose!r}. Use automation (Redroid/GApps) "
        "or camera (Cuttlefish ingest)."
    )


def runtime_for_purpose(purpose=None, runtime=None):
    resolved = resolve_purpose(purpose, runtime)
    if resolved == PURPOSE_CAMERA:
        return RUNTIME_CUTTLEFISH
    return RUNTIME_REDROID
