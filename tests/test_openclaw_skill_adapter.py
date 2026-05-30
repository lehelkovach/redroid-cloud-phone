#!/usr/bin/env python3
import json
import unittest

from skills.openclaw.cloud_android_phone import CloudAndroidPhone, ControlApiError


class FakeTransport:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({
            "method": method,
            "url": url,
            "headers": headers,
            "body": body,
            "timeout": timeout,
        })
        if self.responses:
            return self.responses.pop(0)
        return 200, "application/json", b'{"success": true}'


class CloudAndroidPhoneAdapterTests(unittest.TestCase):
    def test_adds_bearer_token_and_base_url(self):
        transport = FakeTransport()
        phone = CloudAndroidPhone("http://phone:8080/", token="secret", transport=transport)

        self.assertEqual(phone.health(), {"success": True})
        call = transport.calls[0]
        self.assertEqual(call["method"], "GET")
        self.assertEqual(call["url"], "http://phone:8080/health")
        self.assertEqual(call["headers"]["Authorization"], "Bearer secret")

    def test_tap_posts_device_input_payload(self):
        transport = FakeTransport()
        phone = CloudAndroidPhone("http://phone:8080", transport=transport)

        phone.tap(10, 20)
        call = transport.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["url"], "http://phone:8080/device/input")
        self.assertEqual(json.loads(call["body"].decode("utf-8")), {
            "type": "tap",
            "x": 10,
            "y": 20,
        })

    def test_screenshot_returns_json_response(self):
        transport = FakeTransport([
            (200, "application/json", b'{"success": true, "image": "abc"}')
        ])
        phone = CloudAndroidPhone("http://phone:8080", transport=transport)

        result = phone.screenshot()
        self.assertEqual(result["image"], "abc")
        self.assertEqual(transport.calls[0]["url"], "http://phone:8080/device/screenshot/base64")

    def test_http_error_raises_control_api_error(self):
        transport = FakeTransport([
            (500, "application/json", b'{"error": "failed"}')
        ])
        phone = CloudAndroidPhone("http://phone:8080", transport=transport)

        with self.assertRaises(ControlApiError) as ctx:
            phone.status()
        self.assertEqual(ctx.exception.status, 500)
        self.assertIn("failed", ctx.exception.message)


if __name__ == "__main__":
    unittest.main()

