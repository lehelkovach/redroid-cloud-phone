#!/usr/bin/env python3
"""
Tiered test runner — executes the test suite in the staged development order so
each component/step is asserted tier by tier. Development proceeds along this line:

  Tier 1  Build deploys & functions: RTMP A/V stream -> camera/mic sinks
  Tier 2  Launch a new VM & validate provisioning (image + launch config + services)
  Tier 3  Orchestrator <-> instance control plane (IPC: route, ops, monitor, admin)
  Tier 4  UI commandlets (tap/swipe/text/key/screen) over adb | appium

Steps that require a live Cuttlefish device / OCI launch / Appium server are
reported as SKIP with the reason (they can't run in a plain dev container), so the
tier map is complete and honest. Run from the repo root: `python tests/run_tiers.py`.
"""

import os
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TIERS = [
    ("Tier 1 — Build deploys & functions (RTMP A/V -> camera/mic sinks)", [
        ("1.1 ffmpeg loop -> nginx-rtmp -> bridge -> front/back/mic sinks", "bridge_stream"),
        ("1.2 OBS -> Cuttlefish camera-app injection (live)", "skip:needs live Cuttlefish device (KVM) + SSH"),
    ]),
    ("Tier 2 — Launch new VM & provisioning validation", [
        ("2.1 launch config render/parse + cloud-init delivery", "unittest:tests.test_launch_config"),
        ("2.2 orchestrator provision (incl. launch config) + async fleet fan-out", "script:tests/test_orchestrator_fleet.py"),
        ("2.3 register existing dev phone by IP (env) + route to it", "script:tests/test_orchestrator_register.py"),
        ("2.4 live provision new VM + validate files/services", "skip:needs OCI launch + device (tests/test_connectivity.py)"),
    ]),
    ("Tier 3 — Orchestrator <-> instance control plane (IPC)", [
        ("3.1 orchestrator unit", "unittest:tests.test_orchestrator_unit"),
        ("3.2 orchestrator routing integration", "script:tests/test_orchestrator_integration.py"),
        ("3.3 e2e login flow", "script:tests/test_orchestrator_e2e.py"),
        ("3.4 management/IPC: monitor + admin restart/shutdown", "script:tests/test_orchestrator_admin.py"),
    ]),
    ("Tier 4 — UI commandlets (adb | appium)", [
        ("4.1 command building, percent coords, backend select", "unittest:tests.test_ui_control"),
        ("4.2 UI commandlet endpoints (adb mapping)", "script:tests/test_ui_endpoints.py"),
        ("4.3 appium backend (live)", "skip:needs Appium server + device"),
    ]),
]

GREEN, RED, YELLOW, BLUE, NC = "\033[32m", "\033[31m", "\033[33m", "\033[34m", "\033[0m"


def _run(cmd):
    return subprocess.run(cmd, cwd=ROOT).returncode == 0


def _nginx_rtmp_up():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8081/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def run_step(kind):
    """Return (status, detail) where status in {pass, fail, skip}."""
    if kind.startswith("skip:"):
        return ("skip", kind[len("skip:"):])
    if kind.startswith("unittest:"):
        return ("pass" if _run([sys.executable, "-m", "unittest", kind.split(":", 1)[1]]) else "fail", "")
    if kind.startswith("script:"):
        return ("pass" if _run([sys.executable, kind.split(":", 1)[1]]) else "fail", "")
    if kind == "bridge_stream":
        if not _nginx_rtmp_up():
            return ("skip", "nginx-rtmp not running on :8081 (start it to run this tier locally)")
        ok = _run(["bash", "scripts/test-cuttlefish-rtmp-bridge.sh", "--local", "--duration", "12"])
        return ("pass" if ok else "fail", "")
    return ("fail", f"unknown step kind: {kind}")


def main():
    results = []
    for tier_name, steps in TIERS:
        print(f"\n{BLUE}=== {tier_name} ==={NC}")
        for label, kind in steps:
            status, detail = run_step(kind)
            mark = {"pass": f"{GREEN}PASS{NC}", "fail": f"{RED}FAIL{NC}", "skip": f"{YELLOW}SKIP{NC}"}[status]
            print(f"  [{mark}] {label}" + (f"  ({detail})" if detail else ""))
            results.append((tier_name, label, status))

    passed = sum(1 for *_, s in results if s == "pass")
    failed = sum(1 for *_, s in results if s == "fail")
    skipped = sum(1 for *_, s in results if s == "skip")
    print(f"\n{BLUE}Summary:{NC} {GREEN}{passed} passed{NC}, {RED}{failed} failed{NC}, {YELLOW}{skipped} skipped (need live device/OCI/Appium){NC}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
