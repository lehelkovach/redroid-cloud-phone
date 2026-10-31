"""The `chrome` runtime pool: one browser per owner, never shared.

OSLO used to drive ONE Chrome (`--user-data-dir=/home/ubuntu/stack/chrome-profile`,
port 9222). Whoever attached drove every logged-in session in it. This module
is the browser half of the orchestrator's per-owner leasing, next to the phone
pool, with the same rules the phones follow:

  * one profile directory per owner token, mode 0700, never mounted into
    another owner's container;
  * one internal DevTools port per lease, bound to loopback on the
    orchestrator host and never handed to a caller: callers reach the browser
    only through the authenticated relay (`orchestrator/relay.py`);
  * one proxy id and upstream per lease, taken from the request (the owner's
    own vault on the OSLO side), never from a box-wide variable.

Lease rules:

  * a lease is refused across owners, never silently reassigned;
  * TTL and expiry are explicit (`expires_at` on every lease);
  * release and expiry stop the browser and keep the profile, so the owner's
    sessions survive; deleting a profile is its own explicit call
    (`delete_profile`), refused while the owner holds a lease.

Runtimes (`ORCH_CHROME_RUNTIME`):

  mock    no process; the upstream DevTools URL comes from a test hook. Default
          when ORCH_DEPLOY_MODE=mock.
  local   a Chromium process per lease on this host, `--remote-debugging-address=127.0.0.1`.
  docker  a container per lease: `-p 127.0.0.1:<port>:9222`, only that owner's
          profile mounted. Default otherwise.
"""

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import time
import urllib.request

PURPOSE_BROWSER = "browser"
RUNTIME_CHROME = "chrome"

DEFAULT_TTL_SECONDS = 3600
MAX_TTL_SECONDS = 12 * 3600

# Slots: one owner may hold several browsers, one per slot, each its own
# runtime and profile and all still that owner's. `main` is the browser every
# caller had before slots existed, and stays byte-for-byte the same.
DEFAULT_SLOT = "main"
SLOT_RE = re.compile(r"^[a-z0-9_-]{1,32}$")


class LeaseError(Exception):
    """A refused lease operation. `status` is the HTTP status to answer with.

    `extra` is merged into the JSON refusal (for example the slot cap).
    """

    def __init__(self, code, message, status=409, **extra):
        super().__init__(message)
        self.code = code
        self.status = status
        self.extra = extra


def normalize_slot(slot):
    """`main` when unset; otherwise the slot name, or a 400 for one that does not fit."""
    if slot is None:
        return DEFAULT_SLOT
    raw = str(slot).strip()
    if not SLOT_RE.match(raw):
        raise LeaseError("bad_slot", "slot must match ^[a-z0-9_-]{1,32}$", 400)
    return raw


def isolation_key(owner, slot=DEFAULT_SLOT):
    """`<owner token>/<slot>`: names one browser runtime, distinct per owner and per slot."""
    return f"{owner_token(owner)}/{normalize_slot(slot)}"


def owner_token(owner):
    """Filesystem-safe token for an owner id, `<slug>-<8 hex of sha256>`.

    The same algorithm as OSLO's `ownerToken` (src/identity/browser_lease.mjs),
    so a profile directory here and a cookie jar there carry the same name for
    the same account. The digest is taken over the RAW id, so `a:b` and `a-b`
    (one slug) get different tokens, and `..` cannot survive as a path part.
    """
    raw = str(owner if owner is not None else "").strip()
    if not raw:
        return ""
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", raw).strip("-")[:48]
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}" if slug else digest


def clamp_ttl(ttl_seconds):
    try:
        n = int(ttl_seconds)
    except (TypeError, ValueError):
        return DEFAULT_TTL_SECONDS
    if n <= 0:
        return DEFAULT_TTL_SECONDS
    return max(10, min(n, MAX_TTL_SECONDS))


# --------------------------------------------------------------------------
# Profile volumes
# --------------------------------------------------------------------------

