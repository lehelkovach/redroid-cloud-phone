#!/usr/bin/env python3
"""Units of multi-user isolation: state, profiles, tickets, rate limits, start-up policy.

Two owners must never share a browser, a profile, a proxy exit, a phone or a
ticket. These tests pin the rules one at a time; tests/test_isolation_gate.py
drives them together through a live orchestrator. Mock runtime, fake values.
"""

import os
import socket
import socketserver
import stat
import tempfile
import threading
import types
import unittest

from orchestrator import browser_pool as bp
from orchestrator import relay as rly
from orchestrator import server as orch
from orchestrator.proxy_relay import LeaseProxyRelay, parse_upstream
from orchestrator.state_store import StateStore, redact_upstream


class OwnerTokenTests(unittest.TestCase):
    def test_matches_oslo_owner_token(self):
        # Vectors computed with OSLO's ownerToken (src/identity/browser_lease.mjs):
        # a profile here and a cookie jar there carry the same name.
        self.assertEqual(bp.owner_token("slack:U0FAKE1"), "slack-U0FAKE1-27558faf")
        self.assertEqual(bp.owner_token("a-b"), "a-b-d44362d6")
        self.assertEqual(bp.owner_token("a:b"), "a-b-6783a31e")
        self.assertEqual(bp.owner_token(".."), "5ec1f7e7")

    def test_one_slug_two_owners_two_tokens(self):
        self.assertNotEqual(bp.owner_token("a:b"), bp.owner_token("a-b"))
        self.assertEqual(bp.owner_token(""), "")


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.root = os.path.join(tempfile.mkdtemp(prefix="orch-prof-"), "profiles")

    def test_profile_is_0700_and_per_owner(self):
        a = bp.ensure_profile_dir(self.root, "owner-a")
        b = bp.ensure_profile_dir(self.root, "owner-b")
        self.assertNotEqual(a, b)
        for path in (self.root, a, b):
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o700, path)
        self.assertEqual(os.path.dirname(a), self.root)

    def test_loosened_profile_is_tightened(self):
        a = bp.ensure_profile_dir(self.root, "owner-a")
        os.chmod(a, 0o755)
        bp.ensure_profile_dir(self.root, "owner-a")
        self.assertEqual(stat.S_IMODE(os.stat(a).st_mode), 0o700)

    def test_symlinked_profile_is_refused(self):
        b = bp.ensure_profile_dir(self.root, "owner-b")
        a_path = bp.profile_dir_for(self.root, "owner-a")
        os.symlink(b, a_path)  # someone points A's profile at B's
        with self.assertRaises(bp.LeaseError) as ctx:
            bp.ensure_profile_dir(self.root, "owner-a")
        self.assertEqual(ctx.exception.code, "profile_not_dir")

    def test_dotdot_owner_stays_under_root(self):
        path = bp.ensure_profile_dir(self.root, "..")
        self.assertEqual(os.path.dirname(path), self.root)

    def test_delete_profile_only_touches_that_owner(self):
        a = bp.ensure_profile_dir(self.root, "owner-a")
        b = bp.ensure_profile_dir(self.root, "owner-b")
        open(os.path.join(b, "Cookies"), "w").close()
        self.assertTrue(bp.delete_profile_dir(self.root, "owner-a"))
        self.assertFalse(os.path.exists(a))
        self.assertTrue(os.path.exists(os.path.join(b, "Cookies")))


