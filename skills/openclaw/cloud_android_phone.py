#!/usr/bin/env python3
"""
OpenClaw-compatible adapter for the Cloud Phone Control API.

The adapter intentionally uses only the Python standard library so it can be
vendored into agent runtimes without installing additional dependencies.
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional, Tuple


Transport = Callable[[str, str, Dict[str, str], Optional[bytes], int], Tuple[int, str, bytes]]


class ControlApiError(RuntimeError):
    """Raised when the Control API returns an error response."""

    def __init__(self, status: int, message: str):
        super().__init__(f"Control API request failed ({status}): {message}")
        self.status = status
        self.message = message


def _default_transport(
    method: str,
    url: str,
    headers: Dict[str, str],
    body: Optional[bytes],
    timeout: int,
) -> Tuple[int, str, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            return response.status, content_type, response.read()
    except urllib.error.HTTPError as exc:
        content_type = exc.headers.get("Content-Type", "") if exc.headers else ""
        return exc.code, content_type, exc.read()


class CloudAndroidPhone:
    """Small typed wrapper around `api/server.py` Control API endpoints."""

    def __init__(
        self,
        base_url: str,
        token: str = "",
        timeout: int = 30,
        transport: Optional[Transport] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport or _default_transport
        self.headers = {"Content-Type": "application/json"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def _request(self, method: str, path: str, payload: Optional[dict] = None) -> Any:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        status, content_type, data = self.transport(
            method,
            f"{self.base_url}{path}",
            dict(self.headers),
            body,
            self.timeout,
        )
        if status >= 400:
            message = data.decode("utf-8", "replace") if data else ""
            raise ControlApiError(status, message)

        if not data:
            return None
        if "application/json" in content_type or data[:1] in (b"{", b"["):
            return json.loads(data.decode("utf-8"))
        return data

    def health(self) -> dict:
        return self._request("GET", "/health")

    def status(self) -> dict:
        return self._request("GET", "/status")

    def screenshot(self) -> dict:
        return self._request("GET", "/device/screenshot/base64")

    def tap(self, x: int, y: int) -> dict:
        return self._request("POST", "/device/input", {"type": "tap", "x": x, "y": y})

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int = 300) -> dict:
        return self._request(
            "POST",
            "/device/input",
            {"type": "swipe", "x1": x1, "y1": y1, "x2": x2, "y2": y2, "duration": duration},
        )

    def type_text(self, text: str) -> dict:
        return self._request("POST", "/device/input", {"type": "text", "text": text})

    def key(self, keycode: int) -> dict:
        return self._request("POST", "/device/input", {"type": "key", "keycode": keycode})

    def start_app(self, package: str) -> dict:
        return self._request("POST", f"/apps/{package}/start")

    def stop_app(self, package: str) -> dict:
        return self._request("POST", f"/apps/{package}/stop")

    def list_apps(self) -> dict:
        return self._request("GET", "/apps")

    def adb_shell(self, command: str, timeout: int = 30) -> dict:
        return self._request("POST", "/adb/shell", {"command": command, "timeout": timeout})

    def create_job(self, job_type: str, payload: Optional[dict] = None) -> dict:
        return self._request("POST", "/jobs", {"type": job_type, "payload": payload or {}})

    def poll_job(self, job_id: str) -> dict:
        return self._request("GET", f"/jobs/{job_id}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cloud Android phone skill adapter")
    parser.add_argument("--base-url", required=True, help="Control API URL, e.g. http://1.2.3.4:8080")
    parser.add_argument("--token", default="", help="Bearer token if API_TOKEN is configured")
    parser.add_argument("--timeout", type=int, default=30)

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("health")
    sub.add_parser("status")
    sub.add_parser("screenshot")
    sub.add_parser("apps")

    tap = sub.add_parser("tap")
    tap.add_argument("--x", type=int, required=True)
    tap.add_argument("--y", type=int, required=True)

    text = sub.add_parser("text")
    text.add_argument("--text", required=True)

    key = sub.add_parser("key")
    key.add_argument("--keycode", type=int, required=True)

    app = sub.add_parser("start-app")
    app.add_argument("--package", required=True)

    shell = sub.add_parser("shell")
    shell.add_argument("--command", required=True)

    return parser


def main() -> int:
    args = _build_parser().parse_args()
    phone = CloudAndroidPhone(args.base_url, token=args.token, timeout=args.timeout)

    if args.command == "health":
        result = phone.health()
    elif args.command == "status":
        result = phone.status()
    elif args.command == "screenshot":
        result = phone.screenshot()
    elif args.command == "apps":
        result = phone.list_apps()
    elif args.command == "tap":
        result = phone.tap(args.x, args.y)
    elif args.command == "text":
        result = phone.type_text(args.text)
    elif args.command == "key":
        result = phone.key(args.keycode)
    elif args.command == "start-app":
        result = phone.start_app(args.package)
    elif args.command == "shell":
        result = phone.adb_shell(args.command)
    else:
        raise AssertionError(f"unhandled command {args.command}")

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

