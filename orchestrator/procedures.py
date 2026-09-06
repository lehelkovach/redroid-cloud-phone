"""Surface-agnostic procedures.

A procedure is a list of steps. A step names an action from one shared
vocabulary; a *surface adapter* decides how that action reaches the world:

    mobile   cloud Android (Control API -> adb)      Redroid phones / Cuttlefish
    web      cloud browser (Playwright driver)       headless egress
    chrome   the user's own tab (extension bridge)   real IP, human present
    console  shell on the host                       adb-less, no UI

The same `{"action": "type", "text": "..."}` runs on any of them, so a login
procedure written against a phone can be replayed in a browser tab without a
rewrite. Steps may override the surface individually, which is how a procedure
reads a code on `mobile` and types it on `web`.

Two rules the callers depend on:

1. **Validate before executing.** An unsupported action fails the whole
   procedure before step 1 touches a device — a half-applied procedure on a
   logged-in phone is worse than one that never started.
2. **Sensitive steps need approval.** Install, submit, and pay stay gated even
   when the surface could do them silently.
"""

import time

MOBILE = "mobile"
WEB = "web"
CHROME = "chrome"
CONSOLE = "console"

SURFACES = (MOBILE, WEB, CHROME, CONSOLE)

# Actions that outlive the session or spend money/reputation. Gated everywhere,
# because the surface that *can* do it silently is exactly the risk.
SENSITIVE_ACTIONS = frozenset({"install", "submit", "purchase"})

# Canonical vocabulary. Adapters implement a subset and declare it.
ACTIONS = frozenset({
    "open",        # launch app / navigate URL
    "tap",         # tap or click at a point
    "tap_label",   # tap the element carrying a label
    "type",        # enter text into the focused field
    "key",         # keycode / named key
    "swipe",       # directional drag
    "wait",        # sleep for duration_ms
    "read",        # read the UI/DOM tree
    "screenshot",
    "shell",       # run a command on the host/device
    "install",     # sensitive
    "submit",      # sensitive
    "purchase",    # sensitive
})


class ProcedureError(Exception):
    """Base for procedure problems that should not be retried blindly."""


class StepValidationError(ProcedureError):
    pass


class UnknownSurfaceError(ProcedureError):
    pass


class UnsupportedActionError(ProcedureError):
    pass


class ApprovalRequiredError(ProcedureError):
    def __init__(self, action, index):
        super().__init__(f"step {index} ({action}) requires approval")
        self.action = action
        self.index = index


class SurfaceAdapter:
    """Executes canonical actions on one surface."""

    name = "abstract"
    capabilities = frozenset()

    def supports(self, action):
        return action in self.capabilities

    def execute(self, action, step, context):
        raise NotImplementedError


class MobileAdapter(SurfaceAdapter):
    """Cloud Android via the Control API (Redroid phones, Cuttlefish guests)."""

    name = MOBILE
    capabilities = frozenset({
        "open", "tap", "tap_label", "type", "key", "swipe", "wait",
        "read", "screenshot", "shell", "install", "submit",
    })

    def __init__(self, control_post, control_get=None, instance=None):
        self.control_post = control_post
        self.control_get = control_get
        self.instance = instance

    def _post(self, path, payload=None):
        return self.control_post(path, payload, instance=self.instance)

    def execute(self, action, step, context):
        if action == "open":
            package = step.get("package") or step.get("target")
            return self._post(f"/apps/{package}/start", {})
        if action == "tap":
            return self._post("/device/input", {
                "type": "tap",
                "x": int(step.get("x", 0)),
                "y": int(step.get("y", 0)),
            })
        if action == "tap_label":
            return self._post("/device/input", {
                "type": "tap_label",
                "label": step.get("label") or step.get("target"),
            })
        if action == "type":
            return self._post("/device/input", {"type": "text", "text": step.get("text", "")})
        if action == "key":
            return self._post("/device/input", {
                "type": "key",
                "keycode": int(step.get("keycode", 66)),
            })
        if action == "swipe":
            return self._post("/device/input", {
                "type": "swipe",
                "x1": int(step.get("x1", 0)), "y1": int(step.get("y1", 0)),
                "x2": int(step.get("x2", 0)), "y2": int(step.get("y2", 0)),
                "duration": int(step.get("duration_ms", 300)),
            })
        if action == "read":
            if self.control_get:
                return self.control_get("/device/ui", instance=self.instance)
            return self._post("/device/ui", {})
        if action == "screenshot":
            if self.control_get:
                return self.control_get("/device/screenshot/base64", instance=self.instance)
            return self._post("/device/screenshot/base64", {})
        if action == "shell":
            return self._post("/adb/shell", {"command": step.get("command", "")})
        if action == "install":
            return self._post("/apps/install", {"path": step.get("path")})
        if action == "submit":
            return self._post("/device/input", {"type": "key", "keycode": 66})
        raise UnsupportedActionError(f"{self.name} cannot {action}")