class EncryptionCheckTests(unittest.TestCase):
    MOUNTINFO = (
        "22 1 8:1 / / rw,relatime - ext4 /dev/sda1 rw\n"
        "40 22 253:0 / /var/lib/cloud-phone-orchestrator rw - ext4 /dev/mapper/orchprofiles rw\n"
    )

    def _reader(self, uuid):
        def read(path):
            if path == "/proc/self/mountinfo":
                return self.MOUNTINFO
            if path == "/sys/block/dm-0/dm/uuid":
                return uuid
            raise OSError(path)
        return read

    def test_none_is_explicitly_unchecked(self):
        ok, detail = bp.check_profile_encryption("/x", "none")
        self.assertTrue(ok)
        self.assertIn("not required", detail)

    def test_luks_volume_accepted(self):
        ok, detail = bp.check_profile_encryption(
            "/var/lib/cloud-phone-orchestrator/profiles", "luks",
            read_text=self._reader("CRYPT-LUKS2-abc-orchprofiles"), realpath=lambda p: "/dev/dm-0")
        self.assertTrue(ok, detail)

    def test_plain_dm_refused(self):
        ok, _ = bp.check_profile_encryption(
            "/var/lib/cloud-phone-orchestrator/profiles", "luks",
            read_text=self._reader("LVM-xyz"), realpath=lambda p: "/dev/dm-0")
        self.assertFalse(ok)

    def test_unencrypted_disk_refused(self):
        ok, detail = bp.check_profile_encryption(
            "/srv/profiles", "luks", read_text=self._reader("CRYPT-x"), realpath=lambda p: p)
        self.assertFalse(ok)
        self.assertIn("/dev/sda1", detail)

    def test_fscrypt(self):
        enc = types.SimpleNamespace(returncode=0, stdout='"/p" is encrypted with fscrypt.', stderr="")
        plain = types.SimpleNamespace(returncode=1, stdout="", stderr='"/p" is not encrypted')
        self.assertTrue(bp.check_profile_encryption("/p", "fscrypt", run=lambda argv: enc)[0])
        self.assertFalse(bp.check_profile_encryption("/p", "fscrypt", run=lambda argv: plain)[0])

    def test_unknown_mode_refused(self):
        self.assertFalse(bp.check_profile_encryption("/p", "rot13")[0])


class DockerArgvTests(unittest.TestCase):
    def test_only_this_owners_profile_and_a_loopback_port(self):
        sess = {"owner_token": "owner-a-1234abcd", "cdp_port": 19222, "lease_id": "bl-1",
                "profile_dir": "/var/lib/p/owner-a-1234abcd"}
        argv = bp.docker_run_argv(sess, image="chrome:fake", network="browsers",
                                  proxy_server="http://127.0.0.1:41000")
        mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
        self.assertEqual(mounts, ["/var/lib/p/owner-a-1234abcd:/data/profile:rw"])
        ports = [argv[i + 1] for i, a in enumerate(argv) if a == "-p"]
        self.assertEqual(ports, ["127.0.0.1:19222:9222"], "DevTools must never be published off loopback")
        self.assertIn("--proxy-server=http://127.0.0.1:41000", argv)

    def test_credentials_never_reach_chrome_flags(self):
        with self.assertRaises(ValueError):
            bp.proxy_server_arg("http://user:secret@proxy.example.test:8000")


class StateStoreTests(unittest.TestCase):
    def test_round_trip_and_file_mode(self):
        path = os.path.join(tempfile.mkdtemp(prefix="orch-state-"), "s.sqlite3")
        store = StateStore(path)
        store.put("leases", "i1", {"owner": "a", "expires_at": 1.0})
        store.close()
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        again = StateStore(path)
        self.assertEqual(again.load("leases"), {"i1": {"owner": "a", "expires_at": 1.0}})

    def test_unknown_namespace_refused(self):
        with self.assertRaises(ValueError):
            StateStore().put("secrets", "k", {})

    def test_redact_upstream_drops_credentials(self):
        self.assertEqual(redact_upstream("http://user:pw@proxy.example.test:8000"), "http://proxy.example.test:8000")
        self.assertIsNone(redact_upstream(""))


