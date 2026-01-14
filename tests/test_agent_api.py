#!/usr/bin/env python3
"""
Comprehensive Test Suite for Cloud Phone Agent API

Tests all API endpoints with detailed logging and error reporting.
Designed to run against a live Redroid instance.

Usage:
    python test_agent_api.py                    # Run all tests
    python test_agent_api.py --api-url http://localhost:8080
    python test_agent_api.py --log-file /var/log/test-results.log
    python test_agent_api.py --verbose
"""

import os
import sys
import json
import time
import base64
import logging
import argparse
import traceback
from datetime import datetime
from typing import Dict, Any, List, Tuple, Optional
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
    
    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if config.verbose else logging.INFO)
    console.setFormatter(formatter)
    logger.addHandler(console)
    
    # File handler
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
        """Validate response and return data."""
        if resp.status_code >= 500:
            raise Exception(f"Server error: {resp.status_code} - {resp.text}")
        
        data = resp.json()
        if data.get("success") != expected_success:
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
        self.screen_width = 0
        self.screen_height = 0
    
    def run_test(self, name: str, test_func) -> TestResult:
        """Run a single test with timing and error handling."""
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
    
    # =========================================================================
    # Health Tests
    # =========================================================================
    
    def test_health_check(self):
        """Test health endpoint."""
        resp = self.client.get("/health")
        data = self.client.check_response(resp)
        assert data["data"]["adb_connected"], "ADB not connected"
    
    def test_api_index(self):
        """Test API index/documentation endpoint."""
        resp = self.client.get("/")
        assert resp.status_code == 200
        data = resp.json()
        assert "endpoints" in data
        assert len(data["endpoints"]) > 0
    
    # =========================================================================
    # Screen Tests
    # =========================================================================
    
    def test_screen_info(self):
        """Test screen info endpoint."""
        resp = self.client.get("/screen/info")
        data = self.client.check_response(resp)
        
        assert data["data"]["width"] > 0, "Screen width should be positive"
        assert data["data"]["height"] > 0, "Screen height should be positive"
        
        # Save for other tests
        self.screen_width = data["data"]["width"]
        self.screen_height = data["data"]["height"]
        
        self.logger.debug(f"Screen size: {self.screen_width}x{self.screen_height}")
    
    def test_screenshot(self):
        """Test screenshot capture."""
        resp = self.client.get("/screen/screenshot?format=base64")
        data = self.client.check_response(resp)
        
        assert "image" in data["data"], "Screenshot should return image"
        assert len(data["data"]["image"]) > 1000, "Screenshot should have content"
        
        # Verify it's valid base64 PNG
        img_data = base64.b64decode(data["data"]["image"])
        assert img_data[:8] == b'\x89PNG\r\n\x1a\n', "Should be valid PNG"
    
    def test_screenshot_png(self):
        """Test screenshot as PNG binary."""
        resp = self.client.get("/screen/screenshot?format=png")
        assert resp.status_code == 200
        assert resp.headers.get("Content-Type", "").startswith("image/")
        assert len(resp.content) > 1000
    
    # =========================================================================
    # Input Tests
    # =========================================================================
    
    def test_tap_pixels(self):
        """Test tap at pixel coordinates."""
        # Tap center of screen
        x = self.screen_width // 2 if self.screen_width else 540
        y = self.screen_height // 2 if self.screen_height else 1200
        
        resp = self.client.post("/input/tap", {"x": x, "y": y})
        data = self.client.check_response(resp)
        
        assert data["data"]["action"] == "tap"
        assert data["data"]["x"] == x
        assert data["data"]["y"] == y
    
    def test_tap_percentage(self):
        """Test tap using percentage coordinates."""
        resp = self.client.post("/input/tap", {
            "x": 50,  # 50% from left
            "y": 50,  # 50% from top
            "percentage": True
        })
        data = self.client.check_response(resp)
        
        assert data["data"]["action"] == "tap"
        # Check converted to reasonable pixels
        assert data["data"]["x"] > 100
        assert data["data"]["y"] > 100
    
    def test_swipe(self):
        """Test swipe gesture."""
        resp = self.client.post("/input/swipe", {
            "x1": 50, "y1": 75,
            "x2": 50, "y2": 25,
            "percentage": True,
            "duration": 300
        })
        data = self.client.check_response(resp)
        
        assert data["data"]["action"] == "swipe"
        assert data["data"]["duration"] == 300
    
    def test_long_press(self):
        """Test long press."""
        resp = self.client.post("/input/long_press", {
            "x": 50, "y": 50,
            "percentage": True,
            "duration": 500
        })
        data = self.client.check_response(resp)
        
        assert data["data"]["action"] == "long_press"
        assert data["data"]["duration"] == 500
    
    def test_text_input(self):
        """Test text input."""
        resp = self.client.post("/input/text", {"text": "hello"})
        data = self.client.check_response(resp)
        
        assert data["data"]["action"] == "text"
        assert data["data"]["length"] == 5
    
    def test_key_input(self):
        """Test key input."""
        resp = self.client.post("/input/key", {"key": "KEYCODE_HOME"})
        data = self.client.check_response(resp)
        
        assert data["data"]["action"] == "key"
        assert data["data"]["key"] == "KEYCODE_HOME"
    
    def test_back_button(self):
        """Test back button shortcut."""
        resp = self.client.post("/input/back")
        data = self.client.check_response(resp)
        assert data["data"]["action"] == "back"
    
    def test_home_button(self):
        """Test home button shortcut."""
        resp = self.client.post("/input/home")
        data = self.client.check_response(resp)
        assert data["data"]["action"] == "home"
    
    # =========================================================================
    # App Tests
    # =========================================================================
    
    def test_list_apps(self):
        """Test listing installed apps."""
        resp = self.client.get("/apps")
        data = self.client.check_response(resp)
        
        assert "packages" in data["data"]
        assert data["data"]["count"] >= 0
    
    def test_list_system_apps(self):
        """Test listing system apps."""
        resp = self.client.get("/apps?type=system")
        data = self.client.check_response(resp)
        
        assert data["data"]["count"] > 0, "Should have system apps"
    
    def test_current_app(self):
        """Test getting current app."""
        resp = self.client.get("/apps/current")
        data = self.client.check_response(resp)
        
        # May be null if no activity focused
        assert "package" in data["data"]
    
    def test_launch_settings(self):
        """Test launching Settings app."""
        resp = self.client.post("/apps/com.android.settings/launch")
        data = self.client.check_response(resp)
        
        assert data["data"]["launched"]
        
        # Wait and verify
        time.sleep(2)
        resp = self.client.get("/apps/current")
        current = resp.json()
        assert "settings" in str(current.get("data", {})).lower()
    
    def test_close_app(self):
        """Test closing an app."""
        resp = self.client.post("/apps/com.android.settings/close")
        data = self.client.check_response(resp)
        assert data["data"]["closed"]
    
    # =========================================================================
    # Device Tests
    # =========================================================================
    
    def test_device_info(self):
        """Test device info endpoint."""
        resp = self.client.get("/device/info")
        data = self.client.check_response(resp)
        
        assert "model" in data["data"]
        assert "android_version" in data["data"]
        assert "screen" in data["data"]
    
    def test_device_status(self):
        """Test device status endpoint."""
        resp = self.client.get("/device/status")
        data = self.client.check_response(resp)
        
        assert "battery" in data["data"]
        assert "adb_connected" in data["data"]
    
    # =========================================================================
    # File Tests
    # =========================================================================
    
    def test_list_files(self):
        """Test listing files."""
        resp = self.client.get("/files/list?path=/sdcard")
        data = self.client.check_response(resp)
        
        assert "files" in data["data"]
        assert data["data"]["path"] == "/sdcard"
    
    def test_write_and_read_file(self):
        """Test writing and reading a file."""
        test_content = f"Test content {datetime.now().isoformat()}"
        test_path = "/sdcard/cloud_phone_test.txt"
        
        # Write
        resp = self.client.post("/files/write", {
            "path": test_path,
            "content": test_content
        })
        data = self.client.check_response(resp)
        assert data["data"]["written"]
        
        # Read back
        resp = self.client.get(f"/files/read?path={test_path}")
        data = self.client.check_response(resp)
        assert data["data"]["content"] == test_content
    
    # =========================================================================
    # Shell Tests
    # =========================================================================
    
    def test_shell_command(self):
        """Test shell command execution."""
        resp = self.client.post("/shell", {"command": "echo hello"})
        data = self.client.check_response(resp)
        
        assert "hello" in data["data"]["stdout"]
        assert data["data"]["exit_code"] == 0
    
    def test_shell_getprop(self):
        """Test getprop via shell."""
        resp = self.client.post("/shell", {"command": "getprop ro.build.version.release"})
        data = self.client.check_response(resp)
        
        # Should return Android version like "11" or "12"
        assert data["data"]["stdout"].strip().isdigit() or "." in data["data"]["stdout"]
    
    # =========================================================================
    # Wait Tests
    # =========================================================================
    
    def test_wait_idle(self):
        """Test wait for idle."""
        resp = self.client.post("/wait/idle", {"timeout": 5})
        data = self.client.check_response(resp)
        
        assert data["data"]["idle"]
    
    # =========================================================================
    # Integration Tests
    # =========================================================================
    
    def test_full_interaction_flow(self):
        """Test a complete interaction flow."""
        # 1. Go home
        self.client.post("/input/home")
        time.sleep(1)
        
        # 2. Take screenshot
        resp = self.client.get("/screen/screenshot?format=base64")
        assert resp.status_code == 200
        
        # 3. Launch settings
        resp = self.client.post("/apps/com.android.settings/launch")
        time.sleep(2)
        
        # 4. Take another screenshot
        resp = self.client.get("/screen/screenshot?format=base64")
        assert resp.status_code == 200
        
        # 5. Go back
        self.client.post("/input/back")
        time.sleep(1)
        
        # 6. Go home
        self.client.post("/input/home")
    
    # =========================================================================
    # Run All Tests
    # =========================================================================
    
    def run_all(self) -> Tuple[int, int, int]:
        """Run all tests and return (passed, failed, errors)."""
        tests = [
            # Health
            ("health_check", self.test_health_check),
            ("api_index", self.test_api_index),
            
            # Screen
            ("screen_info", self.test_screen_info),
            ("screenshot_base64", self.test_screenshot),
            ("screenshot_png", self.test_screenshot_png),
            
            # Input
            ("tap_pixels", self.test_tap_pixels),
            ("tap_percentage", self.test_tap_percentage),
            ("swipe", self.test_swipe),
            ("long_press", self.test_long_press),
            ("text_input", self.test_text_input),
            ("key_input", self.test_key_input),
            ("back_button", self.test_back_button),
            ("home_button", self.test_home_button),
            
            # Apps
            ("list_apps", self.test_list_apps),
            ("list_system_apps", self.test_list_system_apps),
            ("current_app", self.test_current_app),
            ("launch_settings", self.test_launch_settings),
            ("close_app", self.test_close_app),
            
            # Device
            ("device_info", self.test_device_info),
            ("device_status", self.test_device_status),
            
            # Files
            ("list_files", self.test_list_files),
            ("write_read_file", self.test_write_and_read_file),
            
            # Shell
            ("shell_command", self.test_shell_command),
            ("shell_getprop", self.test_shell_getprop),
            
            # Wait
            ("wait_idle", self.test_wait_idle),
            
            # Integration
            ("full_interaction_flow", self.test_full_interaction_flow),
        ]
        
        for name, test_func in tests:
            self.run_test(name, test_func)
            time.sleep(0.5)  # Small delay between tests
        
        passed = len([r for r in self.results if r.status == TestStatus.PASSED])
        failed = len([r for r in self.results if r.status == TestStatus.FAILED])
        errors = len([r for r in self.results if r.status == TestStatus.ERROR])
        
        return passed, failed, errors
    
    def generate_report(self) -> str:
        """Generate test report."""
        total = len(self.results)
        passed = len([r for r in self.results if r.status == TestStatus.PASSED])
        failed = len([r for r in self.results if r.status == TestStatus.FAILED])
        errors = len([r for r in self.results if r.status == TestStatus.ERROR])
        
        report = []
        report.append("=" * 60)
        report.append("CLOUD PHONE AGENT API TEST REPORT")
        report.append("=" * 60)
        report.append(f"Date: {datetime.now().isoformat()}")
        report.append(f"Total: {total} | Passed: {passed} | Failed: {failed} | Errors: {errors}")
        report.append("=" * 60)
        report.append("")
        
        for result in self.results:
            status_icon = {
                TestStatus.PASSED: "✓",
                TestStatus.FAILED: "✗",
                TestStatus.ERROR: "!",
                TestStatus.SKIPPED: "-"
            }.get(result.status, "?")
            
            report.append(f"{status_icon} {result.name} ({result.duration_ms}ms) - {result.status.value}")
            if result.message:
                report.append(f"  Message: {result.message}")
            if result.exception:
                report.append(f"  Exception:\n{result.exception}")
        
        report.append("")
        report.append("=" * 60)
        
        if failed + errors == 0:
            report.append("ALL TESTS PASSED ✓")
        else:
            report.append(f"TESTS FAILED: {failed + errors}")
        
        report.append("=" * 60)
        
        return "\n".join(report)
    
    def export_json(self) -> str:
        """Export results as JSON."""
        return json.dumps({
            "timestamp": datetime.now().isoformat(),
            "summary": {
                "total": len(self.results),
                "passed": len([r for r in self.results if r.status == TestStatus.PASSED]),
                "failed": len([r for r in self.results if r.status == TestStatus.FAILED]),
                "errors": len([r for r in self.results if r.status == TestStatus.ERROR])
            },
            "results": [
                {
                    "name": r.name,
                    "status": r.status.value,
                    "duration_ms": r.duration_ms,
                    "message": r.message,
                    "exception": r.exception
                }
                for r in self.results
            ]
        }, indent=2)

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
        
        # Print report
        print("\n")
        print(suite.generate_report())
        
        # Save JSON if requested
        if args.output_json:
            with open(args.output_json, "w") as f:
                f.write(suite.export_json())
            logger.info(f"JSON results saved to: {args.output_json}")
        
        # Exit code based on results
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
