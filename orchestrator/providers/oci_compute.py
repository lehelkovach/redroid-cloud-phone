"""OciComputeProvider: a phone per OCI Compute instance, launched on demand.

Selected with `ORCH_PHONE_PROVIDER=oci` (default unset: the orchestrator
behaves exactly as before). Each automation phone becomes its own
`VM.Standard.A1.Flex` instance launched from a custom image of the configured
GApps phone host, addressed on its VCN-private IP as `http://<ip>:8080`.
docs/deploy-oci.md ("Phones as OCI instances") has the operator side.

Design rules, in order of importance:

1. **A bug must never spawn an unbounded bill.** Two hard caps are checked
   before any OCI call: `ORCH_OCI_MAX_INSTANCES` (phones this provider has
   alive, default 2) and `ORCH_OCI_MAX_LAUNCHES_PER_HOUR` (launch *attempts*,
   default 4; a failing launch counts, so a crash loop stops itself). Over
   either: `ProviderError("oci_cap", ..., 429)` and no API call is made.
2. **Every OCI error is surfaced honestly.** A `ServiceError` becomes
   `ProviderError("oci_error")` carrying the service `code` and HTTP status;
   nothing here retries. The caller decides.
3. **A phone that never answered is terminated.** The launch waits (bounded)
   for RUNNING and then for the Control API `/health` on the private IP. If
   either gives up, the instance is terminated so it does not bill, and the
   refusal says whether that termination succeeded.
4. **No credentials in this process when running on OCI.** Auth is by
   instance principal (`InstancePrincipalsSecurityTokenSigner`); a dev box
   without one falls back to `~/.oci/config` (`ORCH_OCI_CONFIG` /
   `ORCH_OCI_PROFILE`). Which one was used is reported, never the key.

The `oci` SDK is imported lazily and can be replaced (`sdk=`) so the tests
run without it and without a network.
"""

from __future__ import annotations

import collections
import logging
import os
import re
import time
from datetime import datetime, timezone

logger = logging.getLogger("orchestrator.oci")

SHAPE = "VM.Standard.A1.Flex"
TAG_OWNER = "oslo-owner"
TAG_ROLE = "oslo-role"
TAG_CREATED = "oslo-created"
ROLE_PHONE = "phone"
TERMINAL_STATES = {"TERMINATED", "TERMINATING"}

_OWNER_TAG_RE = re.compile(r"[^A-Za-z0-9_.-]+")


class ProviderError(RuntimeError):
    """A refused or failed provider call. `status` is the HTTP status to answer with."""

    def __init__(self, code, message, status=502, **extra):
        super().__init__(message)
        self.code = code
        self.status = status
        self.extra = extra


def _env_int(env, name, default):
    raw = env.get(name, "")
    try:
        return int(raw) if raw.strip() else int(default)
    except ValueError as exc:
        raise ProviderError("oci_config", f"{name} must be an integer (got {raw!r})", 500) from exc


def _env_float(env, name, default):
    raw = env.get(name, "")
    try:
        return float(raw) if raw.strip() else float(default)
    except ValueError as exc:
        raise ProviderError("oci_config", f"{name} must be a number (got {raw!r})", 500) from exc


