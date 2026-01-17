#!/usr/bin/env python3
"""
Cloud Phone Tool CLI

Thin wrapper around the Agent API for tool-based integrations.
Accepts JSON input (stdin or --input-json) and outputs JSON.
"""

import argparse
import json
import os
import sys
from typing import Any, Dict, Tuple
from urllib import request, error, parse

DEFAULT_BASE_URL = os.environ.get("CLOUD_PHONE_API_URL", "http://127.0.0.1:8081")
DEFAULT_TIMEOUT = int(os.environ.get("CLOUD_PHONE_API_TIMEOUT", "30"))


def load_payload(args: argparse.Namespace) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}

    if args.input_json:
        payload = json.loads(args.input_json)
    elif not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
        if raw:
            payload = json.loads(raw)

    if args.base_url:
        payload["base_url"] = args.base_url
    if args.path:
        payload["path"] = args.path
    if args.method:
        payload["method"] = args.method
    if args.data:
        payload["data"] = json.loads(args.data)
    if args.token:
        payload["token"] = args.token
    if args.timeout:
        payload["timeout"] = args.timeout

    return payload


def decode_response(body: str) -> Any:
    if not body:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return body


def build_request(payload: Dict[str, Any]) -> Tuple[request.Request, str]:
    base_url = payload.get("base_url", DEFAULT_BASE_URL).rstrip("/")
    path = payload.get("path", "/")
    method = payload.get("method", "GET").upper()
    data = payload.get("data")
    token = payload.get("token")
    timeout = int(payload.get("timeout", DEFAULT_TIMEOUT))

    url = f"{base_url}{path}"
    body = None
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    if method == "GET" and isinstance(data, dict) and data:
        query = parse.urlencode(data, doseq=True)
        url = f"{url}?{query}"
    elif data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = request.Request(url, data=body, headers=headers, method=method)
    req.timeout = timeout  # type: ignore[attr-defined]
    return req, url


def main() -> int:
    parser = argparse.ArgumentParser(description="Cloud Phone Agent API tool wrapper")
    parser.add_argument("--base-url", help="Agent API base URL")
    parser.add_argument("--path", help="API path, e.g., /screen/info")
    parser.add_argument("--method", help="HTTP method, e.g., GET, POST")
    parser.add_argument("--data", help="JSON string for request body/query")
    parser.add_argument("--token", help="Bearer token for API auth")
    parser.add_argument("--timeout", type=int, help="Request timeout in seconds")
    parser.add_argument("--input-json", help="Full JSON input payload")
    args = parser.parse_args()

    try:
        payload = load_payload(args)
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"Invalid JSON input: {exc}"}))
        return 2

    if not payload.get("path") and not args.path:
        print(json.dumps({"ok": False, "error": "Missing required field: path"}))
        return 2

    req, url = build_request(payload)
    method = payload.get("method", "GET").upper()

    try:
        with request.urlopen(req, timeout=payload.get("timeout", DEFAULT_TIMEOUT)) as resp:
            body = resp.read().decode("utf-8")
            status = resp.status
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8") if exc.fp else ""
        status = exc.code
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1

    response = decode_response(body)
    ok = 200 <= status < 300
    output = {
        "ok": ok,
        "status_code": status,
        "response": response,
        "request": {"method": method, "url": url},
    }
    print(json.dumps(output))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
