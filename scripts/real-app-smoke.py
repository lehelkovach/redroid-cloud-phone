#!/usr/bin/env python3
"""real-app-smoke.py: drive real open-source apps through the Control API.

Installs three small F-Droid apps by HTTPS URL + pinned SHA-256, then for
each one launches it, reads the UI tree, taps named controls, types text,
presses back/home, reads the result back from the screen and takes a
screenshot. Every call goes through the same Control API endpoints OSLO's
mobile.* tools use (/apps/install, /apps/<pkg>/start, /device/ui,
/device/input tap_label|tap|text|key, /device/screenshot).

  ./scripts/real-app-smoke.py --api-url http://127.0.0.1:8080 --out /tmp/smoke
  CLOUD_PHONE_API_TOKEN=... ./scripts/real-app-smoke.py --api-url ... --apps calculator
  ./scripts/real-app-smoke.py --api-url ... --json      # one JSON object on stdout

Exit codes: 0 every step passed, 1 a step failed, 77 skipped (no phone
reachable at --api-url, or it reports itself unusable), 64 usage error.

Token: --token-file, else $CLOUD_PHONE_API_TOKEN, else $API_TOKEN. It is sent
as a bearer header and never printed. Screenshots go to --out (default a
temporary directory); keep them out of git.

Stdlib only, so it runs on a phone host without a virtualenv.
"""

import argparse
import json
import os
import random
import string
import sys
import tempfile
import time
import urllib.error
import urllib.request

# Pinned from F-Droid's index-v2.json (https://f-droid.org/repo/) on
# 2026-10-30. Bump the version and hash together; a mismatch makes the
# Control API delete the download and install nothing.
APPS = {
    "calculator": {
        "package": "org.fossify.math",
        "url": "https://f-droid.org/repo/org.fossify.math_10.apk",
        "sha256": "7c5443c22d6d62c67dfe80df33ea9161ff0fa440f92590bcb45d7008b57613ed",
    },
    "notes": {
        "package": "org.fossify.notes",
        "url": "https://f-droid.org/repo/org.fossify.notes_13.apk",
        "sha256": "5a56e0e39cc488e1f3b947d3801006d3b7450ec73c67f03195c64c5fd3b6bced",
    },
    "contacts": {
        "package": "org.fossify.contacts",
        "url": "https://f-droid.org/repo/org.fossify.contacts_13.apk",
        "sha256": "6a89be96b5ec39f5e2b11bd61dc7031f4b88261b324ab13af5743fbc62f0cddb",
    },
}

KEY_HOME = 3
KEY_BACK = 4
EXIT_SKIP = 77