def config_from_env(env=None):
    """Read `ORCH_OCI_*`. Missing required values are reported by `validate()`."""
    env = os.environ if env is None else env
    return {
        "image_id": env.get("ORCH_OCI_IMAGE_ID", "").strip(),
        "subnet_id": env.get("ORCH_OCI_SUBNET_ID", "").strip(),
        "compartment_id": env.get("ORCH_OCI_COMPARTMENT_ID", "").strip(),
        "availability_domain": env.get("ORCH_OCI_AVAILABILITY_DOMAIN", "").strip(),
        "ocpus": _env_float(env, "ORCH_OCI_OCPUS", 2),
        "memory_gb": _env_float(env, "ORCH_OCI_MEMORY_GB", 12),
        "max_instances": _env_int(env, "ORCH_OCI_MAX_INSTANCES", 2),
        "max_launches_per_hour": _env_int(env, "ORCH_OCI_MAX_LAUNCHES_PER_HOUR", 4),
        "running_timeout": _env_int(env, "ORCH_OCI_RUNNING_TIMEOUT", 300),
        "health_timeout": _env_int(env, "ORCH_OCI_HEALTH_TIMEOUT", 300),
        "poll_seconds": _env_float(env, "ORCH_OCI_POLL_SECONDS", 5),
        "orphan_ttl": _env_int(env, "ORCH_OCI_ORPHAN_TTL", 7200),
        "name_prefix": env.get("ORCH_OCI_NAME_PREFIX", "oslo-phone").strip() or "oslo-phone",
        "auth": (env.get("ORCH_OCI_PROVIDER_AUTH", "auto").strip().lower() or "auto"),
        "config_file": env.get("ORCH_OCI_CONFIG", os.path.expanduser("~/.oci/config")),
        "profile": env.get("ORCH_OCI_PROFILE", "DEFAULT"),
        "api_port": _env_int(env, "ORCH_OCI_PHONE_PORT", 8080),
    }


def validate(config):
    """The config refusal (a string) or None. Checked at startup, before any launch."""
    for key, var in (("image_id", "ORCH_OCI_IMAGE_ID"), ("subnet_id", "ORCH_OCI_SUBNET_ID"),
                     ("compartment_id", "ORCH_OCI_COMPARTMENT_ID")):
        if not config.get(key):
            return f"ORCH_PHONE_PROVIDER=oci needs {var}"
    if config["max_instances"] < 1:
        return "ORCH_OCI_MAX_INSTANCES must be at least 1"
    if config["max_launches_per_hour"] < 1:
        return "ORCH_OCI_MAX_LAUNCHES_PER_HOUR must be at least 1"
    if config["ocpus"] <= 0 or config["memory_gb"] <= 0:
        return "ORCH_OCI_OCPUS and ORCH_OCI_MEMORY_GB must be positive"
    if config["auth"] not in {"auto", "instance_principal", "config"}:
        return "ORCH_OCI_PROVIDER_AUTH must be auto, instance_principal or config"
    return None


def owner_tag(owner):
    """A tag-safe owner token (OCI freeform tag values: printable, no secrets)."""
    raw = str(owner if owner is not None else "").strip()
    return _OWNER_TAG_RE.sub("-", raw).strip("-")[:100] or "unknown"


def _iso_now(clock):
    return datetime.fromtimestamp(clock(), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value):
    try:
        return datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def _default_http_get(url, timeout):
    import requests  # local: the orchestrator has it; tests replace this seam

    resp = requests.get(url, timeout=timeout)
    try:
        body = resp.json()
    except ValueError:
        body = None
    return resp.status_code, body


