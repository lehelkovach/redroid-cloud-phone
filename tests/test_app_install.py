#!/usr/bin/env python3
"""App install on the Control API: by path, by URL + sha256, and from Play.

No device, no network, no `adb`: `run_adb` is replaced by a fake phone and
downloads by an in-memory opener. What these pin down:

- a URL is never installed without a matching SHA-256, and a mismatch
  installs nothing and leaves no file behind;
- only https, no private/link-local hosts, no paths outside APP_INSTALL_DIRS;
- split sets and .apks/.xapk bundles go through `adb install-multiple`;
- the Play path refuses until GApps are present and a Google account is
  signed in, never buys, and never reveals the account name;
- URL query strings (where signed URLs keep their tokens) never reach the
  logs or the response.
"""

import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "api"))

import server as control_api  # noqa: E402
import app_install  # noqa: E402
from cloudphone_logging import clear_logs, recent_logs  # noqa: E402

SECRET = "sig-SECRET-do-not-log-0123"


def apk_bytes(package="com.example.app"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("AndroidManifest.xml", f"binary manifest for {package}")
        zf.writestr("classes.dex", "dex")
    return buf.getvalue()


def bundle_bytes(extra=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("base.apk", apk_bytes())
        zf.writestr("split_config.arm64_v8a.apk", apk_bytes())
        zf.writestr("manifest.json", "{}")
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return buf.getvalue()


def sha(data):
    return hashlib.sha256(data).hexdigest()


class FakeResponse(io.BytesIO):
    def __init__(self, data, url):
        super().__init__(data)
        self._url = url

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def fake_opener(data, final_url=None):
    def opener(url, timeout=None):
        return FakeResponse(data, final_url or url)
    return opener


def public_resolver(host, port):
    return [(2, 1, 6, "", ("93.184.216.34", port))]


class FakePhone:
    """Just enough of `adb` for install, pm and the Play Store page."""

    def __init__(self, packages=(), accounts="", gapps=True, install_output="Success",
                 play_screens=None, installs_after_tap=True, gsf_id="3987654321012345678"):
        self.packages = set(packages)
        if gapps:
            self.packages |= {"com.google.android.gms", "com.android.vending", "com.google.android.gsf"}
        self.accounts = accounts
        self.install_output = install_output
        self.play_screens = list(play_screens or [])
        self.installs_after_tap = installs_after_tap
        self.gsf_id = gsf_id
        self.calls = []
        self.pending_play_install = None

    def __call__(self, *args, timeout=30):
        joined = " ".join(str(a) for a in args)
        self.calls.append(joined)
        if args and args[0] == "devices":
            return True, f"List of devices attached\n{control_api.current_adb_connect()}\tdevice", ""
        if args and args[0] in ("install", "install-multiple"):
            if self.install_output == "Success":
                self.packages.add("com.example.app")
                return True, "Performing Streamed Install\nSuccess", ""
            return True, self.install_output, ""
        if args and args[0] == "shell":
            return self.shell(args[1])
        return True, "", ""

    def shell(self, cmd):
        if cmd.startswith("pm list packages"):
            return True, "\n".join(f"package:{p}" for p in sorted(self.packages)), ""
        if cmd.startswith("pm path "):
            pkg = cmd.split()[-1]
            if pkg == self.pending_play_install and self.installs_after_tap:
                self.packages.add(pkg)
            if pkg in self.packages:
                return True, f"package:/data/app/{pkg}/base.apk", ""
            return False, "", ""
        if cmd.startswith("dumpsys account"):
            return True, self.accounts, ""
        if cmd.startswith("dumpsys package "):
            return True, "    versionCode=4210 minSdk=24 targetSdk=34\n    versionName=4.2.1\n", ""
        if "sqlite3" in cmd:
            return (True, self.gsf_id, "") if self.gsf_id else (False, "", "sqlite3: not found")
        if cmd.startswith("am start"):
            return True, "Starting: Intent { act=android.intent.action.VIEW }", ""
        if cmd.startswith("uiautomator dump"):
            screen = self.play_screens.pop(0) if self.play_screens else []
            return True, ui_xml(screen), ""
        if cmd.startswith("input tap"):
            self.pending_play_install = self._play_target
            return True, "", ""
        return True, "", ""

    _play_target = "com.example.app"


def ui_xml(labels):
    nodes = "".join(
        f'<node text="{label}" resource-id="" clickable="true" bounds="[100,{200 + i * 100}][300,{260 + i * 100}]" />'
        for i, label in enumerate(labels)
    )
    return f'<?xml version="1.0" encoding="UTF-8"?><hierarchy rotation="0">{nodes}</hierarchy>'


SIGNED_IN = "Accounts: 1\n  Account {name=owner@example.com, type=com.google}\n"


class ParseRequestTests(unittest.TestCase):
    def parse(self, body, env=None):
        return app_install.parse_install_request(body, env or {})

    def assertRefused(self, body, code):
        with self.assertRaises(app_install.InstallError) as ctx:
            self.parse(body)
        self.assertEqual(ctx.exception.code, code)
        return ctx.exception

    def test_exactly_one_source(self):
        self.assertRefused({}, "invalid_body")
        self.assertRefused({"path": "/a.apk", "url": "https://x/a.apk", "sha256": "0" * 64}, "invalid_body")
        self.assertRefused("not an object", "invalid_body")

    def test_url_needs_a_checksum(self):
        err = self.assertRefused({"url": f"https://cdn.example.com/a.apk?token={SECRET}"}, "checksum_required")
        self.assertNotIn(SECRET, json.dumps(err.as_dict()))

    def test_every_split_url_needs_a_checksum(self):
        self.assertRefused({"urls": [{"url": "https://x/a.apk", "sha256": "a" * 64},
                                     {"url": "https://x/b.apk"}]}, "checksum_required")

    def test_checksum_must_be_hex_sha256(self):
        self.assertRefused({"url": "https://x/a.apk", "sha256": "abc"}, "invalid_checksum")
        self.assertRefused({"path": "/a.apk", "sha256": "z" * 64}, "invalid_checksum")

    def test_checksum_is_normalised_to_lower_case(self):
        req = self.parse({"url": "https://x/a.apk", "sha256": "AB" * 32})
        self.assertEqual(req["sources"][0]["sha256"], "ab" * 32)

    def test_options_are_booleans_not_raw_adb_flags(self):
        self.assertRefused({"path": "/a.apk", "replace": "-r; reboot"}, "invalid_body")
        req = self.parse({"path": "/a.apk", "grant_permissions": True})
        self.assertEqual(req["options"], {"replace": True, "grant_permissions": True, "downgrade": False})

    def test_expected_package_is_validated(self):
        self.assertRefused({"path": "/a.apk", "expected_package": "com.x; rm -rf /"}, "invalid_package")

    def test_file_count_is_capped(self):
        with self.assertRaises(app_install.InstallError) as ctx:
            self.parse({"paths": [f"/p{i}.apk" for i in range(5)]}, {"APP_INSTALL_MAX_FILES": "4"})
        self.assertEqual(ctx.exception.code, "too_many_files")


class HelperTests(unittest.TestCase):
    def test_safe_url_drops_query_fragment_and_userinfo(self):
        url = f"https://user:{SECRET}@cdn.example.com:8443/path/app.apk?X-Amz-Signature={SECRET}#frag"
        safe = app_install.safe_url_for_log(url)
        self.assertEqual(safe, "https://cdn.example.com:8443/path/app.apk")

    def test_adb_args_single_and_split(self):
        self.assertEqual(app_install.adb_install_args(["a.apk"], {"replace": True}), ["install", "-r", "a.apk"])
        self.assertEqual(
            app_install.adb_install_args(["b.apk", "s.apk"], {"replace": False, "grant_permissions": True, "downgrade": True}),
            ["install-multiple", "-g", "-d", "b.apk", "s.apk"],
        )

    def test_package_names(self):
        for ok in ("com.example.app", "a.b", "com.ex_ample.app2"):
            self.assertTrue(app_install.is_valid_package(ok), ok)
        for bad in ("noDots", "com..x", "1com.x", "com.x;id", "com.x app", "", None, "$(id).x"):
            self.assertFalse(app_install.is_valid_package(bad), bad)

    def test_google_account_presence_only(self):
        self.assertTrue(app_install.has_google_account(SIGNED_IN))
        self.assertFalse(app_install.has_google_account("Accounts: 0\n"))
        # A GApps phone with nobody signed in still lists the authenticator.
        signed_out = (
            "User UserInfo{0:Owner:c13}:\n  Accounts: 0\n\n  RegisteredServicesCache: 1 services\n"
            "    ServiceInfo: AuthenticatorDescription {type=com.google}, ComponentInfo{"
            "com.google.android.gms/com.google.android.gms.auth.account.authenticator."
            "GoogleAccountAuthenticatorService}, uid 10054\n"
        )
        self.assertFalse(app_install.has_google_account(signed_out))

    def test_parse_package_info(self):
        info = app_install.parse_package_info("versionCode=12 minSdk=21\nversionName=1.2\n", "com.x.y")
        self.assertEqual(info["version_name"], "1.2")
        self.assertEqual(info["version_code"], "12")


class PathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.allowed = os.path.join(self.tmp.name, "apks")
        os.mkdir(self.allowed)
        self.apk = os.path.join(self.allowed, "app.apk")
        with open(self.apk, "wb") as fh:
            fh.write(apk_bytes())

    def tearDown(self):
        self.tmp.cleanup()

    def test_inside_allowed_dir(self):
        self.assertEqual(app_install.resolve_host_path(self.apk, [os.path.realpath(self.allowed)]),
                         os.path.realpath(self.apk))

    def test_outside_allowed_dir_is_refused(self):
        with self.assertRaises(app_install.InstallError) as ctx:
            app_install.resolve_host_path("/etc/passwd", [os.path.realpath(self.allowed)])
        self.assertEqual(ctx.exception.code, "path_not_allowed")

    def test_dotdot_and_symlink_escape_are_refused(self):
        outside = os.path.join(self.tmp.name, "secret.apk")
        with open(outside, "wb") as fh:
            fh.write(b"x")
        link = os.path.join(self.allowed, "link.apk")
        os.symlink(outside, link)
        for candidate in (link, os.path.join(self.allowed, "..", "secret.apk")):
            with self.assertRaises(app_install.InstallError) as ctx:
                app_install.resolve_host_path(candidate, [os.path.realpath(self.allowed)])
            self.assertEqual(ctx.exception.code, "path_not_allowed")

    def test_prefix_sibling_dir_is_not_inside(self):
        sibling = self.allowed + "-evil"
        os.mkdir(sibling)
        target = os.path.join(sibling, "a.apk")
        with open(target, "wb") as fh:
            fh.write(b"x")
        with self.assertRaises(app_install.InstallError):
            app_install.resolve_host_path(target, [os.path.realpath(self.allowed)])


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dest = os.path.join(self.tmp.name, "dl.apk")

    def tearDown(self):
        self.tmp.cleanup()

    def test_matching_checksum(self):
        data = apk_bytes()
        out = app_install.download("https://cdn.example.com/a.apk", self.dest, sha(data),
                                   opener=fake_opener(data), resolver=public_resolver)
        self.assertEqual(out["bytes"], len(data))
        self.assertTrue(os.path.exists(self.dest))

    def test_mismatch_deletes_the_file_and_hides_the_actual_hash(self):
        data = apk_bytes()
        with self.assertRaises(app_install.InstallError) as ctx:
            app_install.download(f"https://cdn.example.com/a.apk?token={SECRET}", self.dest, "0" * 64,
                                 opener=fake_opener(data), resolver=public_resolver)
        err = ctx.exception
        self.assertEqual(err.code, "checksum_mismatch")
        self.assertEqual(err.status, 422)
        self.assertFalse(os.path.exists(self.dest), "a refused download must not stay on disk")
        body = json.dumps(err.as_dict())
        self.assertNotIn(sha(data), body)
        self.assertNotIn(SECRET, body)

    def test_plain_http_is_refused(self):
        with self.assertRaises(app_install.InstallError) as ctx:
            app_install.download("http://cdn.example.com/a.apk", self.dest, "0" * 64,
                                 opener=fake_opener(b""), resolver=public_resolver)
        self.assertEqual(ctx.exception.code, "insecure_url")

    def test_redirect_to_http_is_refused(self):
        data = apk_bytes()
        with self.assertRaises(app_install.InstallError) as ctx:
            app_install.download("https://cdn.example.com/a.apk", self.dest, sha(data),
                                 opener=fake_opener(data, final_url="http://cdn.example.com/a.apk"),
                                 resolver=public_resolver)
        self.assertEqual(ctx.exception.code, "insecure_url")
        self.assertFalse(os.path.exists(self.dest))

    def test_metadata_and_private_addresses_are_refused(self):
        for ip in ("169.254.169.254", "10.0.0.5", "127.0.0.1", "192.168.1.1"):
            resolver = lambda host, port, ip=ip: [(2, 1, 6, "", (ip, port))]  # noqa: E731
            with self.assertRaises(app_install.InstallError) as ctx:
                app_install.download("https://internal.example/a.apk", self.dest, "0" * 64,
                                     opener=fake_opener(b""), resolver=resolver)
            self.assertEqual(ctx.exception.code, "private_address", ip)

    def test_private_allowed_when_configured(self):
        data = apk_bytes()
        resolver = lambda host, port: [(2, 1, 6, "", ("10.0.0.5", port))]  # noqa: E731
        app_install.download("https://mirror.internal/a.apk", self.dest, sha(data),
                             opener=fake_opener(data), resolver=resolver, allow_private=True)

    def test_size_cap(self):
        data = b"x" * 2048
        with self.assertRaises(app_install.InstallError) as ctx:
            app_install.download("https://cdn.example.com/a.apk", self.dest, sha(data), max_bytes=1024,
                                 opener=fake_opener(data), resolver=public_resolver)
        self.assertEqual(ctx.exception.code, "too_large")
        self.assertFalse(os.path.exists(self.dest))

    def test_network_error_is_reported_without_the_query(self):
        def broken(url, timeout=None):
            raise OSError(f"connection reset for {url}")
        with self.assertRaises(app_install.InstallError) as ctx:
            app_install.download(f"https://cdn.example.com/a.apk?t={SECRET}", self.dest, "0" * 64,
                                 opener=broken, resolver=public_resolver)
        self.assertEqual(ctx.exception.code, "download_failed")
        self.assertNotIn(SECRET, json.dumps(ctx.exception.as_dict()))


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, data):
        path = os.path.join(self.tmp.name, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def test_classify(self):
        self.assertEqual(app_install.classify_archive(self.write("a.apk", apk_bytes())), "apk")
        self.assertEqual(app_install.classify_archive(self.write("a.xapk", bundle_bytes())), "bundle")
        for name, data in (("t.apk", b"not a zip"), ("e.zip", _zip({"readme.txt": "hi"}))):
            with self.assertRaises(app_install.InstallError) as ctx:
                app_install.classify_archive(self.write(name, data))
            self.assertEqual(ctx.exception.code, "not_an_apk")

    def test_bundle_extract_puts_base_first_and_reports_obb(self):
        path = self.write("a.xapk", bundle_bytes({"Android/obb/com.example.app/main.1.obb": "data"}))
        out_dir = os.path.join(self.tmp.name, "out")
        os.mkdir(out_dir)
        apks, obb = app_install.extract_bundle(path, out_dir)
        self.assertEqual(os.path.basename(apks[0]), "base.apk")
        self.assertEqual(len(apks), 2)
        self.assertEqual(obb, ["Android/obb/com.example.app/main.1.obb"])

    def test_bundle_entries_cannot_escape(self):
        path = self.write("evil.apks", _zip({"../../escape.apk": apk_bytes(), "base.apk": apk_bytes()}))
        out_dir = os.path.join(self.tmp.name, "out")
        os.mkdir(out_dir)
        apks, _ = app_install.extract_bundle(path, out_dir)
        self.assertEqual([os.path.basename(a) for a in apks], ["base.apk"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "escape.apk")))


def _zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


class PlayScreenTests(unittest.TestCase):
    def state(self, labels, clickable=True):
        return app_install.play_screen_state(
            [{"label": l, "clickable": clickable, "x": 1, "y": 2} for l in labels]
        )["state"]

    def test_states(self):
        self.assertEqual(self.state(["Example App", "Install"]), "install")
        self.assertEqual(self.state(["Example App", "Uninstall", "Open"]), "installed")
        self.assertEqual(self.state(["Example App", "$4.99"]), "paid")
        self.assertEqual(self.state(["Example App", "Buy"]), "paid")
        self.assertEqual(self.state(["Sign in"]), "sign_in")
        self.assertEqual(self.state(["Item not found."]), "unavailable")
        self.assertEqual(self.state(["Your device isn't compatible with this version."]), "unavailable")
        self.assertEqual(self.state([]), "unknown")

    def test_install_target_is_the_button(self):
        out = app_install.play_screen_state([{"label": "Install", "clickable": True, "x": 10, "y": 20}])
        self.assertEqual((out["target"]["x"], out["target"]["y"]), (10, 20))


class InstallEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = control_api.app.test_client()
        control_api.API_TOKEN = ""
        self.tmp = tempfile.TemporaryDirectory()
        self.apk_dir = os.path.join(self.tmp.name, "apks")
        os.mkdir(self.apk_dir)
        self.env = patch.dict(os.environ, {"APP_INSTALL_DIRS": self.apk_dir})
        self.env.start()
        clear_logs()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def put(self, name, data):
        path = os.path.join(self.apk_dir, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def post(self, body, phone):
        with patch.object(control_api, "run_adb", phone):
            return self.client.post("/apps/install", json=body)

    def test_install_by_path(self):
        phone = FakePhone()
        path = self.put("app.apk", apk_bytes())
        res = self.post({"path": path, "expected_package": "com.example.app"}, phone)
        body = res.get_json()
        self.assertEqual(res.status_code, 200, body)
        self.assertTrue(body["success"])
        self.assertTrue(body["verified"])
        self.assertEqual(body["new_packages"], ["com.example.app"])
        self.assertIn(f"install -r {os.path.realpath(path)}", phone.calls)

    def test_path_checksum_mismatch_installs_nothing(self):
        phone = FakePhone()
        path = self.put("app.apk", apk_bytes())
        res = self.post({"path": path, "sha256": "0" * 64}, phone)
        self.assertEqual(res.status_code, 422)
        self.assertEqual(res.get_json()["code"], "checksum_mismatch")
        self.assertFalse(any(c.startswith("install") for c in phone.calls))

    def test_path_outside_dirs_is_403(self):
        res = self.post({"path": "/etc/hostname"}, FakePhone())
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.get_json()["code"], "path_not_allowed")

    def test_split_set_uses_install_multiple(self):
        phone = FakePhone()
        base = self.put("base.apk", apk_bytes())
        split = self.put("split_config.arm64_v8a.apk", apk_bytes())
        res = self.post({"paths": [base, split]}, phone)
        self.assertEqual(res.status_code, 200, res.get_json())
        self.assertEqual(res.get_json()["method"], "install-multiple")
        self.assertTrue(any(c.startswith("install-multiple -r") for c in phone.calls))

    def test_bundle_is_unpacked_into_install_multiple(self):
        phone = FakePhone()
        path = self.put("app.xapk", bundle_bytes({"Android/obb/x/main.obb": "d"}))
        res = self.post({"path": path}, phone)
        body = res.get_json()
        self.assertEqual(res.status_code, 200, body)
        self.assertEqual(body["files"], ["base.apk", "split_config.arm64_v8a.apk"])
        self.assertEqual(body["obb_not_installed"], ["Android/obb/x/main.obb"])

    def test_install_by_url_checks_sha_and_keeps_the_query_out_of_logs(self):
        phone = FakePhone()
        data = apk_bytes()
        url = f"https://cdn.example.com/app.apk?X-Amz-Signature={SECRET}"
        with patch.object(app_install.urllib.request, "urlopen", fake_opener(data)), \
                patch.object(app_install.socket, "getaddrinfo", public_resolver):
            res = self.post({"url": url, "sha256": sha(data)}, phone)
        body = res.get_json()
        self.assertEqual(res.status_code, 200, body)
        self.assertEqual(body["sources"], ["https://cdn.example.com/app.apk"])
        self.assertNotIn(SECRET, json.dumps(body))
        self.assertNotIn(SECRET, json.dumps(recent_logs(n=500)))
        self.assertTrue(any(c.startswith("install -r") for c in phone.calls))

    def test_url_checksum_mismatch_is_422_and_nothing_installed(self):
        phone = FakePhone()
        data = apk_bytes()
        url = f"https://cdn.example.com/app.apk?token={SECRET}"
        with patch.object(app_install.urllib.request, "urlopen", fake_opener(data)), \
                patch.object(app_install.socket, "getaddrinfo", public_resolver):
            res = self.post({"url": url, "sha256": "f" * 64}, phone)
        self.assertEqual(res.status_code, 422)
        self.assertEqual(res.get_json()["code"], "checksum_mismatch")
        self.assertFalse(any(c.startswith("install") for c in phone.calls))
        self.assertNotIn(SECRET, json.dumps(res.get_json()))
        self.assertNotIn(SECRET, json.dumps(recent_logs(n=500)))

    def test_url_without_sha_is_400_before_any_download(self):
        def must_not_download(*a, **k):
            raise AssertionError("downloaded without a checksum")
        with patch.object(app_install.urllib.request, "urlopen", must_not_download):
            res = self.post({"url": "https://cdn.example.com/app.apk"}, FakePhone())
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.get_json()["code"], "checksum_required")

    def test_adb_failure_is_reported(self):
        phone = FakePhone(install_output="Failure [INSTALL_FAILED_VERSION_DOWNGRADE]")
        path = self.put("app.apk", apk_bytes())
        res = self.post({"path": path}, phone)
        self.assertEqual(res.status_code, 502)
        self.assertEqual(res.get_json()["install_error"], "INSTALL_FAILED_VERSION_DOWNGRADE")

    def test_temp_files_are_removed(self):
        phone = FakePhone()
        path = self.put("app.xapk", bundle_bytes())
        made = []
        real = app_install.new_temp_dir

        def tracking():
            d = real()
            made.append(d)
            return d
        with patch.object(app_install, "new_temp_dir", tracking):
            self.post({"path": path}, phone)
        self.assertTrue(made)
        self.assertFalse(os.path.exists(made[0]))

    def test_needs_auth_when_token_set(self):
        control_api.API_TOKEN = "t0ken"
        try:
            res = self.client.post("/apps/install", json={"path": "/x.apk"})
            self.assertEqual(res.status_code, 401)
        finally:
            control_api.API_TOKEN = ""


