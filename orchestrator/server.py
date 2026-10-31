#!/usr/bin/env python3
"""
Orchestrator for cloud phone instances.

Default pool: Redroid + GApps OCI VMs for mobile automation.
Camera/stream purpose: spawn Cuttlefish + nginx-rtmp ingest VMs.

Features:
- Provision on-demand (mock, local redroid-up, or OCI golden)
- Separate idle pools per runtime (never mix Play phones with ingest hosts)
- One-phone-per-user sessions (Playwright-like acquire/release)
- Queue operations, procedures, and relay to Control API
"""

import json
import os
import subprocess
import threading
import time
import uuid
from urllib.parse import urlparse
from pathlib import Path

import requests
from flask import Flask, Response, jsonify, request

try:
    from orchestrator.runtimes import (
        PURPOSE_AUTOMATION,
        PURPOSE_CAMERA,
        RUNTIME_CUTTLEFISH,
        RUNTIME_REDROID,
        resolve_purpose,
        runtime_for_purpose,
    )
except ImportError:  # python orchestrator/server.py
    from runtimes import (  # type: ignore
        PURPOSE_AUTOMATION,
        PURPOSE_CAMERA,
        RUNTIME_CUTTLEFISH,
        RUNTIME_REDROID,
        resolve_purpose,
        runtime_for_purpose,
    )

try:
    from orchestrator import procedures as proc
    from orchestrator import browser_pool as bp
    from orchestrator import relay as rly
    from orchestrator.state_store import StateStore, redact_upstream
    from orchestrator.proxy_relay import LeaseProxyRelay
    from orchestrator.runtimes import is_browser_request
    from orchestrator import user_volumes as uv
    from orchestrator.providers import oci_compute as ocp
except ImportError:  # running server.py directly
    import procedures as proc
    import browser_pool as bp  # type: ignore
    import relay as rly  # type: ignore
    from state_store import StateStore, redact_upstream  # type: ignore
    from proxy_relay import LeaseProxyRelay  # type: ignore
    from runtimes import is_browser_request  # type: ignore
    import user_volumes as uv  # type: ignore
    from providers import oci_compute as ocp  # type: ignore

try:  # WebSocket relay (CDP, phone screen). Optional so the phone API runs without it.
    import simple_websocket
    from simple_websocket import ConnectionClosed as _WsClosed
except ImportError:  # pragma: no cover - exercised only where the dep is missing
    simple_websocket = None
    _WsClosed = Exception

try:
    from api.cloudphone_logging import configure as _configure_logging
    from api.cloudphone_logging import redact, recent_logs
except ImportError:
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from api.cloudphone_logging import configure as _configure_logging
    from api.cloudphone_logging import redact, recent_logs

app = Flask(__name__)


class _RedactTicketFilter:
    """Keep `?ticket=` values out of the access log (werkzeug logs the request line)."""

    import re as _re
    _pattern = _re.compile(r"(ticket=)[^&\s\"']+")

    def filter(self, record):
        try:
            if record.args:
                record.args = tuple(self._pattern.sub(r"\1[redacted]", a) if isinstance(a, str) else a
                                    for a in (record.args if isinstance(record.args, tuple) else (record.args,)))
            if isinstance(record.msg, str):
                record.msg = self._pattern.sub(r"\1[redacted]", record.msg)
        except Exception:  # noqa: BLE001 - a log filter must never break a request
            pass
        return True


import logging as _logging  # noqa: E402
_logging.getLogger("werkzeug").addFilter(_RedactTicketFilter())

LOG_LEVEL = os.environ.get("ORCH_LOG_LEVEL", os.environ.get("LOG_LEVEL", "INFO")).upper()
logger = _configure_logging("orchestrator", log_type="ORC", level=LOG_LEVEL)
cmd_logger = logger.bind("CMD")
apm_logger = logger.bind("APM")
vnc_logger = logger.bind("VNC")
redroid_logger = logger.bind("RDR")
cvd_logger = logger.bind("CVD")

# Config
# mock | redroid | oci
# oci: Redroid golden for automation (default), Cuttlefish golden for camera
ORCH_DEPLOY_MODE = os.environ.get("ORCH_DEPLOY_MODE", "mock")
ORCH_MOCK_API_URL = os.environ.get("ORCH_MOCK_API_URL", "http://127.0.0.1:8080")
ORCH_MOCK_CAMERA_API_URL = os.environ.get("ORCH_MOCK_CAMERA_API_URL", "")
ORCH_API_TOKEN = os.environ.get("ORCH_API_TOKEN", "")
# Outbound token for the phones' Control API. Separate from ORCH_API_TOKEN,
# which authenticates callers of *this* service: one value cannot serve both,
# and forcing it to meant an operator could not express "the phone uses a
# different token" — the mismatch just showed up as 401s mid-run.
ORCH_CONTROL_API_TOKEN = os.environ.get("ORCH_CONTROL_API_TOKEN", "") or ORCH_API_TOKEN
ORCH_API_TIMEOUT = int(os.environ.get("ORCH_API_TIMEOUT", "30"))
ORCH_INSTANCE_NAME_PREFIX = os.environ.get("ORCH_INSTANCE_NAME_PREFIX", "orchestrated-phone")
ORCH_GOLDEN_IMAGE_ID = os.environ.get("GOLDEN_IMAGE_ID", "")
ORCH_REDROID_GOLDEN_IMAGE_ID = os.environ.get(
    "REDROID_GOLDEN_IMAGE_ID", os.environ.get("ORCH_REDROID_GOLDEN_IMAGE_ID", "")
)
ORCH_CUTTLEFISH_GOLDEN_IMAGE_ID = os.environ.get(
    "CUTTLEFISH_GOLDEN_IMAGE_ID",
    os.environ.get("ORCH_CUTTLEFISH_GOLDEN_IMAGE_ID", ORCH_GOLDEN_IMAGE_ID),
)
ORCH_MAX_INSTANCES = int(os.environ.get("ORCH_MAX_INSTANCES", "5"))
ORCH_MAX_REDROID_INSTANCES = int(os.environ.get("ORCH_MAX_REDROID_INSTANCES", "3"))
ORCH_MAX_CUTTLEFISH_INSTANCES = int(os.environ.get("ORCH_MAX_CUTTLEFISH_INSTANCES", "2"))
ORCH_DEPLOY_SCRIPT = os.environ.get(
    "ORCH_DEPLOY_SCRIPT",
    str(Path(__file__).resolve().parents[1] / "scripts" / "deploy-from-golden.sh")
)
ORCH_REDROID_UP_SCRIPT = os.environ.get(
    "ORCH_REDROID_UP_SCRIPT",
    str(Path(__file__).resolve().parents[1] / "scripts" / "redroid-up.sh")
)
ORCH_REDROID_ADB_PORT_BASE = int(os.environ.get("ORCH_REDROID_ADB_PORT_BASE", "5555"))
ORCH_OCI_PROFILE = os.environ.get("ORCH_OCI_PROFILE", "DEFAULT")
ORCH_OCI_CONFIG = os.environ.get("ORCH_OCI_CONFIG", str(Path.home() / ".oci" / "config"))
ORCH_OCI_AUTH = os.environ.get("ORCH_OCI_AUTH", "security_token")
# Where new automation phones come from. Unset: as before (containers or
# deploy-from-golden VMs by ORCH_DEPLOY_MODE). `oci`: one OCI Compute instance
# per phone through orchestrator/providers/oci_compute.py (ORCH_OCI_*).
ORCH_PHONE_PROVIDER = os.environ.get("ORCH_PHONE_PROVIDER", "").strip().lower()

# Isolation: persisted state, the chrome pool, and the ticketed relay.
# docs/isolation.md explains each one; docs/configuration.md lists them.
ORCH_STATE_DB = os.environ.get("ORCH_STATE_DB", "")
ORCH_PROFILE_ROOT = os.environ.get("ORCH_PROFILE_ROOT", "/var/lib/cloud-phone-orchestrator/profiles")
ORCH_PROFILE_ENCRYPTION = os.environ.get("ORCH_PROFILE_ENCRYPTION", "none")
# Per-owner encrypted, size-limited volumes (user_volumes.py, docs/isolation.md).
# Both flags default to the old behaviour.
ORCH_PHONE_DATA = os.environ.get("ORCH_PHONE_DATA", "instance")          # instance | per_user
ORCH_BROWSER_PROFILE = os.environ.get("ORCH_BROWSER_PROFILE", "dir")     # dir | volume
ORCH_VOLUME_ROOT = os.environ.get("ORCH_VOLUME_ROOT", "/var/lib/cloud-phone-orchestrator/volumes")
ORCH_VOLUME_MASTER_KEY_FILE = os.environ.get("ORCH_VOLUME_MASTER_KEY_FILE", "")
ORCH_VOLUME_QUOTA_FILE = os.environ.get("ORCH_VOLUME_QUOTA_FILE", "")
ORCH_QUOTA_PHONE_GB = float(os.environ.get("ORCH_QUOTA_PHONE_GB", "8"))
ORCH_QUOTA_BROWSER_GB = float(os.environ.get("ORCH_QUOTA_BROWSER_GB", "1"))
ORCH_HOST_MIN_FREE_GB = float(os.environ.get("ORCH_HOST_MIN_FREE_GB", "10"))
ORCH_VOLUME_SAMPLE_SECONDS = int(os.environ.get("ORCH_VOLUME_SAMPLE_SECONDS", "300"))
ORCH_CHROME_RUNTIME = os.environ.get("ORCH_CHROME_RUNTIME", "")
ORCH_CHROME_BIN = os.environ.get("ORCH_CHROME_BIN", "")
ORCH_CHROME_IMAGE = os.environ.get("ORCH_CHROME_IMAGE", "")
ORCH_CHROME_NETWORK = os.environ.get("ORCH_CHROME_NETWORK", "cloud-phone-browsers")
ORCH_CHROME_CDP_PORT_BASE = int(os.environ.get("ORCH_CHROME_CDP_PORT_BASE", "19222"))
ORCH_MAX_CHROME_INSTANCES = int(os.environ.get("ORCH_MAX_CHROME_INSTANCES", "4"))
# Browsers one owner may hold at once, one per `slot` (default 1: the single
# `main` browser every caller had before slots existed).
ORCH_BROWSER_SLOTS_PER_OWNER = int(os.environ.get("ORCH_BROWSER_SLOTS_PER_OWNER", "1"))
ORCH_CHROME_RELAY_BIND = os.environ.get("ORCH_CHROME_RELAY_BIND", "127.0.0.1")
ORCH_TICKET_TTL_SECONDS = int(os.environ.get("ORCH_TICKET_TTL_SECONDS", "300"))
ORCH_RELAY_RATE = float(os.environ.get("ORCH_RELAY_RATE", "50"))
ORCH_RELAY_BURST = int(os.environ.get("ORCH_RELAY_BURST", "200"))
ORCH_REQUIRE_TICKETS = os.environ.get("ORCH_REQUIRE_TICKETS", "").lower() in {"1", "true", "yes"}
ORCH_PUBLIC_URL = os.environ.get("ORCH_PUBLIC_URL", "")
ORCH_PHONE_FPS = float(os.environ.get("ORCH_PHONE_FPS", "4"))

# In-memory state
_instances = {}
_instances_lock = threading.Lock()
_ops = {}
_ops_lock = threading.Lock()
_leases = {}
_leases_lock = threading.Lock()

# Who last had Android state on an instance, and when it was last provably
# reset. A lease is a *booking*; tenancy is about *data left behind*, and the
# two have different lifetimes: releasing a lease frees the slot, it does not
# empty /data.
_instance_tenancy = {}
_instance_tenancy_lock = threading.Lock()
_user_sessions = {}
_user_sessions_lock = threading.Lock()
_next_adb_port = ORCH_REDROID_ADB_PORT_BASE
_adb_port_lock = threading.Lock()

# Write-through persistence (state_store.py). In memory until init_state()
# opens ORCH_STATE_DB; the __main__ block does that before serving.
_store = StateStore(":memory:")


def _persist(namespace, key, value):
    try:
        if value is None:
            _store.delete(namespace, key)
        else:
            _store.put(namespace, key, value)
    except Exception:  # noqa: BLE001 - a failed write must be loud, not fatal
        logger.exception("state write failed namespace=%s", namespace)
def _presented_token():
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return header.strip()


def _auth_state():
    token = _presented_token()
    return {
        "required": bool(ORCH_API_TOKEN),
        "presented": bool(token),
        "ok": (not ORCH_API_TOKEN) or token == ORCH_API_TOKEN,
    }


def _require_auth():
    if not ORCH_API_TOKEN:
        return None
    state = _auth_state()
    if state["ok"]:
        return None
    body = {
        "success": False,
        "error": "Unauthorized",
        "code": "auth_invalid" if state["presented"] else "auth_required",
        "auth_required": True,
    }
    response = jsonify(body)
    response.headers["WWW-Authenticate"] = 'Bearer realm="cloud-phone-orchestrator"'
    return response, 401


@app.before_request
def _auth_middleware():
    if request.path == "/health":
        return None
    if request.path.startswith("/relay/"):
        # The relay authenticates by session ticket (checked in each route),
        # not by the orchestrator token: a ticket is what OSLO hands a session
        # or the Watch page, and it opens one lease only.
        return None
    if ORCH_REQUIRE_TICKETS and request.path.startswith("/phones/"):
        return jsonify({
            "success": False,
            "error": "direct phone routes are off (ORCH_REQUIRE_TICKETS=1); use /relay/phone with a session ticket",
            "code": "tickets_required",
        }), 403
    return _require_auth()


