#!/usr/bin/env python3
"""
Integration test for orchestrator phone routing endpoints with mock control API.
"""

import os
import sys
import time
import threading
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

    @app.route("/status", methods=["GET"])
    def status():
        return jsonify({"connected": True, "device": {"model": "Mock"}})

    @app.route("/device/input", methods=["POST"])
    def device_input():
        data = request.get_json() or {}
        log.calls.append({"endpoint": "device_input", "data": data})
        return jsonify({"success": True})

    @app.route("/device/screenshot/base64", methods=["GET"])
    def screenshot_base64():
        return jsonify({"success": True, "image_base64": "AAAA"})

    @app.route("/jobs", methods=["POST"])
    def jobs():
        return jsonify({"job_id": "job1", "status": "queued"}), 202

    @app.route("/jobs/job1", methods=["GET"])
    def job_poll():
        return jsonify({"id": "job1", "status": "done", "result": {"success": True}})

    app.run(host="127.0.0.1", port=port, threaded=True)


def find_free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_for_url(url: str, timeout_s: int = 20, headers=None):
    start = time.time()
    while time.time() - start < timeout_s:
        try:
            resp = requests.get(url, headers=headers, timeout=3)
            if resp.status_code < 500:
                return True
        except Exception:
            time.sleep(0.5)
    return False


def main():
    mock_port = find_free_port()
    orch_port = find_free_port()
    token = "testtoken"

    log = CallLog(calls=[])
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
    env["ORCH_API_TOKEN"] = token

    orch_proc = subprocess.Popen(
        [sys.executable, os.path.join(os.path.dirname(__file__), "..", "orchestrator", "server.py")],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    headers = {"Authorization": f"Bearer {token}"}
    try:
        if not wait_for_url(f"http://127.0.0.1:{orch_port}/health", timeout_s=10, headers=headers):
            print("Orchestrator did not start", file=sys.stderr)
            return 2

        resp = requests.post(f"http://127.0.0.1:{orch_port}/instances", headers=headers, timeout=5)
        resp.raise_for_status()
        instance_id = resp.json()["id"]

        resp = requests.get(f"http://127.0.0.1:{orch_port}/phones/{instance_id}/status", headers=headers, timeout=5)
        resp.raise_for_status()

        resp = requests.post(
            f"http://127.0.0.1:{orch_port}/phones/{instance_id}/input",
            headers=headers,
            json={"type": "tap", "x": 10, "y": 20},
            timeout=5
        )
        resp.raise_for_status()

        resp = requests.get(
            f"http://127.0.0.1:{orch_port}/phones/{instance_id}/screenshot",
            headers=headers,
            timeout=5
        )
        resp.raise_for_status()

        resp = requests.post(
            f"http://127.0.0.1:{orch_port}/phones/{instance_id}/jobs",
            headers=headers,
            json={"type": "adb_shell", "payload": {"command": "echo ok"}},
            timeout=5
        )
        resp.raise_for_status()
        job_id = resp.json()["job_id"]

        resp = requests.get(
            f"http://127.0.0.1:{orch_port}/phones/{instance_id}/jobs/{job_id}",
            headers=headers,
            timeout=5
        )
        resp.raise_for_status()

        if not log.calls:
            print("No input calls recorded by mock control API", file=sys.stderr)
            return 1

        print("Orchestrator integration test passed.")
        return 0
    finally:
        orch_proc.terminate()
        try:
            orch_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            orch_proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
