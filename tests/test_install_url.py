#!/usr/bin/env python3
"""Install an APK the Control API downloads itself (Aptoide / F-Droid / any URL).

Play is not on every image (the GApps bake is an operator step), so OSLO needs
a way to put an app on the phone from the web. Pushing a multi-hundred-MB APK
through the agent box and a multipart upload is the wrong shape; the phone
host has the bandwidth and the adb socket. This route downloads on the host,
refuses anything that is not an APK or an APK bundle, optionally checks a
sha256 the caller names, and installs with `adb install` / `install-multiple`.

The download is patched (`_fetch_to_file`), adb is patched: no network, no device.
"""

import hashlib
import io
import os
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "api"))

import server as control_api  # noqa: E402
import device_control  # noqa: E402

from tests.test_control_api import fake_adb  # noqa: E402


def make_apk_bytes(manifest=True):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        if manifest:
            z.writestr("AndroidManifest.xml", b"\x03\x00\x08\x00fake")
        z.writestr("classes.dex", b"dex\n035")
    return buf.getvalue()


def make_bundle_bytes():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("base.apk", make_apk_bytes())
        z.writestr("split_config.arm64_v8a.apk", make_apk_bytes())
        z.writestr("manifest.json", b"{}")
    return buf.getvalue()


def fake_fetch(payload, record=None):
    def _fetch(url, dest, proxy=None, max_bytes=None, timeout=300):
        if record is not None:
            record.append({"url": url, "proxy": proxy})
        with open(dest, "wb") as f:
            f.write(payload)
        return len(payload)

    return _fetch


class InspectApkTests(unittest.TestCase):
    def _write(self, data):
        fd, path = tempfile.mkstemp(suffix=".bin")
        os.write(fd, data)
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        return path

    def test_single_apk(self):
        info = device_control.inspect_apk_file(self._write(make_apk_bytes()))
        self.assertEqual(info["kind"], "apk")
        self.assertEqual(info["sha256"], hashlib.sha256(make_apk_bytes()).hexdigest())

    def test_bundle(self):
        info = device_control.inspect_apk_file(self._write(make_bundle_bytes()))
        self.assertEqual(info["kind"], "bundle")
        self.assertEqual(sorted(info["apks"]), ["base.apk", "split_config.arm64_v8a.apk"])

    def test_html_error_page_is_invalid(self):
        info = device_control.inspect_apk_file(self._write(b"<!doctype html><html>404</html>"))
        self.assertEqual(info["kind"], "invalid")

    def test_zip_without_manifest_or_apks_is_invalid(self):
        info = device_control.inspect_apk_file(self._write(make_apk_bytes(manifest=False)))
        self.assertEqual(info["kind"], "invalid")


class InstallUrlRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = control_api.app.test_client()
        control_api.API_TOKEN = ""

    def test_downloads_validates_and_installs_single_apk(self):
        calls, fetched = [], []
        apk = make_apk_bytes()
        table = {"pm path com.example.app": (True, "package:/data/app/x/base.apk", "")}
        with patch.object(control_api, "_fetch_to_file", fake_fetch(apk, fetched)), patch.object(
            control_api, "run_adb", fake_adb(table, record=calls)
        ):
            body = self.client.post(
                "/apps/install_url",
                json={"url": "https://example.test/app.apk", "package": "com.example.app"},
            ).get_json()
        self.assertTrue(body["success"], body)
        self.assertEqual(body["kind"], "apk")
        self.assertEqual(body["sha256"], hashlib.sha256(apk).hexdigest())
        self.assertEqual(body["bytes"], len(apk))
        self.assertTrue(body["installed"])
        self.assertEqual(fetched[0]["url"], "https://example.test/app.apk")
        install = [c[1] for c in calls if c[1].startswith("install")]
        self.assertEqual(len(install), 1)
        self.assertTrue(install[0].startswith("install -r "), install[0])

    def test_bundle_uses_install_multiple(self):
        calls = []
        with patch.object(control_api, "_fetch_to_file", fake_fetch(make_bundle_bytes())), patch.object(
            control_api, "run_adb", fake_adb(record=calls)
        ):
            body = self.client.post("/apps/install_url", json={"url": "https://example.test/app.xapk"}).get_json()
        self.assertTrue(body["success"], body)
        self.assertEqual(body["kind"], "bundle")
        multi = [c[1] for c in calls if c[1].startswith("install-multiple")]
        self.assertEqual(len(multi), 1)
        self.assertIn("base.apk", multi[0])
        self.assertIn("split_config.arm64_v8a.apk", multi[0])

    def test_sha256_mismatch_refuses_install(self):
        calls = []
        with patch.object(control_api, "_fetch_to_file", fake_fetch(make_apk_bytes())), patch.object(
            control_api, "run_adb", fake_adb(record=calls)
        ):
            res = self.client.post(
                "/apps/install_url", json={"url": "https://example.test/app.apk", "sha256": "00" * 32}
            )
        self.assertEqual(res.status_code, 422)
        self.assertEqual(res.get_json()["code"], "sha256_mismatch")
        self.assertFalse(any("install" in c[1] for c in calls))

    def test_not_an_apk_refuses_install(self):
        calls = []
        with patch.object(control_api, "_fetch_to_file", fake_fetch(b"<html>login</html>")), patch.object(
            control_api, "run_adb", fake_adb(record=calls)
        ):
            res = self.client.post("/apps/install_url", json={"url": "https://example.test/app.apk"})
        self.assertEqual(res.status_code, 422)
        self.assertEqual(res.get_json()["code"], "not_an_apk")
        self.assertFalse(any("install" in c[1] for c in calls))

    def test_rejects_non_http_url(self):
        with patch.object(control_api, "run_adb", fake_adb()):
            res = self.client.post("/apps/install_url", json={"url": "file:///etc/passwd"})
        self.assertEqual(res.status_code, 400)

    def test_download_failure_is_502(self):
        def failing(url, dest, proxy=None, max_bytes=None, timeout=300):
            raise RuntimeError("connection reset")

        with patch.object(control_api, "_fetch_to_file", failing), patch.object(control_api, "run_adb", fake_adb()):
            res = self.client.post("/apps/install_url", json={"url": "https://example.test/app.apk"})
        self.assertEqual(res.status_code, 502)
        self.assertEqual(res.get_json()["code"], "download_failed")

    def test_proxy_from_body_reaches_the_fetcher(self):
        fetched = []
        with patch.object(control_api, "_fetch_to_file", fake_fetch(make_apk_bytes(), fetched)), patch.object(
            control_api, "run_adb", fake_adb()
        ):
            self.client.post(
                "/apps/install_url",
                json={"url": "https://example.test/app.apk", "proxy": "http://u:p@geo.example:12321"},
            )
        self.assertEqual(fetched[0]["proxy"], "http://u:p@geo.example:12321")

    def test_adb_install_failure_surfaces_adb_message(self):
        table = {"install": (False, "", "Failure [INSTALL_FAILED_NO_MATCHING_ABIS]")}
        with patch.object(control_api, "_fetch_to_file", fake_fetch(make_apk_bytes())), patch.object(
            control_api, "run_adb", fake_adb(table)
        ):
            res = self.client.post("/apps/install_url", json={"url": "https://example.test/app.apk"})
        self.assertEqual(res.status_code, 502)
        self.assertIn("INSTALL_FAILED_NO_MATCHING_ABIS", res.get_json()["error"])


class AppInfoRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = control_api.app.test_client()
        control_api.API_TOKEN = ""

    def test_installed_with_version(self):
        table = {
            "pm path com.airbnb.android": (True, "package:/data/app/~~x/com.airbnb.android-1/base.apk", ""),
            "dumpsys package com.airbnb.android": (True, "    versionCode=12345 minSdk=23\n    versionName=25.40\n", ""),
        }
        with patch.object(control_api, "run_adb", fake_adb(table)):
            body = self.client.get("/apps/com.airbnb.android/info").get_json()
        self.assertTrue(body["installed"])
        self.assertEqual(body["version_name"], "25.40")
        self.assertEqual(body["version_code"], 12345)

    def test_not_installed(self):
        with patch.object(control_api, "run_adb", fake_adb({"pm path": (True, "", "")})):
            body = self.client.get("/apps/com.missing.app/info").get_json()
        self.assertFalse(body["installed"])

    def test_bad_package_name_is_400(self):
        with patch.object(control_api, "run_adb", fake_adb()):
            self.assertEqual(self.client.get("/apps/not%20a%20package/info").status_code, 400)


if __name__ == "__main__":
    unittest.main()