def _is_within(child, root):
    child = os.path.realpath(child)
    root = os.path.realpath(root)
    return child == root or child.startswith(root.rstrip(os.sep) + os.sep)


def ensure_profile_root(root):
    """Create the profile root 0700 and refuse one anyone else can read."""
    if not root:
        raise LeaseError("profile_root_required", "ORCH_PROFILE_ROOT is not set", 500)
    os.makedirs(root, mode=0o700, exist_ok=True)
    st = os.lstat(root)
    if stat.S_ISLNK(st.st_mode):
        raise LeaseError("profile_root_symlink", "profile root must not be a symlink", 500)
    if st.st_mode & 0o077:
        os.chmod(root, 0o700)
    return root


def profile_dir_for(root, owner, slot=DEFAULT_SLOT):
    """`<root>/<token>` for the main slot; `<root>/<token>.slot-<slot>` for any other.

    A slot profile is a sibling, not a child, of the main profile: only the
    main directory is mounted into the main browser's container, so a slot's
    cookies never ride along into it. No owner token contains a dot, so the
    sibling cannot collide with another owner's directory.
    """
    token = owner_token(owner)
    if not token:
        raise LeaseError("owner_required", "a profile needs an owner", 400)
    slot = normalize_slot(slot)
    path = os.path.join(root, token if slot == DEFAULT_SLOT else f"{token}.slot-{slot}")
    if not _is_within(path, root) or os.path.realpath(path) == os.path.realpath(root):
        raise LeaseError("bad_owner", "owner does not map to a directory under the profile root", 400)
    return path


def ensure_profile_dir(root, owner, slot=DEFAULT_SLOT):
    """The owner's profile directory (for this slot), created 0700 if missing.

    Refuses a symlink (it could point into another owner's directory) and
    tightens a directory someone loosened.
    """
    ensure_profile_root(root)
    path = profile_dir_for(root, owner, slot)
    if os.path.lexists(path):
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise LeaseError("profile_not_dir", "profile path is not a plain directory", 500)
        if st.st_mode & 0o077:
            os.chmod(path, 0o700)
    else:
        os.mkdir(path, 0o700)
        os.chmod(path, 0o700)  # mkdir honours umask; be explicit
    return path


def delete_profile_dir(root, owner):
    """Delete the owner's main profile and every slot profile of theirs. Nobody else's."""
    main = profile_dir_for(root, owner)
    slots = [os.path.join(root, name) for name in (os.listdir(root) if os.path.isdir(root) else [])
             if name.startswith(os.path.basename(main) + ".slot-")]
    deleted = False
    for path in [main] + slots:
        if not os.path.lexists(path):
            continue
        if os.path.islink(path):
            os.unlink(path)  # never follow it
            deleted = True
            continue
        if not _is_within(path, root):
            raise LeaseError("bad_owner", "refusing to delete outside the profile root", 400)
        shutil.rmtree(path)
        deleted = True
    return deleted


# --------------------------------------------------------------------------
# Encryption at rest (verification, not key management)
# --------------------------------------------------------------------------

