"""Fakes for the isolation tests: a DevTools endpoint, a phone, and a live orchestrator.

The relay is WebSocket traffic, which Flask's test client cannot carry, so the
orchestrator is served for real on a loopback port (werkzeug, threaded) and
the fakes behind it are real servers too. Nothing here touches Docker, a real
browser or a real phone. Every token and owner id is fake.
"""

import base64
import json
import os
import shutil
import tempfile
import threading
import time

import simple_websocket
from flask import Flask, Response, jsonify, request
from werkzeug.serving import make_server

from orchestrator import browser_pool as bp
from orchestrator import server as orch

FAKE_ORCH_TOKEN = "orch-test-token-not-real"
OWNER_A = "slack:U0FAKEA"
OWNER_B = "slack:U0FAKEB"


class Served:
    def __init__(self, app):
        self._srv = make_server("127.0.0.1", 0, app, threaded=True)
        self.port = self._srv.server_port
        self.url = f"http://127.0.0.1:{self.port}"
        self.ws_url = f"ws://127.0.0.1:{self.port}"
        self._t = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._t.start()

    def close(self):
        self._srv.shutdown()


def _ws(handler):
    ws = simple_websocket.Server(request.environ)
    try:
        handler(ws)
    except simple_websocket.ConnectionClosed:
        pass
    try:
        ws.close()
    except Exception:  # noqa: BLE001
        pass

    class R(Response):
        def __call__(self, *a, **k):
            return super().__call__(*a, **k)

    return R()


def fake_cdp_app():
    """A DevTools stand-in. Each browser is a path; replies say which browser answered.

    `cookies[tag]` is that browser's cookie store, so a test can log one owner
    in and ask the other owner's browser whether it is logged in.
    """
    app = Flask("fake-cdp")
    cookies = {}

    @app.route("/devtools/browser/<tag>", websocket=True)
    def browser(tag):
        def run(ws):
            stop = threading.Event()

            def screencast():
                seq = 0
                while not stop.is_set():
                    try:
                        ws.send(json.dumps({"method": "Page.screencastFrame",
                                            "params": {"sessionId": seq, "data": f"frame-{tag}-{seq}"}}))
                    except Exception:  # noqa: BLE001
                        return
                    seq += 1
                    stop.wait(0.02)

            try:
                while True:
                    msg = ws.receive()
                    obj = json.loads(msg)
                    method = obj.get("method")
                    params = obj.get("params") or {}
                    result = {"browser": tag}
                    if method == "Network.setCookie":
                        cookies.setdefault(tag, {})[params.get("name")] = params.get("value")
                    elif method == "Network.getAllCookies":
                        result["cookies"] = [{"name": k, "value": v} for k, v in cookies.get(tag, {}).items()]
                    elif method == "Page.startScreencast":
                        threading.Thread(target=screencast, daemon=True).start()
                    ws.send(json.dumps({"id": obj.get("id"), "result": result}))
            finally:
                stop.set()

        return _ws(run)

    return app, cookies


def fake_phone_app():
    """A Control API stand-in shared by every mock phone; the ADB header tells them apart."""
    app = Flask("fake-phone")
    hits = []

    def who():
        return request.headers.get("X-Cloud-Phone-Adb", "")

    @app.route("/health")
    def health():
        hits.append(("GET", "/health", who()))
        return jsonify({"status": "healthy", "phone": who()})

    @app.route("/device/input", methods=["POST"])
    def device_input():
        hits.append(("POST", "/device/input", who()))
        return jsonify({"success": True, "phone": who()})

    @app.route("/device/screenshot/base64")
    def shot():
        hits.append(("GET", "/device/screenshot/base64", who()))
        return jsonify({"success": True, "phone": who(),
                        "image": base64.b64encode(f"png-of-{who()}".encode()).decode()})

    @app.route("/config", methods=["GET", "POST"])
    def config():
        hits.append((request.method, "/config", who()))
        return jsonify({"config": "should never be relayed"})

    return app, hits


