#!/usr/bin/env python3
"""
Control API CLI client for Redroid instances.

Usage examples:
  python scripts/control-client.py --api-url http://IP:8080 health
  python scripts/control-client.py --api-url http://IP:8080 tap --x 540 --y 1200
  python scripts/control-client.py --api-url http://IP:8080 text --text "hello"
  python scripts/control-client.py --api-url http://IP:8080 screenshot --out /tmp/screen.png
  python scripts/control-client.py --api-url http://IP:8080 shell --cmd "getprop ro.build.version.release"
"""

import argparse
import json
import sys
import time
from pathlib import Path

import requests


def build_headers(token: str) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def request_json(method: str, url: str, headers: dict, data=None, timeout=30):
    resp = requests.request(method, url, headers=headers, json=data, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def main():
    parser = argparse.ArgumentParser(description="Redroid Control API client")
    parser.add_argument("--api-url", required=True, help="Base API URL, e.g. http://IP:8080")
    parser.add_argument("--token", default="", help="Bearer token if configured")
    parser.add_argument("--timeout", type=int, default=30, help="Request timeout")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health")
    sub.add_parser("status")

    tap = sub.add_parser("tap")
    tap.add_argument("--x", type=int, required=True)
    tap.add_argument("--y", type=int, required=True)

    swipe = sub.add_parser("swipe")
    swipe.add_argument("--x1", type=int, required=True)
    swipe.add_argument("--y1", type=int, required=True)
    swipe.add_argument("--x2", type=int, required=True)
    swipe.add_argument("--y2", type=int, required=True)
    swipe.add_argument("--duration", type=int, default=300)

    text = sub.add_parser("text")
    text.add_argument("--text", required=True)

    key = sub.add_parser("key")
    key.add_argument("--keycode", type=int, required=True)

    screen = sub.add_parser("screen")
    screen.add_argument("--action", choices=["on", "off", "toggle", "unlock"], default="toggle")

    shot = sub.add_parser("screenshot")
    shot.add_argument("--out", required=True, help="Output PNG path")

    shell = sub.add_parser("shell")
    shell.add_argument("--cmd", required=True)

    apps = sub.add_parser("apps")
    apps.add_argument("--start")
    apps.add_argument("--stop")

    watch = sub.add_parser("watch-health")
    watch.add_argument("--interval", type=int, default=5)

    job = sub.add_parser("job-submit")
    job.add_argument("--type", required=True, dest="job_type")
    job.add_argument("--payload", default="{}", help="JSON payload for the job")

    poll = sub.add_parser("job-poll")
    poll.add_argument("--job-id", required=True)

    args = parser.parse_args()
    base = args.api_url.rstrip("/")
    headers = build_headers(args.token)

    try:
        if args.command == "health":
            print(json.dumps(request_json("GET", f"{base}/health", headers, timeout=args.timeout), indent=2))
        elif args.command == "status":
            print(json.dumps(request_json("GET", f"{base}/status", headers, timeout=args.timeout), indent=2))
        elif args.command == "tap":
            data = request_json("POST", f"{base}/device/input", headers,
                                {"type": "tap", "x": args.x, "y": args.y}, args.timeout)
            print(json.dumps(data, indent=2))
        elif args.command == "swipe":
            data = request_json("POST", f"{base}/device/input", headers,
                                {"type": "swipe", "x1": args.x1, "y1": args.y1,
                                 "x2": args.x2, "y2": args.y2, "duration": args.duration},
                                args.timeout)
            print(json.dumps(data, indent=2))
        elif args.command == "text":
            data = request_json("POST", f"{base}/device/input", headers,
                                {"type": "text", "text": args.text}, args.timeout)
            print(json.dumps(data, indent=2))
        elif args.command == "key":
            data = request_json("POST", f"{base}/device/input", headers,
                                {"type": "key", "keycode": args.keycode}, args.timeout)
            print(json.dumps(data, indent=2))
        elif args.command == "screen":
            data = request_json("POST", f"{base}/device/screen", headers,
                                {"action": args.action}, args.timeout)
            print(json.dumps(data, indent=2))
        elif args.command == "screenshot":
            resp = requests.get(f"{base}/device/screenshot", headers=headers, timeout=args.timeout)
            resp.raise_for_status()
            out = Path(args.out)
            out.write_bytes(resp.content)
            print(f"Wrote {out}")
        elif args.command == "shell":
            data = request_json("POST", f"{base}/adb/shell", headers,
                                {"command": args.cmd, "timeout": args.timeout}, args.timeout)
            print(json.dumps(data, indent=2))
        elif args.command == "apps":
            if args.start:
                data = request_json("POST", f"{base}/apps/{args.start}/start", headers, timeout=args.timeout)
                print(json.dumps(data, indent=2))
            elif args.stop:
                data = request_json("POST", f"{base}/apps/{args.stop}/stop", headers, timeout=args.timeout)
                print(json.dumps(data, indent=2))
            else:
                print(json.dumps(request_json("GET", f"{base}/apps", headers, timeout=args.timeout), indent=2))
        elif args.command == "watch-health":
            while True:
                data = request_json("GET", f"{base}/health", headers, timeout=args.timeout)
                print(json.dumps(data))
                time.sleep(args.interval)
        elif args.command == "job-submit":
            payload = json.loads(args.payload)
            data = request_json("POST", f"{base}/jobs", headers,
                                {"type": args.job_type, "payload": payload}, args.timeout)
            print(json.dumps(data, indent=2))
        elif args.command == "job-poll":
            data = request_json("GET", f"{base}/jobs/{args.job_id}", headers, timeout=args.timeout)
            print(json.dumps(data, indent=2))
        else:
            parser.print_help()
            return 2
    except requests.RequestException as exc:
        print(f"Request failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
