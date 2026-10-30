"""Install apps on a phone without Google Play, and through Play once signed in.

Three ways an app reaches the phone, from most to least under our control:

1. An APK (or a split set / `.apks` / `.xapk` bundle) already on the Control
   API host, under one of `APP_INSTALL_DIRS`.
2. An APK fetched from an HTTPS URL. A SHA-256 is required and checked before
   anything is installed; a mismatch deletes the download.
3. Google Play, by package name. This drives the Play Store's own Install
   button and therefore only works after the phone's owner has signed in to
   Play on the phone themselves (see docs/GAPPS-AND-APP-INSTALL.md). Nothing
   here signs in, stores a Google credential or talks to Play's APIs.

The functions here are pure or take their side effects as arguments, so the
tests can drive them with no device, no network and no `adb`.
"""

from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import shutil
import socket
import tempfile
import urllib.parse
import urllib.request
import zipfile

PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")

DEFAULT_INSTALL_DIRS = "/opt/cloud-phone/apks"
DEFAULT_MAX_BYTES = 1024 * 1024 * 1024  # 1 GiB: large games ship big splits
DEFAULT_MAX_FILES = 32
DOWNLOAD_TIMEOUT = 120

# Play Store button labels. The Store is localised; these cover the English UI
# the default image ships with. Anything else is reported, never guessed at.
PLAY_INSTALL_LABELS = ("install",)
PLAY_INSTALLED_LABELS = ("open", "uninstall", "play")
PLAY_SIGN_IN_LABELS = ("sign in",)
PLAY_UNAVAILABLE_PHRASES = (
    "item not found",
    "isn't available",
    "not available for your device",
    "your device isn't compatible",
)
_PRICE_RE = re.compile(r"^(buy\b|[$€£¥₹]\s?\d|\d+[.,]\d{2}\s?[$€£¥₹])", re.IGNORECASE)


class InstallError(Exception):
    """A refusal with a machine-readable code and an HTTP status."""

    def __init__(self, code, message, status=400, **extra):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.extra = extra

    def as_dict(self):
        body = {"success": False, "code": self.code, "error": self.message}
        body.update(self.extra)
        return body


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def is_valid_package(name):
    return isinstance(name, str) and len(name) <= 255 and bool(PACKAGE_RE.match(name))


def safe_url_for_log(url):
    """scheme://host/path only: signed URLs carry credentials in the query."""
    try:
        parts = urllib.parse.urlsplit(str(url))
    except ValueError:
        return "<unparseable url>"
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urllib.parse.urlunsplit((parts.scheme, host, parts.path, "", ""))


def install_dirs(env=None):
    env = os.environ if env is None else env
    raw = env.get("APP_INSTALL_DIRS", DEFAULT_INSTALL_DIRS)
    return [os.path.realpath(p) for p in raw.split(":") if p.strip()]


def resolve_host_path(path, allowed_dirs):
    """Real path of `path`, refused unless it sits inside an allowed dir.

    The Control API runs as root on the phone host. Without this, "install
    this path" would be a way to make it read any file on the host.
    """
    if not isinstance(path, str) or not path.strip():
        raise InstallError("invalid_path", "path must be a non-empty string")
    real = os.path.realpath(path)
    for base in allowed_dirs:
        if real == base or real.startswith(base.rstrip(os.sep) + os.sep):
            if not os.path.isfile(real):
                raise InstallError("not_found", f"no file at {path}", status=404)
            return real
    raise InstallError(
        "path_not_allowed",
        "path is outside APP_INSTALL_DIRS; copy the APK there or install by URL",
        status=403,
        allowed_dirs=allowed_dirs,
    )


def _check_url(url, allow_private=False, resolver=None):
    if not isinstance(url, str) or not url:
        raise InstallError("invalid_url", "url must be a non-empty string")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https":
        raise InstallError("insecure_url", "only https:// URLs are accepted")
    if not parts.hostname:
        raise InstallError("invalid_url", "url has no host")
    if allow_private:
        return
    resolver = resolver or socket.getaddrinfo
    try:
        infos = resolver(parts.hostname, parts.port or 443)
    except OSError as exc:
        raise InstallError("dns_failed", f"cannot resolve {parts.hostname}: {exc}", status=502)
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            # Cloud metadata services live on link-local addresses; the phone
            # host must not be a way to reach them.
            raise InstallError(
                "private_address",
                f"{parts.hostname} resolves to a private address; set APP_INSTALL_ALLOW_PRIVATE=1 to allow",
                status=403,
            )


