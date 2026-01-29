#!/usr/bin/env python3
"""
Spawn Cursor Cloud Agents via API.

This script is intentionally endpoint-configurable because Cloud Agents
endpoints may vary by account/plan.
"""

import argparse
import base64
import json
import os
import sys

import requests


def basic_auth_header(api_key: str) -> str:
    token = base64.b64encode(f"{api_key}:".encode()).decode()
    return f"Basic {token}"


def main():
    parser = argparse.ArgumentParser(description="Spawn Cursor Cloud Agents")
    parser.add_argument("--endpoint", required=True, help="Full Cloud Agents create endpoint URL")
    parser.add_argument("--count", type=int, default=2, help="Number of agents to create")
    parser.add_argument("--payload", default="{}", help="JSON payload for agent creation")
    args = parser.parse_args()

    api_key = os.environ.get("CURSOR_API_KEY")
    if not api_key:
        print("CURSOR_API_KEY env var is required", file=sys.stderr)
        return 1

    headers = {
        "Authorization": basic_auth_header(api_key),
        "Content-Type": "application/json"
    }

    try:
        payload = json.loads(args.payload)
    except Exception as exc:
        print(f"Invalid JSON payload: {exc}", file=sys.stderr)
        return 1

    created = []
    for i in range(args.count):
        resp = requests.post(args.endpoint, headers=headers, json=payload, timeout=30)
        if resp.status_code >= 300:
            print(f"Failed to create agent {i+1}: {resp.status_code} {resp.text}", file=sys.stderr)
            return 1
        created.append(resp.json())

    print(json.dumps({"created": created}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
