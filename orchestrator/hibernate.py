"""Hibernate idle paid phone hosts and wake them on demand.

A stopped A1.Flex instance stops billing for compute. Its boot volume is
still billed. So a phone host nobody has used for `HIBERNATE_IDLE_MIN`
(default 10) minutes is stopped, and the next lease on it starts it again.
The orchestrator itself stays up the whole time, because it is the thing
that does the stopping and starting.

Rules, most important first:

1. **Only named hosts.** Only hosts named in `ORCH_HIBERNATE_ALLOWLIST` are
   touched. Each entry is an OCID or an OCI display name. An empty list is
   the default, and with it nothing here makes a single OCI call.
   `HIBERNATE=0` (or `ORCH_HIBERNATE=0`) turns the feature off entirely.
2. **A hard denylist the config cannot override.** The names in
   `PROTECTED_NAME_PATTERNS` are never stopped or started, even when they are
   allowlisted:
   - the Captain's own projects;
   - prod;
   - the orchestrator's own VM.

   `ORCH_SELF_OCID`, `ORCH_SELF_NAME` and this machine's hostname are
   protected the same way. Every stop and start first reads the instance's
   *live* OCI display name and checks it again, so a renamed instance or a
   stale record cannot slip through.
3. **Never stop a host in use.** A host is not stopped while any of these
   holds:
   - a valid lease on an orchestrator record bound to it;
   - an unexpired hold from `POST /hosts/<key>/wake`;
   - a wake still in progress;
   - activity within the idle window.

   The lease is re-checked right before SOFTSTOP. A lease that arrives
   after SOFTSTOP cancels the escalation to STOP, and the wake that the
   lease triggered then starts the host again once it has stopped.
   Nothing here ever terminates an instance.
4. **State comes from OCI, not memory.** Each decision reads
   `lifecycle_state` from OCI. After an orchestrator restart, a lease on a
   stopped host still starts it.
5. **Honest failures.** "Out of host capacity" on START is retried with
   bounded exponential backoff. If every retry fails, the caller gets
   `oci_capacity` and a plain sentence saying the phone is still stopped.

The OCI compute client, clock, sleep, HTTP probe and thread spawner are all
injected, so the tests run against a fake without network or credentials.
"""

from __future__ import annotations

import logging
import os
import re
import socket
import threading
import time

logger = logging.getLogger("orchestrator.hibernate")

# Never stopped or started, whatever the allowlist says (case-insensitive
# search in the OCI display name). Changing this list is a code change, on
# purpose.
PROTECTED_NAME_PATTERNS = (
    re.compile(r"omnistream", re.I),        # the Captain's personal multi-streaming project
    re.compile(r"ksg-main-eb91", re.I),     # KnowShowGo prod
    re.compile(r"^cloud-phone-orch", re.I),  # the orchestrator VM: it must never stop itself
)

RUNNING = "RUNNING"
STOPPED = "STOPPED"
STOPPING = "STOPPING"
STARTING = "STARTING"
GONE = {"TERMINATED", "TERMINATING"}

_OFF = {"0", "false", "no", "off"}


def _num(env, names, default, cast=float):
    for name in names:
        raw = (env.get(name) or "").strip()
        if raw:
            try:
                return cast(raw)
            except ValueError:
                logger.error("%s=%r is not a number; using %s", name, raw, default)
                return cast(default)
    return cast(default)


def _split(raw):
    return [p.strip() for p in (raw or "").replace(";", ",").split(",") if p.strip()]