def parse_install_request(data, env=None):
    """Turn a request body into a list of sources, or raise InstallError.

    Accepted shapes (exactly one):
      {"path": "/opt/cloud-phone/apks/app.apk", "sha256"?: "..."}
      {"paths": ["base.apk", "split_config.arm64_v8a.apk", ...]}
      {"url": "https://...", "sha256": "..."}
      {"urls": [{"url": "https://...", "sha256": "..."}, ...]}
    plus optional booleans replace (default true), grant_permissions,
    downgrade, and expected_package.
    """
    env = os.environ if env is None else env
    if not isinstance(data, dict):
        raise InstallError("invalid_body", "JSON object body required")

    given = [k for k in ("path", "paths", "url", "urls") if data.get(k) not in (None, "", [])]
    if len(given) != 1:
        raise InstallError(
            "invalid_body",
            "give exactly one of path, paths, url, urls",
            given=given,
        )
    kind = given[0]
    max_files = int(env.get("APP_INSTALL_MAX_FILES", DEFAULT_MAX_FILES))

    sources = []
    if kind == "path":
        sources.append({"path": data["path"], "sha256": data.get("sha256")})
    elif kind == "paths":
        if not isinstance(data["paths"], list):
            raise InstallError("invalid_body", "paths must be a list")
        sources.extend({"path": p, "sha256": None} for p in data["paths"])
    elif kind == "url":
        sources.append({"url": data["url"], "sha256": data.get("sha256")})
    else:
        if not isinstance(data["urls"], list):
            raise InstallError("invalid_body", "urls must be a list of {url, sha256}")
        for item in data["urls"]:
            if not isinstance(item, dict):
                raise InstallError("invalid_body", "each urls entry must be {url, sha256}")
            sources.append({"url": item.get("url"), "sha256": item.get("sha256")})

    if len(sources) > max_files:
        raise InstallError("too_many_files", f"at most {max_files} files per install")

    for src in sources:
        sha = src.get("sha256")
        if "url" in src:
            if not sha:
                raise InstallError(
                    "checksum_required",
                    "a sha256 is required for every URL; the APK is not installed without one",
                    url=safe_url_for_log(src.get("url")),
                )
        if sha is not None and (not isinstance(sha, str) or not SHA256_RE.match(sha)):
            raise InstallError("invalid_checksum", "sha256 must be 64 hex characters")
        if sha:
            src["sha256"] = sha.lower()

    expected = data.get("expected_package")
    if expected is not None and not is_valid_package(expected):
        raise InstallError("invalid_package", f"{expected!r} is not a valid Android package name")

    options = {}
    for key, default in (("replace", True), ("grant_permissions", False), ("downgrade", False)):
        value = data.get(key, default)
        if not isinstance(value, bool):
            raise InstallError("invalid_body", f"{key} must be true or false")
        options[key] = value

    return {"kind": kind, "sources": sources, "options": options, "expected_package": expected}


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url, dest, expected_sha256, max_bytes=DEFAULT_MAX_BYTES, opener=None,
             allow_private=False, resolver=None, timeout=DOWNLOAD_TIMEOUT):
    """Fetch `url` to `dest`, hashing as it streams. Deletes `dest` on refusal.

    The actual digest of a mismatching download is deliberately not returned
    or logged: on a misconfigured allow-list it would be a hash of whatever the
    URL pointed at, and `/logs` is readable without a token.
    """
    _check_url(url, allow_private=allow_private, resolver=resolver)
    opener = opener or urllib.request.urlopen
    digest = hashlib.sha256()
    size = 0
    try:
        with opener(url, timeout=timeout) as resp, open(dest, "wb") as out:
            final = getattr(resp, "geturl", lambda: url)()
            if urllib.parse.urlsplit(final).scheme != "https":
                raise InstallError("insecure_url", "download was redirected away from https")
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise InstallError(
                        "too_large", f"download exceeds APP_INSTALL_MAX_BYTES ({max_bytes})", status=413
                    )
                digest.update(chunk)
                out.write(chunk)
    except InstallError:
        _unlink(dest)
        raise
    except Exception as exc:  # network errors, HTTP errors
        _unlink(dest)
        raise InstallError(
            "download_failed", f"download failed: {type(exc).__name__}", status=502,
            url=safe_url_for_log(url),
        )
    if digest.hexdigest() != expected_sha256:
        _unlink(dest)
        raise InstallError(
            "checksum_mismatch",
            "sha256 of the download does not match; nothing was installed",
            status=422,
            url=safe_url_for_log(url),
            expected_sha256=expected_sha256,
            bytes=size,
        )
    return {"path": dest, "bytes": size, "sha256": expected_sha256}


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def classify_archive(path):
    """"apk" for a single APK, "bundle" for .apks/.xapk/.apkm, else refuse."""
    if not zipfile.is_zipfile(path):
        raise InstallError("not_an_apk", f"{os.path.basename(path)} is not an APK (not a zip)", status=422)
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
    if "AndroidManifest.xml" in names:
        return "apk"
    if any(n.lower().endswith(".apk") and "/" not in n.strip("/") for n in names):
        return "bundle"
    raise InstallError(
        "not_an_apk",
        f"{os.path.basename(path)} has no AndroidManifest.xml and no top-level .apk entries",
        status=422,
    )


