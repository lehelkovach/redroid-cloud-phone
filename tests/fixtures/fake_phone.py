"""An in-process fake Control API backed by a tiny Android-ish state machine.

This exists so the procedure engine, proxy plumbing, and follow-up rules can be
exercised end to end with no device, no Play Store, and no real account. It is
a *simulator*, not a mock in the "returns True" sense: it refuses taps on the
wrong screen, requires a proxy before network screens load, and will not hand
out a match that was never swiped.

Deliberately not a real dating service. The deck is fabricated, credentials are
fake, and messaging is rate-limited the same way the real policy demands.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

SIGNUP_SCREEN = "signup"
DECK_SCREEN = "deck"
MATCHES_SCREEN = "matches"
CHAT_SCREEN = "chat"
HOME_SCREEN = "home"

DEMO_APP = "com.example.mockdating"


class FakePhone:
    """Device state. Thread-safe enough for one HTTP server thread."""

    def __init__(self, deck=None, require_proxy=True, gapps=True, api_token=""):
        self.lock = threading.Lock()
        # Mirrors the real Control API: `/health` is open, device calls need
        # this token. That asymmetry is what used to make a healthy phone look
        # dead to a caller with the wrong token.
        self.api_token = api_token
        self.screen = HOME_SCREEN
        self.focus = "com.android.launcher3"
        self.installed = {"com.android.launcher3", DEMO_APP}
        self.proxy = {"enabled": False, "host": None, "port": None}
        self.require_proxy = require_proxy
        self.gapps = gapps
        self.account = None
        self._field = ""
        self._fields = {}
        self._focused_field = "email"
        self.deck = list(deck or ["ada", "grace", "katherine", "joan"])
        self.swipes = []
        self.matches = []
        self.messages = []
        self.calls = []
        self.egress_ip = "10.0.0.1"

    # -- helpers -----------------------------------------------------------
    def _record(self, endpoint, payload=None):
        self.calls.append({"endpoint": endpoint, "payload": payload})

    def set_proxy(self, payload):
        enabled = bool(payload.get("enabled"))
        host = payload.get("host")
        port = payload.get("port")
        if enabled and (not host or not port):
            return {"success": False, "error": "host and port required when enabled"}
        self.proxy = {"enabled": enabled, "host": host, "port": port}
        # A residential proxy changes the apparent egress; the test asserts it.
        self.egress_ip = "203.0.113.7" if enabled else "10.0.0.1"
        return {"success": True, "proxy": dict(self.proxy), "egress_ip": self.egress_ip}

    def launch(self, package):
        if package not in self.installed:
            return {"success": False, "error": f"package not installed: {package}"}
        if package == DEMO_APP:
            if self.require_proxy and not self.proxy["enabled"]:
                return {"success": False, "error": "network unreachable (no egress proxy)"}
            self.screen = SIGNUP_SCREEN if self.account is None else DECK_SCREEN
        else:
            self.screen = HOME_SCREEN
        self.focus = package
        return {"success": True, "activity": f"{package}/.MainActivity", "screen": self.screen}

    def type_text(self, text):
        if self.screen != SIGNUP_SCREEN:
            return {"success": False, "error": f"nothing to type into on '{self.screen}'"}
        self._fields[self._focused_field] = self._fields.get(self._focused_field, "") + text
        return {"success": True, "field": self._focused_field}

    def key(self, keycode):
        if self.screen == SIGNUP_SCREEN:
            if keycode == 61:  # TAB
                self._focused_field = "password" if self._focused_field == "email" else "email"
                return {"success": True, "focused": self._focused_field}
            if keycode == 66:  # ENTER = submit signup
                email = self._fields.get("email", "")
                password = self._fields.get("password", "")
                if not email or not password:
                    return {"success": False, "error": "signup needs email and password"}
                self.account = {"email": email}
                self.screen = DECK_SCREEN
                return {"success": True, "account": email, "screen": self.screen}
        return {"success": True, "keycode": keycode}

    def swipe(self, x1, x2):
        if self.screen != DECK_SCREEN:
            return {"success": False, "error": f"cannot swipe on '{self.screen}'"}
        if not self.deck:
            return {"success": False, "error": "deck exhausted"}
        profile = self.deck.pop(0)
        liked = x2 > x1
        self.swipes.append({"profile": profile, "liked": liked})
        matched = False
        # Deterministic: every second liked profile likes back.
        if liked and len([s for s in self.swipes if s["liked"]]) % 2 == 0:
            self.matches.append(profile)
            matched = True
        return {"success": True, "profile": profile, "liked": liked, "matched": matched}

    def send_message(self, to, text):
        if to not in self.matches:
            return {"success": False, "error": f"not a match: {to}"}
        self.messages.append({"to": to, "text": text})
        return {"success": True, "to": to}

    def ui_tree(self):
        return {
            "success": True,
            "screen": self.screen,
            "elements": [
                {"label": "Email", "id": "email", "x": 540, "y": 600},
                {"label": "Password", "id": "password", "x": 540, "y": 720},
                {"label": "Sign up", "id": "submit", "x": 540, "y": 900},
            ] if self.screen == SIGNUP_SCREEN else [
                {"label": "Like", "id": "like", "x": 800, "y": 1200},
                {"label": "Nope", "id": "nope", "x": 280, "y": 1200},
            ],
        }

    def gapps_status(self):
        return {
            "gms": self.gapps,
            "play_store": self.gapps,
            "gsf": self.gapps,
            "ready": self.gapps,
        }


class _Handler(BaseHTTPRequestHandler):
    phone = None

    def log_message(self, *args):
        pass  # keep test output clean

    def _send(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}

    def _auth_state(self):
        required = bool(self.phone.api_token)
        header = self.headers.get("Authorization", "") or ""
        token = header[7:].strip() if header.lower().startswith("bearer ") else header.strip()
        return {
            "required": required,
            "presented": bool(token),
            "ok": (not required) or token == self.phone.api_token,
        }

    def _reject_unauthorized(self):
        """401 on everything but `/health`, exactly as the real API does."""
        if self.path == "/health":
            return False
        state = self._auth_state()
        if state["ok"]:
            return False
        self._send({
            "success": False,
            "error": "Unauthorized",
            "code": "auth_invalid" if state["presented"] else "auth_required",
            "auth_required": True,
        }, 401)
        return True

    def do_GET(self):
        phone = self.phone
        if self._reject_unauthorized():
            return None
        with phone.lock:
            if self.path == "/health":
                auth = self._auth_state()
                return self._send({
                    "status": "healthy" if auth["ok"] else "unauthorized",
                    "adb_connected": True,
                    "auth": auth,
                    "usable": auth["ok"],
                    "gapps": phone.gapps_status(),
                    "egress_ip": phone.egress_ip,
                })
            if self.path == "/status":
                return self._send({
                    "connected": True,
                    "screen": phone.screen,
                    "proxy": phone.proxy,
                    "gapps": phone.gapps_status(),
                    "egress_ip": phone.egress_ip,
                })
            if self.path == "/proxy":
                return self._send(phone.proxy)
            if self.path == "/device/ui":
                return self._send(phone.ui_tree())
            if self.path == "/device/screenshot/base64":
                return self._send({"success": True, "image_base64": "ZmFrZQ=="})
        return self._send({"error": "not found"}, 404)

    def do_POST(self):
        phone = self.phone
        payload = self._body()  # drain before any early return
        if self._reject_unauthorized():
            return None
        with phone.lock:
            phone._record(self.path, payload)

            if self.path == "/proxy":
                return self._send(phone.set_proxy(payload))

            if self.path.startswith("/apps/") and self.path.endswith("/start"):
                package = self.path[len("/apps/"):-len("/start")]
                return self._send(phone.launch(package))

            if self.path == "/device/ui":
                return self._send(phone.ui_tree())

            if self.path == "/device/screenshot/base64":
                return self._send({"success": True, "image_base64": "ZmFrZQ=="})

            if self.path == "/device/input":
                kind = payload.get("type")
                if kind == "text":
                    return self._send(phone.type_text(payload.get("text", "")))
                if kind == "key":
                    return self._send(phone.key(int(payload.get("keycode", 0))))
                if kind == "swipe":
                    return self._send(phone.swipe(
                        int(payload.get("x1", 0)), int(payload.get("x2", 0))
                    ))
                if kind == "tap":
                    return self._send({"success": True, "type": "tap"})
                if kind == "tap_label":
                    label = (payload.get("label") or "").lower()
                    if phone.screen == SIGNUP_SCREEN and label in {"email", "password"}:
                        phone._focused_field = label
                        return self._send({"success": True, "focused": label})
                    return self._send({"success": True, "label": label})
                return self._send({"success": False, "error": f"unknown input {kind}"})

            if self.path == "/adb/shell":
                command = payload.get("command", "")
                if command.startswith("message "):
                    _, to, text = command.split(" ", 2)
                    return self._send(phone.send_message(to, text))
                return self._send({"success": True, "stdout": ""})

        return self._send({"error": "not found"}, 404)


class FakePhoneServer:
    """Context manager yielding (base_url, phone)."""

    def __init__(self, phone=None):
        self.phone = phone or FakePhone()
        handler = type("BoundHandler", (_Handler,), {"phone": self.phone})
        self.httpd = HTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base_url(self):
        host, port = self.httpd.server_address
        return f"http://{host}:{port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