def _normalize_steps(steps):
    if not isinstance(steps, list):
        raise ValueError("steps must be a list")
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError("each step must be an object")
        action = step.get("action")
        if action not in {"start_app", "input_text", "key", "tap", "sleep_ms"}:
            raise ValueError(f"Unsupported action: {action}")
        if action == "start_app" and not step.get("package"):
            raise ValueError("start_app requires package")
    return steps


def _get_lease(instance_id):
    with _leases_lock:
        return _leases.get(instance_id)


def _set_lease(instance_id, owner, ttl_seconds):
    with _leases_lock:
        _leases[instance_id] = {
            "owner": owner,
            "expires_at": time.time() + ttl_seconds
        }
        snapshot = dict(_leases[instance_id])
    _persist("leases", instance_id, snapshot)
    # The guard can only refuse what it knows about, so recording is part of
    # taking the lease rather than a separate step someone has to remember.
    record_tenancy(instance_id, owner)


def record_tenancy(instance_id, owner):
    """Note that `owner` had Android state on this instance."""
    with _instance_tenancy_lock:
        entry = _instance_tenancy.setdefault(instance_id, {})
        entry["last_owner"] = owner
        entry["last_used_at"] = time.time()
        snapshot = dict(entry)
    _persist("tenancy", instance_id, snapshot)


def record_reset(instance_id):
    """Note that this instance's /data was wiped.

    Call this only after a reset has actually completed. It is the evidence
    the guard looks for, so recording it optimistically defeats the guard.
    """
    with _instance_tenancy_lock:
        entry = _instance_tenancy.setdefault(instance_id, {})
        entry["reset_at"] = time.time()
        snapshot = dict(entry)
    _persist("tenancy", instance_id, snapshot)


def lease_decision(instance_id, owner):
    """May `owner` take this instance? Returns (ok, reason).

    Fails closed. An instance that carried someone else's Android state is
    refused unless a reset was recorded *after* that use -- so adding a wipe
    later makes instances reusable again automatically, with no change here.
    """
    with _instance_tenancy_lock:
        entry = dict(_instance_tenancy.get(instance_id) or {})
    last_owner = entry.get("last_owner")
    if not last_owner or last_owner == owner:
        return True, "no foreign tenancy"
    last_used = entry.get("last_used_at") or 0
    reset_at = entry.get("reset_at") or 0
    if reset_at > last_used:
        return True, "reset since the previous tenant"
    # Deliberately does not name the previous tenant: the caller is not
    # entitled to know whose data is on the box they were refused.
    return False, (
        "instance carries another tenant's Android state and has not been "
        "reset since; refusing to hand over /data"
    )


def can_lease_to(instance_id, owner):
    ok, _ = lease_decision(instance_id, owner)
    return ok


def _clear_lease(instance_id):
    with _leases_lock:
        _leases.pop(instance_id, None)
    if instance_id:
        _persist("leases", instance_id, None)


def _is_lease_valid(instance_id, owner=None):
    lease = _get_lease(instance_id)
    if not lease:
        return False
    if lease["expires_at"] < time.time():
        _clear_lease(instance_id)
        return False
    if owner and lease["owner"] != owner:
        return False
    return True


def _control_headers(instance=None):
    headers = {"Content-Type": "application/json"}
    if ORCH_CONTROL_API_TOKEN:
        headers["Authorization"] = f"Bearer {ORCH_CONTROL_API_TOKEN}"
    adb = (instance or {}).get("adb_connect")
    if adb:
        headers["X-Cloud-Phone-Adb"] = adb
    return headers


class ControlAuthError(RuntimeError):
    """The Control API rejected this orchestrator's token.

    Named separately so an operator config problem cannot be reported as a
    broken phone: ORCH_CONTROL_API_TOKEN here must equal API_TOKEN on the phone.
    """


def _raise_for_control_status(resp, url):
    if resp.status_code in (401, 403):
        raise ControlAuthError(
            f"Control API rejected the orchestrator token ({resp.status_code}) at {url} — "
            "ORCH_CONTROL_API_TOKEN must match API_TOKEN on the phone"
        )
    resp.raise_for_status()


def _control_post(api_url: str, path: str, payload=None, instance=None):
    url = f"{api_url}{path}"
    logger.info("Control POST %s payload=%s", url, redact(payload))
    resp = requests.post(
        url, json=payload, headers=_control_headers(instance), timeout=ORCH_API_TIMEOUT
    )
    _raise_for_control_status(resp, url)
    return resp.json()


def _control_get(api_url: str, path: str, instance=None):
    url = f"{api_url}{path}"
    logger.info("Control GET %s", url)
    resp = requests.get(url, headers=_control_headers(instance), timeout=ORCH_API_TIMEOUT)
    _raise_for_control_status(resp, url)
    return resp.json()


def _assert_phone_usable(health, api_url):
    """Reject a phone whose own `/health` says our token will not work.

    `/health` is open on the Control API, so it answers 200 to a caller every
    device endpoint will 401. Checking the auth block it reports turns that into
    one clear failure instead of a run where every step 401s.
    """
    if not isinstance(health, dict):
        return
    auth = health.get("auth") or {}
    if auth.get("required") and not auth.get("ok", True):
        raise ControlAuthError(
            f"phone at {api_url} reports status={health.get('status')} for this token — "
            "ORCH_CONTROL_API_TOKEN must match API_TOKEN on the phone"
        )


def _runtime_for_mode(mode=None):
    mode = mode or ORCH_DEPLOY_MODE
    if mode == "redroid":
        return "redroid"
    if mode == "oci":
        return "cuttlefish"
    return "mock"


def _create_instance_record(api_url: str, name: str, runtime=None, purpose=None, adb_connect=None, extra=None):
    inst_id = uuid.uuid4().hex
    purpose = resolve_purpose(purpose, runtime)
    runtime = runtime_for_purpose(purpose)
    record = {
        "id": inst_id,
        "name": name,
        "api_url": api_url,
        "created_at": time.time(),
        "last_used": time.time(),
        "mode": ORCH_DEPLOY_MODE,
        "runtime": runtime,
        "purpose": purpose,
        "adb_connect": adb_connect,
        "instance_ocid": None,
        "gapps": purpose == PURPOSE_AUTOMATION,
    }
    if extra:
        record.update(extra)
    with _instances_lock:
        _instances[inst_id] = record
    _persist("instances", inst_id, record)
    logger.info(
        "Instance registered id=%s name=%s api_url=%s mode=%s runtime=%s purpose=%s",
        inst_id, name, api_url, ORCH_DEPLOY_MODE, runtime, purpose,
    )
    return record


def _allocate_adb_port():
    global _next_adb_port
    with _adb_port_lock:
        port = _next_adb_port
        _next_adb_port += 1
        return port


def _count_runtime(runtime):
    return sum(1 for inst in _instances.values() if inst.get("runtime") == runtime)


_oci_provider = None
_oci_provider_lock = threading.Lock()


def _phone_provider():
    return (ORCH_PHONE_PROVIDER or "").strip().lower()


def oci_provider():
    """The OciComputeProvider, built from ORCH_OCI_* on first use (tests inject a fake)."""
    global _oci_provider
    with _oci_provider_lock:
        if _oci_provider is None:
            _oci_provider = ocp.OciComputeProvider()
        return _oci_provider


def set_oci_provider(provider):
    global _oci_provider
    with _oci_provider_lock:
        _oci_provider = provider


def provider_config_refusal():
    """Why ORCH_PHONE_PROVIDER cannot be served as configured, or None."""
    name = _phone_provider()
    if not name:
        return None
    if name != "oci":
        return f"ORCH_PHONE_PROVIDER must be unset or 'oci' (got {name!r})"
    if (ORCH_PHONE_DATA or "instance").strip().lower() == "per_user":
        return "ORCH_PHONE_PROVIDER=oci does not combine with ORCH_PHONE_DATA=per_user (a phone VM has no host volume)"
    try:
        return ocp.validate(ocp.config_from_env())
    except ocp.ProviderError as exc:
        return str(exc)


def _provider_health():
    if _phone_provider() != "oci":
        return {"name": "builtin"}
    out = {"name": "oci"}
    if _oci_provider is not None:
        out.update(_oci_provider.caps())
        out["auth"] = _oci_provider.auth_mode
    return out


def _runtime_limit(runtime):
    if runtime == RUNTIME_REDROID:
        if _phone_provider() == "oci" and _oci_provider is not None:
            return min(ORCH_MAX_REDROID_INSTANCES, _oci_provider.config["max_instances"])
        return ORCH_MAX_REDROID_INSTANCES
    if runtime == RUNTIME_CUTTLEFISH:
        return ORCH_MAX_CUTTLEFISH_INSTANCES
    return ORCH_MAX_INSTANCES


def _mock_api_url(runtime):
    if runtime == RUNTIME_CUTTLEFISH and ORCH_MOCK_CAMERA_API_URL:
        return ORCH_MOCK_CAMERA_API_URL
    return ORCH_MOCK_API_URL


def _golden_image_for(runtime):
    if runtime == RUNTIME_REDROID:
        return ORCH_REDROID_GOLDEN_IMAGE_ID or ORCH_GOLDEN_IMAGE_ID
    return ORCH_CUTTLEFISH_GOLDEN_IMAGE_ID or ORCH_GOLDEN_IMAGE_ID


def _find_idle_instance(runtime):
    with _instances_lock:
        for inst in _instances.values():
            if inst.get("runtime") != runtime:
                continue
            if _is_lease_valid(inst["id"]):
                continue
            inst["last_used"] = time.time()
            return inst
    return None


def _phone_container_cmd(argv):
    """Run redroid-up.sh. One seam so tests can read the argv without Docker."""
    return subprocess.check_output(argv, text=True)


def _redroid_dry_run():
    return os.environ.get("ORCH_REDROID_DRY_RUN", "").lower() in {"1", "true", "yes"}


def _provision_redroid_local(data_dir=None, data_source=None):
    port = _allocate_adb_port()
    name = f"{ORCH_INSTANCE_NAME_PREFIX}-{port}"
    cmd = [
        ORCH_REDROID_UP_SCRIPT,
        "--name", name,
        "--adb-port", str(port),
    ]
    if data_dir:
        # ORCH_PHONE_DATA=per_user: the owner's mounted volume is /data from the start.
        cmd += ["--data-dir", data_dir]
    cmd.append("--json")
    if _redroid_dry_run():
        cmd.append("--dry-run")
    redroid_logger.info("Provisioning local Redroid container: %s", " ".join(cmd))
    out = _phone_container_cmd(cmd)
    line = out.strip().splitlines()[-1] if out.strip() else "{}"
    data = json.loads(line)
    adb = data.get("adb_connect") or f"127.0.0.1:{port}"
    api_url = os.environ.get("ORCH_REDROID_API_URL", ORCH_MOCK_API_URL)
    return _create_instance_record(
        api_url,
        name,
        runtime=RUNTIME_REDROID,
        purpose=PURPOSE_AUTOMATION,
        adb_connect=adb,
        extra={"container": data.get("name"), "image": data.get("image"),
               **({"data_source": data_source} if data_source else {})},
    )


def _provision_oci(runtime, purpose):
    image_id = _golden_image_for(runtime)
    if not image_id:
        env_name = "REDROID_GOLDEN_IMAGE_ID" if runtime == RUNTIME_REDROID else "CUTTLEFISH_GOLDEN_IMAGE_ID"
        raise RuntimeError(f"{env_name} required for OCI {runtime} provisioning")

    name = f"{ORCH_INSTANCE_NAME_PREFIX}-{runtime}-{time.strftime('%Y%m%d-%H%M%S')}"
    cmd = [
        ORCH_DEPLOY_SCRIPT,
        "--platform", runtime,
        "--image-id", image_id,
        "--name", name,
        "--wait-check",
    ]
    (cvd_logger if runtime == RUNTIME_CUTTLEFISH else redroid_logger).info(
        "Provisioning OCI %s instance: %s", runtime, " ".join(cmd)
    )
    subprocess.check_call(cmd)

    info_path = Path(f"/tmp/instance-{name}.json")
    if not info_path.exists():
        raise RuntimeError(f"Instance info not found: {info_path}")
    data = json.loads(info_path.read_text())
    public_ip = data.get("public_ip")
    if not public_ip:
        raise RuntimeError("Public IP missing in instance info")
    instance_ocid = data.get("instance_ocid")
    api_url = f"http://{public_ip}:8080"
    record = _create_instance_record(
        api_url,
        name,
        runtime=runtime,
        purpose=purpose,
        extra={"public_ip": public_ip, "golden_image": image_id},
    )
    record["instance_ocid"] = instance_ocid
    return record


def _provision_oci_compute(purpose, owner=None):
    """ORCH_PHONE_PROVIDER=oci: one Compute instance per phone, tagged with the owner."""
    rec = oci_provider().launch(owner or "unowned")
    return _create_instance_record(
        rec["api_url"],
        rec["name"],
        runtime=RUNTIME_REDROID,
        purpose=purpose,
        extra={
            "provider": "oci",
            "instance_ocid": rec["instance_ocid"],
            "private_ip": rec.get("private_ip"),
            "image": rec.get("image_id"),
            "shape": rec.get("shape"),
            "owner_tag": rec.get("owner_tag"),
        },
    )


