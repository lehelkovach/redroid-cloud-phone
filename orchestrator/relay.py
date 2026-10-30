"""The orchestrator as router: every command from an OSLO session to its browser or phone passes here.

A caller never gets a container address. It gets a **ticket**: a short-lived,
revocable bearer bound to one owner and one lease. The relay resolves the
ticket to the lease, the lease to the container, and forwards. Whatever
address, instance id or ADB serial a request carries is ignored -- routing by a
caller-supplied address is how one owner reaches another's container.

On every message the relay enforces:

  * the ticket is live (not revoked, not expired) and its lease is still held
    by the ticket's owner;
  * a per-lease rate limit (token bucket);
  * one audit line per command: owner token, lease, verb, milliseconds, ok.
    Never payloads, cookies, URLs or keystrokes;
  * a per-lease kill switch that drops every open stream at once.

Tickets are stored as SHA-256 digests (state_store "tickets"), so the
database never holds a usable bearer, and revocations survive a restart.
"""

import collections
import hashlib
import json
import secrets
import threading
import time

TICKET_PREFIX = "ort_"
DEFAULT_TICKET_TTL = 300
MAX_TICKET_TTL = 3600


class TicketError(Exception):
    def __init__(self, code, message, status=401):
        super().__init__(message)
        self.code = code
        self.status = status


def digest(token):
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


class TicketBook:
    """Mint, check and revoke tickets. `lease_check(record)` -> (ok, why)."""

    def __init__(self, store=None, now=time.time, lease_check=None):
        self.store = store
        self.now = now
        self.lease_check = lease_check
        self._lock = threading.Lock()
        self._tickets = {}

    def load(self):
        if self.store is None:
            return
        with self._lock:
            self._tickets = dict(self.store.load("tickets"))

    def clear(self):
        with self._lock:
            self._tickets.clear()

    def _persist(self, key, record):
        if self.store is not None:
            self.store.put("tickets", key, record)

    def mint(self, *, owner, owner_token, lease_id, kind, ttl=DEFAULT_TICKET_TTL, not_after=None):
        try:
            ttl = int(ttl)
        except (TypeError, ValueError):
            ttl = DEFAULT_TICKET_TTL
        ttl = max(5, min(ttl, MAX_TICKET_TTL))
        t = self.now()
        expires = t + ttl
        if not_after is not None:
            expires = min(expires, float(not_after))  # never outlive the lease
        token = TICKET_PREFIX + secrets.token_urlsafe(32)
        key = digest(token)
        record = {
            "owner": owner,
            "owner_token": owner_token,
            "lease_id": lease_id,
            "kind": kind,
            "issued_at": t,
            "expires_at": expires,
            "revoked": False,
            "id": key[:16],
        }
        with self._lock:
            self._tickets[key] = record
        self._persist(key, record)
        return token, dict(record)

    def check(self, token, kind=None, lease_id=None):
        if not token or not str(token).startswith(TICKET_PREFIX):
            raise TicketError("ticket_required", "a session ticket is required")
        key = digest(token)
        with self._lock:
            record = self._tickets.get(key)
            record = dict(record) if record else None
        if not record:
            raise TicketError("ticket_unknown", "unknown ticket")
        if record.get("revoked"):
            raise TicketError("ticket_revoked", "ticket revoked")
        if record["expires_at"] <= self.now():
            raise TicketError("ticket_expired", "ticket expired")
        if kind and record["kind"] != kind:
            raise TicketError("ticket_wrong_kind", f"ticket is for {record['kind']}, not {kind}", 403)
        if lease_id and record["lease_id"] != lease_id:
            # The path names a lease this ticket was not issued for.
            raise TicketError("ticket_wrong_lease", "ticket does not grant that lease", 403)
        if self.lease_check is not None:
            ok, why = self.lease_check(record)
            if not ok:
                raise TicketError("lease_gone", why or "lease no longer held by this owner", 403)
        return record

    def revoke(self, token):
        return self.revoke_digest(digest(token))

    def revoke_digest(self, key):
        with self._lock:
            record = self._tickets.get(key)
            if not record or record.get("revoked"):
                return False
            record["revoked"] = True
            snapshot = dict(record)
        self._persist(key, snapshot)
        return True

    def revoke_lease(self, lease_id):
        with self._lock:
            keys = [k for k, r in self._tickets.items() if r["lease_id"] == lease_id and not r.get("revoked")]
        return sum(1 for k in keys if self.revoke_digest(k))

    def find_by_id(self, ticket_id):
        with self._lock:
            for k, r in self._tickets.items():
                if r.get("id") == ticket_id:
                    return k, dict(r)
        return None, None

    def reap(self, grace=3600):
        """Forget tickets that expired more than `grace` seconds ago."""
        cutoff = self.now() - grace
        with self._lock:
            dead = [k for k, r in self._tickets.items() if r["expires_at"] < cutoff]
            for k in dead:
                self._tickets.pop(k, None)
        if self.store is not None:
            for k in dead:
                self.store.delete("tickets", k)
        return len(dead)