class Harness:
    """A live orchestrator in mock mode with a fake browser and phone behind it."""

    def __init__(self, *, max_browsers=4, max_phones=4, ticket_ttl=300, rate=1000.0, burst=1000,
                 state_db=None):
        self.tmp = tempfile.mkdtemp(prefix="orch-iso-")
        self.profile_root = os.path.join(self.tmp, "profiles")
        self.state_db = state_db or os.path.join(self.tmp, "state.sqlite3")
        self.cdp_app, self.cookies = fake_cdp_app()
        self.cdp = Served(self.cdp_app)
        self.phone_app, self.phone_hits = fake_phone_app()
        self.phone = Served(self.phone_app)
        self._configure(max_browsers, max_phones, ticket_ttl, rate, burst)
        orch.init_state(self.state_db)
        self.orch = Served(orch.app)
        self.base = self.orch.url

    def _configure(self, max_browsers, max_phones, ticket_ttl, rate, burst):
        orch.ORCH_DEPLOY_MODE = "mock"
        orch.ORCH_API_TOKEN = FAKE_ORCH_TOKEN
        orch.ORCH_CONTROL_API_TOKEN = "control-test-token-not-real"
        orch.ORCH_MOCK_API_URL = self.phone.url
        orch.ORCH_MAX_INSTANCES = max_phones
        orch.ORCH_MAX_REDROID_INSTANCES = max_phones
        orch.ORCH_MAX_CHROME_INSTANCES = max_browsers
        orch.ORCH_PROFILE_ROOT = self.profile_root
        orch.ORCH_PROFILE_ENCRYPTION = "none"
        orch.ORCH_TICKET_TTL_SECONDS = ticket_ttl
        orch.ORCH_PUBLIC_URL = ""
        orch.ORCH_REQUIRE_TICKETS = False
        orch._limiter.rate = float(rate)
        orch._limiter.burst = float(burst)
        orch._limiter._buckets.clear()
        orch._audit.lines.clear()
        cdp_ws = self.cdp.ws_url
        orch.set_chrome_runtime(bp.MockChromeRuntime(
            upstream_for=lambda sess: f"{cdp_ws}/devtools/browser/{sess['owner_token']}"))

    # -- HTTP helpers -------------------------------------------------------
    def headers(self, ticket=None):
        if ticket:
            return {"X-Orch-Ticket": ticket}
        return {"Authorization": f"Bearer {FAKE_ORCH_TOKEN}"}

    def browser(self, owner, **extra):
        import requests
        body = {"owner_user_id": owner, "purpose": "browser", "ttl_seconds": 600, **extra}
        return requests.post(f"{self.base}/sessions", json=body, headers=self.headers(), timeout=10)

    def phone_lease(self, owner, **extra):
        import requests
        body = {"owner_user_id": owner, "ttl_seconds": 600, **extra}
        return requests.post(f"{self.base}/sessions", json=body, headers=self.headers(), timeout=10)

    def enable_volumes(self, host, manager_cls=None):
        """Turn on per-owner volumes (both flags) over a fake host (tests/fixtures/fake_volumes.py).

        The fake host is the kernel: it is not reset by `restart()`, exactly as
        real mounts outlive the orchestrator process.
        """
        from orchestrator import user_volumes as uv
        self.volume_root = os.path.join(self.tmp, "volumes")
        self.master_key_file = os.path.join(self.tmp, "volume-master.key")
        with open(self.master_key_file, "wb") as fh:
            fh.write(os.urandom(32))  # generated per run; fake, never stored
        os.chmod(self.master_key_file, 0o600)
        orch.ORCH_PHONE_DATA = "per_user"
        orch.ORCH_BROWSER_PROFILE = "volume"
        orch.ORCH_VOLUME_ROOT = self.volume_root
        orch.ORCH_VOLUME_MASTER_KEY_FILE = self.master_key_file
        self.volumes = (manager_cls or uv.VolumeManager)(
            self.volume_root, self.master_key_file, phone_quota_gb=8, browser_quota_gb=1, host_min_free_gb=10,
            **host.hooks())
        orch.set_volume_manager(self.volumes)
        return self.volumes

    def cdp_connect(self, url, ticket):
        return simple_websocket.Client.connect(url, headers={"X-Orch-Ticket": ticket})

    def restart(self):
        """What a process restart leaves: the database. Every dict is dropped and reloaded."""
        orch._instances.clear()
        orch._leases.clear()
        orch._instance_tenancy.clear()
        orch._user_sessions.clear()
        orch._browser_sessions.clear()
        orch._tickets.clear()
        orch.init_state(self.state_db)

    def close(self):
        for s in (self.orch, self.cdp, self.phone):
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass
        orch.set_chrome_runtime(None)
        orch.set_volume_manager(None)
        orch.ORCH_PHONE_DATA = "instance"
        orch.ORCH_BROWSER_PROFILE = "dir"
        orch.init_state(":memory:")
        orch._instances.clear()
        orch._leases.clear()
        orch._instance_tenancy.clear()
        orch._user_sessions.clear()
        orch._browser_sessions.clear()
        orch._tickets.clear()
        orch.ORCH_API_TOKEN = ""
        shutil.rmtree(self.tmp, ignore_errors=True)


def cdp_call(ws, msg_id, method, params=None, timeout=5.0):
    """Send one CDP command and wait for its reply, skipping events."""
    ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = ws.receive(timeout=deadline - time.time())
        if raw is None:
            break
        obj = json.loads(raw)
        if obj.get("id") == msg_id:
            return obj
    raise TimeoutError(f"no reply to {method}")