def _check_capacity(runtime):
    """Raise the pool-full RuntimeError if one more `runtime` instance would not fit."""
    with _instances_lock:
        if len(_instances) >= ORCH_MAX_INSTANCES:
            raise RuntimeError(f"Instance limit reached (ORCH_MAX_INSTANCES={ORCH_MAX_INSTANCES})")
        n = _count_runtime(runtime)
        limit = _runtime_limit(runtime)
        if n >= limit:
            raise RuntimeError(
                f"{runtime} pool full ({n}/{limit}). "
                f"Camera streams use Cuttlefish; automation uses Redroid."
            )


def _provision_instance(purpose=None, runtime=None, data_dir=None, data_source=None, owner=None):
    if is_browser_request(purpose, runtime):
        raise ValueError("browser leases go through POST /sessions with purpose=browser")
    purpose = resolve_purpose(purpose, runtime)
    runtime = runtime_for_purpose(purpose)

    _check_capacity(runtime)

    if runtime == RUNTIME_REDROID and _phone_provider() == "oci":
        return _provision_oci_compute(purpose, owner=owner)

    if ORCH_DEPLOY_MODE == "mock":
        api_url = _mock_api_url(runtime)
        name = f"{ORCH_INSTANCE_NAME_PREFIX}-{runtime}-mock-{_count_runtime(runtime) + 1}"
        logger.info("Mock provisioning %s (%s) -> %s", runtime, purpose, api_url)
        return _create_instance_record(
            api_url,
            name,
            runtime=runtime,
            purpose=purpose,
            adb_connect=f"mock://phone-{uuid.uuid4().hex[:8]}",
            extra={"data_source": data_source} if data_source else None,
        )

    if runtime == RUNTIME_REDROID and ORCH_DEPLOY_MODE == "redroid":
        return _provision_redroid_local(data_dir=data_dir, data_source=data_source)

    if runtime == RUNTIME_CUTTLEFISH and ORCH_DEPLOY_MODE == "redroid":
        if not _golden_image_for(RUNTIME_CUTTLEFISH):
            raise RuntimeError(
                "camera purpose needs a Cuttlefish OCI golden "
                "(CUTTLEFISH_GOLDEN_IMAGE_ID); local Redroid mode has no ingest host"
            )
        return _provision_oci(RUNTIME_CUTTLEFISH, PURPOSE_CAMERA)

    if ORCH_DEPLOY_MODE != "oci":
        raise RuntimeError(f"Unsupported ORCH_DEPLOY_MODE: {ORCH_DEPLOY_MODE}")

    return _provision_oci(runtime, purpose)


def _terminate_instance(instance_ocid: str):
    if not instance_ocid:
        raise RuntimeError("instance_ocid required to terminate")
    cmd = [
        "oci", "compute", "instance", "terminate",
        "--instance-id", instance_ocid,
        "--force",
        "--profile", ORCH_OCI_PROFILE,
        "--config-file", ORCH_OCI_CONFIG,
        "--auth", ORCH_OCI_AUTH
    ]
    logger.info("Terminating OCI instance: %s", " ".join(cmd))
    subprocess.check_call(cmd)


def _get_or_create_instance(instance_id=None, purpose=None, runtime=None, provision=True, owner=None):
    purpose = resolve_purpose(purpose, runtime)
    runtime = runtime_for_purpose(purpose)

    if instance_id:
        with _instances_lock:
            inst = _instances.get(instance_id)
        if not inst:
            raise RuntimeError(f"instance not found: {instance_id}")
        if inst.get("runtime") != runtime:
            raise RuntimeError(
                f"instance {instance_id} is {inst.get('runtime')} but request needs {runtime}"
            )
        inst["last_used"] = time.time()
        logger.info("Using requested instance id=%s runtime=%s", inst["id"], runtime)
        return inst

    idle = _find_idle_instance(runtime)
    if idle:
        logger.info("Reusing idle %s instance id=%s name=%s", runtime, idle["id"], idle["name"])
        return idle
    if not provision:
        raise RuntimeError("phone in use")
    logger.info("No idle %s instance; provisioning purpose=%s", runtime, purpose)
    return _provision_instance(purpose=purpose, runtime=runtime, owner=owner)


def _run_steps(api_url: str, steps, instance=None):
    results = []
    for step in steps:
        action = step.get("action")
        cmd_logger.info("step action=%s payload=%s", action, redact(step))
        if action == "start_app":
            package = step.get("package")
            if not package:
                raise ValueError("start_app requires package")
            results.append(_control_post(api_url, f"/apps/{package}/start", instance=instance))
        elif action == "input_text":
            text = step.get("text", "")
            results.append(_control_post(
                api_url, "/device/input", {"type": "text", "text": text}, instance=instance
            ))
        elif action == "key":
            keycode = int(step.get("keycode", 66))
            results.append(_control_post(
                api_url, "/device/input", {"type": "key", "keycode": keycode}, instance=instance
            ))
        elif action == "tap":
            x = int(step.get("x", 500))
            y = int(step.get("y", 500))
            results.append(_control_post(
                api_url, "/device/input", {"type": "tap", "x": x, "y": y}, instance=instance
            ))
        elif action == "sleep_ms":
            time.sleep(int(step.get("duration", 500)) / 1000.0)
            results.append({"success": True, "sleep_ms": step.get("duration", 500)})
        else:
            raise ValueError(f"Unsupported action: {action}")
    return results


def _build_login_steps(payload):
    app_package = payload.get("app_package")
    login = payload.get("login", {})
    username = login.get("username", "")
    password = login.get("password", "")
    password_tap = login.get("password_tap")
    submit_tap = login.get("submit_tap")

    steps = [{"action": "start_app", "package": app_package}]
    steps.append({"action": "sleep_ms", "duration": 800})
    steps.append({"action": "input_text", "text": username})
    steps.append({"action": "sleep_ms", "duration": 300})
    if password_tap and "x" in password_tap and "y" in password_tap:
        steps.append({"action": "tap", "x": password_tap["x"], "y": password_tap["y"]})
    else:
        steps.append({"action": "key", "keycode": 61})
    steps.append({"action": "input_text", "text": password})
    if submit_tap and "x" in submit_tap and "y" in submit_tap:
        steps.append({"action": "tap", "x": submit_tap["x"], "y": submit_tap["y"]})
    else:
        steps.append({"action": "key", "keycode": 66})
    logger.info("Built login steps for package=%s steps=%s", app_package, steps)
    return steps


def _run_operation(op_id, payload):
    with _ops_lock:
        op = _ops.get(op_id)
        if not op:
            return
        op["status"] = "running"
        op["updated_at"] = time.time()

    try:
        logger.info("Operation started id=%s payload=%s", op_id, payload)
        instance = _get_or_create_instance(
            payload.get("instance_id"),
            purpose=payload.get("purpose"),
            runtime=payload.get("runtime"),
        )
        api_url = instance["api_url"]
        _assert_phone_usable(_control_get(api_url, "/health", instance=instance), api_url)

        if payload.get("steps"):
            steps = _normalize_steps(payload["steps"])
        else:
            steps = _build_login_steps(payload)
        results = _run_steps(api_url, steps, instance=instance)

        with _ops_lock:
            op["status"] = "done"
            op["result"] = {"steps": steps, "results": results, "instance": instance}
            op["updated_at"] = time.time()
        logger.info("Operation complete id=%s status=done", op_id)
    except Exception as exc:
        logger.exception("Operation failed")
        with _ops_lock:
            op["status"] = "failed"
            op["error"] = str(exc)
            op["updated_at"] = time.time()


@app.route("/operations", methods=["POST"])
def create_operation():
    payload = request.get_json() or {}
    if payload.get("operation") != "login" and not payload.get("steps"):
        return jsonify({"error": "operation=login or steps required"}), 400
    if payload.get("operation") == "login" and not payload.get("app_package"):
        return jsonify({"error": "app_package required for login operation"}), 400

    op_id = uuid.uuid4().hex
    op = {
        "id": op_id,
        "status": "queued",
        "created_at": time.time(),
        "updated_at": time.time(),
        "payload": payload
    }
    with _ops_lock:
        _ops[op_id] = op
    logger.info("Queued operation id=%s", op_id)

    thread = threading.Thread(target=_run_operation, args=(op_id, payload), daemon=True)
    thread.start()

    return jsonify({"operation_id": op_id, "status": "queued"}), 202


@app.route("/operations/<op_id>", methods=["GET"])
def get_operation(op_id):
    with _ops_lock:
        op = _ops.get(op_id)
    if not op:
        return jsonify({"error": "operation not found"}), 404
    return jsonify(op)


@app.route("/instances", methods=["GET"])
def list_instances():
    with _instances_lock:
        return jsonify(list(_instances.values()))


