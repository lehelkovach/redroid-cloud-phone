#!/usr/bin/env python3
"""
Integration test: register an already-running phone with the orchestrator by its
Control API URL (no provisioning) — the dev-phone-from-env flow — and route to it.
"""

import os
import sys
import time
import threading
import subprocess

import requests
from flask import Flask, jsonify


def start_mock_control_api(port):
    app = Flask("mock_control_api")

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"status": "healthy"})

    @app.route("/launch-config/apply", methods=["POST"])
    def apply():
        return jsonify({"applied": []})

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
            time.sleep(0.3)
    return False


def main():
    mock_port = find_free_port()
    orch_port = find_free_port()
    token = "testtoken"
    headers = {"Authorization": f"Bearer {token}"}
    api_url = f"http://127.0.0.1:{mock_port}"

    threading.Thread(target=start_mock_control_api, args=(mock_port,), daemon=True).start()
    if not wait_for_url(f"{api_url}/health"):
        print("mock control API did not start", file=sys.stderr); return 2

    env = os.environ.copy()
    env.update({"ORCH_DEPLOY_MODE": "mock", "ORCH_MOCK_API_URL": api_url,
                "ORCH_HOST": "127.0.0.1", "ORCH_PORT": str(orch_port), "ORCH_API_TOKEN": token,
                # auto-register the "dev phone" from env on startup
                "ORCH_REGISTER_API_URLS": api_url})
    proc = subprocess.Popen(
        [sys.executable, os.path.join(os.path.dirname(__file__), "..", "orchestrator", "server.py")],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        if not wait_for_url(f"http://127.0.0.1:{orch_port}/health", headers=headers):
            print("orchestrator did not start", file=sys.stderr); return 2

        # auto-registered instance should already be present
        r = requests.get(f"http://127.0.0.1:{orch_port}/instances", headers=headers, timeout=5)
        r.raise_for_status()
        insts = r.json()
        if not any(i["mode"] == "external" and i["api_url"] == api_url for i in insts):
            print(f"auto-register from env failed: {insts}", file=sys.stderr); return 1

        # explicit registration by api_url (no provisioning)
        r = requests.post(f"http://127.0.0.1:{orch_port}/instances", headers=headers,
                          json={"api_url": api_url, "name": "dev-phone"}, timeout=5)
        r.raise_for_status()
        # same url -> deduped to the existing record
        r = requests.get(f"http://127.0.0.1:{orch_port}/instances", headers=headers, timeout=5)
        ext = [i for i in r.json() if i["api_url"] == api_url]
        if len(ext) != 1:
            print(f"expected dedup to 1 external instance, got {len(ext)}", file=sys.stderr); return 1

        # routing to the registered phone works
        iid = ext[0]["id"]
        r = requests.get(f"http://127.0.0.1:{orch_port}/phones/{iid}/health", headers=headers, timeout=5)
        r.raise_for_status()
        if r.json().get("status") != "healthy":
            print(f"routing to external instance failed: {r.json()}", file=sys.stderr); return 1

        print("Orchestrator external-registration test passed.")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