def extract_bundle(path, dest_dir):
    """Top-level .apk entries of a split bundle, extracted under dest_dir.

    Only top-level APKs are taken and names are reduced to their basename, so
    an entry like `../../etc/x.apk` cannot escape dest_dir. OBB data in an
    XAPK is not pushed; that is reported to the caller.
    """
    out = []
    skipped = []
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            name = info.filename.strip("/")
            if name.lower().endswith(".apk") and "/" not in name:
                target = os.path.join(dest_dir, os.path.basename(name))
                with zf.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                out.append(target)
            elif not info.is_dir():
                skipped.append(name)
    if not out:
        raise InstallError("not_an_apk", "bundle contains no APKs", status=422)
    # base.apk first keeps adb's error messages about the right file.
    out.sort(key=lambda p: (os.path.basename(p) != "base.apk", os.path.basename(p)))
    obb = [n for n in skipped if n.lower().endswith(".obb")]
    return out, obb


def adb_install_args(apks, options):
    """argv after `adb -s SERIAL` for one APK or a split set."""
    flags = []
    if options.get("replace", True):
        flags.append("-r")
    if options.get("grant_permissions"):
        flags.append("-g")
    if options.get("downgrade"):
        flags.append("-d")
    verb = "install" if len(apks) == 1 else "install-multiple"
    return [verb, *flags, *apks]


def parse_packages(pm_output):
    return sorted(
        line.strip()[len("package:"):]
        for line in (pm_output or "").splitlines()
        if line.strip().startswith("package:")
    )


def parse_package_info(dumpsys_output, package):
    """versionName / versionCode / install paths from `dumpsys package PKG`."""
    info = {"package": package}
    for line in (dumpsys_output or "").splitlines():
        line = line.strip()
        if line.startswith("versionName=") and "version_name" not in info:
            info["version_name"] = line.split("=", 1)[1]
        elif line.startswith("versionCode=") and "version_code" not in info:
            info["version_code"] = line.split("=", 1)[1].split()[0]
        elif line.startswith("firstInstallTime=") and "first_install_time" not in info:
            info["first_install_time"] = line.split("=", 1)[1]
        elif line.startswith("lastUpdateTime=") and "last_update_time" not in info:
            info["last_update_time"] = line.split("=", 1)[1]
    return info


_ACCOUNT_LINE = re.compile(r"^\s*Account \{.*\btype=com\.google\}", re.MULTILINE)


def has_google_account(dumpsys_account_output):
    """Whether `dumpsys account` lists a com.google account (names not kept).

    Only `Account {name=..., type=com.google}` lines count. The same dump
    always lists Play services' authenticator as
    `AuthenticatorDescription {type=com.google}`, signed in or not, so a plain
    substring match reported every GApps phone as signed in (seen on the
    first real boot, 2026-10-30).
    """
    return bool(_ACCOUNT_LINE.search(dumpsys_account_output or ""))


# ---------------------------------------------------------------------------
# Play Store screen
# ---------------------------------------------------------------------------

def play_screen_state(elements):
    """Read a Play Store details page: what can be done here, and where.

    Returns {"state": ..., "target": element-or-None}. States:
      install       an Install button is on screen (target is it)
      installed     Open / Uninstall / Play are on screen
      paid          a price or Buy button; this path never buys anything
      sign_in       Play is asking for a Google sign-in
      unavailable   item not found / not available for this device
      unknown       none of the above (still loading, a dialog, a new layout)
    """
    labels = [(e, (e.get("label") or "").strip().lower()) for e in elements or []]
    text = " ".join(lbl for _, lbl in labels)

    for phrase in PLAY_UNAVAILABLE_PHRASES:
        if phrase in text:
            return {"state": "unavailable", "target": None}
    for e, lbl in labels:
        if lbl in PLAY_SIGN_IN_LABELS:
            return {"state": "sign_in", "target": e}
    for e, lbl in labels:
        if lbl in PLAY_INSTALL_LABELS:
            return {"state": "install", "target": e}
    for e, lbl in labels:
        if e.get("clickable") and _PRICE_RE.match(lbl):
            return {"state": "paid", "target": None}
    if any(lbl in PLAY_INSTALLED_LABELS for _, lbl in labels):
        return {"state": "installed", "target": None}
    return {"state": "unknown", "target": None}


def new_temp_dir():
    return tempfile.mkdtemp(prefix="cloud-phone-apk-")
