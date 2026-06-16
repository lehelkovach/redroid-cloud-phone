#!/usr/bin/env python3
"""
Integration test for the orchestrator fleet fan-out: dispatch one operation to
several registered instances asynchronously and verify each is driven.
"""

import os
import sys
import time
import threading
import subprocess
from dataclasses import dataclass, field
from typing import List

import requests
from flask import Flask, jsonify, request


@dataclass
class CallLog:
    calls: List[dict] = field(default_factory=list)


def start_mock_control_api(port: int, log: CallLog):
    app = Flask("mock_control_api")

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"status": "healthy", "adb_connected": True})

    @app.route("/apps/<package>/start", methods=["POST"])
    def start_app(package):
        log.calls.append({"endpoint": "start_app", "package": package})
        return jsonify({"success": True})

    @app.route("/device/input", methods=["POST"])
    def device_input():
        log.calls.append({"endpoint": "device_input", "data": request.get_json() or {}})
        return jsonify({"success": True})

    @app.route("/launch-config/apply", methods=["POST"])
    def launch_apply():
        log.calls.append({"endpoint": "launch_apply", "data": request.get_json() or {}})
        return jsonify({"applied": ["startup_tasks"]})

    app.run(host="127.0.0.1", port=port, threaded=True)


def find_free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_for_url(url, timeout_s=15, headers=None):
    start = time.time()
    while time.time() - start < timeout_s:
        try:
            if requests.get(url, headers=headers, timeout=3).status_code < 500:
                return True
        except Exception:
            time.sleep(0.4)
    return False


def main():
    mock_port = find_free_port()
    orch_port = find_free_port()
    token = "testtoken"
    headers = {"Authorization": f"Bearer {token}"}

    log = CallLog()
    threading.Thread(target=start_mock_control_api, args=(mock_port, log), daemon=True).start()
    if not wait_for_url(f"http://127.0.0.1:{mock_port}/health"):
        print("mock control API did not start", file=sys.stderr)
        return 2

    env = os.environ.copy()
    env.update({
        "ORCH_DEPLOY_MODE": "mock",
        "ORCH_MOCK_API_URL": f"http://127.0.0.1:{mock_port}",
        "ORCH_HOST": "127.0.0.1",
        "ORCH_PORT": str(orch_port),
        "ORCH_API_TOKEN": token,
        "ORCH_MAX_INSTANCES": "5",
    })
    proc = subprocess.Popen(
        [sys.executable, os.path.join(os.path.dirname(__file__), "..", "orchestrator", "server.py")],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    try:
        if not wait_for_url(f"http://127.0.0.1:{orch_port}/health", headers=headers):
            print("orchestrator did not start", file=sys.stderr)
            return 2

        # Register two instances; the second carries a launch config.
        ids = []
        r = requests.post(f"http://127.0.0.1:{orch_port}/instances", headers=headers, timeout=5)
        r.raise_for_status()
        ids.append(r.json()["id"])
        r = requests.post(
            f"http://127.0.0.1:{orch_port}/instances",
            headers=headers,
            json={"launch_config": {"startup_tasks": [{"type": "adb_shell", "payload": {"command": "echo hi"}}],
                                    "labels": {"role": "dev"}}},
            timeout=5)
        r.raise_for_status()
        ids.append(r.json()["id"])

        if not any(c["endpoint"] == "launch_apply" for c in log.calls):
            print("launch config was not applied to control API", file=sys.stderr)
            return 1

        # Fan out a login operation to ALL instances asynchronously.
        r = requests.post(
            f"http://127.0.0.1:{orch_port}/fleet/operations",
            headers=headers,
            json={"operation": "login", "app_package": "com.example.app",
                  "login": {"username": "u", "password": "p"}},
            timeout=5)
        r.raise_for_status()
        fleet_id = r.json()["fleet_operation_id"]
        if sorted(r.json()["targets"]) != sorted(ids):
            print(f"unexpected targets: {r.json()['targets']} vs {ids}", file=sys.stderr)
            return 1

        deadline = time.time() + 30
        status = None
        while time.time() < deadline:
            poll = requests.get(f"http://127.0.0.1:{orch_port}/fleet/operations/{fleet_id}",
                                headers=headers, timeout=5).json()
            status = poll.get("status")
            if status in ("done", "failed", "partial"):
                break
            time.sleep(0.5)

        if status != "done":
            print(f"fleet op not done: {status}", file=sys.stderr)
            return 1

        # Each registered instance should have driven a start_app on the control API.
        start_calls = [c for c in log.calls if c["endpoint"] == "start_app"]
        if len(start_calls) < len(ids):
            print(f"expected >= {len(ids)} start_app calls, got {len(start_calls)}", file=sys.stderr)
            return 1

        print("Orchestrator fleet fan-out test passed.")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
