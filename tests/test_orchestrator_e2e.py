#!/usr/bin/env python3
"""
E2E test: mock agent connects to orchestrator, which provisions a device (mock)
and performs a login-style operation.

Usage:
  python tests/test_orchestrator_e2e.py
"""

import json
import os
import sys
import time
import threading
import argparse
import subprocess
from dataclasses import dataclass
from typing import List

import requests
from flask import Flask, jsonify, request


@dataclass
class CallLog:
    calls: List[dict]


def start_mock_control_api(port: int, log: CallLog):
    app = Flask("mock_control_api")

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"status": "healthy", "adb_connected": True})

    @app.route("/apps/<package>/start", methods=["POST"])
    def start_app(package):
        log.calls.append({"endpoint": "start_app", "package": package})
        return jsonify({"success": True, "message": "started"})

    @app.route("/device/input", methods=["POST"])
    def device_input():
        data = request.get_json() or {}
        log.calls.append({"endpoint": "device_input", "data": data})
        return jsonify({"success": True})

    app.run(host="127.0.0.1", port=port, threaded=True)


def find_free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_for_url(url: str, timeout_s: int = 20):
    start = time.time()
    while time.time() - start < timeout_s:
        try:
            resp = requests.get(url, timeout=3)
            if resp.status_code < 500:
                return True
        except Exception:
            time.sleep(0.5)
    return False


def main():
    parser = argparse.ArgumentParser(description="Orchestrator E2E test")
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    mock_port = find_free_port()
    orch_port = find_free_port()

    log = CallLog(calls=[])
    print(f"Starting mock Control API on port {mock_port}")
    thread = threading.Thread(target=start_mock_control_api, args=(mock_port, log), daemon=True)
    thread.start()

    if not wait_for_url(f"http://127.0.0.1:{mock_port}/health", timeout_s=10):
        print("Mock Control API did not start", file=sys.stderr)
        return 2

    env = os.environ.copy()
    env["ORCH_DEPLOY_MODE"] = "mock"
    env["ORCH_MOCK_API_URL"] = f"http://127.0.0.1:{mock_port}"
    env["ORCH_HOST"] = "127.0.0.1"
    env["ORCH_PORT"] = str(orch_port)
    env["ORCH_API_TOKEN"] = "testtoken"

    print(f"Starting orchestrator on port {orch_port}")
    orch_proc = subprocess.Popen(
        [sys.executable, os.path.join(os.path.dirname(__file__), "..", "orchestrator", "server.py")],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    try:
        if not wait_for_url(f"http://127.0.0.1:{orch_port}/health", timeout_s=10):
            print("Orchestrator did not start", file=sys.stderr)
            return 2

        payload = {
            "operation": "login",
            "app_package": "com.example.app",
            "login": {"username": "testuser", "password": "testpass"}
        }
        print("Submitting login operation...")
        headers = {"Authorization": "Bearer testtoken"}
        resp = requests.post(
            f"http://127.0.0.1:{orch_port}/operations",
            json=payload,
            headers=headers,
            timeout=5
        )
        resp.raise_for_status()
        op_id = resp.json()["operation_id"]
        print(f"Operation ID: {op_id}")

        deadline = time.time() + args.timeout
        status = "queued"
        result = None
        while time.time() < deadline:
            poll = requests.get(
                f"http://127.0.0.1:{orch_port}/operations/{op_id}",
                headers=headers,
                timeout=5
            ).json()
            status = poll.get("status")
            if status in ("done", "failed"):
                result = poll
                break
            time.sleep(0.5)

        if status != "done":
            print(f"Operation failed or timed out: {status}", file=sys.stderr)
            if result:
                print(json.dumps(result, indent=2), file=sys.stderr)
            return 1

        endpoints = [c["endpoint"] for c in log.calls]
        if "start_app" not in endpoints or "device_input" not in endpoints:
            print("Mock Control API did not receive expected calls", file=sys.stderr)
            print(log.calls, file=sys.stderr)
            return 1

        print("Orchestrator E2E test passed.")
        return 0
    finally:
        orch_proc.terminate()
        try:
            orch_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            orch_proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
