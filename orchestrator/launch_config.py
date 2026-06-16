#!/usr/bin/env python3
"""
Per-instance launch configuration for cloud phones.

The orchestrator builds a LaunchConfig when it launches a phone (from the golden
image) and delivers it to the instance via cloud-init user-data. On boot the
Control API reads the rendered JSON (default /etc/cloud-phone/launch.json) and
applies it: sets the proxy, applies a device-identity profile, and runs any
fire-and-forget startup tasks.

The schema is intentionally open: well-known fields are first-class, and anything
else can be carried in `extra` so the format can grow without breaking callers.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

# Default on-instance path the Control API reads at boot.
DEFAULT_CONFIG_PATH = "/etc/cloud-phone/launch.json"

# Job types the Control API understands for startup tasks.
VALID_TASK_TYPES = {
    "adb_shell", "device_input", "screen", "screenshot",
    "app_start", "app_stop", "app_clear", "app_uninstall",
}


@dataclass
class LaunchConfig:
    """Declarative startup config for a single phone instance."""

    instance_id: str
    # OCI golden image this instance is launched from (informational).
    golden_image_id: Optional[str] = None
    # Proxy to apply, matching the Control API POST /proxy body, e.g.
    # {"enabled": true, "type": "socks5", "host": "1.2.3.4", "port": 1080}
    proxy: Optional[Dict[str, Any]] = None
    # Optional device-identity / anti-detection profile (Control API /device/identity).
    device_identity: Optional[Dict[str, Any]] = None
    # UI input backend the instance should use: "adb" (default) or "appium".
    ui_backend: Optional[str] = None
    # Fire-and-forget tasks run once at boot. Each: {"type": <job type>, "payload": {...}}
    startup_tasks: List[Dict[str, Any]] = field(default_factory=list)
    # Free-form labels (e.g. {"role": "dev"}).
    labels: Dict[str, Any] = field(default_factory=dict)
    # Open extension point for anything not modeled above.
    extra: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> "LaunchConfig":
        if not self.instance_id or not isinstance(self.instance_id, str):
            raise ValueError("instance_id is required and must be a string")
        if self.proxy is not None and not isinstance(self.proxy, dict):
            raise ValueError("proxy must be an object")
        if not isinstance(self.startup_tasks, list):
            raise ValueError("startup_tasks must be a list")
        for task in self.startup_tasks:
            if not isinstance(task, dict):
                raise ValueError("each startup task must be an object")
            ttype = task.get("type")
            if ttype not in VALID_TASK_TYPES:
                raise ValueError(f"unsupported startup task type: {ttype}")
        return self

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LaunchConfig":
        if not isinstance(data, dict):
            raise ValueError("launch config must be an object")
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        kwargs = {k: v for k, v in data.items() if k in known}
        # Anything unknown is preserved under extra so we never lose fields.
        unknown = {k: v for k, v in data.items() if k not in known}
        if unknown:
            merged = dict(kwargs.get("extra") or {})
            merged.update(unknown)
            kwargs["extra"] = merged
        # Surface a clear ValueError (not TypeError) when instance_id is missing.
        kwargs.setdefault("instance_id", None)
        return cls(**kwargs).validate()

    @classmethod
    def from_json(cls, text: str) -> "LaunchConfig":
        return cls.from_dict(json.loads(text))

    def to_cloud_init_userdata(self, config_path: str = DEFAULT_CONFIG_PATH) -> str:
        """Render an OCI cloud-init user-data script.

        On first boot it writes this config to `config_path` and asks the
        Control API to apply it (and restarts the service as a fallback so the
        boot-time loader picks it up even if the API is not yet listening).
        """
        payload_b64 = base64.b64encode(self.to_json().encode("utf-8")).decode("ascii")
        return f"""#!/bin/bash
set -e
mkdir -p "$(dirname {config_path})"
echo {payload_b64} | base64 -d > {config_path}
chmod 600 {config_path}
# Ask a running Control API to apply immediately; ignore if not up yet.
curl -fsS -X POST http://127.0.0.1:8080/launch-config/apply >/dev/null 2>&1 || true
# Ensure the service (re)reads the config on boot.
systemctl restart control-api.service >/dev/null 2>&1 || true
"""


def build_launch_config(
    instance_id: str,
    *,
    golden_image_id: Optional[str] = None,
    proxy: Optional[Dict[str, Any]] = None,
    device_identity: Optional[Dict[str, Any]] = None,
    ui_backend: Optional[str] = None,
    startup_tasks: Optional[List[Dict[str, Any]]] = None,
    labels: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> LaunchConfig:
    return LaunchConfig(
        instance_id=instance_id,
        golden_image_id=golden_image_id,
        proxy=proxy,
        device_identity=device_identity,
        ui_backend=ui_backend,
        startup_tasks=list(startup_tasks or []),
        labels=dict(labels or {}),
        extra=dict(extra or {}),
    ).validate()