class ListAndInfoTests(unittest.TestCase):
    def setUp(self):
        self.client = control_api.app.test_client()
        control_api.API_TOKEN = ""

    def test_scopes(self):
        phone = FakePhone(packages={"com.example.app"})
        with patch.object(control_api, "run_adb", phone):
            body = self.client.get("/apps?scope=all").get_json()
            bad = self.client.get("/apps?scope=everything")
        self.assertIn("com.example.app", body["packages"])
        self.assertIn("shell pm list packages", phone.calls)
        self.assertEqual(bad.status_code, 400)

    def test_default_scope_is_third_party(self):
        phone = FakePhone()
        with patch.object(control_api, "run_adb", phone):
            self.client.get("/apps")
        self.assertIn("shell pm list packages -3", phone.calls)

    def test_info(self):
        phone = FakePhone(packages={"com.example.app"})
        with patch.object(control_api, "run_adb", phone):
            body = self.client.get("/apps/com.example.app").get_json()
            missing = self.client.get("/apps/com.example.other")
            invalid = self.client.get("/apps/not;valid")
        self.assertEqual(body["version_name"], "4.2.1")
        self.assertEqual(missing.status_code, 404)
        self.assertFalse(missing.get_json()["installed"])
        self.assertEqual(invalid.status_code, 400)


