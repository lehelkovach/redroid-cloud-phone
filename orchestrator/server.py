#!/usr/bin/env python3
"""
Orchestrator for cloud phone instances.

Default pool: Redroid + GApps OCI VMs for mobile automation.
Camera/stream purpose: spawn Cuttlefish + nginx-rtmp ingest VMs.

Features:
- Provision on-demand (mock, local redroid-up, or OCI golden)
- Separate idle pools per runtime (never mix Play phones with ingest hosts)
- Queue operations (login flow or custom steps)
- Relay commands to Control API
"""

import json
import logging
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path

import requests
from flask import Flask, jsonify, request

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

app = Flask(__name__)

# Logging
LOG_LEVEL = os.environ.get("ORCH_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger("orchestrator")

# Config
# mock | redroid | oci
# oci: Redroid golden for automation (default), Cuttlefish golden for camera
ORCH_DEPLOY_MODE = os.environ.get("ORCH_DEPLOY_MODE", "mock")
ORCH_MOCK_API_URL = os.environ.get("ORCH_MOCK_API_URL", "http://127.0.0.1:8080")
ORCH_API_TOKEN = os.environ.get("ORCH_API_TOKEN", "")
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

# In-memory state
_instances = {}
_instances_lock = threading.Lock()
_ops = {}
_ops_lock = threading.Lock()
_leases = {}
_leases_lock = threading.Lock()
_user_sessions = {}
_user_sessions_lock = threading.Lock()
_next_adb_port = ORCH_REDROID_ADB_PORT_BASE
_adb_port_lock = threading.Lock()
def _require_auth():
    if not ORCH_API_TOKEN:
        return None
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if token != ORCH_API_TOKEN:
        return jsonify({"error": "Unauthorized"}), 401
    return None


@app.before_request
def _auth_middleware():
    if request.path == "/health":
        return None
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


def _clear_lease(instance_id):
    with _leases_lock:
        _leases.pop(instance_id, None)


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


def _control_headers():
    headers = {"Content-Type": "application/json"}
    if ORCH_API_TOKEN:
        headers["Authorization"] = f"Bearer {ORCH_API_TOKEN}"
    return headers


def _control_post(api_url: str, path: str, payload=None):
    url = f"{api_url}{path}"
    logger.info("Control POST %s payload=%s", url, payload)
    resp = requests.post(url, json=payload, headers=_control_headers(), timeout=ORCH_API_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _control_get(api_url: str, path: str):
    url = f"{api_url}{path}"
    logger.info("Control GET %s", url)
    resp = requests.get(url, headers=_control_headers(), timeout=ORCH_API_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


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


def _runtime_limit(runtime):
    if runtime == RUNTIME_REDROID:
        return ORCH_MAX_REDROID_INSTANCES
    if runtime == RUNTIME_CUTTLEFISH:
        return ORCH_MAX_CUTTLEFISH_INSTANCES
    return ORCH_MAX_INSTANCES


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


def _provision_redroid_local():
    port = _allocate_adb_port()
    name = f"{ORCH_INSTANCE_NAME_PREFIX}-{port}"
    cmd = [
        ORCH_REDROID_UP_SCRIPT,
        "--name", name,
        "--adb-port", str(port),
        "--json",
    ]
    if os.environ.get("ORCH_REDROID_DRY_RUN", "").lower() in {"1", "true", "yes"}:
        cmd.append("--dry-run")
    logger.info("Provisioning local Redroid container: %s", " ".join(cmd))
    out = subprocess.check_output(cmd, text=True)
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
        extra={"container": data.get("name"), "image": data.get("image")},
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
    logger.info("Provisioning OCI %s instance: %s", runtime, " ".join(cmd))
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


def _provision_instance(purpose=None, runtime=None):
    purpose = resolve_purpose(purpose, runtime)
    runtime = runtime_for_purpose(purpose)

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

    if ORCH_DEPLOY_MODE == "mock":
        name = f"{ORCH_INSTANCE_NAME_PREFIX}-{runtime}-mock-{_count_runtime(runtime) + 1}"
        logger.info("Mock provisioning %s (%s) -> %s", runtime, purpose, ORCH_MOCK_API_URL)
        return _create_instance_record(
            ORCH_MOCK_API_URL,
            name,
            runtime=runtime,
            purpose=purpose,
            adb_connect="mock://phone",
        )

    if runtime == RUNTIME_REDROID and ORCH_DEPLOY_MODE == "redroid":
        return _provision_redroid_local()

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


def _get_or_create_instance(instance_id=None, purpose=None, runtime=None, provision=True):
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
    return _provision_instance(purpose=purpose, runtime=runtime)


def _run_steps(api_url: str, steps):
    results = []
    for step in steps:
        action = step.get("action")
        logger.info("Executing step action=%s payload=%s", action, step)
        if action == "start_app":
            package = step.get("package")
            if not package:
                raise ValueError("start_app requires package")
            results.append(_control_post(api_url, f"/apps/{package}/start"))
        elif action == "input_text":
            text = step.get("text", "")
            results.append(_control_post(api_url, "/device/input", {"type": "text", "text": text}))
        elif action == "key":
            keycode = int(step.get("keycode", 66))
            results.append(_control_post(api_url, "/device/input", {"type": "key", "keycode": keycode}))
        elif action == "tap":
            x = int(step.get("x", 500))
            y = int(step.get("y", 500))
            results.append(_control_post(api_url, "/device/input", {"type": "tap", "x": x, "y": y}))
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
        _control_get(api_url, "/health")

        if payload.get("steps"):
            steps = _normalize_steps(payload["steps"])
        else:
            steps = _build_login_steps(payload)
        results = _run_steps(api_url, steps)

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
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify(inst), 201


@app.route("/instances/<instance_id>", methods=["DELETE"])
def delete_instance(instance_id):
    with _instances_lock:
        inst = _instances.get(instance_id)
    if not inst:
        return jsonify({"error": "instance not found"}), 404
    if inst.get("mode") == "redroid" and inst.get("name"):
        try:
            subprocess.check_call(
                [ORCH_REDROID_UP_SCRIPT, "--name", inst["name"], "--down"],
            )
        except Exception as exc:
            logger.warning("redroid-down failed: %s", exc)
        with _instances_lock:
            _instances.pop(instance_id, None)
        _clear_lease(instance_id)
        return jsonify({"success": True, "message": "redroid container removed"}), 200

    if inst.get("mode") != "oci":
        with _instances_lock:
            _instances.pop(instance_id, None)
        return jsonify({"success": True, "message": "instance removed"}), 200

    try:
        _terminate_instance(inst.get("instance_ocid"))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    with _instances_lock:
        _instances.pop(instance_id, None)
    return jsonify({"success": True, "message": "instance terminated"}), 200


@app.route("/instances/<instance_id>/lease", methods=["POST"])
def lease_instance(instance_id):
    data = request.get_json() or {}
    owner = data.get("owner", "default")
    ttl = int(data.get("ttl_seconds", 300))
    if ttl < 10:
        return jsonify({"error": "ttl_seconds must be >= 10"}), 400
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
    data = _control_get(inst["api_url"], "/status")
    return jsonify(data)


@app.route("/phones/<instance_id>/health", methods=["GET"])
def phone_health(instance_id):
    inst, err = _require_instance(instance_id)
    if err:
        return err
    data = _control_get(inst["api_url"], "/health")
    return jsonify(data)


@app.route("/phones/<instance_id>/input", methods=["POST"])
def phone_input(instance_id):
    inst, err = _require_instance(instance_id)
    if err:
        return err
    data = request.get_json() or {}
    payload = {"type": data.get("type", "tap")}
    payload.update(data)
    result = _control_post(inst["api_url"], "/device/input", payload)
    return jsonify(result)


@app.route("/phones/<instance_id>/screenshot", methods=["GET"])
def phone_screenshot(instance_id):
    inst, err = _require_instance(instance_id)
    if err:
        return err
    data = _control_get(inst["api_url"], "/device/screenshot/base64")
    return jsonify(data)


@app.route("/phones/<instance_id>/jobs", methods=["POST"])
def phone_job_submit(instance_id):
    inst, err = _require_instance(instance_id)
    if err:
        return err
    payload = request.get_json() or {}
    data = _control_post(inst["api_url"], "/jobs", payload)
    return jsonify(data), 202


@app.route("/phones/<instance_id>/jobs/<job_id>", methods=["GET"])
def phone_job_poll(instance_id, job_id):
    inst, err = _require_instance(instance_id)
    if err:
        return err
    data = _control_get(inst["api_url"], f"/jobs/{job_id}")
    return jsonify(data)


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
    }


def _acquire_user_session(owner_user_id, ttl_seconds=3600, provision=True, purpose=None, runtime=None):
    if not owner_user_id:
        raise ValueError("owner_user_id required")
    ttl_seconds = max(int(ttl_seconds), 10)
    purpose = resolve_purpose(purpose, runtime)
    runtime = runtime_for_purpose(purpose)

    with _user_sessions_lock:
        existing = _user_sessions.get(owner_user_id)
        if existing:
            inst_id = existing.get("instance_id")
            same_runtime = existing.get("runtime") == runtime
            if inst_id and same_runtime and _is_lease_valid(inst_id, owner=owner_user_id):
                _set_lease(inst_id, owner_user_id, ttl_seconds)
                existing["ttl_seconds"] = ttl_seconds
                existing["purpose"] = purpose
                logger.info("Renewed session owner=%s instance=%s runtime=%s", owner_user_id, inst_id, runtime)
                return existing, False
            if inst_id and not same_runtime:
                logger.info(
                    "Owner %s switching purpose %s -> %s; releasing %s",
                    owner_user_id, existing.get("purpose"), purpose, inst_id,
                )
                _clear_lease(inst_id)
                _user_sessions.pop(owner_user_id, None)

    inst = _get_or_create_instance(purpose=purpose, runtime=runtime, provision=provision)
    if _is_lease_valid(inst["id"]) and not _is_lease_valid(inst["id"], owner=owner_user_id):
        raise RuntimeError("phone in use")
    _set_lease(inst["id"], owner_user_id, ttl_seconds)
    sess = _session_from_instance(owner_user_id, inst, ttl_seconds, purpose)
    with _user_sessions_lock:
        _user_sessions[owner_user_id] = sess
    logger.info(
        "Acquired session owner=%s instance=%s runtime=%s purpose=%s",
        owner_user_id, inst["id"], sess["runtime"], purpose,
    )
    return sess, True


def _release_user_session(owner_user_id):
    with _user_sessions_lock:
        sess = _user_sessions.pop(owner_user_id, None)
    if not sess:
        return None
    _clear_lease(sess.get("instance_id"))
    logger.info("Released session owner=%s instance=%s", owner_user_id, sess.get("instance_id"))
    return sess


@app.route("/pool", methods=["GET"])
def get_pool():
    return jsonify({"success": True, "pool": _pool_snapshot()})


@app.route("/sessions", methods=["GET"])
def list_sessions():
    with _user_sessions_lock:
        items = list(_user_sessions.values())
    return jsonify({"count": len(items), "sessions": items})


@app.route("/sessions", methods=["POST"])
def create_session():
    data = request.get_json() or {}
    owner = data.get("owner_user_id") or data.get("owner") or data.get("user_id")
    ttl = int(data.get("ttl_seconds") or data.get("ttl") or 3600)
    provision = True if "provision" not in data else bool(data.get("provision"))
    try:
        sess, created = _acquire_user_session(
            owner,
            ttl,
            provision=provision,
            purpose=data.get("purpose"),
            runtime=data.get("runtime"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        status = 409 if "in use" in str(exc) or "full" in str(exc) or "limit" in str(exc) else 500
        return jsonify({"error": str(exc)}), status
    return jsonify({"success": True, "session": sess, "created": created}), (201 if created else 200)


@app.route("/sessions/<owner_user_id>", methods=["GET"])
def get_session(owner_user_id):
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
    sess = _release_user_session(owner_user_id)
    if not sess:
        return jsonify({"error": "session not found"}), 404
    return jsonify({"success": True, "session": sess})


@app.route("/health", methods=["GET"])
def health():
    with _instances_lock:
        items = list(_instances.values())
    pool = _pool_snapshot(items)
    return jsonify({
        "status": "ok",
        "default_purpose": PURPOSE_AUTOMATION,
        "default_runtime": RUNTIME_REDROID,
        "deploy_mode": ORCH_DEPLOY_MODE,
        "instances": len(items),
        "max_instances": ORCH_MAX_INSTANCES,
        "pool": pool,
    })


if __name__ == "__main__":
    host = os.environ.get("ORCH_HOST", "0.0.0.0")
    port = int(os.environ.get("ORCH_PORT", "8090"))
    logger.info(
        "Starting orchestrator on %s:%s (mode=%s default_runtime=%s)",
        host, port, ORCH_DEPLOY_MODE, RUNTIME_REDROID,
    )
    app.run(host=host, port=port, threaded=True)