class TicketTests(unittest.TestCase):
    def setUp(self):
        self.t = [1000.0]
        self.alive = {"bl-a": True}
        self.book = rly.TicketBook(now=lambda: self.t[0],
                                   lease_check=lambda r: (self.alive.get(r["lease_id"], False), "gone"))

    def mint(self, **kw):
        args = dict(owner="a", owner_token="a-tok", lease_id="bl-a", kind="browser", ttl=60)
        args.update(kw)
        return self.book.mint(**args)

    def test_live_ticket_checks(self):
        tok, rec = self.mint()
        self.assertTrue(tok.startswith("ort_"))
        self.assertEqual(self.book.check(tok, kind="browser", lease_id="bl-a")["owner"], "a")
        self.assertNotIn(tok, repr(rec), "a minted record must not carry the bearer")

    def test_expired_revoked_wrong_lease_wrong_kind(self):
        tok, _ = self.mint()
        with self.assertRaises(rly.TicketError) as e:
            self.book.check(tok, lease_id="bl-b")
        self.assertEqual(e.exception.code, "ticket_wrong_lease")
        with self.assertRaises(rly.TicketError) as e:
            self.book.check(tok, kind="phone")
        self.assertEqual(e.exception.code, "ticket_wrong_kind")
        self.t[0] += 61
        with self.assertRaises(rly.TicketError) as e:
            self.book.check(tok)
        self.assertEqual(e.exception.code, "ticket_expired")
        tok2, _ = self.mint()
        self.book.revoke(tok2)
        with self.assertRaises(rly.TicketError) as e:
            self.book.check(tok2)
        self.assertEqual(e.exception.code, "ticket_revoked")

    def test_ticket_never_outlives_its_lease(self):
        _, rec = self.mint(ttl=3600, not_after=1010.0)
        self.assertEqual(rec["expires_at"], 1010.0)

    def test_released_lease_kills_ticket(self):
        tok, _ = self.mint()
        self.alive["bl-a"] = False
        with self.assertRaises(rly.TicketError) as e:
            self.book.check(tok)
        self.assertEqual(e.exception.code, "lease_gone")

    def test_store_holds_digests_not_bearers(self):
        store = StateStore()
        book = rly.TicketBook(store=store)
        tok, _ = book.mint(owner="a", owner_token="t", lease_id="l", kind="phone")
        rows = store.load("tickets")
        self.assertEqual(len(rows), 1)
        self.assertNotIn(tok, repr(rows))
        self.assertIn(rly.digest(tok), rows)

    def test_ticket_sources(self):
        self.assertEqual(rly.ticket_from_request({"X-Orch-Ticket": "ort_x"}, {}), "ort_x")
        self.assertEqual(rly.ticket_from_request({"Authorization": "Bearer ort_y"}, {}), "ort_y")
        # The orchestrator API token in Authorization is NOT a ticket.
        self.assertEqual(rly.ticket_from_request({"Authorization": "Bearer api-token"}, {}), "")
        self.assertEqual(rly.ticket_from_request({}, {"ticket": "ort_z"}), "ort_z")


class RateAndAuditTests(unittest.TestCase):
    def test_bucket_per_lease(self):
        t = [0.0]
        lim = rly.RateLimiter(rate_per_sec=1, burst=2, now=lambda: t[0])
        self.assertTrue(lim.allow("a"))
        self.assertTrue(lim.allow("a"))
        self.assertFalse(lim.allow("a"))
        self.assertTrue(lim.allow("b"), "one lease's burst must not throttle another's")
        t[0] += 1.0
        self.assertTrue(lim.allow("a"))

    def test_audit_fields_are_fixed_and_verb_capped(self):
        audit = rly.Audit()
        e = audit.record(owner="o", lease="l", kind="cdp", verb="Input.insertText" + "x" * 500, ms=1.234, ok=True)
        self.assertEqual(set(e), set(rly.Audit.FIELDS) | {"at"})
        self.assertLessEqual(len(e["verb"]), 64)


class StartupPolicyTests(unittest.TestCase):
    def test_no_token_refused_unless_loopback_dev(self):
        self.assertIsNotNone(orch.startup_refusal("127.0.0.1", "", env={}))
        self.assertIsNotNone(orch.startup_refusal("10.0.0.5", "", env={"ORCH_ALLOW_NO_TOKEN": "1"}))
        self.assertIsNone(orch.startup_refusal("127.0.0.1", "", env={"ORCH_ALLOW_NO_TOKEN": "1"}))

    def test_open_bind_refused(self):
        self.assertIsNotNone(orch.startup_refusal("0.0.0.0", "tok", env={}))
        self.assertIsNotNone(orch.startup_refusal("203.0.113.9", "tok", env={}))
        self.assertIsNone(orch.startup_refusal("0.0.0.0", "tok", env={"ORCH_ALLOW_PUBLIC_BIND": "1"}))

    def test_loopback_and_private_accepted(self):
        for host in ("127.0.0.1", "::1", "10.0.0.5", "192.168.1.4", "172.16.0.2", "100.64.1.1", "fd00::1"):
            self.assertIsNone(orch.startup_refusal(host, "tok", env={}), host)

    def test_systemd_unit_binds_loopback_and_persists(self):
        unit = open(os.path.join(os.path.dirname(__file__), "..", "systemd",
                                 "cloud-phone-orchestrator.service")).read()
        self.assertIn("EnvironmentFile=", unit)
        self.assertIn("ORCH_HOST=127.0.0.1", unit)
        self.assertIn("ORCH_STATE_DB=", unit)
        self.assertNotIn("0.0.0.0", unit)
        self.assertNotRegex(unit, r"ORCH_API_TOKEN=\S", "the token belongs in the EnvironmentFile, not the unit")