class WebAdapter(SurfaceAdapter):
    """Cloud browser. `driver` is any callable taking (action, step)."""

    name = WEB
    capabilities = frozenset({
        "open", "tap", "tap_label", "type", "key", "wait",
        "read", "screenshot", "submit", "purchase",
    })

    def __init__(self, driver):
        self.driver = driver

    def execute(self, action, step, context):
        return self.driver(action, step)


class ChromeAdapter(SurfaceAdapter):
    """The user's own tab through the extension bridge.

    Deliberately narrower than `web`: the helper proposes a fill and the person
    clicks Apply, so there is no unattended submit and no purchase here.
    """

    name = CHROME
    capabilities = frozenset({
        "open", "tap", "tap_label", "type", "wait", "read", "screenshot",
    })

    def __init__(self, bridge):
        self.bridge = bridge

    def execute(self, action, step, context):
        return self.bridge(action, step)


class ConsoleAdapter(SurfaceAdapter):
    """Host shell. No UI, so only the non-visual verbs."""

    name = CONSOLE
    capabilities = frozenset({"shell", "wait", "read"})

    def __init__(self, runner):
        self.runner = runner

    def execute(self, action, step, context):
        if action == "wait":
            return {"success": True, "waited_ms": int(step.get("duration_ms", 0))}
        return self.runner(action, step)


def normalize_step(step, index, default_surface):
    """Return a validated copy, or raise. Never mutates the caller's dict."""
    if not isinstance(step, dict):
        raise StepValidationError(f"step {index} must be an object")

    action = str(step.get("action") or "").strip()
    if not action:
        raise StepValidationError(f"step {index} is missing an action")
    if action not in ACTIONS:
        raise StepValidationError(
            f"step {index}: unknown action '{action}' (known: {', '.join(sorted(ACTIONS))})"
        )

    surface = str(step.get("surface") or default_surface or "").strip()
    if surface not in SURFACES:
        raise UnknownSurfaceError(
            f"step {index}: unknown surface '{surface}' (known: {', '.join(SURFACES)})"
        )

    normalized = dict(step)
    normalized["action"] = action
    normalized["surface"] = surface

    if action == "open" and not (step.get("package") or step.get("url") or step.get("target")):
        raise StepValidationError(f"step {index}: open requires package, url, or target")
    if action == "tap_label" and not (step.get("label") or step.get("target")):
        raise StepValidationError(f"step {index}: tap_label requires a label")
    if action == "shell" and not step.get("command"):
        raise StepValidationError(f"step {index}: shell requires a command")
    if action == "install" and not step.get("path"):
        raise StepValidationError(f"step {index}: install requires a path")
    if action == "wait":
        try:
            normalized["duration_ms"] = int(step.get("duration_ms", 0))
        except (TypeError, ValueError):
            raise StepValidationError(f"step {index}: duration_ms must be an integer")
        if normalized["duration_ms"] < 0:
            raise StepValidationError(f"step {index}: duration_ms must be >= 0")

    return normalized