def _read_text(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _mount_for(path, mountinfo_text):
    """(mount_point, source) of the mount holding `path`, longest prefix wins."""
    real = os.path.realpath(path)
    best = None
    for line in mountinfo_text.splitlines():
        parts = line.split()
        if " - " not in line or len(parts) < 5:
            continue
        mount_point = parts[4].replace("\\040", " ")
        after = line.split(" - ", 1)[1].split()
        source = after[1] if len(after) > 1 else ""
        inside = mount_point == "/" or real == mount_point or real.startswith(mount_point.rstrip("/") + "/")
        if inside and (best is None or len(mount_point) > len(best[0])):
            best = (mount_point, source)
    return best


def check_profile_encryption(root, mode, *, read_text=_read_text, run=None, realpath=os.path.realpath):
    """Verify the profile root is encrypted at rest. Returns (ok, detail).

    mode "none": nothing checked (the default; say so in /health).
    mode "luks": the mount holding `root` must be a dm-crypt device
                 (/sys/block/dm-N/dm/uuid starts with "CRYPT-").
    mode "fscrypt": `fscrypt status <root>` must report the directory
                 encrypted. New owner directories created inside inherit the
                 policy, so checking the root covers them.

    This verifies; it does not set anything up or hold keys. See
    docs/isolation.md for what each option protects and what it does not.
    """
    mode = (mode or "none").strip().lower()
    if mode in ("", "none", "off"):
        return True, "not required (ORCH_PROFILE_ENCRYPTION=none)"
    if mode == "luks":
        try:
            info = _mount_for(root, read_text("/proc/self/mountinfo"))
        except OSError as exc:
            return False, f"cannot read mountinfo: {exc}"
        if not info:
            return False, "no mount found for the profile root"
        source = info[1]
        dev = os.path.basename(realpath(source)) if source.startswith("/dev/") else ""
        if not dev.startswith("dm-"):
            return False, f"profile root is on {source or 'an unknown device'}, not a device-mapper volume"
        try:
            uuid = read_text(f"/sys/block/{dev}/dm/uuid").strip()
        except OSError:
            return False, f"cannot read the dm uuid for {dev}"
        if uuid.startswith("CRYPT-"):
            return True, f"profile root on dm-crypt volume {dev} mounted at {info[0]}"
        return False, f"{dev} is device-mapper but not dm-crypt"
    if mode == "fscrypt":
        runner = run or (lambda argv: subprocess.run(argv, capture_output=True, text=True, timeout=10))
        try:
            out = runner(["fscrypt", "status", root])
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"fscrypt unavailable: {exc}"
        text = (getattr(out, "stdout", "") or "") + (getattr(out, "stderr", "") or "")
        if getattr(out, "returncode", 1) == 0 and re.search(r"\bis encrypted\b", text):
            return True, "profile root is an fscrypt-encrypted directory"
        return False, "fscrypt reports the profile root is not encrypted"
    return False, f"unknown ORCH_PROFILE_ENCRYPTION mode {mode!r} (use none, luks or fscrypt)"


# --------------------------------------------------------------------------
# Browser runtimes
# --------------------------------------------------------------------------

def proxy_server_arg(upstream):
    """Chrome `--proxy-server` value for an upstream WITHOUT credentials."""
    if not upstream:
        return None
    raw = str(upstream)
    if "@" in raw:
        raise ValueError("an upstream with credentials must go through a per-lease relay")
    return raw


def docker_run_argv(session, *, image, network, container_prefix="orch-chrome", proxy_server=None,
                    extra_args=None):
    """`docker run` for one lease. Pure: tests read it without Docker.

    Only THIS owner's profile directory is mounted. The DevTools port is
    published on 127.0.0.1 only, so nothing off the host -- and nothing on it
    but the orchestrator, which owns the relay -- can reach it.
    """
    name = f"{container_prefix}-{session['owner_token']}"
    slot = session.get("slot") or DEFAULT_SLOT
    if slot != DEFAULT_SLOT:
        name += f".{slot}"  # a dot: never in a token or a slot, so one name per (owner, slot)
    argv = [
        "docker", "run", "-d", "--rm",
        "--name", name,
        "--network", network,
        "-p", f"127.0.0.1:{int(session['cdp_port'])}:9222",
        "-v", f"{session['profile_dir']}:/data/profile:rw",
        "--security-opt", "no-new-privileges",
        "--cap-drop", "ALL",
        "--label", f"cloud-phone.owner-token={session['owner_token']}",
        "--label", f"cloud-phone.lease={session['lease_id']}",
        image,
        "--user-data-dir=/data/profile",
        "--remote-debugging-address=0.0.0.0",  # inside the container; published on host loopback only
        "--remote-debugging-port=9222",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if proxy_server:
        argv.append(f"--proxy-server={proxy_server}")
    argv.extend(extra_args or [])
    return argv


def local_chrome_argv(session, *, chrome_bin, proxy_server=None, headless=True, extra_args=None):
    argv = [
        chrome_bin,
        f"--user-data-dir={session['profile_dir']}",
        "--remote-debugging-address=127.0.0.1",
        f"--remote-debugging-port={int(session['cdp_port'])}",
        "--no-first-run",
        "--no-default-browser-check",
        "--no-sandbox",
        "--disable-dev-shm-usage",
    ]
    if headless:
        argv.append("--headless=new")
    if proxy_server:
        argv.append(f"--proxy-server={proxy_server}")
    argv.extend(extra_args or [])
    argv.append("about:blank")
    return argv


def wait_for_devtools(port, timeout=20.0, host="127.0.0.1"):
    """The browser-level DevTools WebSocket URL once the port answers."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://{host}:{int(port)}/json/version", timeout=2) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            ws = data.get("webSocketDebuggerUrl")
            if ws:
                return ws
        except Exception as exc:  # noqa: BLE001 - polling until the browser answers
            last = exc
        time.sleep(0.2)
    raise RuntimeError(f"DevTools on 127.0.0.1:{port} did not answer within {timeout}s ({last})")


class MockChromeRuntime:
    """No process. `upstream_for(session)` (a test hook) names the DevTools URL."""

    name = "mock"

    def __init__(self, upstream_for=None):
        self.upstream_for = upstream_for
        self.started = []
        self.stopped = []

    def start(self, session, proxy_server=None):
        self.started.append({"lease_id": session["lease_id"], "proxy_server": bool(proxy_server)})
        ws = self.upstream_for(session) if self.upstream_for else None
        return {"cdp_ws_internal": ws, "pid": None, "container": None}

    def stop(self, session):
        self.stopped.append(session["lease_id"])
        return True


class LocalChromeRuntime:
    """One Chromium process per lease on this host."""

    name = "local"

    def __init__(self, chrome_bin, headless=True, extra_args=None):
        self.chrome_bin = chrome_bin
        self.headless = headless
        self.extra_args = extra_args or []
        self._procs = {}

    def start(self, session, proxy_server=None):
        if not self.chrome_bin:
            raise RuntimeError("ORCH_CHROME_BIN is required for ORCH_CHROME_RUNTIME=local")
        argv = local_chrome_argv(session, chrome_bin=self.chrome_bin, proxy_server=proxy_server,
                                 headless=self.headless, extra_args=self.extra_args)
        proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._procs[session["lease_id"]] = proc
        try:
            ws = wait_for_devtools(session["cdp_port"])
        except RuntimeError:
            proc.kill()
            raise
        return {"cdp_ws_internal": ws, "pid": proc.pid, "container": None}

    def stop(self, session):
        proc = self._procs.pop(session["lease_id"], None)
        if proc is None:
            return False
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        return True


class DockerChromeRuntime:
    """One container per lease."""

    name = "docker"

    def __init__(self, image, network, container_prefix="orch-chrome", run=None):
        self.image = image
        self.network = network
        self.container_prefix = container_prefix
        self.run = run or (lambda argv: subprocess.check_output(argv, text=True))

    def start(self, session, proxy_server=None):
        if not self.image:
            raise RuntimeError("ORCH_CHROME_IMAGE is required for ORCH_CHROME_RUNTIME=docker")
        argv = docker_run_argv(session, image=self.image, network=self.network,
                               container_prefix=self.container_prefix, proxy_server=proxy_server)
        self.run(argv)
        ws = wait_for_devtools(session["cdp_port"])
        return {"cdp_ws_internal": ws, "pid": None, "container": argv[argv.index("--name") + 1]}

    def stop(self, session):
        name = session.get("container")
        if not name:
            return False
        try:
            self.run(["docker", "rm", "-f", name])
        except (OSError, subprocess.SubprocessError):
            return False
        return True


def public_view(session):
    """What a caller may see. Never the internal port or upstream URL."""
    hidden = {"cdp_port", "cdp_ws_internal", "pid", "container"}
    return {k: v for k, v in session.items() if k not in hidden}