class _FakeUpstreamProxy(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class ProxyRelayTests(unittest.TestCase):
    """The per-lease relay adds THIS lease's credential, and only to its own upstream."""

    def setUp(self):
        self.seen = []
        seen = self.seen

        class H(socketserver.BaseRequestHandler):
            def handle(self):
                head = b""
                while b"\r\n\r\n" not in head:
                    chunk = self.request.recv(4096)
                    if not chunk:
                        return
                    head += chunk
                text = head.decode("latin-1")
                seen.append(text)
                if "Proxy-Authorization: Basic dXNlci1hOnBhc3MtYQ==" in text:  # user-a:pass-a
                    self.request.sendall(b"HTTP/1.1 200 OK\r\n\r\n")
                    self.request.sendall(b"tunnel-open")
                else:
                    self.request.sendall(b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n")

        self.up = _FakeUpstreamProxy(("127.0.0.1", 0), H)
        threading.Thread(target=self.up.serve_forever, daemon=True).start()
        self.port = self.up.server_address[1]

    def tearDown(self):
        self.up.shutdown()
        self.up.server_close()

    def _connect_through(self, relay):
        s = socket.create_connection(("127.0.0.1", relay.port), timeout=5)
        s.sendall(b"CONNECT site.example.test:443 HTTP/1.1\r\nHost: site.example.test:443\r\n\r\n")
        data = b""
        while b"\r\n\r\n" not in data:
            data += s.recv(4096)
        rest = data.split(b"\r\n\r\n", 1)[1]
        if b" 200" in data.split(b"\r\n", 1)[0] and not rest:
            rest = s.recv(4096)
        s.close()
        return data.split(b"\r\n", 1)[0], rest

    def test_relay_authenticates_with_its_own_lease_credential(self):
        relay = LeaseProxyRelay(f"http://user-a:pass-a@127.0.0.1:{self.port}").start()
        try:
            status, rest = self._connect_through(relay)
            self.assertIn(b"200", status)
            self.assertEqual(rest, b"tunnel-open")
            self.assertNotIn("pass-a", repr(relay))
        finally:
            relay.stop()

    def test_wrong_lease_credential_is_refused_upstream(self):
        relay = LeaseProxyRelay(f"http://user-b:pass-b@127.0.0.1:{self.port}").start()
        try:
            status, _ = self._connect_through(relay)
            self.assertIn(b"407", status)
        finally:
            relay.stop()

    def test_parse_upstream(self):
        host, port, auth = parse_upstream("http://u%40x:p@proxy.example.test:9000")
        self.assertEqual((host, port), ("proxy.example.test", 9000))
        self.assertTrue(auth.startswith("Basic "))
        self.assertIsNone(parse_upstream("proxy.example.test:9000")[2])


if __name__ == "__main__":
    unittest.main()


class AccessLogTests(unittest.TestCase):
    def test_ticket_query_is_redacted_in_access_log(self):
        import logging
        record = logging.LogRecord("werkzeug", logging.INFO, __file__, 1, '"%s" %s %s',
                                   ("GET /relay/phone/stream?ticket=ort_fake_secret&x=1 HTTP/1.1", "401", "-"), None)
        for f in logging.getLogger("werkzeug").filters:
            f.filter(record)
        self.assertNotIn("ort_fake_secret", record.getMessage())
        self.assertIn("ticket=[redacted]", record.getMessage())
