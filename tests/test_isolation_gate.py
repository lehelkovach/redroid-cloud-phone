#!/usr/bin/env python3
"""The two-owner isolation gate, through a live orchestrator in mock mode.

Owners A and B each lease a browser and a phone. Every way one could reach
the other's is tried, through the real relay over real sockets: a ticket used
on the wrong lease, a forged container address, a revoked or expired ticket
mid-stream, a restart. Any crossover fails the suite.

The last class shows the gate is not vacuous: point both owners at one shared
browser (what OSLO's single `oslo-chrome.service` is today) and the same
detector that passes above fires.
"""

import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest

import requests
import simple_websocket

from api.cloudphone_logging import recent_logs
from orchestrator import browser_pool as bp
from orchestrator import server as orch
from tests.fixtures.isolation_harness import (
    FAKE_ORCH_TOKEN,
    OWNER_A,
    OWNER_B,
    Harness,
    cdp_call,
)

SECRET_COOKIE = "sess-a-fake-0000"


def logged_in(ws, msg_id=900):
    """The detector: does this browser hold a session cookie?"""
    reply = cdp_call(ws, msg_id, "Network.getAllCookies")
    return any(c["name"] == "session" for c in reply["result"].get("cookies", []))


def wait_closed(ws, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            ws.receive(timeout=0.2)
        except simple_websocket.ConnectionClosed:
            return True
    return False


class TwoOwnerGate(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.a = self.h.browser(OWNER_A).json()
        self.b = self.h.browser(OWNER_B).json()
        self.pa = self.h.phone_lease(OWNER_A).json()
        self.pb = self.h.phone_lease(OWNER_B).json()
        self._open = []

    def tearDown(self):
        for ws in self._open:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass
        self.h.close()

    def connect(self, lease, ticket):
        ws = self.h.cdp_connect(lease["relay"]["cdp_ws_url"], ticket)
        self._open.append(ws)
        return ws

    # 1 & 2 ----------------------------------------------------------------
    def test_browser_leases_are_separate_and_hide_container_addresses(self):
        sa, sb = self.a["session"], self.b["session"]
        self.assertNotEqual(sa["lease_id"], sb["lease_id"])
        self.assertNotEqual(sa["profile_dir"], sb["profile_dir"])
        self.assertNotEqual(self.a["relay"]["cdp_ws_url"], self.b["relay"]["cdp_ws_url"])
        for body in (self.a, self.b):
            text = json.dumps(body)
            self.assertNotIn("cdp_port", text)
            self.assertNotIn("cdp_ws_internal", text)
            self.assertNotIn(self.h.cdp.ws_url, text, "a caller must never learn the browser's address")
            self.assertIn("/relay/cdp/", body["relay"]["cdp_ws_url"])
            self.assertEqual(stat.S_IMODE(os.stat(body["session"]["profile_dir"]).st_mode), 0o700)

    def test_a_logs_in_and_b_is_not_logged_in(self):
        wa = self.connect(self.a, self.a["ticket"])
        wb = self.connect(self.b, self.b["ticket"])
        cdp_call(wa, 1, "Network.setCookie", {"name": "session", "value": SECRET_COOKIE})
        self.assertTrue(logged_in(wa))
        self.assertFalse(logged_in(wb), "B's browser carries A's session: crossover")
        # Each reply came from that owner's own browser.
        self.assertEqual(cdp_call(wa, 2, "Browser.getVersion")["result"]["browser"], self.a["session"]["owner_token"])
        self.assertEqual(cdp_call(wb, 2, "Browser.getVersion")["result"]["browser"], self.b["session"]["owner_token"])

    def test_b_ticket_cannot_attach_to_a_browser(self):
        with self.assertRaises(Exception):
            self.connect(self.a, self.b["ticket"])
        r = requests.get(self.a["relay"]["cdp_ws_url"].replace("ws://", "http://"),
                         headers={"X-Orch-Ticket": self.b["ticket"]}, timeout=5)
        self.assertNotEqual(r.status_code, 101)
        # A phone ticket is not a browser ticket either.
        with self.assertRaises(Exception):
            self.connect(self.a, self.pa["ticket"])

    def test_cross_owner_lease_request_refused_not_reassigned(self):
        steal = self.h.browser(OWNER_B, lease_id=self.a["session"]["lease_id"])
        self.assertEqual(steal.status_code, 403, steal.text)
        self.assertNotIn(OWNER_A, steal.text, "a refusal must not name the holder")
        mint = requests.post(f"{self.h.base}/tickets", headers=self.h.headers(), timeout=5, json={
            "owner_user_id": OWNER_B, "purpose": "browser", "lease_id": self.a["session"]["lease_id"]})
        self.assertEqual(mint.status_code, 403, mint.text)
        mine = requests.get(f"{self.h.base}/sessions/{OWNER_A}?purpose=browser", headers=self.h.headers(), timeout=5)
        self.assertEqual(mine.json()["lease_id"], self.a["session"]["lease_id"])

    def test_capacity_refuses_rather_than_evicting(self):
        orch.ORCH_MAX_CHROME_INSTANCES = 2
        r = self.h.browser("slack:U0FAKEC")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "at_capacity")
        self.assertEqual(len(orch._browser_sessions), 2)

    # 5 --------------------------------------------------------------------
    def test_phone_relay_routes_by_ticket_and_ignores_forged_addresses(self):
        adb_a = self.pa["session"]["adb_connect"]
        adb_b = self.pb["session"]["adb_connect"]
        self.assertNotEqual(adb_a, adb_b)
        self.assertNotEqual(self.pa["session"]["lease_id"], self.pb["session"]["lease_id"])
        self.h.phone_hits.clear()
        forged = {
            "type": "tap", "x": 1, "y": 2,
            "instance_id": self.pb["session"]["instance_id"],
            "api_url": "http://127.0.0.1:1/evil",
            "adb_connect": adb_b,
        }
        r = requests.post(f"{self.h.base}/relay/phone/device/input?instance_id={self.pb['session']['instance_id']}",
                          json=forged, timeout=5,
                          headers={"X-Orch-Ticket": self.pa["ticket"], "X-Cloud-Phone-Adb": adb_b})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["phone"], adb_a, "forged address steered the relay")
        self.assertEqual([h[2] for h in self.h.phone_hits], [adb_a])
        rb = requests.get(f"{self.h.base}/relay/phone/health", headers={"X-Orch-Ticket": self.pb["ticket"]}, timeout=5)
        self.assertEqual(rb.json()["phone"], adb_b)

    def test_relay_refuses_without_a_valid_phone_ticket(self):
        for headers in ({}, {"Authorization": f"Bearer {FAKE_ORCH_TOKEN}"}, {"X-Orch-Ticket": self.a["ticket"]},
                        {"X-Orch-Ticket": "ort_forged"}):
            r = requests.get(f"{self.h.base}/relay/phone/health", headers=headers, timeout=5)
            self.assertIn(r.status_code, (401, 403), (headers.keys(), r.text))

    def test_config_and_traversal_not_relayed(self):
        for path in ("config", "device/../config"):
            r = requests.get(f"{self.h.base}/relay/phone/{path}", headers={"X-Orch-Ticket": self.pa["ticket"]}, timeout=5)
            self.assertIn(r.status_code, (403, 404), path)
        self.assertFalse([h for h in self.h.phone_hits if h[1] == "/config"])

    def test_b_ticket_streams_only_b_phone(self):
        ws = simple_websocket.Client.connect(self.pb["relay"]["phone_stream_url"],
                                             headers={"X-Orch-Ticket": self.pb["ticket"]})
        self._open.append(ws)
        frame = json.loads(ws.receive(timeout=5))
        self.assertEqual(frame["frame"]["phone"], self.pb["session"]["adb_connect"])
        with self.assertRaises(Exception):
            simple_websocket.Client.connect(self.pa["relay"]["phone_stream_url"],
                                            headers={"X-Orch-Ticket": self.b["ticket"]})

    # mid-stream revocation, expiry, kill switch ------------------------------
    def test_revoked_ticket_is_dropped_mid_stream(self):
        wa = self.connect(self.a, self.a["ticket"])
        cdp_call(wa, 1, "Page.startScreencast")
        self.assertIn("screencastFrame", wa.receive(timeout=2))
        r = requests.post(f"{self.h.base}/tickets/revoke", json={"ticket": self.a["ticket"]},
                          headers=self.h.headers(), timeout=5)
        self.assertTrue(r.json()["revoked"])
        self.assertTrue(wait_closed(wa), "Watch stream kept flowing after the ticket was revoked")
        with self.assertRaises(Exception):
            self.connect(self.a, self.a["ticket"])

    def test_expired_ticket_is_dropped_mid_stream(self):
        wa = self.connect(self.a, self.a["ticket"])
        cdp_call(wa, 1, "Page.startScreencast")
        self.assertIn("screencastFrame", wa.receive(timeout=2))
        real = time.time
        orch._tickets.now = lambda: real() + 3600  # the ticket's TTL has passed
        try:
            self.assertTrue(wait_closed(wa), "expired ticket kept streaming")
        finally:
            orch._tickets.now = real

    def test_kill_switch_drops_only_that_owners_streams(self):
        wa = self.connect(self.a, self.a["ticket"])
        wb = self.connect(self.b, self.b["ticket"])
        pa_ws = simple_websocket.Client.connect(self.pa["relay"]["phone_stream_url"],
                                                headers={"X-Orch-Ticket": self.pa["ticket"]})
        self._open.append(pa_ws)
        pa_ws.receive(timeout=5)
        cdp_call(wa, 1, "Browser.getVersion")
        r = requests.post(f"{self.h.base}/sessions/{OWNER_A}/kill", json={"purpose": "browser"},
                          headers=self.h.headers(), timeout=5)
        self.assertEqual(r.json()["streams_dropped"], 1)
        self.assertTrue(wait_closed(wa))
        r = requests.post(f"{self.h.base}/sessions/{OWNER_A}/kill", json={}, headers=self.h.headers(), timeout=5)
        self.assertEqual(r.json()["streams_dropped"], 1)
        self.assertTrue(wait_closed(pa_ws))
        self.assertFalse(logged_in(wb, 7), "B's stream must survive A's kill switch")

    def test_release_revokes_tickets_and_keeps_profile(self):
        profile = self.a["session"]["profile_dir"]
        open(os.path.join(profile, "Cookies"), "w").close()
        r = requests.delete(f"{self.h.base}/sessions/{OWNER_A}?purpose=browser", headers=self.h.headers(), timeout=5)
        self.assertTrue(r.json()["profile_kept"])
        self.assertTrue(os.path.exists(os.path.join(profile, "Cookies")))
        with self.assertRaises(Exception):
            self.connect(self.a, self.a["ticket"])
        again = self.h.browser(OWNER_A).json()
        self.assertEqual(again["session"]["profile_dir"], profile, "the owner gets their own profile back")
        self.assertNotEqual(again["session"]["lease_id"], self.a["session"]["lease_id"])

    def test_profile_delete_is_explicit(self):
        url = f"{self.h.base}/profiles/{OWNER_A}"
        r = requests.delete(url, json={"confirm": OWNER_A}, headers=self.h.headers(), timeout=5)
        self.assertEqual(r.status_code, 409, "deleting a leased profile must be refused")
        requests.delete(f"{self.h.base}/sessions/{OWNER_A}?purpose=browser", headers=self.h.headers(), timeout=5)
        r = requests.delete(url, json={"confirm": OWNER_B}, headers=self.h.headers(), timeout=5)
        self.assertEqual(r.status_code, 400)
        r = requests.delete(url, json={"confirm": OWNER_A}, headers=self.h.headers(), timeout=5)
        self.assertTrue(r.json()["deleted"])
        self.assertTrue(os.path.isdir(self.b["session"]["profile_dir"]))

    def test_rate_limit_is_per_lease(self):
        orch._limiter.rate = 1.0
        orch._limiter.burst = 3.0
        orch._limiter._buckets.clear()
        wa = self.connect(self.a, self.a["ticket"])
        replies = [cdp_call(wa, i, "Runtime.evaluate") for i in range(1, 7)]
        limited = [r for r in replies if "error" in r]
        self.assertTrue(limited, "no command was rate limited")
        self.assertIn("rate limited", limited[0]["error"]["message"])
        wb = self.connect(self.b, self.b["ticket"])
        self.assertNotIn("error", cdp_call(wb, 1, "Runtime.evaluate"), "A's flood throttled B")

    def test_audit_has_one_line_per_command_and_no_payloads(self):
        orch._audit.lines.clear()
        wa = self.connect(self.a, self.a["ticket"])
        cdp_call(wa, 1, "Network.setCookie", {"name": "session", "value": SECRET_COOKIE})
        cdp_call(wa, 2, "Input.insertText", {"text": "hunter2-fake"})
        requests.post(f"{self.h.base}/relay/phone/device/input", json={"type": "text", "text": "pin-0000-fake"},
                      headers={"X-Orch-Ticket": self.pa["ticket"]}, timeout=5)
        lines = [l for l in orch._audit.lines if not l["verb"].startswith("stream.")]
        self.assertEqual([l["verb"] for l in lines], ["Network.setCookie", "Input.insertText", "POST device/input"])
        for line in lines:
            self.assertEqual(line["owner"], bp.owner_token(OWNER_A))
            self.assertIn("ms", line)
        blob = json.dumps(list(orch._audit.lines)) + json.dumps(recent_logs(n=4000))
        for secret in (SECRET_COOKIE, "hunter2-fake", "pin-0000-fake", self.a["ticket"], self.pa["ticket"]):
            self.assertNotIn(secret, blob)

    # 6 --------------------------------------------------------------------
    def test_each_lease_exits_through_its_own_owners_proxy_or_direct(self):
        requests.delete(f"{self.h.base}/sessions/{OWNER_A}?purpose=browser", headers=self.h.headers(), timeout=5)
        a = self.h.browser(OWNER_A, proxy={"id": "owner-a-resi",
                                           "upstream": "http://user-a:pass-a-fake@proxy-a.example.test:8000"}).json()
        self.assertEqual(a["session"]["egress"], "owner")
        self.assertEqual(a["session"]["proxy_id"], "owner-a-resi")
        self.assertEqual(a["session"]["proxy_upstream"], "http://proxy-a.example.test:8000")
        self.assertEqual(self.b["session"]["egress"], "direct")
        self.assertIsNone(self.b["session"]["proxy_upstream"])
        with open(self.h.state_db, "rb") as fh:
            self.assertNotIn(b"pass-a-fake", fh.read(), "a proxy credential was written to disk")
        changed = self.h.browser(OWNER_A, proxy={"id": "other", "upstream": "http://proxy-b.example.test:9000"})
        self.assertEqual(changed.status_code, 409)

    # 7 --------------------------------------------------------------------
    def test_restart_keeps_every_lease_with_its_owner(self):
        revoked = requests.post(f"{self.h.base}/tickets", headers=self.h.headers(), timeout=5,
                                json={"owner_user_id": OWNER_B, "purpose": "browser"}).json()["ticket"]
        requests.post(f"{self.h.base}/tickets/revoke", json={"ticket": revoked}, headers=self.h.headers(), timeout=5)
        self.h.restart()
        wa = self.connect(self.a, self.a["ticket"])
        self.assertEqual(cdp_call(wa, 1, "Browser.getVersion")["result"]["browser"], self.a["session"]["owner_token"])
        with self.assertRaises(Exception):
            self.connect(self.b, revoked)
        with self.assertRaises(Exception):
            self.connect(self.a, self.b["ticket"])
        self.assertEqual(self.h.browser(OWNER_B, lease_id=self.a["session"]["lease_id"]).status_code, 403)
        r = requests.get(f"{self.h.base}/relay/phone/health", headers={"X-Orch-Ticket": self.pa["ticket"]}, timeout=5)
        self.assertEqual(r.json()["phone"], self.pa["session"]["adb_connect"])
        sessions = requests.get(f"{self.h.base}/sessions", headers=self.h.headers(), timeout=5).json()
        owners = {s["owner_user_id"]: s["lease_id"] for s in sessions["browser_sessions"]}
        self.assertEqual(owners, {OWNER_A: self.a["session"]["lease_id"], OWNER_B: self.b["session"]["lease_id"]})

    def test_require_tickets_closes_direct_phone_routes(self):
        orch.ORCH_REQUIRE_TICKETS = True
        try:
            inst = self.pa["session"]["instance_id"]
            r = requests.get(f"{self.h.base}/phones/{inst}/health", headers=self.h.headers(), timeout=5)
            self.assertEqual(r.status_code, 403)
            self.assertEqual(r.json()["code"], "tickets_required")
        finally:
            orch.ORCH_REQUIRE_TICKETS = False


