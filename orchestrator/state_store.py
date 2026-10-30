"""Durable orchestrator state: who holds which phone, browser, profile and ticket.

Before this module every lease lived in a dict, so a restart forgot who held
what. The phone or browser kept running with one owner's Android data or
logged-in profile on it, and the next request could hand that instance to
somebody else. Persisting the bookings closes that: after a restart the
orchestrator knows the same owners it knew before, and refuses the same
cross-owner requests.

Deliberately small. One SQLite file, one table of JSON rows keyed by
(namespace, key). The orchestrator's in-memory dicts stay the working copy;
every mutation is written through here, and `load()` refills the dicts at
start. SQLite (stdlib) rather than a server, because the orchestrator is one
process on one host and the state is a few hundred rows at most.

What is never written here: proxy credentials, bearer tokens, raw ticket
values, volume keys or the volume master key (the "volumes" namespace holds
only sizes, open state and last usage, keyed by owner token). Tickets are stored as SHA-256 digests; a proxy upstream is stored with
its userinfo stripped (`redact_upstream`). A restart therefore keeps every
booking and loses only secrets, which the owner's client supplies again on its
next acquire.

`path=":memory:"` (or empty) keeps the old behaviour for tests and for anyone
who has not configured ORCH_STATE_DB.
"""

import json
import os
import sqlite3
import threading
import time
from urllib.parse import urlsplit, urlunsplit

NAMESPACES = (
    "instances",
    "leases",
    "tenancy",
    "user_sessions",
    "browser_sessions",
    "tickets",
    "volumes",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS state (
    namespace TEXT NOT NULL,
    key       TEXT NOT NULL,
    value     TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (namespace, key)
);
"""


def redact_upstream(upstream):
    """`scheme://host:port` with any user:password removed, or None."""
    if not upstream:
        return None
    raw = str(upstream).strip()
    if "://" not in raw:
        raw = "http://" + raw
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    host = parts.hostname or ""
    if not host:
        return None
    netloc = host if parts.port is None else f"{host}:{parts.port}"
    return urlunsplit((parts.scheme or "http", netloc, "", "", ""))


class StateStore:
    """Write-through key/value persistence for the orchestrator's state dicts."""

    def __init__(self, path=":memory:"):
        self.path = str(path or ":memory:")
        self.persistent = self.path != ":memory:"
        if self.persistent:
            parent = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(parent, mode=0o700, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        if self.persistent:
            # The file holds who-owns-what, and ticket digests: not secrets, but
            # nobody else on the host needs to read it.
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(_SCHEMA)

    def put(self, namespace, key, value):
        if namespace not in NAMESPACES:
            raise ValueError(f"unknown state namespace: {namespace}")
        payload = json.dumps(value, sort_keys=True, default=str)
        with self._lock:
            self._db.execute(
                "INSERT INTO state(namespace, key, value, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(namespace, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (namespace, str(key), payload, time.time()),
            )

    def delete(self, namespace, key):
        with self._lock:
            self._db.execute("DELETE FROM state WHERE namespace=? AND key=?", (namespace, str(key)))

    def get(self, namespace, key):
        with self._lock:
            row = self._db.execute(
                "SELECT value FROM state WHERE namespace=? AND key=?", (namespace, str(key))
            ).fetchone()
        return json.loads(row[0]) if row else None

    def load(self, namespace):
        """Every row in a namespace, as {key: value}."""
        with self._lock:
            rows = self._db.execute(
                "SELECT key, value FROM state WHERE namespace=?", (namespace,)
            ).fetchall()
        return {k: json.loads(v) for k, v in rows}

    def clear(self):
        with self._lock:
            self._db.execute("DELETE FROM state")

    def close(self):
        with self._lock:
            try:
                self._db.close()
            except sqlite3.Error:
                pass
