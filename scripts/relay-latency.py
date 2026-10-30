#!/usr/bin/env python3
"""What the orchestrator relay costs: CDP round trips and phone frames, direct vs relayed.

    python scripts/relay-latency.py                       # fake DevTools + fake phone
    python scripts/relay-latency.py --chrome-bin PATH     # also a real Chromium (local runtime)
    python scripts/relay-latency.py --json

Everything runs on loopback in mock mode: the numbers are the relay's own
overhead (ticket check, rate limit, audit, an extra WebSocket hop), not
network distance. Fake tokens only.
"""

import argparse
import json
import os
import statistics
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("LOG_LEVEL", "WARNING")

import logging  # noqa: E402

import requests  # noqa: E402
import simple_websocket  # noqa: E402

logging.getLogger("werkzeug").setLevel(logging.ERROR)

from orchestrator import browser_pool as bp  # noqa: E402
from orchestrator import server as orch  # noqa: E402
from tests.fixtures.isolation_harness import OWNER_A, Harness, cdp_call  # noqa: E402


def stats(samples_ms):
    s = sorted(samples_ms)
    return {
        "n": len(s),
        "p50_ms": round(statistics.median(s), 3),
        "p95_ms": round(s[int(0.95 * (len(s) - 1))], 3),
        "mean_ms": round(statistics.fmean(s), 3),
    }


def time_cdp(ws, n, method="Browser.getVersion", start_id=1):
    out = []
    for i in range(n):
        t0 = time.perf_counter()
        cdp_call(ws, start_id + i, method)
        out.append((time.perf_counter() - t0) * 1000.0)
    return out


def time_http(url, headers, n):
    sess = requests.Session()
    out = []
    for _ in range(n):
        t0 = time.perf_counter()
        r = sess.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        out.append((time.perf_counter() - t0) * 1000.0)
    return out


def bench_fake(n):
    h = Harness(rate=1e9, burst=1e9)
    try:
        a = h.browser(OWNER_A).json()
        direct = simple_websocket.Client.connect(f"{h.cdp.ws_url}/devtools/browser/{a['session']['owner_token']}")
        relayed = h.cdp_connect(a["relay"]["cdp_ws_url"], a["ticket"])
        time_cdp(direct, 20, start_id=10_000)
        time_cdp(relayed, 20, start_id=10_000)
        res = {"cdp_fake_direct": stats(time_cdp(direct, n)), "cdp_fake_relayed": stats(time_cdp(relayed, n))}
        direct.close()
        relayed.close()

        p = h.phone_lease(OWNER_A).json()
        adb = {"X-Cloud-Phone-Adb": p["session"]["adb_connect"]}
        res["frame_http_direct"] = stats(time_http(f"{h.phone.url}/device/screenshot/base64", adb, n))
        res["frame_http_relayed"] = stats(time_http(f"{h.base}/relay/phone/device/screenshot/base64",
                                                    {"X-Orch-Ticket": p["ticket"]}, n))
        # Frames pushed over the stream: inter-arrival at the configured fps
        # says nothing about overhead, so time one grab+send instead.
        orch.ORCH_PHONE_FPS = 50
        ws = simple_websocket.Client.connect(p["relay"]["phone_stream_url"], headers={"X-Orch-Ticket": p["ticket"]})
        arrivals = []
        last = time.perf_counter()
        for _ in range(min(n, 100)):
            ws.receive(timeout=5)
            now = time.perf_counter()
            arrivals.append((now - last) * 1000.0)
            last = now
        ws.close()
        res["frame_stream_interarrival_at_50fps"] = stats(arrivals[1:])
        return res
    finally:
        h.close()


def bench_chrome(chrome_bin, n):
    h = Harness(rate=1e9, burst=1e9)
    try:
        orch.set_chrome_runtime(bp.LocalChromeRuntime(chrome_bin))
        a = h.browser(OWNER_A).json()
        sess = orch._browser_sessions[OWNER_A]
        direct = simple_websocket.Client.connect(sess["cdp_ws_internal"])
        relayed = h.cdp_connect(a["relay"]["cdp_ws_url"], a["ticket"])
        time_cdp(direct, 20, start_id=10_000)
        time_cdp(relayed, 20, start_id=10_000)
        res = {"cdp_chrome_direct": stats(time_cdp(direct, n)), "cdp_chrome_relayed": stats(time_cdp(relayed, n))}
        # A screenshot is the Watch frame's real cost: capture through the relay.
        target = cdp_call(direct, 50_000, "Target.createTarget", {"url": "about:blank"})["result"]["targetId"]
        att = cdp_call(relayed, 50_001, "Target.attachToTarget", {"targetId": target, "flatten": True})
        sid = att["result"]["sessionId"]

        def shot(ws, i):
            t0 = time.perf_counter()
            ws.send(json.dumps({"id": i, "sessionId": sid, "method": "Page.captureScreenshot",
                                "params": {"format": "jpeg", "quality": 60}}))
            while True:
                obj = json.loads(ws.receive(timeout=10))
                if obj.get("id") == i:
                    return (time.perf_counter() - t0) * 1000.0

        att_d = cdp_call(direct, 50_002, "Target.attachToTarget", {"targetId": target, "flatten": True})
        sid_d = att_d["result"]["sessionId"]

        def shot_d(i):
            t0 = time.perf_counter()
            direct.send(json.dumps({"id": i, "sessionId": sid_d, "method": "Page.captureScreenshot",
                                    "params": {"format": "jpeg", "quality": 60}}))
            while True:
                obj = json.loads(direct.receive(timeout=10))
                if obj.get("id") == i:
                    return (time.perf_counter() - t0) * 1000.0

        k = max(10, n // 10)
        res["frame_chrome_screenshot_direct"] = stats([shot_d(60_000 + i) for i in range(k)])
        res["frame_chrome_screenshot_relayed"] = stats([shot(relayed, 70_000 + i) for i in range(k)])
        direct.close()
        relayed.close()
        requests.delete(f"{h.base}/sessions/{OWNER_A}?purpose=browser", headers=h.headers(), timeout=10)
        return res
    finally:
        h.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--chrome-bin", default=os.environ.get("ORCH_CHROME_BIN", ""))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    out = bench_fake(args.n)
    if args.chrome_bin:
        out.update(bench_chrome(args.chrome_bin, args.n))
    for direct, relayed in (("cdp_fake_direct", "cdp_fake_relayed"), ("frame_http_direct", "frame_http_relayed"),
                            ("cdp_chrome_direct", "cdp_chrome_relayed"),
                            ("frame_chrome_screenshot_direct", "frame_chrome_screenshot_relayed")):
        if direct in out and relayed in out:
            out[f"added_{relayed}_p50_ms"] = round(out[relayed]["p50_ms"] - out[direct]["p50_ms"], 3)
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        for k, v in out.items():
            print(f"{k:45s} {v}")


if __name__ == "__main__":
    tempfile.tempdir = tempfile.gettempdir()
    main()