class OciComputeProvider:
    def __init__(self, config=None, sdk=None, clock=time.time, sleep=time.sleep, http_get=None):
        self.config = dict(config_from_env() if config is None else config)
        why = validate(self.config)
        if why:
            raise ProviderError("oci_config", why, 500)
        self._sdk = sdk
        self._clock = clock
        self._sleep = sleep
        self._http_get = http_get or _default_http_get
        self._clients = None
        self.auth_mode = None
        # What this provider believes it has alive: ocid -> record. Counted
        # against ORCH_OCI_MAX_INSTANCES before any call is made.
        self._live = {}
        self._launch_times = collections.deque()

    # ------------------------------------------------------------------ sdk
    def _module(self):
        if self._sdk is None:
            try:
                import oci  # type: ignore
            except ImportError as exc:
                raise ProviderError(
                    "oci_sdk_missing",
                    "the oci Python SDK is not installed (pip install -r orchestrator/requirements-oci.txt)",
                    500,
                ) from exc
            self._sdk = oci
        return self._sdk

    def _signer_and_config(self):
        """Instance principal first (`auto`), else the config file. Reports which."""
        sdk = self._module()
        mode = self.config["auth"]
        ip_error = None
        if mode in {"auto", "instance_principal"}:
            try:
                signer = sdk.auth.signers.InstancePrincipalsSecurityTokenSigner()
                return {"region": getattr(signer, "region", None)}, signer, "instance_principal"
            except Exception as exc:  # noqa: BLE001 - the SDK raises several types here
                ip_error = exc
                if mode == "instance_principal":
                    raise ProviderError(
                        "oci_auth", f"instance principal unavailable: {type(exc).__name__}", 500
                    ) from exc
        path = self.config["config_file"]
        if not os.path.exists(path):
            detail = f" (instance principal also failed: {type(ip_error).__name__})" if ip_error else ""
            raise ProviderError("oci_auth", f"no OCI config file at {path}{detail}", 500)
        cfg = sdk.config.from_file(file_location=path, profile_name=self.config["profile"])
        return cfg, None, "config"

    def clients(self):
        if self._clients is None:
            sdk = self._module()
            cfg, signer, mode = self._signer_and_config()
            kwargs = {"signer": signer} if signer is not None else {}
            self._clients = {
                "compute": sdk.core.ComputeClient(cfg, **kwargs),
                "vnet": sdk.core.VirtualNetworkClient(cfg, **kwargs),
                "identity": sdk.identity.IdentityClient(cfg, **kwargs),
            }
            self.auth_mode = mode
            logger.info("OCI provider auth=%s compartment=%s", mode, self.config["compartment_id"])
        return self._clients

    def _service_error(self, exc, what):
        code = getattr(exc, "code", None) or type(exc).__name__
        status = getattr(exc, "status", None)
        message = getattr(exc, "message", None) or str(exc)
        logger.error("OCI %s failed: %s %s %s", what, code, status, message)
        return ProviderError("oci_error", f"OCI {what} failed: {code}: {message}", 502,
                             service_code=code, service_status=status)

    def _call(self, what, fn, *args, **kwargs):
        sdk = self._module()
        service_error = getattr(getattr(sdk, "exceptions", None), "ServiceError", ())
        try:
            return fn(*args, **kwargs)
        except service_error as exc:  # type: ignore[misc]
            raise self._service_error(exc, what) from exc

    # ----------------------------------------------------------------- caps
    def _prune_launches(self):
        now = self._clock()
        while self._launch_times and now - self._launch_times[0] >= 3600:
            self._launch_times.popleft()

    def caps(self):
        self._prune_launches()
        return {
            "max_instances": self.config["max_instances"],
            "live": len(self._live),
            "max_launches_per_hour": self.config["max_launches_per_hour"],
            "launches_last_hour": len(self._launch_times),
        }

    def _check_caps(self):
        caps = self.caps()
        if caps["live"] >= caps["max_instances"]:
            raise ProviderError(
                "oci_cap",
                f"OCI phone cap reached: {caps['live']}/{caps['max_instances']} instances alive "
                "(ORCH_OCI_MAX_INSTANCES)",
                429, **caps,
            )
        if caps["launches_last_hour"] >= caps["max_launches_per_hour"]:
            raise ProviderError(
                "oci_cap",
                f"OCI launch cap reached: {caps['launches_last_hour']} launches in the last hour "
                f"(ORCH_OCI_MAX_LAUNCHES_PER_HOUR={caps['max_launches_per_hour']})",
                429, **caps,
            )

    # --------------------------------------------------------------- launch
    def _availability_domain(self):
        ad = self.config.get("availability_domain")
        if ad:
            return ad
        ident = self.clients()["identity"]
        ads = self._call("list_availability_domains", ident.list_availability_domains,
                         self.config["compartment_id"]).data
        if not ads:
            raise ProviderError("oci_error", "no availability domain in the compartment", 502)
        return ads[0].name

    def launch_details(self, owner, name, availability_domain):
        """The LaunchInstanceDetails as the SDK model (tests read the fields)."""
        models = self._module().core.models
        return models.LaunchInstanceDetails(
            availability_domain=availability_domain,
            compartment_id=self.config["compartment_id"],
            display_name=name,
            shape=SHAPE,
            shape_config=models.LaunchInstanceShapeConfigDetails(
                ocpus=self.config["ocpus"], memory_in_gbs=self.config["memory_gb"],
            ),
            source_details=models.InstanceSourceViaImageDetails(
                source_type="image", image_id=self.config["image_id"],
            ),
            create_vnic_details=models.CreateVnicDetails(
                subnet_id=self.config["subnet_id"], assign_public_ip=False, display_name=name,
            ),
            freeform_tags={
                TAG_OWNER: owner_tag(owner),
                TAG_ROLE: ROLE_PHONE,
                TAG_CREATED: _iso_now(self._clock),
            },
        )

    def launch(self, owner, name=None):
        """Launch one phone for `owner`; return the record the orchestrator stores.

        Order: caps -> launch -> wait RUNNING -> private IP -> wait /health.
        A wait that gives up terminates the instance and raises.
        """
        self._check_caps()
        # The attempt is counted first: a launch that fails still spent a slot
        # of the hourly budget, which is the point of the budget.
        self._launch_times.append(self._clock())
        compute = self.clients()["compute"]
        name = name or f"{self.config['name_prefix']}-{owner_tag(owner)[:24]}-{int(self._clock()) % 100000}"
        details = self.launch_details(owner, name, self._availability_domain())
        inst = self._call("launch_instance", compute.launch_instance, details).data
        ocid = inst.id
        record = {
            "instance_ocid": ocid,
            "name": name,
            "owner_tag": details.freeform_tags[TAG_OWNER],
            "image_id": self.config["image_id"],
            "shape": SHAPE,
            "ocpus": self.config["ocpus"],
            "memory_gb": self.config["memory_gb"],
            "created": details.freeform_tags[TAG_CREATED],
            "auth_mode": self.auth_mode,
        }
        self._live[ocid] = record
        logger.info("OCI launch requested name=%s ocid=%s owner_tag=%s", name, ocid, record["owner_tag"])
        try:
            self._wait_running(ocid)
            ip = self._private_ip(ocid)
            record["private_ip"] = ip
            record["api_url"] = f"http://{ip}:{self.config['api_port']}"
            self._wait_health(record["api_url"])
        except ProviderError as exc:
            terminated = self._terminate_quietly(ocid)
            exc.extra.update({"instance_ocid": ocid, "terminated": terminated})
            exc.args = (f"{exc.args[0]}; instance {'terminated' if terminated else 'NOT terminated'}",)
            raise
        logger.info("OCI phone ready name=%s ocid=%s api_url=%s", name, ocid, record["api_url"])
        return record

    def _wait_running(self, ocid):
        compute = self.clients()["compute"]
        deadline = self._clock() + self.config["running_timeout"]
        state = None
        while True:
            state = self._call("get_instance", compute.get_instance, ocid).data.lifecycle_state
            if state == "RUNNING":
                return
            if state in TERMINAL_STATES:
                raise ProviderError("oci_error", f"instance went {state} before RUNNING", 502,
                                    lifecycle_state=state)
            if self._clock() >= deadline:
                raise ProviderError(
                    "oci_timeout",
                    f"instance not RUNNING after {self.config['running_timeout']}s (state {state})",
                    504, lifecycle_state=state,
                )
            self._sleep(self.config["poll_seconds"])

    def _private_ip(self, ocid):
        c = self.clients()
        attachments = self._call(
            "list_vnic_attachments", c["compute"].list_vnic_attachments,
            compartment_id=self.config["compartment_id"], instance_id=ocid,
        ).data
        attached = [a for a in attachments if getattr(a, "lifecycle_state", "ATTACHED") == "ATTACHED"]
        if not attached:
            raise ProviderError("oci_error", "instance has no attached VNIC", 502)
        vnic = self._call("get_vnic", c["vnet"].get_vnic, attached[0].vnic_id).data
        ip = getattr(vnic, "private_ip", None)
        if not ip:
            raise ProviderError("oci_error", "VNIC has no private IP", 502)
        return ip

    def _wait_health(self, api_url):
        deadline = self._clock() + self.config["health_timeout"]
        last = None
        while True:
            try:
                status, body = self._http_get(f"{api_url}/health", timeout=5)
                last = f"HTTP {status}"
                if status == 200 and isinstance(body, dict) and body.get("status") in {"ok", "healthy"}:
                    return body
                if status == 200 and isinstance(body, dict):
                    last = f"HTTP 200 status={body.get('status')!r}"
            except Exception as exc:  # noqa: BLE001 - connection refused while booting is normal
                last = type(exc).__name__
            if self._clock() >= deadline:
                raise ProviderError(
                    "oci_unhealthy",
                    f"phone API at {api_url} not healthy after {self.config['health_timeout']}s (last: {last})",
                    504, last=last,
                )
            self._sleep(self.config["poll_seconds"])

    # ------------------------------------------------------------ terminate
    def terminate(self, instance_ocid):
        if not instance_ocid:
            raise ProviderError("oci_error", "instance_ocid required to terminate", 400)
        compute = self.clients()["compute"]
        self._call("terminate_instance", compute.terminate_instance, instance_ocid,
                   preserve_boot_volume=False)
        self._live.pop(instance_ocid, None)
        logger.info("OCI terminate requested ocid=%s", instance_ocid)
        return True

    def _terminate_quietly(self, ocid):
        try:
            return self.terminate(ocid)
        except ProviderError as exc:
            logger.error("terminating %s after a failed launch also failed: %s", ocid, exc)
            self._live.pop(ocid, None)
            return False

    def forget(self, instance_ocid):
        """Drop an instance from the live count without an API call (it is gone)."""
        self._live.pop(instance_ocid, None)

    # ----------------------------------------------------------------- list
    def list(self):
        """Phones this provider's tags mark, alive in the compartment."""
        compute = self.clients()["compute"]
        items = self._call("list_instances", compute.list_instances,
                           compartment_id=self.config["compartment_id"]).data
        out = []
        for inst in items:
            tags = getattr(inst, "freeform_tags", None) or {}
            if tags.get(TAG_ROLE) != ROLE_PHONE:
                continue
            if getattr(inst, "lifecycle_state", "") in TERMINAL_STATES:
                continue
            out.append({
                "instance_ocid": inst.id,
                "name": getattr(inst, "display_name", None),
                "lifecycle_state": getattr(inst, "lifecycle_state", None),
                "owner_tag": tags.get(TAG_OWNER),
                "created": tags.get(TAG_CREATED),
            })
        return out

    def reconcile(self, known_ocids, now=None):
        """Boot-time: adopt tagged phones the orchestrator knows, terminate orphans past the TTL.

        An orphan younger than `ORCH_OCI_ORPHAN_TTL` is kept and counted (it
        may belong to an orchestrator that is restarting), so the live cap
        stays honest either way. Returns {"adopted", "terminated", "kept"}.
        """
        now = self._clock() if now is None else now
        known = set(known_ocids or ())
        result = {"adopted": [], "terminated": [], "kept": []}
        for item in self.list():
            ocid = item["instance_ocid"]
            if ocid in known:
                self._live.setdefault(ocid, item)
                result["adopted"].append(ocid)
                continue
            created = _parse_iso(item.get("created"))
            age = (now - created) if created is not None else None
            if age is not None and age < self.config["orphan_ttl"]:
                self._live.setdefault(ocid, item)
                result["kept"].append(ocid)
                continue
            logger.warning("OCI orphan phone %s (%s, age %s) terminated at boot", ocid, item.get("name"),
                           "unknown" if age is None else int(age))
            if self._terminate_quietly(ocid):
                result["terminated"].append(ocid)
            else:
                result["kept"].append(ocid)
        return result