class Api:
    def __init__(self, base, token, timeout=60):
        self.base = base.rstrip("/")
        self.token = token
        self.timeout = timeout

    def call(self, method, path, body=None, raw=False, timeout=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                payload = resp.read()
                status = resp.status
        except urllib.error.HTTPError as exc:
            payload, status = exc.read(), exc.code
        if raw:
            return status, payload
        try:
            return status, json.loads(payload.decode() or "{}")
        except ValueError:
            return status, {"success": False, "error": "non-JSON %s: %s" % (status, payload[:160])}

    # Thin wrappers named after OSLO's CloudPhoneClient methods.
    def health(self):
        return self.call("GET", "/health", timeout=20)[1]

    def install(self, app):
        body = {"url": app["url"], "sha256": app["sha256"], "expected_package": app["package"]}
        return self.call("POST", "/apps/install", body, timeout=600)[1]

    def installed(self, package):
        return self.call("GET", "/apps/" + package)[1].get("installed") is True

    def launch(self, package):
        return self.call("POST", "/apps/%s/start" % package, {})[1]

    def stop(self, package):
        return self.call("POST", "/apps/%s/stop" % package, {})[1]

    def ui(self):
        # The smoke reads typed text back, so it asks for values explicitly;
        # /device/ui redacts what inputs hold by default.
        return self.call("GET", "/device/ui?include_values=1")[1]

    def tap_label(self, label):
        return self.call("POST", "/device/input", {"type": "tap_label", "label": label})[1]

    def tap(self, x, y):
        return self.call("POST", "/device/input", {"type": "tap", "x": x, "y": y})[1]

    def type_text(self, text):
        return self.call("POST", "/device/input", {"type": "text", "text": text})[1]

    def key(self, keycode):
        return self.call("POST", "/device/input", {"type": "key", "keycode": keycode})[1]

    def shell(self, command):
        return self.call("POST", "/adb/shell", {"command": command})[1]

    def focus(self):
        return self.call("GET", "/device/focus")[1].get("focus") or ""

    def screenshot(self):
        status, png = self.call("GET", "/device/screenshot", raw=True)
        return png if status == 200 and png[:8] == b"\x89PNG\r\n\x1a\n" else None


def short_id(element):
    rid = element.get("resource_id") or ""
    return rid.split(":id/", 1)[1] if ":id/" in rid else rid


def find(elements, rid=None, label=None, contains=None):
    for el in elements or []:
        if rid and short_id(el) != rid:
            continue
        if label is not None and el.get("label") != label:
            continue
        if contains is not None and contains not in (el.get("label") or ""):
            continue
        return el
    return None


def keyboard_shown(api):
    out = api.shell("dumpsys input_method").get("stdout") or ""
    return "mInputShown=true" in out


def hide_keyboard(api):
    """BACK only while the keyboard is up: otherwise BACK leaves the screen."""
    if keyboard_shown(api):
        api.key(KEY_BACK)
        time.sleep(1)
        return True
    return False


def answer_permission_prompt(api, allow_app_label):
    """Answer a runtime permission prompt if one is on screen.

    Allow it only when the prompt names the app under test; anything else
    (the AOSP keyboard asking for contacts, say) is refused.
    """
    els = api.ui().get("elements") or []
    msg = find(els, rid="permission_message")
    if not msg:
        return None
    allow = allow_app_label.lower() in (msg.get("label") or "").lower()
    target = find(els, rid="permission_allow_button" if allow else "permission_deny_button")
    if target:
        api.tap(target["x"], target["y"])
        time.sleep(1)
    return "%s: %s" % ("allowed" if allow else "denied", msg.get("label"))


class Run:
    """Records each step as pass/fail with a detail and a duration."""

    def __init__(self, api, out_dir, quiet=False):
        self.api = api
        self.out = out_dir
        self.quiet = quiet
        self.steps = []

    def step(self, app, name, fn):
        started = time.time()
        try:
            ok, detail = fn()
        except Exception as exc:  # a broken step must not hide the others
            ok, detail = False, "%s: %s" % (type(exc).__name__, exc)
        rec = {"app": app, "step": name, "ok": bool(ok), "detail": detail,
               "ms": int((time.time() - started) * 1000)}
        self.steps.append(rec)
        if not self.quiet:
            print("%-4s %-10s %-22s %s" % ("PASS" if ok else "FAIL", app, name, detail), file=sys.stderr)
        return ok

    def wait_ui(self, predicate, timeout=15.0):
        """Poll /device/ui until predicate(elements) is truthy."""
        deadline = time.time() + timeout
        last = []
        while time.time() < deadline:
            tree = self.api.ui()
            last = tree.get("elements") or []
            hit = predicate(last)
            if hit:
                return hit, last
            time.sleep(1)
        return None, last

    def shot(self, app, name):
        png = self.api.screenshot()
        if not png:
            return False, "no PNG from /device/screenshot"
        path = os.path.join(self.out, "%s-%s.png" % (app, name))
        with open(path, "wb") as fh:
            fh.write(png)
        return True, "%s (%d bytes)" % (path, len(png))


def ensure_installed(run, app_name, app):
    def go():
        if run.api.installed(app["package"]):
            return True, "already installed"
        res = run.api.install(app)
        return bool(res.get("success") and res.get("verified")), \
            "installed by URL, sha256 verified" if res.get("success") else "%s: %s" % (res.get("code"), res.get("error"))
    return run.step(app_name, "install", go)


def fresh_launch(run, app_name, package, ready):
    def go():
        run.api.stop(package)
        res = run.api.launch(package)
        if not res.get("success"):
            return False, res.get("error") or "start failed"
        hit, els = run.wait_ui(ready, 20)
        return bool(hit), "ready (%d elements)" % len(els) if hit else "app screen not seen; focus=%s" % run.api.focus()
    return run.step(app_name, "launch+read_ui", go)


def smoke_calculator(run, app):
    name, pkg = "calculator", app["package"]
    if not fresh_launch(run, name, pkg, lambda els: find(els, rid="btn_equals")):
        return
    run.step(name, "tap C (clear)", lambda: (bool(run.api.tap_label("C").get("success")), "tapped C"))
    for label in ("7", "×", "6", "="):
        run.step(name, "tap %s" % label, lambda label=label: (
            bool(run.api.tap_label(label).get("success")), "tap_label %r" % label))

    def result():
        hit, els = run.wait_ui(lambda e: find(e, rid="result", label="42"), 8)
        got = find(els, rid="result")
        return bool(hit), "result shows %r (want '42')" % (got or {}).get("label")
    run.step(name, "read result", result)
    run.step(name, "screenshot", lambda: run.shot(name, "7x6"))
    run.step(name, "home", lambda: (bool(run.api.key(KEY_HOME).get("success")), "HOME sent"))


def smoke_notes(run, app):
    name, pkg = "notes", app["package"]
    token = "smoke%d" % int(time.time())
    if not fresh_launch(run, name, pkg, lambda els: find(els, rid="text_note_view")):
        return

    def tap_field():
        hit, els = run.wait_ui(lambda e: find(e, rid="text_note_view"), 5)
        if not hit:
            return False, "no note field"
        res = run.api.tap(hit["x"], hit["y"])
        return bool(res.get("success")), "tapped note field at %s,%s" % (hit["x"], hit["y"])
    run.step(name, "tap note field", tap_field)
    run.api.key(123)  # KEYCODE_MOVE_END: append after any earlier run's text
    run.step(name, "type text", lambda: (bool(run.api.type_text(" " + token).get("success")), "typed %r" % token))

    def on_screen():
        hit, els = run.wait_ui(lambda e: find(e, rid="text_note_view", contains=token), 8)
        return bool(hit), "field shows the token" if hit else "token not in field"
    run.step(name, "read typed text", on_screen)
    run.step(name, "back", lambda: (bool(run.api.key(KEY_BACK).get("success")), "BACK (closes keyboard)"))
    run.step(name, "home", lambda: (bool(run.api.key(KEY_HOME).get("success")), "HOME sent"))
    time.sleep(2)

    def persists():
        run.api.stop(pkg)  # force-stop: the text must come from storage
        run.api.launch(pkg)
        hit, _ = run.wait_ui(lambda e: find(e, rid="text_note_view", contains=token), 20)
        return bool(hit), "note text survived force-stop + relaunch" if hit else "token missing after relaunch"
    run.step(name, "persist after relaunch", persists)
    run.step(name, "screenshot", lambda: run.shot(name, "persisted"))
    run.api.key(KEY_HOME)


def smoke_contacts(run, app):
    name, pkg = "contacts", app["package"]
    # The apostrophe checks that shell quoting survives the round trip.
    surname = "O'Smoke%d" % int(time.time())
    # A first name no saved contact starts with: a matching one opens the
    # app's "matching contacts" dropdown, which covers Save and Surname and
    # turns the next tap into "pick that old contact".
    first = "Tess" + "".join(random.choice(string.ascii_lowercase) for _ in range(5))

    def ready(els):
        return find(els, rid="fragment_fab") or find(els, rid="permission_allow_button")
    if not fresh_launch(run, name, pkg, ready):
        return

    def permission():
        answered = answer_permission_prompt(run.api, "Allow Contacts")
        hit, _ = run.wait_ui(lambda e: find(e, rid="fragment_fab"), 10)
        return bool(hit), answered or "no permission prompt (granted earlier)"
    run.step(name, "permission", permission)

    def open_editor():
        res = run.api.tap_label("Create new contact")
        hit, _ = run.wait_ui(lambda e: find(e, rid="contact_first_name"), 10)
        return bool(res.get("success") and hit), "editor open" if hit else "editor not seen"
    run.step(name, "tap Create new contact", open_editor)

    def fill(field_label, rid, text):
        def go():
            # The keyboard covers the lower fields, and the AOSP keyboard asks
            # for contacts access the first time it opens in this app.
            hide_keyboard(run.api)
            res = run.api.tap_label(field_label)
            if not res.get("success"):
                return False, "no %r field" % field_label
            time.sleep(1)
            prompt = answer_permission_prompt(run.api, "Allow Contacts")
            if prompt:
                run.api.tap_label(field_label)
            typed = run.api.type_text(text)
            if not typed.get("success"):
                return False, "typing into %s failed: %s" % (field_label, typed.get("error"))
            hit, els = run.wait_ui(lambda e: find(e, rid=rid, label=text), 6)
            if hit:
                detail = "%s = %r" % (field_label, text)
            else:
                detail = "%r not shown in %s (it shows %r; tapped %s,%s)" % (
                    text, field_label, (find(els, rid=rid) or {}).get("label"), res.get("x"), res.get("y"))
            return bool(hit), detail + ("; keyboard prompt " + prompt if prompt else "")
        return go
    run.step(name, "type First name", fill("First name", "contact_first_name", first))
    run.step(name, "type Surname", fill("Surname", "contact_surname", surname))
    run.step(name, "screenshot form", lambda: run.shot(name, "form"))

    def save():
        hide_keyboard(run.api)
        res = run.api.tap_label("Save")
        return bool(res.get("success")), "tapped Save"
    run.step(name, "tap Save", save)
    time.sleep(2)
    run.step(name, "home", lambda: (bool(run.api.key(KEY_HOME).get("success")), "HOME sent"))

    def listed():
        run.api.stop(pkg)
        run.api.launch(pkg)
        hit, els = run.wait_ui(lambda e: find(e, contains=surname), 20)
        return bool(hit), "list shows %r after relaunch" % (hit or {}).get("label") if hit else "contact not listed"
    run.step(name, "read saved contact", listed)
    run.step(name, "screenshot", lambda: run.shot(name, "saved"))
    run.api.key(KEY_HOME)

    def cleanup():
        # Remove only the contact this run made, so runs do not pile up.
        res = run.api.shell(
            "content delete --uri content://com.android.contacts/raw_contacts"
            "?caller_is_syncadapter=true --where \"display_name LIKE '%%%s%%'\"" % surname)
        return bool(res.get("success")), "deleted the test contact"
    run.step(name, "cleanup", cleanup)


SMOKES = {"calculator": smoke_calculator, "notes": smoke_notes, "contacts": smoke_contacts}


def read_token(path):
    if path:
        with open(path) as fh:
            return fh.read().strip()
    return os.environ.get("CLOUD_PHONE_API_TOKEN") or os.environ.get("API_TOKEN") or ""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--api-url", default=os.environ.get("CLOUD_PHONE_API_URL", ""),
                    help="Control API base (default $CLOUD_PHONE_API_URL)")
    ap.add_argument("--token-file", help="file holding the bearer token (else $CLOUD_PHONE_API_TOKEN / $API_TOKEN)")
    ap.add_argument("--apps", default=",".join(APPS), help="comma list of: " + ", ".join(APPS))
    ap.add_argument("--out", help="directory for screenshots (default: a new temp dir)")
    ap.add_argument("--json", action="store_true", help="print a JSON summary on stdout")
    args = ap.parse_args(argv)

    wanted = [a.strip() for a in args.apps.split(",") if a.strip()]
    unknown = [a for a in wanted if a not in APPS]
    if unknown or not wanted:
        print("unknown app(s): %s" % ", ".join(unknown or ["(none given)"]), file=sys.stderr)
        return 64
    if not args.api_url:
        print("SKIP: no --api-url / $CLOUD_PHONE_API_URL", file=sys.stderr)
        return EXIT_SKIP

    api = Api(args.api_url, read_token(args.token_file))
    try:
        health = api.health()
    except Exception as exc:
        print("SKIP: no phone at %s (%s)" % (args.api_url, exc), file=sys.stderr)
        return EXIT_SKIP
    if not health.get("usable", health.get("status") == "healthy"):
        print("SKIP: phone at %s is not usable: %s" % (
            args.api_url, health.get("error") or health.get("status")), file=sys.stderr)
        return EXIT_SKIP

    out = args.out or tempfile.mkdtemp(prefix="real-app-smoke-")
    os.makedirs(out, exist_ok=True)
    run = Run(api, out, quiet=False)
    for app_name in wanted:
        if ensure_installed(run, app_name, APPS[app_name]):
            SMOKES[app_name](run, APPS[app_name])

    passed = sum(1 for s in run.steps if s["ok"])
    summary = {"api_url": args.api_url, "out": out, "passed": passed,
               "failed": len(run.steps) - passed, "steps": run.steps}
    if args.json:
        print(json.dumps(summary, indent=2))
    print("%d/%d steps passed; screenshots in %s" % (passed, len(run.steps), out), file=sys.stderr)
    return 0 if passed == len(run.steps) else 1


if __name__ == "__main__":
    sys.exit(main())