def config_from_env(env=None):
    env = os.environ if env is None else env
    off = any((env.get(n) or "").strip().lower() in _OFF for n in ("HIBERNATE", "ORCH_HIBERNATE"))
    allow = _split(env.get("ORCH_HIBERNATE_ALLOWLIST") or env.get("HIBERNATE_ALLOWLIST") or "")
    urls = {}
    for pair in _split(env.get("ORCH_HIBERNATE_HEALTH_URLS", "")):
        key, sep, url = pair.partition("=")
        if sep and key.strip() and url.strip():
            urls[key.strip()] = url.strip().rstrip("/")
    return {
        "enabled": not off,
        "allowlist": allow,
        "idle_seconds": 60 * _num(env, ("HIBERNATE_IDLE_MIN", "ORCH_HIBERNATE_IDLE_MIN"), 10),
        "softstop_timeout": _num(env, ("ORCH_HIBERNATE_SOFTSTOP_TIMEOUT",), 180),
        "stop_timeout": _num(env, ("ORCH_HIBERNATE_STOP_TIMEOUT",), 300),
        "wake_timeout": _num(env, ("HIBERNATE_WAKE_TIMEOUT", "ORCH_HIBERNATE_WAKE_TIMEOUT"), 240),
        "wake_eta": _num(env, ("ORCH_HIBERNATE_WAKE_ETA",), 150),
        "poll_seconds": _num(env, ("ORCH_HIBERNATE_POLL_SECONDS",), 5),
        "tick_seconds": _num(env, ("ORCH_HIBERNATE_TICK_SECONDS",), 60),
        "capacity_retries": _num(env, ("ORCH_HIBERNATE_CAPACITY_RETRIES",), 3, int),
        "capacity_backoff": _num(env, ("ORCH_HIBERNATE_CAPACITY_BACKOFF",), 20),
        "capacity_backoff_max": _num(env, ("ORCH_HIBERNATE_CAPACITY_BACKOFF_MAX",), 120),
        "compartment_id": (env.get("ORCH_OCI_COMPARTMENT_ID") or "").strip(),
        "self_ocid": (env.get("ORCH_SELF_OCID") or "").strip(),
        "self_name": (env.get("ORCH_SELF_NAME") or "").strip(),
        "health_urls": urls,
        "resolve_every": _num(env, ("ORCH_HIBERNATE_RESOLVE_SECONDS",), 600),
    }


def is_protected(name, ocid=None, config=None):
    """True when this instance must never be stopped or started."""
    config = config or {}
    if ocid and config.get("self_ocid") and ocid == config["self_ocid"]:
        return True
    name = (name or "").strip()
    if not name:
        return False
    if any(p.search(name) for p in PROTECTED_NAME_PATTERNS):
        return True
    for own in (config.get("self_name"), config.get("_hostname")):
        if own and name.lower() == own.lower():
            return True
    return False


def _is_ocid(entry):
    return entry.startswith("ocid1.instance.")


def _is_capacity(exc):
    text = f"{getattr(exc, 'code', '')} {getattr(exc, 'message', '')} {exc}".lower()
    return "out of host capacity" in text or "outofhostcapacity" in text


def _describe(exc):
    code = getattr(exc, "code", None) or type(exc).__name__
    message = getattr(exc, "message", None) or str(exc)
    return f"{code}: {message}"


def _default_http_get(url, timeout):
    import requests  # local: tests replace this seam

    resp = requests.get(url, timeout=timeout)
    try:
        body = resp.json()
    except ValueError:
        body = None
    return resp.status_code, body


def _default_spawn(fn, name):
    threading.Thread(target=fn, name=name, daemon=True).start()


def default_compute_client(env=None):
    """A bare OCI ComputeClient: instance principal first, else ~/.oci/config."""
    env = os.environ if env is None else env
    import oci  # type: ignore  # lazy: only when a host is actually managed

    mode = (env.get("ORCH_OCI_PROVIDER_AUTH", "auto").strip().lower() or "auto")
    if mode in {"auto", "instance_principal"}:
        try:
            signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
            return oci.core.ComputeClient({"region": getattr(signer, "region", None)}, signer=signer)
        except Exception:  # noqa: BLE001 - fall back to the config file under auto
            if mode == "instance_principal":
                raise
    cfg = oci.config.from_file(
        file_location=env.get("ORCH_OCI_CONFIG", os.path.expanduser("~/.oci/config")),
        profile_name=env.get("ORCH_OCI_PROFILE", "DEFAULT"),
    )
    return oci.core.ComputeClient(cfg)


