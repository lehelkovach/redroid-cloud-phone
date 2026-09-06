"""Shared helpers for process-level orchestrator tests (no Docker, no OCI)."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parents[1]


def find_free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def wait_for_url(url, timeout_s=15, headers=None):
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        try:
            resp = requests.get(url, headers=headers, timeout=2)
            if resp.status_code < 500:
                return True
            last = resp.status_code
        except Exception as exc:
            last = exc
        time.sleep(0.1)
    raise TimeoutError(f"timed out waiting for {url}: {last}")


class FlaskThread:
    """Background WSGI server that can be shut down from tests."""

    def __init__(self, app, port):
        self.port = port
        self.server = make_server("127.0.0.1", port, app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def start(self):
        self.thread.start()
        wait_for_url(f"{self.url}/health")
        return self

    def stop(self):
        self.server.shutdown()
        self.thread.join(timeout=5)


class OrchestratorProc:
    def __init__(
        self,
        mock_url,
        camera_url=None,
        token="testtoken",
        extra_env=None,
    ):
        self.port = find_free_port()
        self.token = token
        self.mock_url = mock_url
        self.camera_url = camera_url
        self.extra_env = extra_env or {}
        self.proc = None

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def headers(self):
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def start(self):
        env = os.environ.copy()
        env.update({
            "ORCH_DEPLOY_MODE": "mock",
            "ORCH_MOCK_API_URL": self.mock_url,
            "ORCH_HOST": "127.0.0.1",
            "ORCH_PORT": str(self.port),
            "ORCH_API_TOKEN": self.token,
            "ORCH_LOG_LEVEL": os.environ.get("ORCH_LOG_LEVEL", "DEBUG"),
            "LOG_LEVEL": os.environ.get("LOG_LEVEL", "DEBUG"),
            "CLOUD_PHONE_VERBOSE": os.environ.get("CLOUD_PHONE_VERBOSE", "1"),
        })
        if self.camera_url:
            env["ORCH_MOCK_CAMERA_API_URL"] = self.camera_url
        env.update(self.extra_env)
        self.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "orchestrator" / "server.py")],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        wait_for_url(f"{self.url}/health")
        return self

    def stop(self):
        if not self.proc:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)

    def get(self, path, **kwargs):
        return requests.get(f"{self.url}{path}", headers=self.headers(), timeout=8, **kwargs)

    def post(self, path, json=None, **kwargs):
        return requests.post(
            f"{self.url}{path}", headers=self.headers(), json=json, timeout=8, **kwargs
        )

    def delete(self, path, **kwargs):
        return requests.delete(f"{self.url}{path}", headers=self.headers(), timeout=8, **kwargs)
