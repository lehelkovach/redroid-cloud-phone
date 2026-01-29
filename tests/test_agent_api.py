#!/usr/bin/env python3
"""
Cloud Phone Agent API Test Suite (Redroid)

Tests core API endpoints with logging and error reporting.
Designed to run against a live Redroid instance.

Usage:
    python tests/test_agent_api.py --api-url http://localhost:8080
"""

import os
import sys
import json
import time
import base64
import logging
import argparse
import traceback
from typing import Dict, Any, List, Tuple
from dataclasses import dataclass, field
from enum import Enum

import requests


# =============================================================================
# Configuration
# =============================================================================

@dataclass
class TestConfig:
    api_url: str = "http://localhost:8080"
    api_token: str = ""
    timeout: int = 30
    log_file: str = ""
    verbose: bool = False


class TestStatus(Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"


@dataclass
class TestResult:
    name: str
    status: TestStatus
    duration_ms: int
    message: str = ""
    details: Dict = field(default_factory=dict)
    exception: str = ""


# =============================================================================
# Logging Setup
# =============================================================================

def setup_logging(config: TestConfig) -> logging.Logger:
    logger = logging.getLogger("test_agent_api")
    logger.setLevel(logging.DEBUG if config.verbose else logging.INFO)

    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if config.verbose else logging.INFO)
    console.setFormatter(formatter)
    logger.addHandler(console)

    if config.log_file:
        os.makedirs(os.path.dirname(config.log_file), exist_ok=True)
        file_handler = logging.FileHandler(config.log_file)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


# =============================================================================
# Test Client
# =============================================================================