class _WakeFailure(Exception):
    def __init__(self, code, message, status=503):
        super().__init__(message)
        self.code = code
        self.status = status


class Hibernator:
    def __init__(self, config=None, compute=None, compute_factory=None, records=None,
                 busy=None, clock=time.time, sleep=time.sleep, http_get=None, spawn=None):
        self.config = dict(config_from_env() if config is None else config)
        self.config.setdefault("_hostname", _hostname())
        self._compute = compute
        self._compute_factory = compute_factory or default_compute_client
        self._records = records or (lambda: [])
        self._busy = busy or (lambda record_id: False)
        self._clock = clock
        self._sleep = sleep
        self._http_get = http_get or _default_http_get
        self._spawn = spawn or _default_spawn
        self._lock = threading.RLock()
        self._hosts = {}       # ocid -> host state
        self._waking = set()   # ocids with a wake running
        self._stopping = set()  # ocids with a stop running
        self._resolved_at = None
        self._last_wake_seconds = None

    # ------------------------------------------------------------- config
    @property
    def active(self):
        return bool(self.config.get("enabled")) and bool(self.config.get("allowlist"))

    def compute(self):
        if self._compute is None:
            self._compute = self._compute_factory()
        return self._compute

    def _allowed(self, name, ocid):
        allow = self.config.get("allowlist") or []
        return bool((ocid and ocid in allow) or (name and name in allow))

    def _protected(self, name, ocid):
        return is_protected(name, ocid, self.config)

    # ------------------------------------------------------------ OCI reads
    def _instance(self, ocid):
        return self.compute().get_instance(ocid).data

    def _lifecycle(self, ocid):
        return getattr(self._instance(ocid), "lifecycle_state", None)

    # ------------------------------------------------------------- hosts
    def _host(self, ocid, name=None, now=None):
        now = self._clock() if now is None else now
        with self._lock:
            host = self._hosts.get(ocid)
            if host is None:
                # A host seen for the first time (or after a restart) gets a
                # full idle window before it can be stopped.
                host = self._hosts[ocid] = {
                    "ocid": ocid, "name": name, "state": "unknown", "last_active": now,
                    "hold_until": 0.0, "record_ids": set(), "api_url": None,
                    "error": None, "code": None, "reported": True, "since": now,
                }
            if name:
                host["name"] = name
            return host

    def _set(self, ocid, **fields):
        with self._lock:
            host = self._hosts.get(ocid)
            if host is not None:
                if "state" in fields and fields["state"] != host.get("state"):
                    fields.setdefault("since", self._clock())
                host.update(fields)

    def refresh(self, force=False):
        """Rebuild the managed host table: allowlist entries resolved through OCI, plus records."""
        if not self.active:
            return []
        now = self._clock()
        due = force or self._resolved_at is None or now - self._resolved_at >= self.config.get("resolve_every", 600)
        records = [r for r in self._records() if r.get("instance_ocid")]
        seen = set()
        for rec in records:
            ocid = rec["instance_ocid"]
            if not self._allowed(rec.get("name"), ocid):
                continue
            if self._protected(rec.get("name"), ocid):
                continue
            host = self._host(ocid, rec.get("name"), now)
            with self._lock:
                host["record_ids"].add(rec["id"])
                host["api_url"] = host["api_url"] or rec.get("api_url")
                host["last_active"] = max(host["last_active"], float(rec.get("last_used") or 0))
            seen.add(ocid)
        if due:
            self._resolved_at = now
            for entry in self.config.get("allowlist") or []:
                try:
                    for ocid, name in self._resolve_entry(entry):
                        if self._protected(name, ocid):
                            logger.warning("hibernate: %s is protected; never stopped or started", name or ocid)
                            continue
                        self._host(ocid, name, now)
                        seen.add(ocid)
                except Exception as exc:  # noqa: BLE001 - one bad entry must not stop the loop
                    logger.error("hibernate: could not resolve %r: %s", entry, _describe(exc))
        urls = self.config.get("health_urls") or {}
        with self._lock:
            for host in self._hosts.values():
                url = urls.get(host.get("name") or "") or urls.get(host["ocid"])
                if url:
                    host["api_url"] = url
            return list(self._hosts.keys())

    def _resolve_entry(self, entry):
        if _is_ocid(entry):
            inst = self._instance(entry)
            if getattr(inst, "lifecycle_state", None) in GONE:
                return []
            return [(entry, getattr(inst, "display_name", None))]
        compartment = self.config.get("compartment_id")
        if not compartment:
            return []  # a name without a compartment can only match an orchestrator record
        found = self.compute().list_instances(compartment_id=compartment, display_name=entry).data
        return [(i.id, i.display_name) for i in found
                if getattr(i, "lifecycle_state", None) not in GONE and i.display_name == entry]

    def _find(self, key):
        """A managed host by OCID, display name or orchestrator record id."""
        self.refresh()
        with self._lock:
            for host in self._hosts.values():
                if key in (host["ocid"], host.get("name")) or key in host["record_ids"]:
                    return host
        return None

    def host_for_record(self, record):
        if not self.active or not record or not record.get("instance_ocid"):
            return None
        if not self._allowed(record.get("name"), record["instance_ocid"]):
            return None
        if self._protected(record.get("name"), record["instance_ocid"]):
            return None
        host = self._host(record["instance_ocid"], record.get("name"))
        with self._lock:
            host["record_ids"].add(record["id"])
            host["api_url"] = host["api_url"] or record.get("api_url")
        return host

    def _in_use(self, host, now):
        if host.get("hold_until", 0) > now:
            return True
        return any(self._busy(rid) for rid in list(host["record_ids"]))

    # ------------------------------------------------------------ activity
    def touch(self, record=None, ocid=None):
        ocid = ocid or (record or {}).get("instance_ocid")
        if not ocid:
            return
        with self._lock:
            host = self._hosts.get(ocid)
            if host is not None:
                host["last_active"] = self._clock()

    def is_asleep(self, record):
        """Cheap: from what this process last saw, no OCI call."""
        if not self.active or not record or not record.get("instance_ocid"):
            return False
        with self._lock:
            host = self._hosts.get(record["instance_ocid"])
            return bool(host and host["state"] in {"stopped", "stopping", "waking", "wake_failed"})

    # ---------------------------------------------------------------- stop
    def tick(self):
        """One pass: stop every managed host idle past the window. Returns what it started."""
        if not self.active:
            return []
        self.refresh()
        now = self._clock()
        started = []
        with self._lock:
            hosts = list(self._hosts.values())
        for host in hosts:
            ocid = host["ocid"]
            if self._in_use(host, now):
                host["last_active"] = now
                continue
            with self._lock:
                if ocid in self._waking or ocid in self._stopping:
                    continue
                if host["state"] == "stopped":
                    continue
                if now - host["last_active"] < self.config["idle_seconds"]:
                    continue
                self._stopping.add(ocid)
            started.append(ocid)
            self._spawn(lambda h=host: self._run_stop(h), f"hibernate-{ocid[-6:]}")
        return started

    def _run_stop(self, host):
        try:
            return self.hibernate(host)
        except Exception as exc:  # noqa: BLE001 - logged; the loop must keep going
            logger.error("hibernate: stopping %s failed: %s", host.get("name") or host["ocid"], _describe(exc))
            self._set(host["ocid"], state="unknown", error=_describe(exc))
            return "error"
        finally:
            with self._lock:
                self._stopping.discard(host["ocid"])

    def _wanted(self, host):
        now = self._clock()
        with self._lock:
            waking = host["ocid"] in self._waking
        return waking or self._in_use(host, now) or now - host["last_active"] < self.config["idle_seconds"]

    def hibernate(self, host):
        """SOFTSTOP, wait, escalate to STOP. Never while in use; never a protected name."""
        ocid = host["ocid"]
        inst = self._instance(ocid)
        name = getattr(inst, "display_name", None) or host.get("name")
        if self._protected(name, ocid):
            logger.warning("hibernate: refusing to stop protected instance %s", name)
            return "protected"
        state = getattr(inst, "lifecycle_state", None)
        if state == STOPPED:
            self._set(ocid, state="stopped")
            return "already_stopped"
        if state != RUNNING:
            return f"skipped_{(state or 'unknown').lower()}"
        if self._wanted(host):
            return "in_use"
        logger.info("hibernate: %s idle %.0fs; SOFTSTOP", name, self._clock() - host["last_active"])
        self.compute().instance_action(ocid, "SOFTSTOP")
        self._set(ocid, state="stopping", error=None)
        if self._wait_state(ocid, {STOPPED}, self.config["softstop_timeout"]):
            return self._stopped(ocid, name, "softstop")
        if self._wanted(host):
            # Someone wants it back: do not force it down; the wake waits the soft stop out.
            logger.info("hibernate: %s wanted again during SOFTSTOP; not escalating", name)
            return "softstop_pending"
        logger.warning("hibernate: %s did not stop within %ss; STOP", name, self.config["softstop_timeout"])
        self.compute().instance_action(ocid, "STOP")
        if self._wait_state(ocid, {STOPPED}, self.config["stop_timeout"]):
            return self._stopped(ocid, name, "stop")
        self._set(ocid, error="STOP did not finish in time")
        return "stop_pending"

    def _stopped(self, ocid, name, how):
        with self._lock:
            if ocid not in self._waking:
                self._set(ocid, state="stopped")
        logger.info("hibernate: %s stopped (%s)", name, how)
        return how

    def _wait_state(self, ocid, wanted, timeout):
        deadline = self._clock() + timeout
        while True:
            state = self._lifecycle(ocid)
            if state in wanted:
                return state
            if state in GONE or self._clock() >= deadline:
                return None
            self._sleep(self.config["poll_seconds"])

    # ---------------------------------------------------------------- wake
    def ensure_awake(self, record):
        """None when the host behind `record` is usable now; else a `waking` / failure dict."""
        host = self.host_for_record(record)
        if host is None:
            return None
        return self._ensure(host, record_id=record.get("id"))

    def wake_host(self, key, hold_seconds=0, owner=None):
        """`POST /hosts/<key>/wake`: hold the host for `hold_seconds` and make sure it is up."""
        if not self.active:
            return {"status": "not_managed", "http_status": 404,
                    "message": "hibernation is off or the allowlist is empty"}
        host = self._find(key)
        if host is None:
            return {"status": "not_managed", "http_status": 404,
                    "message": f"{key} is not an allowlisted host (or it is protected)"}
        now = self._clock()
        with self._lock:
            host["last_active"] = now
            if hold_seconds:
                host["hold_until"] = max(host.get("hold_until", 0), now + float(hold_seconds))
                host["hold_owner"] = owner
        res = self._ensure(host)
        if res is None:
            return {"status": "ready", "http_status": 200, "host": self._public(host)}
        return res

    def release_hold(self, key):
        host = self._find(key) if self.active else None
        if host is None:
            return False
        with self._lock:
            host["hold_until"] = 0.0
            host["hold_owner"] = None
            host["last_active"] = self._clock()
        return True

    def _ensure(self, host, record_id=None):
        ocid = host["ocid"]
        with self._lock:
            host["last_active"] = self._clock()
            if ocid in self._waking:
                return self._waking_body(host)
            if host["state"] == "wake_failed" and not host["reported"]:
                host["reported"] = True  # said once; the next request tries again
                return {"status": "wake_failed", "code": host["code"], "http_status": host.get("http_status", 503),
                        "message": host["error"], "instance_ocid": ocid, "name": host.get("name")}
            known_up = host["state"] == "running"
        if known_up:
            return None
        inst = self._instance(ocid)
        name = getattr(inst, "display_name", None) or host.get("name")
        if self._protected(name, ocid):
            return None  # never touched; the caller proceeds as if hibernation did not exist
        state = getattr(inst, "lifecycle_state", None)
        if state == RUNNING and host["state"] in {"unknown", "running"}:
            self._set(ocid, state="running")
            return None
        if state in GONE:
            self._set(ocid, state="gone")
            return {"status": "wake_failed", "code": "oci_gone", "http_status": 410, "name": name,
                    "message": f"the phone host {name} is {state}; it cannot be woken", "instance_ocid": ocid}
        with self._lock:
            if ocid in self._waking:
                return self._waking_body(host)
            self._waking.add(ocid)
            host["wake_started"] = self._clock()
            self._set(ocid, state="waking", error=None, code=None)
        logger.info("hibernate: waking %s (was %s)", name, state)
        self._spawn(lambda: self._run_wake(host), f"wake-{ocid[-6:]}")
        with self._lock:
            if ocid in self._waking:
                return self._waking_body(host)
            if host["state"] == "running":
                return None
        return self._ensure(host, record_id)

    def eta_seconds(self, host):
        est = self._last_wake_seconds or self.config["wake_eta"]
        started = host.get("wake_started") or self._clock()
        return int(max(10, started + est - self._clock()))

    def _waking_body(self, host):
        eta = self.eta_seconds(host)
        minutes = max(1, round(eta / 60))
        return {
            "status": "waking", "code": "waking", "http_status": 503,
            "eta_seconds": eta, "retry_after": int(min(30, max(5, eta // 4))),
            "name": host.get("name"), "instance_ocid": host["ocid"],
            "message": f"Waking the cloud phone, about {minutes} min",
        }

    def _run_wake(self, host):
        ocid = host["ocid"]
        started = self._clock()
        try:
            self.wake(host)
            self._last_wake_seconds = self._clock() - started
            self._set(ocid, state="running", error=None, code=None, reported=True)
            host["last_active"] = self._clock()
            logger.info("hibernate: %s awake after %.0fs", host.get("name") or ocid, self._last_wake_seconds)
        except _WakeFailure as exc:
            logger.error("hibernate: waking %s failed (%s): %s", host.get("name") or ocid, exc.code, exc)
            self._set(ocid, state="wake_failed", error=str(exc), code=exc.code, http_status=exc.status, reported=False)
        except Exception as exc:  # noqa: BLE001
            logger.error("hibernate: waking %s failed: %s", host.get("name") or ocid, _describe(exc))
            self._set(ocid, state="wake_failed", error=f"waking the phone host failed: {_describe(exc)}",
                      code="oci_error", http_status=502, reported=False)
        finally:
            with self._lock:
                self._waking.discard(ocid)

    def wake(self, host):
        """Blocking: (wait out STOPPING) → START with capacity retries → RUNNING → healthy."""
        ocid = host["ocid"]
        name = host.get("name") or ocid
        # A stop already under way finishes first (it will not escalate to
        # STOP now that the host is wanted); starting mid-SOFTSTOP would race it.
        stop_deadline = self._clock() + self.config["stop_timeout"]
        while True:
            with self._lock:
                stopping = ocid in self._stopping
            if not stopping or self._clock() >= stop_deadline:
                break
            self._sleep(self.config["poll_seconds"])
        state = self._lifecycle(ocid)
        if state == STOPPING:
            state = self._wait_state(ocid, {STOPPED, RUNNING}, self.config["stop_timeout"])
            if state is None:
                raise _WakeFailure("oci_stopping", f"the phone host {name} is still shutting down; try again in a minute")
        if state == STOPPED:
            self._start(ocid, name)
        elif state in GONE:
            raise _WakeFailure("oci_gone", f"the phone host {name} is {state}; it cannot be woken", 410)
        deadline = self._clock() + self.config["wake_timeout"]
        if self._wait_state(ocid, {RUNNING}, max(0.0, deadline - self._clock())) is None:
            raise _WakeFailure("oci_timeout", f"the phone host {name} did not reach RUNNING within "
                                              f"{int(self.config['wake_timeout'])}s", 504)
        url = host.get("api_url")
        if url:
            self._wait_healthy(url, name, deadline)

    def _start(self, ocid, name):
        inst = self._instance(ocid)
        live_name = getattr(inst, "display_name", None) or name
        if self._protected(live_name, ocid):
            raise _WakeFailure("protected", f"{live_name} is protected; not starting it", 403)
        retries = int(self.config["capacity_retries"])
        delay = float(self.config["capacity_backoff"])
        began = self._clock()
        for attempt in range(retries + 1):
            try:
                self.compute().instance_action(ocid, "START")
                return
            except Exception as exc:  # noqa: BLE001 - classified below
                if not _is_capacity(exc):
                    raise _WakeFailure("oci_error", f"OCI refused to start {live_name}: {_describe(exc)}", 502) from exc
                if attempt >= retries:
                    waited = int(self._clock() - began)
                    raise _WakeFailure(
                        "oci_capacity",
                        f"OCI has no free capacity to start the phone host {live_name} right now "
                        f"(tried {attempt + 1} times over {waited}s). It is still stopped; try again later.",
                        503,
                    ) from exc
                logger.warning("hibernate: %s out of host capacity (try %d/%d); retrying in %.0fs",
                               live_name, attempt + 1, retries + 1, delay)
                self._sleep(delay)
                delay = min(delay * 2, float(self.config["capacity_backoff_max"]))

    def _wait_healthy(self, url, name, deadline):
        last = None
        while True:
            try:
                status, body = self._http_get(f"{url}/health", timeout=5)
                last = f"HTTP {status}"
                if status == 200 and isinstance(body, dict) and body.get("status") in {"ok", "healthy"}:
                    return body
            except Exception as exc:  # noqa: BLE001 - refused while Android boots is normal
                last = type(exc).__name__
            if self._clock() >= deadline:
                raise _WakeFailure("oci_unhealthy", f"the phone host {name} is RUNNING but its phone did not "
                                                    f"report healthy in time (last: {last})", 504)
            self._sleep(self.config["poll_seconds"])

    # -------------------------------------------------------------- report
    def _public(self, host):
        now = self._clock()
        return {
            "name": host.get("name"), "state": host["state"],
            "idle_seconds": int(max(0, now - host["last_active"])),
            "held": host.get("hold_until", 0) > now,
            "error": host.get("error"), "code": host.get("code"),
        }

    def snapshot(self):
        if not self.config.get("enabled"):
            return {"enabled": False}
        if not self.config.get("allowlist"):
            return {"enabled": True, "active": False, "reason": "ORCH_HIBERNATE_ALLOWLIST is empty"}
        with self._lock:
            hosts = [self._public(h) for h in self._hosts.values()]
        return {"enabled": True, "active": True, "idle_minutes": self.config["idle_seconds"] / 60,
                "hosts": hosts}

    def run_forever(self, stop_event=None):
        stop_event = stop_event or threading.Event()
        logger.info("hibernate: loop on, %d allowlist entries, idle %.0f min",
                    len(self.config["allowlist"]), self.config["idle_seconds"] / 60)
        while not stop_event.is_set():
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001
                logger.error("hibernate: tick failed: %s", _describe(exc))
            stop_event.wait(self.config["tick_seconds"])


def _hostname():
    try:
        return socket.gethostname()
    except Exception:  # noqa: BLE001
        return None
