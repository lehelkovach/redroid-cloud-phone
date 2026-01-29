#!/usr/bin/env python3
"""
Connectivity checks for Cloud Phone instances.

Validates required ports and ADB connectivity with actionable logging.

Usage:
  PUBLIC_IP=1.2.3.4 python tests/test_connectivity.py
"""

import os
import socket
import subprocess
import urllib.request
import sys
from datetime import datetime, timezone


PUBLIC_IP = os.environ.get("PUBLIC_IP", "").strip()
SSH_USER = os.environ.get("SSH_USER", "ubuntu").strip()
SSH_KEY = os.environ.get("SSH_KEY", "").strip()
PORTS = [22, 1935, 4723, 5555, 5900, 8080]


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} {msg}")


def check_port(host, port, timeout=2.5):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        return True, ""
    except Exception as exc:
        return False, str(exc)
    finally:
        sock.close()


def check_ping(host):
    cmd = ["ping", "-c", "1", "-W", "2", host]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0, (result.stdout or result.stderr or "").strip()


def check_http(url):
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return True, resp.read(200).decode("utf-8", "ignore")
    except Exception as exc:
        return False, str(exc)


def check_ssh_banner(host, port=22):
    try:
        with socket.create_connection((host, port), timeout=3) as sock:
            sock.settimeout(3)
            banner = sock.recv(200).decode("utf-8", "ignore")
            return True, banner.strip()
    except Exception as exc:
        return False, str(exc)


def check_ssh_login(host):
    if not SSH_KEY:
        return None, "SSH_KEY not set"
    cmd = [
        "ssh",
        "-i",
        SSH_KEY,
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "ConnectTimeout=5",
        f"{SSH_USER}@{host}",
        "echo ok",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0, (result.stdout or result.stderr or "").strip()


def run_adb_connect(host):
    target = f"{host}:5555"
    result = subprocess.run(
        ["adb", "connect", target],
        capture_output=True,
        text=True,
        timeout=10,
    )
    out = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    return result.returncode, out, err


def main():
    if not PUBLIC_IP:
        log("[FAIL] PUBLIC_IP is required (env var)")
        return 2

    failures = 0

    # Ping
    ok, detail = check_ping(PUBLIC_IP)
    if ok:
        log(f"[PASS] ping {PUBLIC_IP} ok")
    else:
        log(f"[FAIL] ping {PUBLIC_IP} failed: {detail}")
        failures += 1
    for port in PORTS:
        ok, err = check_port(PUBLIC_IP, port)
        if ok:
            log(f"[PASS] {PUBLIC_IP}:{port} reachable")
        else:
            log(f"[FAIL] {PUBLIC_IP}:{port} not reachable: {err}")
            if port in (5555, 5900):
                failures += 1

    # SSH banner + login (if key provided)
    ok, detail = check_ssh_banner(PUBLIC_IP)
    if ok:
        log(f"[PASS] SSH banner: {detail}")
    else:
        log(f"[FAIL] SSH banner check failed: {detail}")

    ok, detail = check_ssh_login(PUBLIC_IP)
    if ok is True:
        log("[PASS] SSH login ok")
    elif ok is False:
        log(f"[FAIL] SSH login failed: {detail}")
        failures += 1
    else:
        log(f"[SKIP] SSH login skipped: {detail}")

    # ADB connectivity
    code, out, err = run_adb_connect(PUBLIC_IP)
    if code == 0 and "connected" in out.lower():
        log("[PASS] adb connect succeeded")
    else:
        failures += 1
        log(f"[FAIL] adb connect failed: {out or err}")
        combined = f"{out}\n{err}".lower()
        if "no route to host" in combined:
            log("[HINT] Outbound ports may be blocked by your local network.")
            log("[HINT] Use SSH tunnel: ssh -L 5555:localhost:5555 user@<ip>")

    # API + Appium
    ok, detail = check_http(f"http://{PUBLIC_IP}:8080/health")
    if ok:
        log("[PASS] API /health reachable")
    else:
        log(f"[FAIL] API /health unreachable: {detail}")
        failures += 1

    ok, detail = check_http(f"http://{PUBLIC_IP}:4723/status")
    if ok:
        log("[PASS] Appium /status reachable")
    else:
        log(f"[FAIL] Appium /status unreachable: {detail}")
        failures += 1

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