def validate_procedure(steps, adapters, default_surface=MOBILE, approve=False):
    """Check every step against its adapter before anything executes."""
    if not isinstance(steps, list) or not steps:
        raise StepValidationError("procedure requires a non-empty list of steps")

    normalized = []
    for index, step in enumerate(steps):
        item = normalize_step(step, index, default_surface)
        adapter = adapters.get(item["surface"])
        if adapter is None:
            raise UnknownSurfaceError(
                f"step {index}: no adapter wired for surface '{item['surface']}'"
            )
        if not adapter.supports(item["action"]):
            raise UnsupportedActionError(
                f"step {index}: surface '{item['surface']}' cannot '{item['action']}'"
            )
        if item["action"] in SENSITIVE_ACTIONS and not approve:
            raise ApprovalRequiredError(item["action"], index)
        normalized.append(item)
    return normalized


def run_procedure(steps, adapters, default_surface=MOBILE, approve=False,
                  logger=None, context=None, sleep=time.sleep):
    """Validate then execute. Returns a per-step record either way."""
    normalized = validate_procedure(steps, adapters, default_surface, approve)
    context = context if context is not None else {}
    results = []

    for index, step in enumerate(normalized):
        action = step["action"]
        surface = step["surface"]
        adapter = adapters[surface]
        started = time.time()
        if logger:
            logger.info("step %s/%s %s on %s", index + 1, len(normalized), action, surface)

        try:
            if action == "wait" and surface != CONSOLE:
                sleep(step["duration_ms"] / 1000.0)
                output = {"success": True, "waited_ms": step["duration_ms"]}
            else:
                output = adapter.execute(action, step, context)
            ok = not (isinstance(output, dict) and output.get("success") is False)
            results.append({
                "index": index,
                "action": action,
                "surface": surface,
                "status": "ok" if ok else "failed",
                "duration_ms": int((time.time() - started) * 1000),
                "result": output,
            })
            if not ok:
                error = (output or {}).get("error", "step reported failure")
                if logger:
                    logger.error("step %s (%s on %s) failed: %s", index, action, surface, error)
                return {"status": "failed", "steps": results, "error": error,
                        "failed_index": index}
        except ProcedureError:
            raise
        except Exception as exc:
            results.append({
                "index": index,
                "action": action,
                "surface": surface,
                "status": "error",
                "duration_ms": int((time.time() - started) * 1000),
                "error": str(exc),
            })
            if logger:
                logger.exception("step %s (%s on %s) raised", index, action, surface)
            return {"status": "failed", "steps": results, "error": str(exc),
                    "failed_index": index}

    return {"status": "done", "steps": results, "error": None, "failed_index": None}


def surface_capabilities(adapters):
    """What each wired surface can actually do — for /procedures/surfaces."""
    return {
        name: sorted(adapter.capabilities)
        for name, adapter in sorted(adapters.items())
    }


def login_procedure(app_target, username, password, surface=MOBILE,
                    password_label=None, submit=True):
    """The one procedure every surface shares, built from the shared vocabulary."""
    steps = [
        {"action": "open", "target": app_target, "package": app_target,
         "url": app_target, "surface": surface},
        {"action": "wait", "duration_ms": 800, "surface": surface},
        {"action": "type", "text": username, "surface": surface},
        {"action": "wait", "duration_ms": 200, "surface": surface},
    ]
    if password_label:
        steps.append({"action": "tap_label", "label": password_label, "surface": surface})
    else:
        steps.append({"action": "key", "keycode": 61, "surface": surface})
    steps.append({"action": "type", "text": password, "surface": surface})
    if submit:
        steps.append({"action": "key", "keycode": 66, "surface": surface})
    return steps
