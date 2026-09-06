#!/usr/bin/env python3
"""R4 live rung — skipped unless CLOUD_PHONE_LIVE=1 and a Control API is reachable."""

import os
import unittest

import requests

LIVE = os.environ.get("CLOUD_PHONE_LIVE", "") in {"1", "true", "yes"}
API = os.environ.get("CLOUD_PHONE_API_URL", "http://127.0.0.1:8080")


@unittest.skipUnless(LIVE, "set CLOUD_PHONE_LIVE=1 to hit a real Control API")
class LiveControlApiTests(unittest.TestCase):
    def test_health_shape(self):
        body = requests.get(f"{API.rstrip('/')}/health", timeout=10).json()
        self.assertIn("status", body)
        self.assertIn("adb_connected", body)
        self.assertIn("gapps", body)

    def test_redroid_gapps_ready_when_required(self):
        if os.environ.get("REQUIRE_GAPPS", "") not in {"1", "true", "yes"}:
            self.skipTest("REQUIRE_GAPPS not set")
        body = requests.get(f"{API.rstrip('/')}/health", timeout=10).json()
        self.assertTrue(body.get("gapps", {}).get("ready"), body)


if __name__ == "__main__":
    unittest.main()
