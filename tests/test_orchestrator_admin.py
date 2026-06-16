#!/usr/bin/env python3
"""
Integration test for orchestrator -> instance management/IPC commands
(monitor, admin restart/shutdown) and the fleet monitor aggregate.
"""

import os
import sys
import time
import threading
import subprocess

import requests
from flask import Flask, jsonify, request


def start_mock_control_api(port, log):
    app = Flask("mock_control_api")

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"status": "healthy", "adb_connected": True})

    @app.route("/monitor", methods=["GET"])
    def monitor():
        return jsonify({"timestamp": time.time(), "services": {"control-api.service": "active"},
                        "rtmp_stream_status": "ok", "adb_connected": True})

    @app.route("/admin/restart", methods=["POST"])
    def restart():
        log.append({"endpoint": "restart", "data": request.get_json(silent=True) or {}})
        return jsonify({"success": True, "action": "restart"})

    @app.route("/admin/shutdown", methods=["POST"])
    def shutdown():
        log.append({"endpoint": "shutdown", "data": request.get_json(silent=True) or {}})
        return jsonify({"success": True, "action": "shutdown"})

    app.run(host="127.0.0.1", port=port, threaded=True)


def find_free_port():
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


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
    log = []

    threading.Thread(target=start_mock_control_api, args=(mock_port, log), daemon=True).start()
    if not wait_for_url(f"http://127.0.0.1:{mock_port}/health"):
        print("mock control API did not start", file=sys.stderr); return 2

    env = os.environ.copy()
    env.update({"ORCH_DEPLOY_MODE": "mock", "ORCH_MOCK_API_URL": f"http://127.0.0.1:{mock_port}",
                "ORCH_HOST": "127.0.0.1", "ORCH_PORT": str(orch_port), "ORCH_API_TOKEN": token})
    proc = subprocess.Popen(
        [sys.executable, os.path.join(os.path.dirname(__file__), "..", "orchestrator", "server.py")],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    try:
        if not wait_for_url(f"http://127.0.0.1:{orch_port}/health", headers=headers):
            print("orchestrator did not start", file=sys.stderr); return 2

        r = requests.post(f"http://127.0.0.1:{orch_port}/instances", headers=headers, timeout=5)
        r.raise_for_status()
        iid = r.json()["id"]

        # monitor routing
        r = requests.get(f"http://127.0.0.1:{orch_port}/phones/{iid}/monitor", headers=headers, timeout=5)
        r.raise_for_status()
        if r.json().get("rtmp_stream_status") != "ok":
            print("monitor did not route correctly", file=sys.stderr); return 1

        # fleet monitor aggregate
        r = requests.get(f"http://127.0.0.1:{orch_port}/fleet/monitor", headers=headers, timeout=5)
        r.raise_for_status()
        if r.json().get("count") != 1 or iid not in r.json().get("instances", {}):
            print("fleet monitor aggregate wrong", file=sys.stderr); return 1

        # admin restart + shutdown routing
        requests.post(f"http://127.0.0.1:{orch_port}/phones/{iid}/admin/restart", headers=headers, timeout=5).raise_for_status()
        requests.post(f"http://127.0.0.1:{orch_port}/phones/{iid}/admin/shutdown",
                      headers=headers, json={"power_off": False}, timeout=5).raise_for_status()
        endpoints = [c["endpoint"] for c in log]
        if "restart" not in endpoints or "shutdown" not in endpoints:
            print(f"admin commands not received by control API: {endpoints}", file=sys.stderr); return 1

        print("Orchestrator admin/monitor IPC test passed.")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
