#!/usr/bin/env python3
"""
GApps Installation Verification

Checks that core Google packages are installed and reports Play Store status.

Usage:
  python tests/test_gapps_install.py
  ADB_CONNECT=132.226.155.1:5555 python tests/test_gapps_install.py
"""

import os
import subprocess
import sys


ADB_CONNECT = os.environ.get("ADB_CONNECT", "127.0.0.1:5555")


def run_adb(*args, timeout=20):
    cmd = ["adb", "-s", ADB_CONNECT] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def ensure_connected():
    code, out, _ = run_adb("devices")
    if code == 0 and ADB_CONNECT in out and "device" in out:
        return True
    run_adb("connect", ADB_CONNECT)
    code, out, _ = run_adb("devices")
    return code == 0 and ADB_CONNECT in out and "device" in out


def check_package(package):
    code, out, _ = run_adb("shell", "pm", "list", "packages", package)
    return code == 0 and package in out


def main():
    if not ensure_connected():
        print(f"[FAIL] ADB not connected to {ADB_CONNECT}")
        return 1

    required = {
        "com.google.android.gms": "Google Play Services",
        "com.android.vending": "Google Play Store",
        "com.google.android.gsf": "Google Services Framework",
    }

    failed = []
    for pkg, label in required.items():
        if check_package(pkg):
            print(f"[PASS] {label} ({pkg}) installed")
        else:
            print(f"[FAIL] {label} ({pkg}) missing")
            failed.append(pkg)

    if failed:
        print("\nGApps not fully installed. Missing packages:")
        for pkg in failed:
            print(f"  - {pkg}")
        return 2

    print("\n[OK] Core GApps packages present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
