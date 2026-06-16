#!/usr/bin/env python3
"""
Integration test for the Control API UI commandlet endpoints (adb backend).
Starts the real api/server.py (no device attached) and verifies the UI commands
map to the correct adb `input` strings and that errors are handled.
"""

import os
import sys
import time
import subprocess

import requests


def find_free_port():
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def wait_for_url(url, timeout_s=15):
    start = time.time()
    while time.time() - start < timeout_s:
        try:
            if requests.get(url, timeout=3).status_code < 500:
                return True
        except Exception:
            time.sleep(0.3)
    return False


def main():
    port = find_free_port()
    env = os.environ.copy()
    env.update({"API_HOST": "127.0.0.1", "API_PORT": str(port),
                "LAUNCH_CONFIG_FILE": "/tmp/does-not-exist.json", "UI_BACKEND": "adb"})
    proc = subprocess.Popen(
        [sys.executable, os.path.join(os.path.dirname(__file__), "..", "api", "server.py")],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    base = f"http://127.0.0.1:{port}"
    try:
        if not wait_for_url(f"{base}/health"):
            print("control API did not start", file=sys.stderr); return 2

        # tap by percent -> adb input tap (default 1080x1920 since no device for wm size)
        r = requests.post(f"{base}/ui/command", json={"action": "tap", "xp": 50, "yp": 50}, timeout=5)
        r.raise_for_status()
        body = r.json()
        if body.get("backend") != "adb" or not body.get("commands", [""])[0].startswith("input tap "):
            print(f"tap mapping wrong: {body}", file=sys.stderr); return 1

        # text shortcut
        r = requests.post(f"{base}/ui/text", json={"text": "hi there"}, timeout=5)
        r.raise_for_status()
        if r.json().get("commands") != ["input text 'hi%sthere'"]:
            print(f"text mapping wrong: {r.json()}", file=sys.stderr); return 1

        # key by name
        r = requests.post(f"{base}/ui/key", json={"key": "back"}, timeout=5)
        r.raise_for_status()
        if r.json().get("commands") != ["input keyevent 4"]:
            print(f"key mapping wrong: {r.json()}", file=sys.stderr); return 1

        # invalid action -> 400
        r = requests.post(f"{base}/ui/command", json={"action": "nope"}, timeout=5)
        if r.status_code != 400:
            print(f"expected 400 for bad action, got {r.status_code}", file=sys.stderr); return 1

        # appium backend selected but unavailable -> 501
        r = requests.post(f"{base}/ui/command", json={"action": "tap", "x": 1, "y": 2, "backend": "appium"}, timeout=5)
        if r.status_code != 501:
            print(f"expected 501 for appium-unavailable, got {r.status_code}", file=sys.stderr); return 1

        # getScreen endpoint responds with JSON (success False here: no device)
        r = requests.get(f"{base}/ui/screen", timeout=5)
        if "success" not in r.json():
            print(f"ui/screen missing success: {r.json()}", file=sys.stderr); return 1

        print("Control API UI commandlet endpoints test passed.")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