class GateCatchesASharedBrowser(unittest.TestCase):
    """Negative control: with one shared browser, the detector above must fire."""

    def test_shared_browser_is_detected(self):
        h = Harness()
        try:
            shared = h.cdp.ws_url + "/devtools/browser/the-one-shared-chrome"
            orch.set_chrome_runtime(bp.MockChromeRuntime(upstream_for=lambda sess: shared))
            a = h.browser(OWNER_A).json()
            b = h.browser(OWNER_B).json()
            wa = h.cdp_connect(a["relay"]["cdp_ws_url"], a["ticket"])
            wb = h.cdp_connect(b["relay"]["cdp_ws_url"], b["ticket"])
            cdp_call(wa, 1, "Network.setCookie", {"name": "session", "value": SECRET_COOKIE})
            self.assertTrue(logged_in(wb), "detector failed to see a shared browser: the gate is vacuous")
            wa.close()
            wb.close()
        finally:
            h.close()


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class RealProcessRestart(unittest.TestCase):
    """The same promise across a real process restart of orchestrator/server.py."""

    def _start(self, env):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        proc = subprocess.Popen([sys.executable, os.path.join(root, "orchestrator", "server.py")], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, cwd=root)
        base = f"http://127.0.0.1:{env['ORCH_PORT']}"
        for _ in range(100):
            try:
                if requests.get(base + "/health", timeout=0.5).ok:
                    return proc, base
            except requests.RequestException:
                time.sleep(0.1)
            if proc.poll() is not None:
                break
        proc.kill()
        raise AssertionError("orchestrator did not start: " + proc.stderr.read().decode()[-400:])

    def test_restart_and_bind_policy(self):
        tmp = tempfile.mkdtemp(prefix="orch-proc-")
        env = {k: v for k, v in os.environ.items() if not k.startswith("ORCH_")}
        env.update({"ORCH_HOST": "127.0.0.1", "ORCH_PORT": str(_free_port()), "ORCH_DEPLOY_MODE": "mock",
                    "ORCH_API_TOKEN": FAKE_ORCH_TOKEN, "ORCH_STATE_DB": os.path.join(tmp, "s.sqlite3"),
                    "ORCH_PROFILE_ROOT": os.path.join(tmp, "profiles"), "LOG_LEVEL": "WARNING"})
        auth = {"Authorization": f"Bearer {FAKE_ORCH_TOKEN}"}

        refused = subprocess.run([sys.executable, "orchestrator/server.py"], capture_output=True, timeout=30,
                                 env={**env, "ORCH_API_TOKEN": ""},
                                 cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.assertEqual(refused.returncode, 2, "started open without ORCH_API_TOKEN")
        wildcard = subprocess.run([sys.executable, "orchestrator/server.py"], capture_output=True, timeout=30,
                                  env={**env, "ORCH_HOST": "0.0.0.0"},
                                  cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.assertEqual(wildcard.returncode, 2, "bound every interface without being asked to")

        proc, base = self._start(env)
        try:
            a = requests.post(base + "/sessions", headers=auth, timeout=5,
                              json={"owner_user_id": OWNER_A, "purpose": "browser"}).json()
            b = requests.post(base + "/sessions", headers=auth, timeout=5,
                              json={"owner_user_id": OWNER_B, "ttl_seconds": 600}).json()
        finally:
            proc.terminate()
            proc.wait(timeout=10)
        proc, base = self._start(env)
        try:
            got = requests.get(f"{base}/sessions/{OWNER_A}?purpose=browser", headers=auth, timeout=5).json()
            self.assertEqual(got["lease_id"], a["session"]["lease_id"])
            self.assertEqual(got["profile_dir"], a["session"]["profile_dir"])
            phone = requests.get(f"{base}/sessions/{OWNER_B}", headers=auth, timeout=5).json()
            self.assertEqual(phone["instance_id"], b["session"]["instance_id"])
            steal = requests.post(base + "/sessions", headers=auth, timeout=5, json={
                "owner_user_id": OWNER_B, "purpose": "browser", "lease_id": a["session"]["lease_id"]})
            self.assertEqual(steal.status_code, 403)
            # A's ticket survived the restart: valid ticket, mock browser -> 503, not 401.
            r = requests.get(f"{base}/relay/cdp/{a['session']['lease_id']}", timeout=5, headers={
                "X-Orch-Ticket": a["ticket"], "Connection": "Upgrade", "Upgrade": "websocket",
                "Sec-WebSocket-Version": "13", "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ=="})
            self.assertEqual(r.status_code, 503, r.text)
            r = requests.get(f"{base}/relay/cdp/{a['session']['lease_id']}", timeout=5, headers={
                "X-Orch-Ticket": b["ticket"], "Connection": "Upgrade", "Upgrade": "websocket",
                "Sec-WebSocket-Version": "13", "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ=="})
            self.assertIn(r.status_code, (401, 403))
        finally:
            proc.terminate()
            proc.wait(timeout=10)


def _find_chrome():
    if os.environ.get("ORCH_CHROME_BIN"):
        return os.environ["ORCH_CHROME_BIN"]
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", os.path.expanduser("~/.cache/ms-playwright"))
    try:
        for d in sorted(os.listdir(root), reverse=True):
            if d.startswith("chromium-"):
                for rel in ("chrome-linux/chrome", "chrome-linux64/chrome"):
                    path = os.path.join(root, d, rel)
                    if os.access(path, os.X_OK):
                        return path
    except OSError:
        pass
    return ""


CHROME = _find_chrome()


@unittest.skipUnless(CHROME, "no Chromium binary (ORCH_CHROME_BIN or PLAYWRIGHT_BROWSERS_PATH)")
class RealChromeIsolation(unittest.TestCase):
    """Two real Chromium processes, one profile each, reached only through the relay."""

    def test_real_profiles_do_not_share_cookies(self):
        h = Harness()
        try:
            orch.set_chrome_runtime(bp.LocalChromeRuntime(CHROME))
            a = h.browser(OWNER_A).json()
            b = h.browser(OWNER_B).json()
            self.assertNotEqual(a["session"]["profile_dir"], b["session"]["profile_dir"])
            wa = h.cdp_connect(a["relay"]["cdp_ws_url"], a["ticket"])
            wb = h.cdp_connect(b["relay"]["cdp_ws_url"], b["ticket"])
            cdp_call(wa, 1, "Storage.setCookies", {"cookies": [
                {"name": "session", "value": SECRET_COOKIE, "domain": "login.example.test", "path": "/"}]})
            got_a = cdp_call(wa, 2, "Storage.getCookies")["result"]["cookies"]
            got_b = cdp_call(wb, 2, "Storage.getCookies")["result"]["cookies"]
            self.assertTrue(any(c["value"] == SECRET_COOKIE for c in got_a))
            self.assertFalse(any(c["value"] == SECRET_COOKIE for c in got_b), "B's real browser has A's cookie")
            with self.assertRaises(Exception):
                h.cdp_connect(a["relay"]["cdp_ws_url"], b["ticket"])
            wa.close()
            wb.close()
            for owner in (OWNER_A, OWNER_B):
                requests.delete(f"{h.base}/sessions/{owner}?purpose=browser", headers=h.headers(), timeout=10)
        finally:
            h.close()


if __name__ == "__main__":
    unittest.main()