@app.route("/instances", methods=["POST"])
def create_instance():
    payload = request.get_json(silent=True) or {}
    try:
        inst = _provision_instance(
            purpose=payload.get("purpose"),
            runtime=payload.get("runtime"),
            owner=payload.get("owner") or payload.get("owner_user_id"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except ocp.ProviderError as exc:
        return jsonify({"error": str(exc), "code": exc.code, **exc.extra}), exc.status
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify(inst), 201


@app.route("/instances/<instance_id>", methods=["DELETE"])
def delete_instance(instance_id):
    with _instances_lock:
        inst = _instances.get(instance_id)
    if not inst:
        return jsonify({"error": "instance not found"}), 404
    if inst.get("provider") == "oci":
        try:
            oci_provider().terminate(inst.get("instance_ocid"))
        except ocp.ProviderError as exc:
            return jsonify({"error": str(exc), "code": exc.code, **exc.extra}), exc.status
        with _instances_lock:
            _instances.pop(instance_id, None)
        _persist("instances", instance_id, None)
        _clear_lease(instance_id)
        return jsonify({"success": True, "message": "OCI phone instance terminated"}), 200
    if inst.get("mode") == "redroid" and inst.get("name"):
        try:
            subprocess.check_call(
                [ORCH_REDROID_UP_SCRIPT, "--name", inst["name"], "--down"],
            )
        except Exception as exc:
            logger.warning("redroid-down failed: %s", exc)
        with _instances_lock:
            _instances.pop(instance_id, None)
        _persist("instances", instance_id, None)
        _clear_lease(instance_id)
        return jsonify({"success": True, "message": "redroid container removed"}), 200

    if inst.get("mode") != "oci":
        with _instances_lock:
            _instances.pop(instance_id, None)
        _persist("instances", instance_id, None)
        return jsonify({"success": True, "message": "instance removed"}), 200

    try:
        _terminate_instance(inst.get("instance_ocid"))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    with _instances_lock:
        _instances.pop(instance_id, None)
    _persist("instances", instance_id, None)
    return jsonify({"success": True, "message": "instance terminated"}), 200


@app.route("/instances/<instance_id>/lease", methods=["POST"])
def lease_instance(instance_id):
    data = request.get_json() or {}
    owner = data.get("owner", "default")
    ttl = int(data.get("ttl_seconds", 300))
    if ttl < 10:
        return jsonify({"error": "ttl_seconds must be >= 10"}), 400
    if _phone_per_user():
        # A raw lease would skip mounting the owner's /data volume (and the
        # tenancy guard); per-user phones are leased through POST /sessions only.
        return jsonify({"error": "ORCH_PHONE_DATA=per_user: lease phones with POST /sessions",
                        "code": "use_sessions"}), 409
    if _is_lease_valid(instance_id):
        return jsonify({"error": "instance already leased"}), 409
    _set_lease(instance_id, owner, ttl)
    return jsonify({"success": True, "instance_id": instance_id, "owner": owner, "ttl_seconds": ttl})


@app.route("/instances/<instance_id>/lease", methods=["DELETE"])
def release_instance(instance_id):
    _clear_lease(instance_id)
    return jsonify({"success": True, "instance_id": instance_id})


def _require_instance(instance_id):
    with _instances_lock:
        inst = _instances.get(instance_id)
    if not inst:
        return None, (jsonify({"error": "instance not found"}), 404)
    return inst, None


@app.route("/phones/<instance_id>/status", methods=["GET"])
def phone_status(instance_id):
    inst, err = _require_instance(instance_id)
    if err:
        return err
    data = _control_get(inst["api_url"], "/status", instance=inst)
    return jsonify(data)


@app.route("/phones/<instance_id>/health", methods=["GET"])
def phone_health(instance_id):
    inst, err = _require_instance(instance_id)
    if err:
        return err
    data = _control_get(inst["api_url"], "/health", instance=inst)
    return jsonify(data)


@app.route("/phones/<instance_id>/input", methods=["POST"])
def phone_input(instance_id):
    inst, err = _require_instance(instance_id)
    if err:
        return err
    data = request.get_json() or {}
    payload = {"type": data.get("type", "tap")}
    payload.update(data)
    result = _control_post(inst["api_url"], "/device/input", payload, instance=inst)
    return jsonify(result)


@app.route("/phones/<instance_id>/screenshot", methods=["GET"])
def phone_screenshot(instance_id):
    inst, err = _require_instance(instance_id)
    if err:
        return err
    data = _control_get(inst["api_url"], "/device/screenshot/base64", instance=inst)
    return jsonify(data)


@app.route("/phones/<instance_id>/jobs", methods=["POST"])
def phone_job_submit(instance_id):
    inst, err = _require_instance(instance_id)
    if err:
        return err
    payload = request.get_json() or {}
    data = _control_post(inst["api_url"], "/jobs", payload, instance=inst)
    return jsonify(data), 202


@app.route("/phones/<instance_id>/jobs/<job_id>", methods=["GET"])
def phone_job_poll(instance_id, job_id):
    inst, err = _require_instance(instance_id)
    if err:
        return err
    data = _control_get(inst["api_url"], f"/jobs/{job_id}", instance=inst)
    return jsonify(data)


@app.route("/phones/<instance_id>/ui", methods=["POST"])
def phone_ui(instance_id):
    inst, err = _require_instance(instance_id)
    if err:
        return err
    payload = request.get_json() or {}
    cmd_logger.info("relay ui instance=%s payload=%s", instance_id, redact(payload))
    data = _control_post(inst["api_url"], "/ui/command", payload, instance=inst)
    return jsonify(data)


@app.route("/phones/<instance_id>/appium", methods=["GET"])
def phone_appium(instance_id):
    inst, err = _require_instance(instance_id)
    if err:
        return err
    apm_logger.info("relay appium status instance=%s", instance_id)
    data = _control_get(inst["api_url"], "/appium/status", instance=inst)
    return jsonify(data)


@app.route("/phones/<instance_id>/vnc", methods=["GET"])
def phone_vnc(instance_id):
    inst, err = _require_instance(instance_id)
    if err:
        return err
    vnc_logger.info("relay vnc viewport instance=%s", instance_id)
    data = _control_get(inst["api_url"], "/vnc/status", instance=inst)
    return jsonify(data)


@app.route("/phones/<instance_id>/logs", methods=["GET"])
def phone_logs(instance_id):
    inst, err = _require_instance(instance_id)
    if err:
        return err
    q = request.query_string.decode() if request.query_string else ""
    path = "/logs" + (f"?{q}" if q else "")
    data = _control_get(inst["api_url"], path, instance=inst)
    return jsonify(data)


@app.route("/logs", methods=["GET"])
def orch_logs():
    log_type = request.args.get("type")
    try:
        n = int(request.args.get("n", "200"))
    except ValueError:
        n = 200
    items = recent_logs(log_type=log_type, n=n)
    return jsonify({"count": len(items), "logs": items})


def _pool_snapshot(items=None):
    if items is None:
        with _instances_lock:
            items = list(_instances.values())
    snapshot = {}
    for purpose, runtime in (
        (PURPOSE_AUTOMATION, RUNTIME_REDROID),
        (PURPOSE_CAMERA, RUNTIME_CUTTLEFISH),
    ):
        members = [i for i in items if i.get("runtime") == runtime]
        leased = sum(1 for i in members if _is_lease_valid(i["id"]))
        snapshot[purpose] = {
            "runtime": runtime,
            "total": len(members),
            "leased": leased,
            "idle": len(members) - leased,
            "max": _runtime_limit(runtime),
        }
    return snapshot



def _build_adapters(instance):
    """Wire the surfaces this orchestrator can actually reach.

    `mobile` is always available (Control API). `web` and `chrome` are reached
    through an agent-side driver URL; when unset the surface is simply absent,
    so a procedure naming it fails validation instead of silently no-op'ing.
    """
    adapters = {
        proc.MOBILE: proc.MobileAdapter(
            control_post=_control_post_for_instance,
            control_get=_control_get_for_instance,
            instance=instance,
        )
    }

    driver_url = os.environ.get("ORCH_WEB_DRIVER_URL", "")
    if driver_url:
        adapters[proc.WEB] = proc.WebAdapter(_remote_driver(driver_url, proc.WEB))

    bridge_url = os.environ.get("ORCH_CHROME_BRIDGE_URL", "")
    if bridge_url:
        adapters[proc.CHROME] = proc.ChromeAdapter(_remote_driver(bridge_url, proc.CHROME))

    if os.environ.get("ORCH_ENABLE_CONSOLE", "").lower() in {"1", "true", "yes"}:
        adapters[proc.CONSOLE] = proc.ConsoleAdapter(_console_runner)

    return adapters


def _control_post_for_instance(path, payload=None, instance=None):
    return _control_post(instance["api_url"], path, payload, instance=instance)


def _control_get_for_instance(path, instance=None):
    return _control_get(instance["api_url"], path, instance=instance)


def _remote_driver(base_url, surface):
    """POST the canonical step to an agent-side driver and return its JSON."""
    def drive(action, step):
        resp = requests.post(
            f"{base_url.rstrip('/')}/{surface}/step",
            json={"action": action, "step": step},
            headers=_control_headers(),
            timeout=ORCH_API_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    return drive


def _console_runner(action, step):
    if action == "read":
        return {"success": True, "stdout": ""}
    completed = subprocess.run(
        step.get("command", ""),
        shell=True,
        capture_output=True,
        text=True,
        timeout=int(step.get("timeout_s", 60)),
    )
    return {
        "success": completed.returncode == 0,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "exit_code": completed.returncode,
    }


@app.route("/procedures/surfaces", methods=["GET"])
def procedure_surfaces():
    adapters = _build_adapters({"api_url": ORCH_MOCK_API_URL})
    return jsonify({
        "surfaces": proc.surface_capabilities(adapters),
        "actions": sorted(proc.ACTIONS),
        "sensitive": sorted(proc.SENSITIVE_ACTIONS),
    })


@app.route("/procedures/validate", methods=["POST"])
def procedure_validate():
    data = request.get_json() or {}
    instance = None
    if data.get("instance_id"):
        instance, err = _require_instance(data["instance_id"])
        if err:
            return err
    adapters = _build_adapters(instance or {"api_url": ORCH_MOCK_API_URL})
    try:
        steps = proc.validate_procedure(
            data.get("steps") or [],
            adapters,
            default_surface=data.get("surface", proc.MOBILE),
            approve=bool(data.get("approve")),
        )
    except proc.ApprovalRequiredError as exc:
        return jsonify({"valid": False, "needs_approval": True, "error": str(exc)}), 200
    except proc.ProcedureError as exc:
        return jsonify({"valid": False, "error": str(exc)}), 400
    return jsonify({"valid": True, "steps": steps, "count": len(steps)})


def _touches_mobile(payload):
    """Whether a procedure will drive a phone, and so needs a usable one."""
    default_surface = payload.get("surface", proc.MOBILE)
    steps = payload.get("steps") or []
    if not steps:
        return default_surface == proc.MOBILE
    return any((step or {}).get("surface", default_surface) == proc.MOBILE
               for step in steps)


def _run_procedure_operation(op_id, payload):
    with _ops_lock:
        op = _ops.get(op_id)
        if not op:
            return
        op["status"] = "running"
        op["updated_at"] = time.time()

    try:
        instance = _get_or_create_instance(
            payload.get("instance_id"),
            purpose=payload.get("purpose"),
            runtime=payload.get("runtime"),
        )
        adapters = _build_adapters(instance)
        steps = payload.get("steps")
        if _touches_mobile(payload):
            api_url = instance["api_url"]
            _assert_phone_usable(
                _control_get(api_url, "/health", instance=instance), api_url
            )
        if not steps:
            steps = proc.login_procedure(
                payload.get("app_package") or payload.get("url"),
                (payload.get("login") or {}).get("username", ""),
                (payload.get("login") or {}).get("password", ""),
                surface=payload.get("surface", proc.MOBILE),
                password_label=(payload.get("login") or {}).get("password_label"),
            )
        outcome = proc.run_procedure(
            steps,
            adapters,
            default_surface=payload.get("surface", proc.MOBILE),
            approve=bool(payload.get("approve")),
            logger=logger,
        )
        with _ops_lock:
            op["status"] = "done" if outcome["status"] == "done" else "failed"
            op["result"] = {"instance": instance, **outcome}
            op["error"] = outcome.get("error")
            op["updated_at"] = time.time()
        logger.info("Procedure %s finished status=%s", op_id, op["status"])
    except proc.ApprovalRequiredError as exc:
        with _ops_lock:
            op["status"] = "needs_approval"
            op["error"] = str(exc)
            op["updated_at"] = time.time()
    except Exception as exc:
        logger.exception("Procedure failed")
        with _ops_lock:
            op["status"] = "failed"
            op["error"] = str(exc)
            op["updated_at"] = time.time()


@app.route("/procedures", methods=["POST"])
def create_procedure_run():
    payload = request.get_json() or {}
    if not payload.get("steps") and not (payload.get("app_package") or payload.get("url")):
        return jsonify({"error": "steps or app_package/url required"}), 400

    op_id = uuid.uuid4().hex
    with _ops_lock:
        _ops[op_id] = {
            "id": op_id,
            "kind": "procedure",
            "status": "queued",
            "created_at": time.time(),
            "updated_at": time.time(),
            "payload": payload,
        }
    logger.info("Queued procedure id=%s surface=%s", op_id, payload.get("surface", proc.MOBILE))

    if payload.get("sync"):
        _run_procedure_operation(op_id, payload)
        with _ops_lock:
            return jsonify(_ops[op_id])

    threading.Thread(target=_run_procedure_operation, args=(op_id, payload), daemon=True).start()
    return jsonify({"procedure_id": op_id, "status": "queued"}), 202


@app.route("/procedures/<op_id>", methods=["GET"])
def get_procedure_run(op_id):
    with _ops_lock:
        op = _ops.get(op_id)
    if not op:
        return jsonify({"error": "procedure not found"}), 404
    return jsonify(op)


def ingest_for(inst):
    """Where OBS should publish for this instance, or None.

    Only ingest instances have a camera to feed. Redroid deliberately has no
    virtual camera (see RUNTIME-SPLIT.md), so handing back a publish URL for
    one would be a lie the caller discovers by streaming into a void.

    The host is read off the instance's own api_url rather than configured
    separately: one fact in one place. nginx-rtmp serves the `live`
    application on the default RTMP port, with `cam` as the stream key
    (docs/CUTTLEFISH_PHASE2_RTMP_BRIDGE.md).
    """
    purpose = resolve_purpose(inst.get("purpose"), inst.get("runtime"))
    if purpose == PURPOSE_AUTOMATION:
        return None
    api_url = (inst or {}).get("api_url") or ""
    try:
        host = urlparse(api_url).hostname
    except (ValueError, TypeError):
        host = None
    if not host:
        # We cannot say where to publish, so we do not say.
        return None
    return {
        "rtmp_url": f"rtmp://{host}/live/cam",
        "rtmp_app": "live",
        "stream_key": "cam",
        "host": host,
    }


def _session_from_instance(owner_user_id, inst, ttl_seconds, purpose=None):
    purpose = resolve_purpose(purpose or inst.get("purpose"), inst.get("runtime"))
    return {
        "owner_user_id": owner_user_id,
        "instance_id": inst["id"],
        "api_url": inst.get("api_url"),
        "adb_connect": inst.get("adb_connect"),
        "runtime": inst.get("runtime"),
        "purpose": purpose,
        "ttl_seconds": ttl_seconds,
        "name": inst.get("name"),
        "gapps": inst.get("gapps"),
        "ingest": ingest_for({**inst, "purpose": purpose}),
    }


# ==========================================================================
# Per-owner volumes (user_volumes.py): phone /data and browser profiles that
# belong to the owner, are size-limited and encrypted with the owner's key.
# Every path below is gated on ORCH_PHONE_DATA / ORCH_BROWSER_PROFILE; with
# both at their defaults nothing here runs.
# ==========================================================================

_volume_manager = None
_volume_manager_lock = threading.Lock()


def _phone_per_user():
    return (ORCH_PHONE_DATA or "instance").strip().lower() == "per_user"


def _browser_volume():
    return (ORCH_BROWSER_PROFILE or "dir").strip().lower() == "volume"


def _volumes_enabled():
    return _phone_per_user() or _browser_volume()


def _wire_volume_manager(mgr):
    """The orchestrator owns the registry (state DB) and the answer to "is it leased?"."""
    mgr.store_get = lambda key: _store.get("volumes", key)
    mgr.store_put = lambda key, value: _persist("volumes", key, value)
    mgr.store_delete = lambda key: _persist("volumes", key, None)
    mgr.lease_check = _volume_in_use
    return mgr


def volume_manager():
    global _volume_manager
    with _volume_manager_lock:
        if _volume_manager is None:
            _volume_manager = _wire_volume_manager(uv.VolumeManager(
                ORCH_VOLUME_ROOT, ORCH_VOLUME_MASTER_KEY_FILE,
                phone_quota_gb=ORCH_QUOTA_PHONE_GB, browser_quota_gb=ORCH_QUOTA_BROWSER_GB,
                quota_file=ORCH_VOLUME_QUOTA_FILE, host_min_free_gb=ORCH_HOST_MIN_FREE_GB,
                logger=logger,
            ))
        return _volume_manager


def set_volume_manager(mgr):
    """Tests (and embedders) swap the manager; None returns to the default."""
    global _volume_manager
    with _volume_manager_lock:
        _volume_manager = _wire_volume_manager(mgr) if mgr is not None else None


def volume_config_refusal():
    """Why the orchestrator must not start with this volume configuration, or None."""
    phone = (ORCH_PHONE_DATA or "instance").strip().lower()
    browser = (ORCH_BROWSER_PROFILE or "dir").strip().lower()
    if phone not in ("instance", "per_user"):
        return f"ORCH_PHONE_DATA must be 'instance' or 'per_user' (got {phone!r})"
    if browser not in ("dir", "volume"):
        return f"ORCH_BROWSER_PROFILE must be 'dir' or 'volume' (got {browser!r})"
    if phone == "instance" and browser == "dir":
        return None
    if phone == "per_user" and ORCH_DEPLOY_MODE not in ("mock", "redroid"):
        return (f"ORCH_PHONE_DATA=per_user needs ORCH_DEPLOY_MODE=redroid (got {ORCH_DEPLOY_MODE}): "
                "oci phones keep /data on their own VM, where this host cannot mount it")
    if not ORCH_VOLUME_ROOT or not os.path.isabs(ORCH_VOLUME_ROOT):
        return "ORCH_VOLUME_ROOT must be an absolute path"
    try:
        uv.load_master_key(ORCH_VOLUME_MASTER_KEY_FILE)
    except uv.VolumeError as exc:
        return f"per-owner volumes are on but {exc}"
    chrome_mode = (ORCH_CHROME_RUNTIME or ("mock" if ORCH_DEPLOY_MODE == "mock" else "docker")).lower()
    containers = (phone == "per_user" and ORCH_DEPLOY_MODE == "redroid") or \
                 (browser == "volume" and chrome_mode == "docker")
    if containers:
        problem = _mount_namespace_check()
        if problem:
            return f"per-owner volumes are on but {problem}"
    return None


def _mount_namespace_check():
    return uv.mount_namespace_problem()


def _volume_in_use(token, kind):
    """Is this owner's volume of this kind held by a live lease (or still attached)?"""
    if kind == uv.KIND_PHONE:
        with _user_sessions_lock:
            sessions = list(_user_sessions.items())
        for owner, sess in sessions:
            if bp.owner_token(owner) == token and _is_lease_valid(sess.get("instance_id"), owner=owner):
                return True
        with _instances_lock:
            sources = [inst.get("data_source") or {} for inst in _instances.values()]
        return any(src.get("owner_token") == token and src.get("attached") for src in sources)
    if kind == uv.KIND_BROWSER:
        with _browser_lock:
            return any(s.get("owner_token") == token for s in _browser_sessions.values())
    return True  # unknown kind: refuse to delete


def _adb_port_of(inst):
    try:
        return int(str(inst.get("adb_connect") or "").rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None


def _start_phone_container(inst, mount):
    """(Re)start a Redroid container with `mount` as /data. Mock instances have no container."""
    if inst.get("mode") != "redroid":
        return
    cmd = [ORCH_REDROID_UP_SCRIPT, "--name", inst["name"], "--adb-port", str(_adb_port_of(inst) or 5555),
           "--data-dir", mount, "--json"]
    if inst.get("image"):
        cmd += ["--image", inst["image"]]
    if _redroid_dry_run():
        cmd.append("--dry-run")
    redroid_logger.info("Starting %s with a per-owner /data volume", inst["name"])
    _phone_container_cmd(cmd)


def _stop_phone_container(inst):
    if inst.get("mode") != "redroid":
        return
    cmd = [ORCH_REDROID_UP_SCRIPT, "--name", inst["name"], "--down"]
    if _redroid_dry_run():
        cmd.append("--dry-run")
    _phone_container_cmd(cmd)


def _save_instance(inst):
    with _instances_lock:
        snapshot = dict(inst)
    _persist("instances", inst["id"], snapshot)


def _attach_phone_data(inst, token, mount):
    """Make the owner's mounted volume this instance's /data.

    Switching /data to a different owner's volume is recorded as a reset: the
    previous tenant's data is no longer reachable from the instance (their
    volume is closed; a per-instance volume is removed by `--down`). The
    tenancy guard then checks that evidence like any other reset.
    """
    src = dict(inst.get("data_source") or {"type": "instance"})
    same_owner = src.get("type") == "user_volume" and src.get("owner_token") == token
    if same_owner and src.get("attached"):
        return
    if src.get("attached"):
        _detach_phone_data(inst)  # another owner's volume, left by a lapsed lease
    if src.get("type") != "user_volume":
        _stop_phone_container(inst)  # running on its per-instance volume; `down -v` drops it
    _start_phone_container(inst, mount)
    inst["data_source"] = {"type": "user_volume", "owner_token": token, "kind": uv.KIND_PHONE,
                           "attached": True, "attached_at": time.time()}
    _save_instance(inst)
    if not same_owner:
        record_reset(inst["id"])
        logger.info("Instance %s /data switched to owner token %s's volume (recorded as a reset)",
                    inst["id"], token)


def _detach_phone_data(inst):
    """Stop the container and close the owner's volume. Keeps the volume."""
    src = dict(inst.get("data_source") or {})
    if src.get("type") != "user_volume" or not src.get("attached"):
        return False
    try:
        _stop_phone_container(inst)
    except Exception:  # noqa: BLE001 - still try to close; umount fails loudly if it is busy
        logger.exception("stopping %s before closing its /data volume failed", inst.get("name"))
    try:
        volume_manager().close_token(src["owner_token"], uv.KIND_PHONE)
    except Exception:  # noqa: BLE001 - stays attached, so the next tick retries
        logger.exception("closing the /data volume of instance %s failed", inst.get("id"))
        return False
    src.update({"attached": False, "detached_at": time.time()})
    inst["data_source"] = src
    _save_instance(inst)
    return True


def _detach_instance_id(instance_id):
    with _instances_lock:
        inst = _instances.get(instance_id) if instance_id else None
    if inst and (inst.get("data_source") or {}).get("attached"):
        _detach_phone_data(inst)


def _reap_phone_volumes():
    """Release lapsed phone leases whose instance still has an owner's volume attached."""
    with _instances_lock:
        attached = [i for i in _instances.values() if (i.get("data_source") or {}).get("attached")]
    for inst in attached:
        if _is_lease_valid(inst["id"]):
            continue
        with _user_sessions_lock:
            stale = [o for o, s in _user_sessions.items() if s.get("instance_id") == inst["id"]]
        for owner in stale:
            _release_user_session(owner)
        _detach_phone_data(inst)


def _volume_tick():
    """One pass of the background sampler: reap lapsed leases, measure open volumes."""
    if not _volumes_enabled():
        return
    if _phone_per_user():
        _reap_phone_volumes()
    _reap_browser_sessions()
    try:
        volume_manager().sample_open()
    except Exception:  # noqa: BLE001
        logger.exception("volume usage sample failed")


def _volume_sampler(stop=None):
    while not (stop and stop.is_set()):
        time.sleep(max(10, ORCH_VOLUME_SAMPLE_SECONDS))
        try:
            _volume_tick()
        except Exception:  # noqa: BLE001 - the sampler must outlive one bad pass
            logger.exception("volume tick failed")


def _volumes_health():
    out = {"enabled": _volumes_enabled(), "phone_data": (ORCH_PHONE_DATA or "instance"),
           "browser_profile": (ORCH_BROWSER_PROFILE or "dir"), "root": ORCH_VOLUME_ROOT,
           "host_free_gb": None}
    if out["enabled"]:
        try:
            out.update(volume_manager().host_status())
        except Exception:  # noqa: BLE001 - health must answer
            out["error"] = "host status unavailable"
    return out


def _acquire_user_session(owner_user_id, ttl_seconds=3600, provision=True, purpose=None, runtime=None):
    if not owner_user_id:
        raise ValueError("owner_user_id required")
    ttl_seconds = max(int(ttl_seconds), 10)
    purpose = resolve_purpose(purpose, runtime)
    runtime = runtime_for_purpose(purpose)
    per_user = _phone_per_user() and runtime == RUNTIME_REDROID
    if per_user:
        if ORCH_DEPLOY_MODE not in ("mock", "redroid"):
            raise uv.VolumeError("per_user_unsupported",
                                 "ORCH_PHONE_DATA=per_user needs ORCH_DEPLOY_MODE=redroid", 501)
        _reap_phone_volumes()

    detach_after = None
    with _user_sessions_lock:
        existing = _user_sessions.get(owner_user_id)
        if existing:
            inst_id = existing.get("instance_id")
            same_runtime = existing.get("runtime") == runtime
            if inst_id and same_runtime and _is_lease_valid(inst_id, owner=owner_user_id):
                _set_lease(inst_id, owner_user_id, ttl_seconds)
                existing["ttl_seconds"] = ttl_seconds
                existing["purpose"] = purpose
                existing["expires_at"] = time.time() + ttl_seconds
                existing.setdefault("lease_id", "pl-" + uuid.uuid4().hex[:16])
                _persist("user_sessions", owner_user_id, existing)
                logger.info("Renewed session owner=%s instance=%s runtime=%s", owner_user_id, inst_id, runtime)
                return existing, False
            if inst_id and not same_runtime:
                logger.info(
                    "Owner %s switching purpose %s -> %s; releasing %s",
                    owner_user_id, existing.get("purpose"), purpose, inst_id,
                )
                _clear_lease(inst_id)
                old = _user_sessions.pop(owner_user_id, None)
                _persist("user_sessions", owner_user_id, None)
                if old and old.get("lease_id"):
                    _tickets.revoke_lease(old["lease_id"])
                    _streams.kill(old["lease_id"], "killed")
                if old:
                    detach_after = inst_id  # stop + close outside the lock
    if detach_after:
        _detach_instance_id(detach_after)

    if per_user:
        inst = _acquire_per_user_instance(owner_user_id, purpose, runtime, provision)
    else:
        inst = _get_or_create_instance(purpose=purpose, runtime=runtime, provision=provision,
                                       owner=owner_user_id)
        if _is_lease_valid(inst["id"]) and not _is_lease_valid(inst["id"], owner=owner_user_id):
            raise RuntimeError("phone in use")
        if (inst.get("data_source") or {}).get("type") == "user_volume":
            # Flag turned off after this instance ran on per-user volumes: whatever
            # its per-instance /data holds is not covered by the tenancy record.
            raise RuntimeError(
                "phone not clean: instance last ran with ORCH_PHONE_DATA=per_user; "
                "delete and reprovision it before using per-instance /data"
            )
        allowed, why = lease_decision(inst["id"], owner_user_id)
        if not allowed:
            logger.warning(
                "Refused instance=%s to owner=%s: %s", inst["id"], owner_user_id, why
            )
            raise RuntimeError(f"phone not clean: {why}")
    _set_lease(inst["id"], owner_user_id, ttl_seconds)
    sess = _session_from_instance(owner_user_id, inst, ttl_seconds, purpose)
    if per_user:
        sess["data"] = {"source": "user_volume", "kind": uv.KIND_PHONE,
                        "owner_token": bp.owner_token(owner_user_id)}
    sess["lease_id"] = "pl-" + uuid.uuid4().hex[:16]
    sess["expires_at"] = time.time() + ttl_seconds
    with _user_sessions_lock:
        _user_sessions[owner_user_id] = sess
    _persist("user_sessions", owner_user_id, sess)
    logger.info(
        "Acquired session owner=%s instance=%s runtime=%s purpose=%s",
        owner_user_id, inst["id"], sess["runtime"], purpose,
    )
    return sess, True


def _acquire_per_user_instance(owner_user_id, purpose, runtime, provision):
    """Pick or provision an instance and give it this owner's /data volume.

    The volume is opened only once a slot is known to exist, so a request
    refused for capacity allocates nothing. Any refusal afterwards closes the
    volume again (and stops the container if it was already started on it).
    """
    token = bp.owner_token(owner_user_id)
    try:
        inst = _get_or_create_instance(purpose=purpose, runtime=runtime, provision=False)
    except RuntimeError:
        inst = None
    if inst is None:
        if not provision:
            raise RuntimeError("phone in use")
        _check_capacity(runtime)
    vol = volume_manager().open_token(token, uv.KIND_PHONE)
    try:
        if inst is None:
            source = {"type": "user_volume", "owner_token": token, "kind": uv.KIND_PHONE,
                      "attached": True, "attached_at": time.time()}
            inst = _provision_instance(purpose=purpose, runtime=runtime, data_dir=vol["mount"],
                                       data_source=source)
        if _is_lease_valid(inst["id"]) and not _is_lease_valid(inst["id"], owner=owner_user_id):
            raise RuntimeError("phone in use")
        _attach_phone_data(inst, token, vol["mount"])
        # Second check, independent of the switch above: /data must now be this
        # owner's volume, and the tenancy guard must agree.
        src = inst.get("data_source") or {}
        if not (src.get("type") == "user_volume" and src.get("owner_token") == token and src.get("attached")):
            logger.warning("Refused instance=%s to owner token=%s: /data is not that owner's volume",
                           inst["id"], token)
            raise RuntimeError("phone not clean: /data was not switched to this owner's volume; "
                               "refusing to hand over the instance's existing /data")
        allowed, why = lease_decision(inst["id"], owner_user_id)
        if not allowed:
            logger.warning("Refused instance=%s to owner token=%s: %s", inst["id"], token, why)
            raise RuntimeError(f"phone not clean: {why}")
        return inst
    except Exception:
        src = (inst or {}).get("data_source") or {}
        if inst is not None and src.get("owner_token") == token and src.get("attached"):
            _detach_phone_data(inst)
        else:
            try:
                volume_manager().close_token(token, uv.KIND_PHONE)
            except Exception:  # noqa: BLE001 - report the refusal, not the cleanup
                logger.exception("closing an unused /data volume failed")
        raise


def _release_user_session(owner_user_id):
    with _user_sessions_lock:
        sess = _user_sessions.pop(owner_user_id, None)
    if not sess:
        return None
    _persist("user_sessions", owner_user_id, None)
    _clear_lease(sess.get("instance_id"))
    if sess.get("lease_id"):
        # Releasing the phone ends every ticket and stream that routed to it.
        _tickets.revoke_lease(sess["lease_id"])
        _streams.kill(sess["lease_id"], "killed")
    # Per-owner /data: stop the container and lock the owner's volume. A
    # per-instance instance has no data_source, so this is a no-op for it.
    _detach_instance_id(sess.get("instance_id"))
    logger.info("Released session owner=%s instance=%s", owner_user_id, sess.get("instance_id"))
    return sess


@app.route("/pool", methods=["GET"])
def get_pool():
    return jsonify({"success": True, "pool": {**_pool_snapshot(), bp.PURPOSE_BROWSER: _browser_pool_snapshot()}})


def _session_purpose_args(data=None):
    data = data or {}
    purpose = data.get("purpose") or request.args.get("purpose")
    runtime = data.get("runtime") or request.args.get("runtime")
    return purpose, runtime


@app.route("/sessions", methods=["GET"])
def list_sessions():
    _reap_browser_sessions()
    with _user_sessions_lock:
        items = list(_user_sessions.values())
    with _browser_lock:
        browsers = [bp.public_view(s) for s in _browser_sessions.values()]
    return jsonify({"count": len(items), "sessions": items,
                    "browser_count": len(browsers), "browser_sessions": browsers})


@app.route("/sessions", methods=["POST"])
def create_session():
    data = request.get_json() or {}
    owner = data.get("owner_user_id") or data.get("owner") or data.get("user_id")
    ttl = int(data.get("ttl_seconds") or data.get("ttl") or 3600)
    purpose, runtime = _session_purpose_args(data)
    if is_browser_request(purpose, runtime):
        return _create_browser_session(owner, ttl, data)
    provision = True if "provision" not in data else bool(data.get("provision"))
    try:
        sess, created = _acquire_user_session(
            owner,
            ttl,
            provision=provision,
            purpose=purpose,
            runtime=runtime,
        )
    except bp.LeaseError as exc:  # includes user_volumes.VolumeError (507 host_disk_low, ...)
        return jsonify({"error": str(exc), "code": exc.code, **(getattr(exc, "extra", None) or {})}), exc.status
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except ocp.ProviderError as exc:  # oci_cap 429, oci_error 502, oci_timeout / oci_unhealthy 504
        return jsonify({"error": str(exc), "code": exc.code, **exc.extra}), exc.status
    except RuntimeError as exc:
        msg = str(exc)
        # "not clean" is a policy refusal like "in use" -- the request conflicts
        # with the instance's state. A 500 would read as an orchestrator fault
        # and invite a retry, which would be refused identically.
        conflict = any(
            k in msg for k in ("in use", "full", "limit", "not clean")
        )
        status = 409 if conflict else 500
        return jsonify({"error": str(exc)}), status
    body = {"success": True, "session": sess, "created": created}
    body.update(_phone_ticket_body(sess))
    return jsonify(body), (201 if created else 200)


def _slot_args(data=None):
    """`slot` and `lease_id` from the body or the query string (browser routes)."""
    data = data or {}
    slot = data.get("slot") or request.args.get("slot")
    lease_id = data.get("lease_id") or request.args.get("lease_id")
    return (str(slot) if slot else None), (str(lease_id) if lease_id else None)


@app.route("/sessions/<owner_user_id>", methods=["GET"])
def get_session(owner_user_id):
    purpose, runtime = _session_purpose_args()
    if is_browser_request(purpose, runtime):
        _reap_browser_sessions()
        slot, lease_id = _slot_args()
        try:
            sess = _browser_session_for(owner_user_id, slot=slot, lease_id=lease_id)
        except bp.LeaseError as exc:
            return jsonify({"error": str(exc), "code": exc.code}), exc.status
        if not sess:
            return jsonify({"error": "session not found"}), 404
        return jsonify(bp.public_view(sess))
    with _user_sessions_lock:
        sess = _user_sessions.get(owner_user_id)
    if not sess:
        return jsonify({"error": "session not found"}), 404
    if sess.get("instance_id") and not _is_lease_valid(sess["instance_id"], owner=owner_user_id):
        _release_user_session(owner_user_id)
        return jsonify({"error": "session expired"}), 404
    return jsonify(sess)


@app.route("/sessions/<owner_user_id>", methods=["DELETE"])
def delete_session(owner_user_id):
    data = request.get_json(silent=True) or {}
    purpose, runtime = _session_purpose_args(data)
    if is_browser_request(purpose, runtime):
        slot, lease_id = _slot_args(data)
        try:
            sess = _release_browser_session(owner_user_id, slot=slot, lease_id=lease_id)
        except bp.LeaseError as exc:
            return jsonify({"error": str(exc), "code": exc.code}), exc.status
        if not sess:
            return jsonify({"error": "session not found"}), 404
        # The profile stays: release is not deletion (DELETE /profiles/<owner>).
        return jsonify({"success": True, "released": bp.public_view(sess), "profile_kept": True})
    sess = _release_user_session(owner_user_id)
    if not sess:
        return jsonify({"error": "session not found"}), 404
    return jsonify({"success": True, "session": sess, "released": sess})


# ==========================================================================
# Isolation: the chrome pool, tickets, and the relay (docs/isolation.md)
# ==========================================================================

# Keyed by _browser_key(owner, slot): the owner id alone for the `main` slot
# (the key every row had before slots existed), `<owner>\x1f<slot>` otherwise.
# Persisted under the same keys in "browser_sessions".
_browser_sessions = {}
_browser_lock = threading.RLock()
_SLOT_SEP = "\x1f"


def _browser_key(owner, slot=bp.DEFAULT_SLOT):
    slot = bp.normalize_slot(slot)
    return owner if slot == bp.DEFAULT_SLOT else f"{owner}{_SLOT_SEP}{slot}"


def _owner_browser_sessions(owner):
    """Every live browser session of this owner, any slot. Takes the lock."""
    with _browser_lock:
        return [s for s in _browser_sessions.values() if s.get("owner_user_id") == owner]


def _browser_lease_holder(lease_id):
    """(key, session) of the browser lease with this id, whoever holds it, or (None, None)."""
    if not lease_id:
        return None, None
    with _browser_lock:
        for key, sess in _browser_sessions.items():
            if sess.get("lease_id") == lease_id:
                return key, sess
    return None, None


def _browser_session_for(owner, slot=None, lease_id=None):
    """This owner's browser in `slot` (default main), or the one holding `lease_id`.

    A lease id another owner holds is not found here: the caller's session
    space is their own, and a refusal never names the holder.
    """
    if lease_id:
        _, sess = _browser_lease_holder(lease_id)
        return sess if sess is not None and sess.get("owner_user_id") == owner else None
    with _browser_lock:
        return _browser_sessions.get(_browser_key(owner, slot))
_proxy_relays = {}              # lease_id -> LeaseProxyRelay; memory only, holds a credential
_chrome_runtime = None


def _default_chrome_runtime():
    mode = (ORCH_CHROME_RUNTIME or ("mock" if ORCH_DEPLOY_MODE == "mock" else "docker")).lower()
    if mode == "mock":
        return bp.MockChromeRuntime()
    if mode == "local":
        return bp.LocalChromeRuntime(ORCH_CHROME_BIN)
    if mode == "docker":
        return bp.DockerChromeRuntime(ORCH_CHROME_IMAGE, ORCH_CHROME_NETWORK)
    raise RuntimeError(f"Unsupported ORCH_CHROME_RUNTIME: {mode}")


def chrome_runtime():
    global _chrome_runtime
    if _chrome_runtime is None:
        _chrome_runtime = _default_chrome_runtime()
    return _chrome_runtime


def set_chrome_runtime(runtime):
    """Tests (and embedders) swap the runtime; None returns to the default."""
    global _chrome_runtime
    _chrome_runtime = runtime


def _lease_check(record):
    """Is the lease a ticket names still held by the ticket's owner?"""
    owner = record.get("owner")
    lease_id = record.get("lease_id")
    if record.get("kind") == "browser":
        sess = _browser_session_for(owner, lease_id=lease_id)
        if not sess:
            return False, "browser lease released or reassigned"
        if sess.get("expires_at", 0) <= time.time():
            return False, "browser lease expired"
        return True, None
    if record.get("kind") == "phone":
        with _user_sessions_lock:
            sess = _user_sessions.get(owner)
        if not sess or sess.get("lease_id") != lease_id:
            return False, "phone lease released or reassigned"
        if not _is_lease_valid(sess.get("instance_id"), owner=owner):
            return False, "phone lease expired"
        return True, None
    return False, "unknown ticket kind"


_tickets = rly.TicketBook(store=None, lease_check=_lease_check)
_limiter = rly.RateLimiter(ORCH_RELAY_RATE, ORCH_RELAY_BURST)
_audit = rly.Audit(logger.bind("AUD"))
_streams = rly.StreamRegistry()


def init_state(path=None):
    """Open the state database and refill every in-memory table from it.

    Called once at start. After it, a restarted orchestrator knows exactly the
    owners, leases, profiles and (revoked) tickets it knew before.
    """
    global _store
    path = path if path is not None else ORCH_STATE_DB
    try:
        _store.close()
    except Exception:  # noqa: BLE001
        pass
    _store = StateStore(path or ":memory:")
    _tickets.store = _store
    with _instances_lock:
        _instances.clear()
        _instances.update(_store.load("instances"))
    with _leases_lock:
        _leases.clear()
        _leases.update(_store.load("leases"))
    with _instance_tenancy_lock:
        _instance_tenancy.clear()
        _instance_tenancy.update(_store.load("tenancy"))
    with _user_sessions_lock:
        _user_sessions.clear()
        _user_sessions.update(_store.load("user_sessions"))
    with _browser_lock:
        _browser_sessions.clear()
        _browser_sessions.update(_store.load("browser_sessions"))
        for sess in _browser_sessions.values():
            # Rows written before slots existed are the owner's main browser.
            sess.setdefault("slot", bp.DEFAULT_SLOT)
            sess.setdefault("isolation_key", f"{sess.get('owner_token') or bp.owner_token(sess.get('owner_user_id'))}/{sess['slot']}")
    _tickets.load()
    logger.info(
        "State %s: instances=%d leases=%d phone_sessions=%d browser_sessions=%d",
        "loaded from " + path if path and path != ":memory:" else "in memory only",
        len(_instances), len(_leases), len(_user_sessions), len(_browser_sessions),
    )
    return _store


def _persist_browser(owner, sess):
    _persist("browser_sessions", owner, sess)


def _free_cdp_port():
    with _browser_lock:
        taken = {s.get("cdp_port") for s in _browser_sessions.values()}
    for port in range(ORCH_CHROME_CDP_PORT_BASE, ORCH_CHROME_CDP_PORT_BASE + ORCH_MAX_CHROME_INSTANCES):
        if port not in taken:
            return port
    return None


def _stop_browser(sess):
    """Stop the browser and its proxy relay. The profile is never touched here."""
    try:
        chrome_runtime().stop(sess)
    except Exception:  # noqa: BLE001
        logger.exception("chrome stop failed lease=%s", sess.get("lease_id"))
    relay_srv = _proxy_relays.pop(sess.get("lease_id"), None)
    if relay_srv is not None:
        relay_srv.stop()
    _tickets.revoke_lease(sess.get("lease_id"))
    _streams.kill(sess.get("lease_id"), "killed")
    _limiter.forget(sess.get("lease_id"))
    if sess.get("profile_volume"):
        # ORCH_BROWSER_PROFILE=volume: lock the owner's profile volume. It is kept.
        _close_profile_volume_if_unused(sess["owner_token"], sess.get("lease_id"))


def _close_profile_volume_if_unused(token, lease_id=None):
    """Close the owner's profile volume unless another slot of theirs still runs on it."""
    with _browser_lock:
        busy = any(s.get("owner_token") == token and s.get("profile_volume") and s.get("lease_id") != lease_id
                   for s in _browser_sessions.values())
    if busy:
        return False
    try:
        volume_manager().close_token(token, uv.KIND_BROWSER)
    except Exception:  # noqa: BLE001
        logger.exception("closing the profile volume failed lease=%s", lease_id)
        return False
    return True


def _reap_browser_sessions(now=None):
    now = now or time.time()
    with _browser_lock:
        expired = [(o, s) for o, s in _browser_sessions.items() if s.get("expires_at", 0) <= now]
        for owner, _ in expired:
            _browser_sessions.pop(owner, None)
    for key, sess in expired:
        _persist_browser(key, None)
        _stop_browser(sess)
        logger.info("Browser lease expired owner=%s slot=%s lease=%s (profile kept)",
                    sess.get("owner_token"), sess.get("slot"), sess.get("lease_id"))
    return [s for _, s in expired]


def _proxy_from_request(data):
    proxy = data.get("proxy") if isinstance(data.get("proxy"), dict) else {}
    proxy_id = str(proxy.get("id") or data.get("proxy_id") or "").strip()
    upstream = proxy.get("upstream") or data.get("proxy_upstream") or None
    if upstream and not proxy_id:
        proxy_id = (redact_upstream(upstream) or "proxy").split("://")[-1]
    return (proxy_id or "direct"), (str(upstream) if upstream else None)


def _acquire_browser_session(owner, ttl_seconds=3600, proxy_id="direct", proxy_upstream=None, lease_id=None,
                             slot=None):
    owner = str(owner or "").strip()
    if not owner:
        raise bp.LeaseError("owner_required", "owner_user_id required", 400)
    slot = bp.normalize_slot(slot)
    _reap_browser_sessions()
    ttl = bp.clamp_ttl(ttl_seconds)
    with _browser_lock:
        if lease_id:
            _, holder = _browser_lease_holder(lease_id)
            if holder is not None and holder.get("owner_user_id") != owner:
                # Never reassigned. The refusal does not say whose it is.
                raise bp.LeaseError("forbidden", "that lease belongs to another account", 403)
            if holder is not None:
                slot = holder.get("slot") or bp.DEFAULT_SLOT  # a lease id names its slot
        key = _browser_key(owner, slot)
        mine = _browser_sessions.get(key)
        if mine:
            if proxy_upstream is not None and proxy_id != mine.get("proxy_id"):
                raise bp.LeaseError(
                    "proxy_changed",
                    "this lease runs behind another exit; release it before changing the proxy "
                    "(cookies travel with the exit that earned them)",
                )
            mine["expires_at"] = time.time() + ttl
            mine["ttl_seconds"] = ttl
            mine["renewals"] = int(mine.get("renewals", 0)) + 1
            _persist_browser(key, mine)
            return mine, False
        held = sorted(s.get("slot") or bp.DEFAULT_SLOT for s in _browser_sessions.values()
                      if s.get("owner_user_id") == owner)
        if len(held) >= ORCH_BROWSER_SLOTS_PER_OWNER:
            # The owner's own cap, before the pool's: a second slot under the
            # default cap of 1 is refused here, and the body says the cap.
            raise bp.LeaseError("slots_exhausted",
                                f"this owner already holds {len(held)} browser slot(s); "
                                f"ORCH_BROWSER_SLOTS_PER_OWNER={ORCH_BROWSER_SLOTS_PER_OWNER}",
                                429, cap=ORCH_BROWSER_SLOTS_PER_OWNER, slots=held)
        if len(_browser_sessions) >= ORCH_MAX_CHROME_INSTANCES:
            raise bp.LeaseError("at_capacity", f"all {ORCH_MAX_CHROME_INSTANCES} browser slots are leased")
        port = _free_cdp_port()
        if port is None:
            raise bp.LeaseError("at_capacity", "no free DevTools port")
        volume = None
        if _browser_volume():
            # The owner's own LUKS volume, sized to their quota, is the profile.
            # Every slot of the owner lives on that one volume; a slot other
            # than main takes a directory of its own on it.
            volume = volume_manager().open_token(bp.owner_token(owner), uv.KIND_BROWSER)
            profile = volume["mount"]
            if slot != bp.DEFAULT_SLOT:
                profile = os.path.join(volume["mount"], "slots", slot)
                try:
                    os.makedirs(profile, mode=0o700, exist_ok=True)
                except OSError as exc:
                    _close_profile_volume_if_unused(bp.owner_token(owner))
                    raise bp.LeaseError("slot_profile_failed", f"cannot create the slot's profile directory: {exc}", 500)
        else:
            bp.ensure_profile_root(ORCH_PROFILE_ROOT)
            ok, detail = bp.check_profile_encryption(ORCH_PROFILE_ROOT, ORCH_PROFILE_ENCRYPTION)
            if not ok:
                raise bp.LeaseError("profile_not_encrypted", f"refusing to create a profile: {detail}", 503)
            profile = bp.ensure_profile_dir(ORCH_PROFILE_ROOT, owner, slot)
        t = time.time()
        sess = {
            "lease_id": "bl-" + uuid.uuid4().hex[:16],
            "owner_user_id": owner,
            "owner_token": bp.owner_token(owner),
            "slot": slot,
            "isolation_key": bp.isolation_key(owner, slot),
            "purpose": bp.PURPOSE_BROWSER,
            "runtime": bp.RUNTIME_CHROME,
            "chrome_runtime": chrome_runtime().name,
            "profile_dir": profile,
            "cdp_port": port,
            "proxy_id": proxy_id or "direct",
            "proxy_upstream": redact_upstream(proxy_upstream),
            "egress": "owner" if proxy_upstream else "direct",
            "acquired_at": t,
            "expires_at": t + ttl,
            "ttl_seconds": ttl,
            "renewals": 0,
        }
        if volume:
            sess["profile_volume"] = True
        proxy_server = None
        if proxy_upstream:
            if "@" in proxy_upstream and chrome_runtime().name != "mock":
                relay_srv = LeaseProxyRelay(proxy_upstream, bind=ORCH_CHROME_RELAY_BIND).start()
                _proxy_relays[sess["lease_id"]] = relay_srv
                proxy_server = relay_srv.server
            else:
                proxy_server = proxy_upstream if "@" not in proxy_upstream else "per-lease-relay"
        try:
            started = chrome_runtime().start(sess, proxy_server=proxy_server)
        except Exception:
            if volume:
                _close_profile_volume_if_unused(sess["owner_token"], sess["lease_id"])
            raise
        sess.update({k: v for k, v in (started or {}).items() if v is not None})
        _browser_sessions[key] = sess
        _persist_browser(key, sess)
    logger.info("Browser lease owner=%s slot=%s lease=%s runtime=%s egress=%s",
                sess["owner_token"], slot, sess["lease_id"], sess["chrome_runtime"], sess["egress"])
    return sess, True


def _release_browser_session(owner, slot=None, lease_id=None):
    """Release one of the owner's browsers: the slot named (default main) or the lease id.

    Another owner's lease id is a 403, never a release. One slot's release
    never touches another slot of the same owner.
    """
    with _browser_lock:
        if lease_id:
            key, sess = _browser_lease_holder(lease_id)
            if sess is not None and sess.get("owner_user_id") != owner:
                raise bp.LeaseError("forbidden", "that lease belongs to another account", 403)
        else:
            key = _browser_key(owner, slot)
            sess = _browser_sessions.get(key)
        if sess is not None:
            _browser_sessions.pop(key, None)
    if not sess:
        return None
    _persist_browser(key, None)
    _stop_browser(sess)
    logger.info("Browser released owner=%s slot=%s lease=%s (profile kept)",
                sess.get("owner_token"), sess.get("slot"), sess.get("lease_id"))
    return sess


def _relay_base():
    base = (ORCH_PUBLIC_URL or request.host_url).rstrip("/")
    return base


def _ws_base():
    base = _relay_base()
    return "wss://" + base[len("https://"):] if base.startswith("https://") else "ws://" + base.split("://", 1)[-1]


def _browser_ticket_body(sess, ttl=None):
    token, rec = _tickets.mint(owner=sess["owner_user_id"], owner_token=sess["owner_token"],
                               lease_id=sess["lease_id"], kind="browser",
                               ttl=ttl or ORCH_TICKET_TTL_SECONDS, not_after=sess["expires_at"])
    return {
        "ticket": token,
        "ticket_id": rec["id"],
        "ticket_expires_at": rec["expires_at"],
        "relay": {"cdp_ws_url": f"{_ws_base()}/relay/cdp/{sess['lease_id']}"},
    }


def _phone_ticket_body(sess, ttl=None):
    if not sess.get("lease_id"):
        return {}
    expires = sess.get("expires_at") or (time.time() + int(sess.get("ttl_seconds") or 60))
    token, rec = _tickets.mint(owner=sess["owner_user_id"], owner_token=bp.owner_token(sess["owner_user_id"]),
                               lease_id=sess["lease_id"], kind="phone",
                               ttl=ttl or ORCH_TICKET_TTL_SECONDS, not_after=expires)
    return {
        "ticket": token,
        "ticket_id": rec["id"],
        "ticket_expires_at": rec["expires_at"],
        "relay": {
            "phone_api_url": f"{_relay_base()}/relay/phone",
            "phone_stream_url": f"{_ws_base()}/relay/phone/stream",
        },
    }


def _create_browser_session(owner, ttl, data):
    proxy_id, upstream = _proxy_from_request(data)
    try:
        sess, created = _acquire_browser_session(owner, ttl, proxy_id=proxy_id, proxy_upstream=upstream,
                                                 lease_id=data.get("lease_id"), slot=data.get("slot"))
    except bp.LeaseError as exc:
        return jsonify({"error": str(exc), "code": exc.code, **(getattr(exc, "extra", None) or {})}), exc.status
    except RuntimeError as exc:
        return jsonify({"error": str(exc), "code": "runtime_failed"}), 500
    body = {"success": True, "session": bp.public_view(sess), "created": created}
    body.update(_browser_ticket_body(sess))
    return jsonify(body), (201 if created else 200)


@app.route("/tickets", methods=["POST"])
def mint_ticket():
    """A fresh ticket for the caller's OWN current lease. Never for someone else's."""
    data = request.get_json() or {}
    owner = str(data.get("owner_user_id") or data.get("owner") or "").strip()
    if not owner:
        return jsonify({"error": "owner_user_id required"}), 400
    ttl = data.get("ttl_seconds") or ORCH_TICKET_TTL_SECONDS
    if is_browser_request(data.get("purpose"), data.get("runtime")):
        _reap_browser_sessions()
        slot, lease_id = _slot_args(data)
        _, holder = _browser_lease_holder(lease_id)
        if holder is not None and holder.get("owner_user_id") != owner:
            return jsonify({"error": "that lease belongs to another account", "code": "forbidden"}), 403
        try:
            sess = _browser_session_for(owner, slot=slot, lease_id=lease_id)
        except bp.LeaseError as exc:
            return jsonify({"error": str(exc), "code": exc.code}), exc.status
        if not sess:
            return jsonify({"error": "no browser lease for this owner", "code": "no_such_lease"}), 404
        return jsonify({"success": True, **_browser_ticket_body(sess, ttl)}), 201
    with _user_sessions_lock:
        sess = _user_sessions.get(owner)
        foreign = any(s.get("lease_id") == data.get("lease_id") and o != owner
                      for o, s in _user_sessions.items())
    if foreign:
        return jsonify({"error": "that lease belongs to another account", "code": "forbidden"}), 403
    if not sess or not _is_lease_valid(sess.get("instance_id"), owner=owner):
        return jsonify({"error": "no phone lease for this owner", "code": "no_such_lease"}), 404
    if data.get("lease_id") and data["lease_id"] != sess.get("lease_id"):
        return jsonify({"error": "no such lease for this owner", "code": "no_such_lease"}), 404
    return jsonify({"success": True, **_phone_ticket_body(sess, ttl)}), 201


@app.route("/tickets/revoke", methods=["POST"])
def revoke_ticket():
    data = request.get_json() or {}
    if data.get("ticket"):
        revoked = _tickets.revoke(data["ticket"])
    elif data.get("ticket_id"):
        key, _ = _tickets.find_by_id(str(data["ticket_id"]))
        revoked = _tickets.revoke_digest(key) if key else False
    else:
        return jsonify({"error": "ticket or ticket_id required"}), 400
    return jsonify({"success": True, "revoked": revoked})


@app.route("/sessions/<owner_user_id>/kill", methods=["POST"])
def kill_session_streams(owner_user_id):
    """Kill switch: revoke every ticket for the owner's lease and drop its streams now."""
    data = request.get_json(silent=True) or {}
    purpose, runtime = _session_purpose_args(data)
    if is_browser_request(purpose, runtime):
        slot, lease_id = _slot_args(data)
        try:
            sess = _browser_session_for(owner_user_id, slot=slot, lease_id=lease_id)
        except bp.LeaseError as exc:
            return jsonify({"error": str(exc), "code": exc.code}), exc.status
    else:
        with _user_sessions_lock:
            sess = _user_sessions.get(owner_user_id)
    if not sess or not sess.get("lease_id"):
        return jsonify({"error": "session not found"}), 404
    revoked = _tickets.revoke_lease(sess["lease_id"])
    dropped = _streams.kill(sess["lease_id"], "killed")
    logger.warning("Kill switch owner=%s lease=%s revoked=%d dropped=%d",
                   bp.owner_token(owner_user_id), sess["lease_id"], revoked, dropped)
    return jsonify({"success": True, "lease_id": sess["lease_id"], "tickets_revoked": revoked,
                    "streams_dropped": dropped})


@app.route("/profiles/<owner_user_id>", methods=["GET"])
def get_profile(owner_user_id):
    if _browser_volume():
        try:
            p = volume_manager().paths(owner_user_id, uv.KIND_BROWSER)
        except bp.LeaseError as exc:
            return jsonify({"error": str(exc), "code": exc.code}), exc.status
        return jsonify({"owner_token": p["token"], "profile_dir": p["mount"], "volume": True,
                        "exists": os.path.isfile(p["image"]) and not os.path.islink(p["image"])})
    try:
        path = bp.profile_dir_for(ORCH_PROFILE_ROOT, owner_user_id)
    except bp.LeaseError as exc:
        return jsonify({"error": str(exc)}), exc.status
    exists = os.path.isdir(path) and not os.path.islink(path)
    mode = oct(os.stat(path).st_mode & 0o777) if exists else None
    return jsonify({"owner_token": bp.owner_token(owner_user_id), "profile_dir": path,
                    "exists": exists, "mode": mode})


@app.route("/profiles/<owner_user_id>", methods=["DELETE"])
def delete_profile(owner_user_id):
    """Delete an owner's browser profile. Separate, explicit, and confirmed."""
    data = request.get_json(silent=True) or {}
    if data.get("confirm") != owner_user_id:
        return jsonify({"error": "confirm must repeat the owner_user_id", "code": "confirm_required"}), 400
    _reap_browser_sessions()
    if _owner_browser_sessions(owner_user_id):
        return jsonify({"error": "release the browser lease first", "code": "lease_active"}), 409
    try:
        if _browser_volume():
            deleted = volume_manager().delete_volume(owner_user_id, uv.KIND_BROWSER)
        else:
            deleted = bp.delete_profile_dir(ORCH_PROFILE_ROOT, owner_user_id)
    except bp.LeaseError as exc:
        return jsonify({"error": str(exc), "code": exc.code}), exc.status
    logger.warning("Profile deleted owner=%s deleted=%s", bp.owner_token(owner_user_id), deleted)
    return jsonify({"success": True, "deleted": deleted})


# ---- per-owner volumes ---------------------------------------------------

@app.route("/volumes/<owner_user_id>", methods=["GET"])
def get_volumes(owner_user_id):
    """One owner's volumes: used and limit per kind. Never anybody else's."""
    if not _volumes_enabled():
        return jsonify({"error": "per-owner volumes are off", "code": "volumes_disabled"}), 404
    token = bp.owner_token(owner_user_id)
    if not token:
        return jsonify({"error": "owner required", "code": "owner_required"}), 400
    kinds = ([uv.KIND_PHONE] if _phone_per_user() else []) + ([uv.KIND_BROWSER] if _browser_volume() else [])
    try:
        vols = {kind: volume_manager().usage_token(token, kind) for kind in kinds}
    except bp.LeaseError as exc:
        return jsonify({"error": str(exc), "code": exc.code}), exc.status
    return jsonify({"owner_token": token, "volumes": vols})


@app.route("/volumes/<owner_user_id>/<kind>", methods=["DELETE"])
def delete_volume(owner_user_id, kind):
    """Destroy one of an owner's volumes. Explicit, confirmed, refused while leased."""
    if kind not in uv.KINDS:
        return jsonify({"error": "unknown volume kind", "code": "bad_kind"}), 404
    data = request.get_json(silent=True) or {}
    if data.get("confirm") != owner_user_id:
        return jsonify({"error": "confirm must repeat the owner_user_id", "code": "confirm_required"}), 400
    _reap_browser_sessions()
    if _phone_per_user():
        _reap_phone_volumes()
    try:
        deleted = volume_manager().delete_volume(owner_user_id, kind)
    except bp.LeaseError as exc:
        return jsonify({"error": str(exc), "code": exc.code}), exc.status
    return jsonify({"success": True, "deleted": deleted, "kind": kind})


# ---- relay ---------------------------------------------------------------

_PHONE_DENY = ("config",)


def _ticket_or_error(kind, lease_id=None):
    token = rly.ticket_from_request(request.headers, request.args)
    try:
        return token, _tickets.check(token, kind=kind, lease_id=lease_id), None
    except rly.TicketError as exc:
        return token, None, (jsonify({"error": str(exc), "code": exc.code}), exc.status)


def _phone_instance_for(record):
    with _user_sessions_lock:
        sess = _user_sessions.get(record["owner"])
    inst_id = (sess or {}).get("instance_id")
    with _instances_lock:
        inst = _instances.get(inst_id)
    return inst


@app.route("/relay/phone/<path:sub>", methods=["GET", "POST", "PUT", "DELETE"])
def relay_phone(sub):
    """Forward one Control API call to the ticket's phone, and only that phone.

    The instance comes from the ticket's lease. A body or query that names an
    instance, API URL or ADB serial is not read for routing, and the caller's
    headers are not forwarded (the orchestrator sets its own).
    """
    token, record, err = _ticket_or_error("phone")
    if err:
        return err
    sub = sub.strip("/")
    if ".." in sub.split("/") or "://" in sub or sub.split("/")[0] in _PHONE_DENY:
        return jsonify({"error": "path not relayed", "code": "path_denied"}), 403
    verb = f"{request.method} {'/'.join(sub.split('/')[:2])}"
    if not _limiter.allow(record["lease_id"]):
        _audit.record(owner=record["owner_token"], lease=record["lease_id"], kind="phone", verb=verb, ms=0, ok=False)
        return jsonify({"error": "rate limited", "code": "rate_limited"}), 429
    inst = _phone_instance_for(record)
    if not inst:
        return jsonify({"error": "phone not found for this lease", "code": "lease_gone"}), 404
    params = [(k, v) for k, v in request.args.items(multi=True) if k != "ticket"]
    body = request.get_json(silent=True) if request.method in ("POST", "PUT") else None
    t0 = time.perf_counter()
    ok = False
    try:
        resp = requests.request(request.method, f"{inst['api_url']}/{sub}", params=params, json=body,
                                headers=_control_headers(inst), timeout=ORCH_API_TIMEOUT)
        ok = resp.status_code < 400
        out = Response(resp.content, status=resp.status_code,
                       content_type=resp.headers.get("Content-Type", "application/json"))
    except requests.RequestException:
        out = (jsonify({"error": "phone unreachable", "code": "upstream_unreachable"}), 502)
    _audit.record(owner=record["owner_token"], lease=record["lease_id"], kind="phone", verb=verb,
                  ms=(time.perf_counter() - t0) * 1000.0, ok=ok)
    return out


def _ws_response(handler):
    """Run `handler(ws)` on an upgraded connection (werkzeug or gunicorn threads)."""
    ws = simple_websocket.Server(request.environ)
    try:
        handler(ws)
    except _WsClosed:
        pass
    try:
        ws.close()
    except Exception:  # noqa: BLE001
        pass

    class _WsResponse(Response):
        def __call__(self, *args, **kwargs):
            if ws.mode == "gunicorn":
                raise StopIteration()
            if ws.mode == "werkzeug":
                return super().__call__(*args, **kwargs)
            return []

    return _WsResponse()


@app.route("/relay/cdp/<lease_id>", websocket=True)
def relay_cdp(lease_id):
    """DevTools for the ticket's browser. The Watch screencast rides on this too."""
    if simple_websocket is None:
        return jsonify({"error": "simple-websocket is not installed", "code": "ws_unavailable"}), 501
    token, record, err = _ticket_or_error("browser", lease_id=lease_id)
    if err:
        return err
    sess = _browser_session_for(record["owner"], lease_id=record["lease_id"])
    upstream_url = (sess or {}).get("cdp_ws_internal")
    if not upstream_url:
        return jsonify({"error": "no browser is running for this lease", "code": "no_browser"}), 503

    def run(ws):
        try:
            up = simple_websocket.Client.connect(upstream_url)
        except Exception:  # noqa: BLE001
            ws.close(reason=rly.CLOSE_UPSTREAM, message="browser unreachable")
            return
        stream = rly.CdpStream(client=ws, upstream=up, token=token, record=record, book=_tickets,
                               limiter=_limiter, audit=_audit, registry=_streams)
        reason = stream.run()
        _audit.record(owner=record["owner_token"], lease=record["lease_id"], kind="cdp",
                      verb=f"stream.close:{reason or 'client'}", ms=0, ok=True)

    return _ws_response(run)


@app.route("/relay/phone/stream", websocket=True)
def relay_phone_stream():
    """The phone's screen and input for the Watch / take-over page."""
    if simple_websocket is None:
        return jsonify({"error": "simple-websocket is not installed", "code": "ws_unavailable"}), 501
    token, record, err = _ticket_or_error("phone")
    if err:
        return err
    inst = _phone_instance_for(record)
    if not inst:
        return jsonify({"error": "phone not found for this lease", "code": "lease_gone"}), 404

    def grab():
        return _control_get(inst["api_url"], "/device/screenshot/base64", instance=inst)

    def send_input(event):
        allowed = {k: event[k] for k in ("type", "x", "y", "x2", "y2", "text", "keycode", "duration") if k in event}
        return _control_post(inst["api_url"], "/device/input", allowed, instance=inst)

    def run(ws):
        stream = rly.PhoneFrameStream(client=ws, token=token, record=record, book=_tickets, limiter=_limiter,
                                      audit=_audit, registry=_streams, grab_frame=grab, send_input=send_input,
                                      fps=ORCH_PHONE_FPS)
        reason = stream.run()
        _audit.record(owner=record["owner_token"], lease=record["lease_id"], kind="phone",
                      verb=f"stream.close:{reason or 'client'}", ms=0, ok=True)

    return _ws_response(run)


def _browser_pool_snapshot():
    with _browser_lock:
        leased = len(_browser_sessions)
    return {"runtime": bp.RUNTIME_CHROME, "chrome_runtime": chrome_runtime().name,
            "total": leased, "leased": leased, "idle": 0, "max": ORCH_MAX_CHROME_INSTANCES,
            "slots_per_owner": ORCH_BROWSER_SLOTS_PER_OWNER}


# ---- start-up policy -------------------------------------------------------

def _host_class(host):
    """'loopback', 'private' or 'public' for a bind address."""
    import ipaddress
    h = (host or "").strip().strip("[]")
    if h in ("localhost",):
        return "loopback"
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return "public"  # a hostname we cannot classify is treated as open
    if ip.is_loopback:
        return "loopback"
    if ip.is_unspecified:
        return "public"  # 0.0.0.0 / :: is every interface, including public ones
    private_nets = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10",
                    "169.254.0.0/16", "fc00::/7", "fe80::/10")
    if any(ip.version == ipaddress.ip_network(n).version and ip in ipaddress.ip_network(n) for n in private_nets):
        return "private"
    return "public"


def startup_refusal(host, token, env=None):
    """Why the orchestrator must not start with this bind and token, or None."""
    env = env if env is not None else os.environ
    klass = _host_class(host)
    if not token:
        if klass == "loopback" and env.get("ORCH_ALLOW_NO_TOKEN", "").lower() in {"1", "true", "yes"}:
            return None
        return ("refusing to start without ORCH_API_TOKEN: anyone who reaches this port could lease, "
                "drive and release every owner's phone and browser. Set ORCH_API_TOKEN (or, for local "
                "development on loopback only, ORCH_ALLOW_NO_TOKEN=1).")
    if klass == "public" and env.get("ORCH_ALLOW_PUBLIC_BIND", "").lower() not in {"1", "true", "yes"}:
        return (f"refusing to bind {host}: not loopback or a private interface. Set ORCH_HOST to "
                "127.0.0.1 or the host's private address, or ORCH_ALLOW_PUBLIC_BIND=1 if a firewall "
                "really does stand in front of it.")
    return None


@app.route("/health", methods=["GET"])
def health():
    """Open (the auth middleware exempts it), so it reports the caller's auth."""
    auth = _auth_state()
    with _instances_lock:
        items = list(_instances.values())
    with _user_sessions_lock:
        sessions = len(_user_sessions)
    pool = _pool_snapshot(items)
    return jsonify({
        "status": "ok" if auth["ok"] else "unauthorized",
        "auth": auth,
        "default_purpose": PURPOSE_AUTOMATION,
        "default_runtime": RUNTIME_REDROID,
        "deploy_mode": ORCH_DEPLOY_MODE,
        "runtime": _runtime_for_mode(),
        "instances": len(items),
        "sessions": sessions,
        "max_instances": ORCH_MAX_INSTANCES,
        "phone_provider": _provider_health(),
        "pool": {**pool, bp.PURPOSE_BROWSER: _browser_pool_snapshot()},
        "isolation": {
            "state": "persistent" if getattr(_store, "persistent", False) else "memory",
            "profile_encryption": ("per-owner-luks-volume" if _browser_volume()
                                   else ORCH_PROFILE_ENCRYPTION or "none"),
            "tickets_required_for_phones": ORCH_REQUIRE_TICKETS,
            "relay_ws": simple_websocket is not None,
            "open_streams": _streams.count(),
        },
        "volumes": _volumes_health(),
    })


def reconcile_oci_phones():
    """Boot: adopt the OCI phones the state DB knows, terminate orphans past the TTL.

    A failure here is logged, not fatal: the caps then count only what this
    process launches, and the first launch surfaces the same error to a caller.
    """
    with _instances_lock:
        known = [i.get("instance_ocid") for i in _instances.values()
                 if i.get("provider") == "oci" and i.get("instance_ocid")]
    try:
        result = oci_provider().reconcile(known)
    except ocp.ProviderError as exc:
        logger.error("OCI reconcile failed (%s): %s", exc.code, exc)
        return None
    logger.info("OCI reconcile: adopted=%d kept=%d terminated=%d",
                len(result["adopted"]), len(result["kept"]), len(result["terminated"]))
    return result


def main():
    host = os.environ.get("ORCH_HOST", "127.0.0.1")
    port = int(os.environ.get("ORCH_PORT", "8090"))
    why = startup_refusal(host, ORCH_API_TOKEN) or volume_config_refusal() or provider_config_refusal()
    if why:
        logger.error(why)
        raise SystemExit(2)
    init_state(ORCH_STATE_DB or ":memory:")
    if _phone_provider() == "oci":
        reconcile_oci_phones()
    if _volumes_enabled():
        threading.Thread(target=_volume_sampler, name="volume-sampler", daemon=True).start()
        logger.info("Per-owner volumes on: phone_data=%s browser_profile=%s root=%s",
                    ORCH_PHONE_DATA, ORCH_BROWSER_PROFILE, ORCH_VOLUME_ROOT)
    if not ORCH_STATE_DB:
        logger.warning("ORCH_STATE_DB unset: leases live in memory and a restart forgets every owner")
    logger.info(
        "Starting orchestrator on %s:%s (mode=%s default_runtime=%s)",
        host, port, ORCH_DEPLOY_MODE, RUNTIME_REDROID,
    )
    app.run(host=host, port=port, threaded=True)


if __name__ == "__main__":
    main()