class RateLimiter:
    """Token bucket per lease. `allow(lease_id)` -> bool."""

    def __init__(self, rate_per_sec=50.0, burst=200, now=time.monotonic):
        self.rate = float(rate_per_sec)
        self.burst = float(burst)
        self.now = now
        self._lock = threading.Lock()
        self._buckets = {}

    def allow(self, lease_id, cost=1.0):
        if self.rate <= 0:
            return True
        t = self.now()
        with self._lock:
            tokens, last = self._buckets.get(lease_id, (self.burst, t))
            tokens = min(self.burst, tokens + (t - last) * self.rate)
            if tokens < cost:
                self._buckets[lease_id] = (tokens, t)
                return False
            self._buckets[lease_id] = (tokens - cost, t)
            return True

    def forget(self, lease_id):
        with self._lock:
            self._buckets.pop(lease_id, None)


class Audit:
    """One line per relayed command. Fields are fixed; nothing else gets in."""

    FIELDS = ("owner", "lease", "kind", "verb", "ms", "ok")

    def __init__(self, logger=None, maxlen=5000):
        self.logger = logger
        self.lines = collections.deque(maxlen=maxlen)

    def record(self, *, owner, lease, kind, verb, ms, ok):
        entry = {
            "owner": str(owner),
            "lease": str(lease),
            "kind": str(kind),
            # A verb is a method or route name. Cap it so a caller cannot use
            # it to smuggle a payload into the log.
            "verb": str(verb)[:64],
            "ms": round(float(ms), 2),
            "ok": bool(ok),
            "at": time.time(),
        }
        self.lines.append(entry)
        if self.logger is not None:
            self.logger.info(
                "relay owner=%s lease=%s kind=%s verb=%s ms=%.2f ok=%s",
                entry["owner"], entry["lease"], entry["kind"], entry["verb"], entry["ms"],
                "true" if entry["ok"] else "false",
            )
        return entry


class StreamRegistry:
    """Open streams per lease, so a kill switch can drop them all at once."""

    def __init__(self):
        self._lock = threading.Lock()
        self._streams = collections.defaultdict(set)

    def add(self, lease_id, stream):
        with self._lock:
            self._streams[lease_id].add(stream)

    def remove(self, lease_id, stream):
        with self._lock:
            self._streams.get(lease_id, set()).discard(stream)

    def count(self, lease_id=None):
        with self._lock:
            if lease_id is None:
                return sum(len(s) for s in self._streams.values())
            return len(self._streams.get(lease_id, ()))

    def kill(self, lease_id, reason="killed"):
        with self._lock:
            streams = list(self._streams.pop(lease_id, ()))
        for s in streams:
            s.kill(reason)
        return len(streams)


def ticket_from_request(headers, args):
    """The ticket a request presents: X-Orch-Ticket, then Bearer, then ?ticket=.

    The query form exists for browser WebSockets (the Watch page), which cannot
    set headers. It is never logged: audit lines carry no URLs.
    """
    tok = (headers.get("X-Orch-Ticket") or "").strip()
    if tok:
        return tok
    auth = headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        cand = auth[7:].strip()
        if cand.startswith(TICKET_PREFIX):
            return cand
    return (args.get("ticket") or "").strip()


# Close codes for a dropped stream (4000-4999 are application-defined).
CLOSE_TICKET = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_KILLED = 4410
CLOSE_RATE = 4429
CLOSE_UPSTREAM = 4502


