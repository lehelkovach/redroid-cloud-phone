#!/usr/bin/env python3
"""Offline unit tests for IPRoyal-style proxy URL parsing + /proxy hooks."""

import os
import unittest
from unittest.mock import patch

from api import server as api


class ProxyUrlParseTests(unittest.TestCase):
    def test_parse_http_with_auth(self):
        parsed = api._parse_proxy_url("http://user:pass@geo.iproyal.com:12321")
        self.assertEqual(parsed["type"], "http")
        self.assertEqual(parsed["host"], "geo.iproyal.com")
        self.assertEqual(parsed["port"], 12321)
        self.assertEqual(parsed["username"], "user")
        self.assertEqual(parsed["password"], "pass")

    def test_parse_socks5(self):
        parsed = api._parse_proxy_url("socks5://geo.iproyal.com:12321")
        self.assertEqual(parsed["type"], "socks5")
        self.assertEqual(parsed["host"], "geo.iproyal.com")
        self.assertEqual(parsed["port"], 12321)

    def test_parse_host_port_defaults_http(self):
        parsed = api._parse_proxy_url("geo.iproyal.com:12321")
        self.assertEqual(parsed["type"], "http")
        self.assertEqual(parsed["host"], "geo.iproyal.com")
        self.assertEqual(parsed["port"], 12321)

    def test_parse_invalid(self):
        self.assertIsNone(api._parse_proxy_url(""))
        self.assertIsNone(api._parse_proxy_url("no-port"))


class SetProxyHooksTests(unittest.TestCase):
    def setUp(self):
        api._state["proxy"] = {"enabled": False, "type": None, "host": None, "port": None}

    @patch("api.server.run_adb_shell")
    def test_set_proxy_from_url(self, mock_adb):
        mock_adb.return_value = (True, "", "")
        result, status = api._set_proxy(
            {"enabled": True, "url": "http://u:p@geo.iproyal.com:12321"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(result["success"])
        self.assertEqual(result["proxy"]["host"], "geo.iproyal.com")
        self.assertEqual(result["proxy"]["port"], 12321)
        self.assertEqual(result["proxy"]["type"], "http")
        mock_adb.assert_called()

    @patch("api.server.run_adb_shell")
    def test_set_proxy_from_env(self, mock_adb):
        mock_adb.return_value = (True, "", "")
        prev = os.environ.get("IPROYAL_PROXY")
        os.environ["IPROYAL_PROXY"] = "http://u:p@geo.iproyal.com:12321"
        try:
            result, status = api._set_proxy({"enabled": True})
            self.assertEqual(status, 200)
            self.assertTrue(result["success"])
            self.assertEqual(result["proxy"]["host"], "geo.iproyal.com")
        finally:
            if prev is None:
                os.environ.pop("IPROYAL_PROXY", None)
            else:
                os.environ["IPROYAL_PROXY"] = prev

    def test_set_proxy_missing_host(self):
        prev = os.environ.pop("IPROYAL_PROXY", None)
        prev2 = os.environ.pop("CLOUD_PHONE_PROXY", None)
        try:
            result, status = api._set_proxy({"enabled": True})
            self.assertEqual(status, 400)
            self.assertIn("host and port", result["error"])
        finally:
            if prev is not None:
                os.environ["IPROYAL_PROXY"] = prev
            if prev2 is not None:
                os.environ["CLOUD_PHONE_PROXY"] = prev2


if __name__ == "__main__":
    unittest.main()