class APIClient:
    def __init__(self, config: TestConfig, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.session = requests.Session()
        if config.api_token:
            self.session.headers["Authorization"] = f"Bearer {config.api_token}"
        self.session.headers["Content-Type"] = "application/json"

    def get(self, path: str, params: Dict = None) -> requests.Response:
        url = f"{self.config.api_url}{path}"
        self.logger.debug(f"GET {url} params={params}")
        return self.session.get(url, params=params, timeout=self.config.timeout)

    def post(self, path: str, data: Dict = None) -> requests.Response:
        url = f"{self.config.api_url}{path}"
        self.logger.debug(f"POST {url} data={data}")
        return self.session.post(url, json=data, timeout=self.config.timeout)

    def check_response(self, resp: requests.Response, expected_success: bool = True) -> Dict:
        if resp.status_code >= 500:
            raise Exception(f"Server error: {resp.status_code} - {resp.text}")
        try:
            data = resp.json()
        except Exception:
            raise Exception(f"Invalid JSON response: {resp.status_code} - {resp.text}")
        if "success" in data and data.get("success") != expected_success:
            raise AssertionError(
                f"Expected success={expected_success}, got {data.get('success')}. "
                f"Error: {data.get('error')}"
            )
        return data


# =============================================================================
# Test Cases
# =============================================================================

class TestSuite:
    def __init__(self, client: APIClient, logger: logging.Logger):
        self.client = client
        self.logger = logger
        self.results: List[TestResult] = []

    def run_test(self, name: str, test_func) -> TestResult:
        self.logger.info(f"Running test: {name}")
        start = time.time()
        try:
            test_func()
            duration = int((time.time() - start) * 1000)
            result = TestResult(name, TestStatus.PASSED, duration)
            self.logger.info(f"  ✓ {name} ({duration}ms)")
        except AssertionError as e:
            duration = int((time.time() - start) * 1000)
            result = TestResult(name, TestStatus.FAILED, duration, str(e))
            self.logger.error(f"  ✗ {name} - FAILED: {e}")
        except Exception as e:
            duration = int((time.time() - start) * 1000)
            result = TestResult(name, TestStatus.ERROR, duration, str(e),
                                exception=traceback.format_exc())
            self.logger.error(f"  ✗ {name} - ERROR: {e}")
        self.results.append(result)
        return result

    def test_health_check(self):
        resp = self.client.get("/health")
        assert resp.status_code == 200
        payload = resp.json()
        assert "adb_connected" in payload

    def test_status(self):
        resp = self.client.get("/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "device" in data

    def test_device_identity(self):
        resp = self.client.get("/device/identity")
        assert resp.status_code == 200
        data = resp.json()
        assert "current" in data

    def test_identity_profiles(self):
        resp = self.client.get("/device/identity/profiles")
        assert resp.status_code == 200
        data = resp.json()
        assert "profiles" in data

    def test_screen_control(self):
        resp = self.client.post("/device/screen", {"action": "toggle"})
        data = self.client.check_response(resp)
        assert data.get("success") is True

    def test_screenshot_base64(self):
        resp = self.client.get("/device/screenshot/base64")
        data = self.client.check_response(resp)
        assert "image_base64" in data
        img = base64.b64decode(data["image_base64"])
        assert img[:8] == b'\x89PNG\r\n\x1a\n'

    def test_screenshot_png(self):
        resp = self.client.get("/device/screenshot")
        assert resp.status_code == 200
        assert resp.headers.get("Content-Type", "").startswith("image/")
        assert len(resp.content) > 1000

    def test_input_tap(self):
        resp = self.client.post("/device/input", {"type": "tap", "x": 540, "y": 1200})
        data = self.client.check_response(resp)
        assert data.get("type") == "tap"

    def test_input_swipe(self):
        resp = self.client.post("/device/input", {
            "type": "swipe",
            "x1": 300, "y1": 800,
            "x2": 300, "y2": 300,
            "duration": 500
        })
        data = self.client.check_response(resp)
        assert data.get("type") == "swipe"

    def test_input_text(self):
        resp = self.client.post("/device/input", {"type": "text", "text": "hello"})
        data = self.client.check_response(resp)
        assert data.get("type") == "text"

    def test_input_key(self):
        resp = self.client.post("/device/input", {"type": "key", "keycode": 3})
        data = self.client.check_response(resp)
        assert data.get("type") == "key"

    def test_list_apps(self):
        resp = self.client.get("/apps")
        data = self.client.check_response(resp)
        assert "packages" in data
        assert isinstance(data["packages"], list)

    def test_start_stop_settings(self):
        resp = self.client.post("/apps/com.android.settings/start")
        data = self.client.check_response(resp)
        assert data.get("success") is True
        time.sleep(1)
        resp = self.client.post("/apps/com.android.settings/stop")
        data = self.client.check_response(resp)
        assert data.get("success") is True

    def test_adb_shell(self):
        resp = self.client.post("/adb/shell", {"command": "echo hello"})
        data = self.client.check_response(resp)
        assert "hello" in data.get("stdout", "")

    def test_adb_getprop(self):
        resp = self.client.post("/adb/shell", {"command": "getprop ro.build.version.release"})
        data = self.client.check_response(resp)
        assert data.get("stdout", "").strip() != ""

    def test_config_get(self):
        resp = self.client.get("/config")
        assert resp.status_code == 200

    def run_all(self) -> Tuple[int, int, int]:
        tests = [
            ("health_check", self.test_health_check),
            ("status", self.test_status),
            ("device_identity", self.test_device_identity),
            ("identity_profiles", self.test_identity_profiles),
            ("screen_control", self.test_screen_control),
            ("screenshot_base64", self.test_screenshot_base64),
            ("screenshot_png", self.test_screenshot_png),
            ("input_tap", self.test_input_tap),
            ("input_swipe", self.test_input_swipe),
            ("input_text", self.test_input_text),
            ("input_key", self.test_input_key),
            ("list_apps", self.test_list_apps),
            ("start_stop_settings", self.test_start_stop_settings),
            ("adb_shell", self.test_adb_shell),
            ("adb_getprop", self.test_adb_getprop),
            ("config_get", self.test_config_get),
        ]

        for name, func in tests:
            self.run_test(name, func)

        passed = sum(1 for r in self.results if r.status == TestStatus.PASSED)
        failed = sum(1 for r in self.results if r.status == TestStatus.FAILED)
        errors = sum(1 for r in self.results if r.status == TestStatus.ERROR)
        return passed, failed, errors

    def generate_report(self) -> str:
        total = len(self.results)
        passed = len([r for r in self.results if r.status == TestStatus.PASSED])
        failed = len([r for r in self.results if r.status == TestStatus.FAILED])
        errors = len([r for r in self.results if r.status == TestStatus.ERROR])
        skipped = len([r for r in self.results if r.status == TestStatus.SKIPPED])

        lines = []
        lines.append("=" * 60)
        lines.append(f"TEST RESULTS: {passed}/{total} passed")
        lines.append("=" * 60)

        if failed > 0 or errors > 0:
            lines.append("")
            lines.append("Failures/Errors:")
            for r in self.results:
                if r.status in [TestStatus.FAILED, TestStatus.ERROR]:
                    lines.append(f"- {r.name}: {r.status.value} - {r.message}")

        lines.append("")
        lines.append(f"Summary: {passed} passed, {failed} failed, {errors} errors, {skipped} skipped")
        return "\n".join(lines)


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Cloud Phone Agent API Test Suite")
    parser.add_argument("--api-url", default="http://localhost:8080", help="API base URL")
    parser.add_argument("--api-token", default="", help="API authentication token")
    parser.add_argument("--timeout", type=int, default=30, help="Request timeout in seconds")
    parser.add_argument("--log-file", default="", help="Log file path")
    parser.add_argument("--output-json", default="", help="Output results as JSON to file")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    config = TestConfig(
        api_url=args.api_url,
        api_token=args.api_token,
        timeout=args.timeout,
        log_file=args.log_file,
        verbose=args.verbose
    )

    logger = setup_logging(config)

    logger.info("=" * 60)
    logger.info("Cloud Phone Agent API Test Suite")
    logger.info("=" * 60)
    logger.info(f"API URL: {config.api_url}")
    logger.info(f"Timeout: {config.timeout}s")
    logger.info("")

    client = APIClient(config, logger)
    suite = TestSuite(client, logger)

    try:
        passed, failed, errors = suite.run_all()
        print("\n")
        print(suite.generate_report())

        if args.output_json:
            with open(args.output_json, "w") as f:
                f.write(json.dumps({"results": [r.__dict__ for r in suite.results]}, indent=2))
            logger.info(f"JSON results saved to: {args.output_json}")

        sys.exit(0 if (failed + errors) == 0 else 1)

    except requests.exceptions.ConnectionError:
        logger.error(f"Cannot connect to API at {config.api_url}")
        logger.error("Make sure the API server is running and accessible")
        sys.exit(2)
    except Exception as e:
        logger.exception(f"Test suite error: {e}")
        sys.exit(3)


if __name__ == "__main__":
    main()
