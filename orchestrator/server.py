#!/usr/bin/env python3
"""
Orchestrator service for Cuttlefish cloud phone instances.

Features:
- Provision instance on-demand (mock or OCI via deploy-from-golden.sh)
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

try:  # works whether run as `python orchestrator/server.py` or imported as a module
    from orchestrator.launch_config import build_launch_config, LaunchConfig
except ImportError:  # pragma: no cover
    from launch_config import build_launch_config, LaunchConfig

app = Flask(__name__)

# Logging
LOG_LEVEL = os.environ.get("ORCH_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger("orchestrator")

# Config
ORCH_DEPLOY_MODE = os.environ.get("ORCH_DEPLOY_MODE", "mock")  # mock | oci
ORCH_MOCK_API_URL = os.environ.get("ORCH_MOCK_API_URL", "http://127.0.0.1:8080")
ORCH_API_TOKEN = os.environ.get("ORCH_API_TOKEN", "")
ORCH_API_TIMEOUT = int(os.environ.get("ORCH_API_TIMEOUT", "30"))
ORCH_INSTANCE_NAME_PREFIX = os.environ.get("ORCH_INSTANCE_NAME_PREFIX", "orchestrated-phone")
ORCH_GOLDEN_IMAGE_ID = os.environ.get("GOLDEN_IMAGE_ID", "")
ORCH_MAX_INSTANCES = int(os.environ.get("ORCH_MAX_INSTANCES", "3"))
ORCH_DEPLOY_SCRIPT = os.environ.get(
    "ORCH_DEPLOY_SCRIPT",
    str(Path(__file__).resolve().parents[1] / "scripts" / "deploy-from-golden.sh")
)
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
_fleet_ops = {}
_fleet_ops_lock = threading.Lock()
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


def _create_instance_record(api_url: str, name: str):
    inst_id = uuid.uuid4().hex
    record = {
        "id": inst_id,
        "name": name,
        "api_url": api_url,
        "created_at": time.time(),
        "last_used": time.time(),
        "mode": ORCH_DEPLOY_MODE,
        "instance_ocid": None,
    }
    with _instances_lock:
        _instances[inst_id] = record
    logger.info("Instance registered id=%s name=%s api_url=%s mode=%s", inst_id, name, api_url, ORCH_DEPLOY_MODE)
    return record


def _provision_instance(launch_config=None):
    """Provision (or register) an instance.

    `launch_config` is an optional dict (see orchestrator/launch_config.py). In
    OCI mode it is delivered to the new instance via cloud-init user-data; in
    mock mode it is applied to the target Control API immediately.
    """
    with _instances_lock:
        if len(_instances) >= ORCH_MAX_INSTANCES:
            raise RuntimeError(f"Instance limit reached (ORCH_MAX_INSTANCES={ORCH_MAX_INSTANCES})")

    if ORCH_DEPLOY_MODE == "mock":
        name = f"{ORCH_INSTANCE_NAME_PREFIX}-mock"
        logger.info("Mock provisioning instance -> %s", ORCH_MOCK_API_URL)
        record = _create_instance_record(ORCH_MOCK_API_URL, name)
        if launch_config:
            cfg = build_launch_config(record["id"], **_launch_kwargs(launch_config)).to_dict()
            record["launch_config"] = cfg
            try:
                _control_post(ORCH_MOCK_API_URL, "/launch-config/apply", cfg)
            except Exception:
                logger.exception("Failed to apply launch config to mock control API")
        return record

    if ORCH_DEPLOY_MODE != "oci":
        raise RuntimeError(f"Unsupported ORCH_DEPLOY_MODE: {ORCH_DEPLOY_MODE}")

    if not ORCH_GOLDEN_IMAGE_ID:
        raise RuntimeError("GOLDEN_IMAGE_ID required for OCI provisioning")

    name = f"{ORCH_INSTANCE_NAME_PREFIX}-{time.strftime('%Y%m%d-%H%M%S')}"
    cmd = [ORCH_DEPLOY_SCRIPT, "--image-id", ORCH_GOLDEN_IMAGE_ID, "--name", name, "--wait-check"]

    cfg = None
    userdata_path = None
    if launch_config:
        cfg = build_launch_config(name, golden_image_id=ORCH_GOLDEN_IMAGE_ID, **_launch_kwargs(launch_config))
        userdata_path = f"/tmp/launch-{name}.userdata"
        Path(userdata_path).write_text(cfg.to_cloud_init_userdata())
        cmd += ["--user-data-file", userdata_path]

    logger.info("Provisioning instance via OCI: %s", " ".join(cmd))
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
    logger.info("OCI instance ready name=%s public_ip=%s api_url=%s ocid=%s", name, public_ip, api_url, instance_ocid)
    record = _create_instance_record(api_url, name)
    record["instance_ocid"] = instance_ocid
    if cfg is not None:
        record["launch_config"] = cfg.to_dict()
    return record


def _launch_kwargs(launch_config):
    """Extract LaunchConfig kwargs from a request-supplied dict (instance_id is
    assigned by the orchestrator, so it is ignored here)."""
    if not isinstance(launch_config, dict):
        raise ValueError("launch_config must be an object")
    return {
        "proxy": launch_config.get("proxy"),
        "device_identity": launch_config.get("device_identity"),
        "ui_backend": launch_config.get("ui_backend"),
        "startup_tasks": launch_config.get("startup_tasks"),
        "labels": launch_config.get("labels"),
        "extra": launch_config.get("extra"),
    }


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


def _get_or_create_instance(instance_id=None):
    with _instances_lock:
        if instance_id and instance_id in _instances:
            inst = _instances[instance_id]
            inst["last_used"] = time.time()
            logger.info("Using existing instance id=%s name=%s", inst["id"], inst["name"])
            return inst
        if _instances:
            inst = next(iter(_instances.values()))
            inst["last_used"] = time.time()
            logger.info("Using any available instance id=%s name=%s", inst["id"], inst["name"])
            return inst
    logger.info("No instances available; provisioning new instance")
    return _provision_instance()


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
        instance = _get_or_create_instance(payload.get("instance_id"))
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
    body = request.get_json(silent=True) or {}
    launch_config = body.get("launch_config")
    try:
        inst = _provision_instance(launch_config=launch_config)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(inst), 201


@app.route("/instances/<instance_id>", methods=["DELETE"])
def delete_instance(instance_id):
    with _instances_lock:
        inst = _instances.get(instance_id)
    if not inst:
        return jsonify({"error": "instance not found"}), 404
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


@app.route("/phones/<instance_id>/ui/command", methods=["POST"])
def phone_ui_command(instance_id):
    inst, err = _require_instance(instance_id)
    if err:
        return err
    return jsonify(_control_post(inst["api_url"], "/ui/command", request.get_json() or {}))


@app.route("/phones/<instance_id>/ui/screen", methods=["GET"])
def phone_ui_screen(instance_id):
    inst, err = _require_instance(instance_id)
    if err:
        return err
    return jsonify(_control_get(inst["api_url"], "/ui/screen"))


@app.route("/phones/<instance_id>/monitor", methods=["GET"])
def phone_monitor(instance_id):
    inst, err = _require_instance(instance_id)
    if err:
        return err
    return jsonify(_control_get(inst["api_url"], "/monitor"))


@app.route("/phones/<instance_id>/admin/restart", methods=["POST"])
def phone_admin_restart(instance_id):
    inst, err = _require_instance(instance_id)
    if err:
        return err
    return jsonify(_control_post(inst["api_url"], "/admin/restart", request.get_json(silent=True) or {}))


@app.route("/phones/<instance_id>/admin/shutdown", methods=["POST"])
def phone_admin_shutdown(instance_id):
    inst, err = _require_instance(instance_id)
    if err:
        return err
    return jsonify(_control_post(inst["api_url"], "/admin/shutdown", request.get_json(silent=True) or {}))


@app.route("/fleet/monitor", methods=["GET"])
def fleet_monitor():
    """Aggregate monitor snapshot across all registered phones."""
    with _instances_lock:
        items = list(_instances.items())
    out = {}
    for iid, inst in items:
        try:
            out[iid] = _control_get(inst["api_url"], "/monitor")
        except Exception as exc:
            out[iid] = {"error": str(exc)}
    return jsonify({"count": len(out), "instances": out})


def _run_fleet_operation(fleet_id, instance_ids, payload):
    """Run the same operation across many instances concurrently. Each instance
    runs in its own thread; per-instance status is tracked independently so the
    orchestrator communicates with each phone asynchronously."""
    with _fleet_ops_lock:
        fop = _fleet_ops.get(fleet_id)
        if not fop:
            return
        fop["status"] = "running"
        fop["updated_at"] = time.time()

    def worker(iid):
        entry = {"instance_id": iid, "status": "running"}
        with _fleet_ops_lock:
            fop["instances"][iid] = entry
        try:
            with _instances_lock:
                inst = _instances.get(iid)
            if not inst:
                raise RuntimeError("instance not found")
            api_url = inst["api_url"]
            _control_get(api_url, "/health")
            steps = _normalize_steps(payload["steps"]) if payload.get("steps") else _build_login_steps(payload)
            results = _run_steps(api_url, steps)
            with _fleet_ops_lock:
                entry["status"] = "done"
                entry["results"] = results
        except Exception as exc:
            logger.exception("Fleet op worker failed instance=%s", iid)
            with _fleet_ops_lock:
                entry["status"] = "failed"
                entry["error"] = str(exc)

    threads = []
    for iid in instance_ids:
        t = threading.Thread(target=worker, args=(iid,), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()

    with _fleet_ops_lock:
        statuses = {e["status"] for e in fop["instances"].values()}
        if statuses == {"done"}:
            fop["status"] = "done"
        elif "done" in statuses:
            fop["status"] = "partial"
        else:
            fop["status"] = "failed"
        fop["updated_at"] = time.time()
    logger.info("Fleet op complete id=%s status=%s", fleet_id, fop["status"])


@app.route("/fleet/operations", methods=["POST"])
def create_fleet_operation():
    """Dispatch an operation to multiple phones asynchronously.

    Body: {"operation":"login"|..., "steps":[...], plus login fields}
          target selection (optional): {"instance_ids": [...]} or {"all": true}.
    Defaults to all currently-registered instances.
    """
    payload = request.get_json() or {}
    if payload.get("operation") != "login" and not payload.get("steps"):
        return jsonify({"error": "operation=login or steps required"}), 400
    if payload.get("operation") == "login" and not payload.get("app_package"):
        return jsonify({"error": "app_package required for login operation"}), 400

    requested = payload.get("instance_ids")
    with _instances_lock:
        available = list(_instances.keys())
    if requested:
        instance_ids = [i for i in requested if i in available]
        missing = [i for i in requested if i not in available]
        if missing:
            return jsonify({"error": "unknown instance_ids", "missing": missing}), 404
    else:
        instance_ids = available
    if not instance_ids:
        return jsonify({"error": "no target instances"}), 400

    fleet_id = uuid.uuid4().hex
    fop = {
        "id": fleet_id,
        "status": "queued",
        "created_at": time.time(),
        "updated_at": time.time(),
        "instance_ids": instance_ids,
        "instances": {},
        "payload": payload,
    }
    with _fleet_ops_lock:
        _fleet_ops[fleet_id] = fop

    threading.Thread(target=_run_fleet_operation, args=(fleet_id, instance_ids, payload), daemon=True).start()
    return jsonify({"fleet_operation_id": fleet_id, "status": "queued", "targets": instance_ids}), 202


@app.route("/fleet/operations/<fleet_id>", methods=["GET"])
def get_fleet_operation(fleet_id):
    with _fleet_ops_lock:
        fop = _fleet_ops.get(fleet_id)
    if not fop:
        return jsonify({"error": "fleet operation not found"}), 404
    return jsonify(fop)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "instances": len(_instances), "max_instances": ORCH_MAX_INSTANCES})


if __name__ == "__main__":
    host = os.environ.get("ORCH_HOST", "0.0.0.0")
    port = int(os.environ.get("ORCH_PORT", "8090"))
    logger.info("Starting orchestrator on %s:%s (mode=%s)", host, port, ORCH_DEPLOY_MODE)
    app.run(host=host, port=port, threaded=True)