def _parse_cdp(msg):
    try:
        obj = json.loads(msg)
    except (TypeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


class CdpStream:
    """One relayed DevTools WebSocket: client <-> orchestrator <-> browser."""

    def __init__(self, *, client, upstream, token, record, book, limiter, audit, registry,
                 poll=0.25, now=time.time):
        self.client = client
        self.upstream = upstream
        self.token = token
        self.record = record
        self.book = book
        self.limiter = limiter
        self.audit = audit
        self.registry = registry
        self.poll = poll
        self.now = now
        self._dead = threading.Event()
        self._reason = None
        self._pending = {}
        self._pending_lock = threading.Lock()
        self.forwarded = 0

    @property
    def lease_id(self):
        return self.record["lease_id"]

    def kill(self, reason="killed"):
        if self._dead.is_set():
            return
        self._reason = reason
        self._dead.set()
        code = {"killed": CLOSE_KILLED, "rate": CLOSE_RATE, "upstream": CLOSE_UPSTREAM}.get(reason, CLOSE_TICKET)
        for ws, c in ((self.client, code), (self.upstream, 1000)):
            try:
                ws.close(reason=c, message=str(reason)[:60])
            except Exception:  # noqa: BLE001 - closing a socket that may be gone
                pass

    def _still_allowed(self):
        try:
            self.book.check(self.token, kind="browser", lease_id=self.lease_id)
            return True
        except TicketError as exc:
            self.kill(exc.code)
            return False

    def _expiry_watch(self):
        # Drop the stream the moment the ticket expires, even if it is idle.
        while not self._dead.is_set():
            remaining = self.record["expires_at"] - self.now()
            if remaining <= 0:
                self._still_allowed()
                return
            self._dead.wait(min(remaining, 1.0))

    def _pump_upstream(self):
        while not self._dead.is_set():
            try:
                msg = self.upstream.receive(timeout=self.poll)
            except Exception:  # noqa: BLE001 - upstream closed
                self.kill("upstream")
                return
            if msg is None:
                continue
            obj = _parse_cdp(msg)
            if obj is not None and "id" in obj:
                with self._pending_lock:
                    started = self._pending.pop(obj["id"], None)
                if started is not None:
                    method, t0 = started
                    self.audit.record(owner=self.record["owner_token"], lease=self.lease_id, kind="cdp",
                                      verb=method, ms=(time.perf_counter() - t0) * 1000.0,
                                      ok="error" not in obj)
            # Events (screencast frames included) are re-checked too: a revoked
            # ticket stops the Watch stream, not only the commands.
            if not self._still_allowed():
                return
            try:
                self.client.send(msg)
            except Exception:  # noqa: BLE001 - client went away
                self.kill("client")
                return

    def run(self):
        self.registry.add(self.lease_id, self)
        threading.Thread(target=self._pump_upstream, daemon=True).start()
        threading.Thread(target=self._expiry_watch, daemon=True).start()
        try:
            while not self._dead.is_set():
                try:
                    msg = self.client.receive(timeout=self.poll)
                except Exception:  # noqa: BLE001 - client closed
                    self.kill("client")
                    break
                if msg is None:
                    continue
                if not self._still_allowed():
                    break
                obj = _parse_cdp(msg)
                method = (obj or {}).get("method") or "?"
                if not self.limiter.allow(self.lease_id):
                    self.audit.record(owner=self.record["owner_token"], lease=self.lease_id, kind="cdp",
                                      verb=method, ms=0, ok=False)
                    if obj is not None and "id" in obj:
                        try:
                            self.client.send(json.dumps({"id": obj["id"], "error": {
                                "code": -32000, "message": "rate limited by orchestrator relay"}}))
                        except Exception:  # noqa: BLE001
                            pass
                    continue
                if obj is not None and "id" in obj:
                    with self._pending_lock:
                        self._pending[obj["id"]] = (method, time.perf_counter())
                try:
                    self.upstream.send(msg)
                    self.forwarded += 1
                except Exception:  # noqa: BLE001
                    self.kill("upstream")
                    break
        finally:
            self.registry.remove(self.lease_id, self)
        return self._reason


class PhoneFrameStream:
    """The phone's screen as a WebSocket: frames out, input events in."""

    def __init__(self, *, client, token, record, book, limiter, audit, registry,
                 grab_frame, send_input, fps=4.0, now=time.time):
        self.client = client
        self.token = token
        self.record = record
        self.book = book
        self.limiter = limiter
        self.audit = audit
        self.registry = registry
        self.grab_frame = grab_frame
        self.send_input = send_input
        self.interval = 1.0 / max(0.5, float(fps))
        self.now = now
        self._dead = threading.Event()
        self._reason = None
        self.frames = 0

    @property
    def lease_id(self):
        return self.record["lease_id"]

    def kill(self, reason="killed"):
        if self._dead.is_set():
            return
        self._reason = reason
        self._dead.set()
        code = {"killed": CLOSE_KILLED}.get(reason, CLOSE_TICKET)
        try:
            self.client.close(reason=code, message=str(reason)[:60])
        except Exception:  # noqa: BLE001
            pass

    def _still_allowed(self):
        try:
            self.book.check(self.token, kind="phone", lease_id=self.lease_id)
            return True
        except TicketError as exc:
            self.kill(exc.code)
            return False

    def _frames(self):
        while not self._dead.is_set():
            if not self._still_allowed():
                return
            try:
                frame = self.grab_frame()
            except Exception:  # noqa: BLE001 - one missed frame is not fatal
                frame = {"error": "frame unavailable"}
            if not self._still_allowed():
                return
            try:
                self.client.send(json.dumps({"type": "frame", "seq": self.frames, "frame": frame}))
                self.frames += 1
            except Exception:  # noqa: BLE001
                self.kill("client")
                return
            self._dead.wait(self.interval)

    def run(self):
        self.registry.add(self.lease_id, self)
        threading.Thread(target=self._frames, daemon=True).start()
        try:
            while not self._dead.is_set():
                try:
                    msg = self.client.receive(timeout=0.25)
                except Exception:  # noqa: BLE001
                    self.kill("client")
                    break
                if msg is None:
                    continue
                if not self._still_allowed():
                    break
                obj = _parse_cdp(msg) or {}
                verb = f"input.{obj.get('type', '?')}"
                if not self.limiter.allow(self.lease_id):
                    self.audit.record(owner=self.record["owner_token"], lease=self.lease_id, kind="phone",
                                      verb=verb, ms=0, ok=False)
                    continue
                t0 = time.perf_counter()
                ok = True
                try:
                    self.send_input(obj)
                except Exception:  # noqa: BLE001
                    ok = False
                self.audit.record(owner=self.record["owner_token"], lease=self.lease_id, kind="phone",
                                  verb=verb, ms=(time.perf_counter() - t0) * 1000.0, ok=ok)
        finally:
            self.registry.remove(self.lease_id, self)
        return self._reason