class PlayTests(unittest.TestCase):
    def setUp(self):
        self.client = control_api.app.test_client()
        control_api.API_TOKEN = ""
        self.clock = [1000.0]
        self.patches = [
            patch.object(control_api, "_sleep", lambda s: self.clock.__setitem__(0, self.clock[0] + s)),
            patch.object(control_api, "_now", lambda: self.clock[0]),
        ]
        for p in self.patches:
            p.start()
        clear_logs()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def install(self, phone, **body):
        body.setdefault("package", "com.example.app")
        with patch.object(control_api, "run_adb", phone):
            return self.client.post("/apps/play/install", json=body)

    def test_without_gapps(self):
        res = self.install(FakePhone(gapps=False))
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.get_json()["code"], "gapps_missing")

    def test_without_sign_in_names_the_owner_action(self):
        res = self.install(FakePhone(accounts="Accounts: 0\n"))
        body = res.get_json()
        self.assertEqual(res.status_code, 409)
        self.assertEqual(body["code"], "play_sign_in_required")
        self.assertIn("owner", body["owner_action"])
        self.assertIn("google.com/android/uncertified", body["owner_action"])

    def test_invalid_package_and_timeout(self):
        self.assertEqual(self.install(FakePhone(), package="com.x;reboot").status_code, 400)
        self.assertEqual(self.install(FakePhone(), timeout=99999).status_code, 400)

    def test_signed_in_install_taps_install_and_waits(self):
        phone = FakePhone(accounts=SIGNED_IN, play_screens=[[], ["Example App", "Install"]])
        res = self.install(phone, wait=True)
        body = res.get_json()
        self.assertEqual(res.status_code, 200, body)
        self.assertTrue(body["success"])
        self.assertEqual(body["via"], "play_store")
        self.assertTrue(any("market://details?id=com.example.app" in c for c in phone.calls))
        self.assertTrue(any(c.startswith("shell input tap") for c in phone.calls))

    def test_already_installed_does_not_open_play(self):
        phone = FakePhone(packages={"com.example.app"}, accounts=SIGNED_IN)
        body = self.install(phone, wait=True).get_json()
        self.assertTrue(body["already_installed"])
        self.assertFalse(any("market://" in c for c in phone.calls))

    def test_paid_app_is_never_bought(self):
        phone = FakePhone(accounts=SIGNED_IN, play_screens=[["Example App", "$4.99"]])
        res = self.install(phone, wait=True)
        self.assertEqual(res.get_json()["code"], "paid_app")
        self.assertFalse(any(c.startswith("shell input tap") for c in phone.calls))

    def test_play_asking_to_sign_in_stops(self):
        phone = FakePhone(accounts=SIGNED_IN, play_screens=[["Sign in"]])
        self.assertEqual(self.install(phone, wait=True).get_json()["code"], "play_sign_in_required")

    def test_no_install_button_times_out_cleanly(self):
        phone = FakePhone(accounts=SIGNED_IN, play_screens=[["Loading"]] * 100)
        body = self.install(phone, wait=True, timeout=60).get_json()
        self.assertEqual(body["code"], "install_button_not_found")

    def test_download_that_never_finishes(self):
        phone = FakePhone(accounts=SIGNED_IN, play_screens=[["Install"]], installs_after_tap=False)
        body = self.install(phone, wait=True, timeout=30).get_json()
        self.assertEqual(body["code"], "install_timeout")

    def test_default_is_a_job(self):
        control_api._jobs.clear()
        phone = FakePhone(packages={"com.example.app"}, accounts=SIGNED_IN)
        res = self.install(phone)
        self.assertEqual(res.status_code, 202)
        self.assertEqual(res.get_json()["type"], "play_install")

    def test_job_stays_on_the_phone_that_queued_it(self):
        """A job thread has no request; it must not fall back to ADB_CONNECT."""
        import time as _time
        control_api._jobs.clear()
        phone = FakePhone(packages={"com.example.app"}, accounts=SIGNED_IN)
        seen = []

        def recording(*args, timeout=30):
            seen.append(control_api.current_adb_connect())
            return phone(*args, timeout=timeout)
        with patch.object(control_api, "run_adb", recording):
            res = self.client.post("/apps/play/install", json={"package": "com.example.app"},
                                   headers={"X-Cloud-Phone-Adb": "127.0.0.1:5557"})
            job_id = res.get_json()["job_id"]
            for _ in range(100):
                job = self.client.get(f"/jobs/{job_id}").get_json()
                if job["status"] in ("done", "failed"):
                    break
                _time.sleep(0.01)
        self.assertEqual(job["status"], "done")
        self.assertTrue(job["result"]["already_installed"])
        self.assertEqual(set(seen), {"127.0.0.1:5557"})

    def test_status_reports_presence_not_the_account(self):
        phone = FakePhone(accounts=SIGNED_IN)
        with patch.object(control_api, "run_adb", phone):
            body = self.client.get("/apps/play/status").get_json()
        self.assertTrue(body["ready"])
        self.assertTrue(body["google_account"])
        self.assertEqual(body["gsf_android_id"], "3987654321012345678")
        self.assertNotIn("owner@example.com", json.dumps(body))
        self.assertNotIn("owner@example.com", json.dumps(recent_logs(n=500)))

    def test_status_before_sign_in(self):
        phone = FakePhone(accounts="", gsf_id=None)
        with patch.object(control_api, "run_adb", phone):
            body = self.client.get("/apps/play/status").get_json()
        self.assertFalse(body["ready"])
        self.assertIsNone(body["gsf_android_id"])
        self.assertIn("owner_action", body)


if __name__ == "__main__":
    unittest.main()
