"""Per-owner, size-limited, encrypted volumes for phone /data and browser profiles.

Before this module a phone's Android /data was one Docker volume per
*instance*, and every browser profile sat in one directory tree under
ORCH_PROFILE_ROOT with (at best) one key for all of them. Storage followed the
machine, not the person, nothing stopped one owner filling the host, and a key
that unlocks one profile unlocks all of them.

Here storage belongs to the OWNER:

  ORCH_VOLUME_ROOT/<owner token>/            0700
      phone-data.img       sparse image, LUKS2 + ext4, size = phone quota
      phone-data.mnt/      where it is mounted while leased, 0700
      browser-profile.img  the same for the Chrome profile
      browser-profile.mnt/

  * Bound to the owner: the path is the owner token (`browser_pool.owner_token`),
    and the LUKS key is derived for that owner and that kind only, so owner B's
    key cannot open owner A's image even if a bug pointed B at it.
  * Size-limited: the image IS the limit. ext4 inside it answers ENOSPC when the
    owner's quota is full; the host's filesystem never sees more than the image
    size. New images are refused (`507 host_disk_low`) when they would take the
    host below ORCH_HOST_MIN_FREE_GB, counting every existing image as fully
    allocated even though it is sparse.
  * Encrypted with a key per owner: HKDF-SHA256(master, info="orch-volume:" +
    owner_token + ":" + kind). The master lives in ORCH_VOLUME_MASTER_KEY_FILE
    (0600). Keys reach cryptsetup on stdin (`--key-file=-`), never in argv, the
    environment, the state database, a log line or /health.
  * Never handed to another owner: a new owner's first lease creates a new
    image. Nothing is ever re-labelled or re-used across tokens.

Every privileged command (losetup, cryptsetup, mkfs.ext4, mount, umount) goes
through one injectable `runner(argv, input=None) -> CompletedProcess`, so tests
replace the kernel with a fake (tests/fixtures/fake_volumes.py).

The LUKS PBKDF is PBKDF2 at its minimum iteration count on purpose: the
passphrase is a 512-bit HKDF output, not something a person typed, so key
stretching adds nothing, while the argon2id default would cost ~1 GiB of
memory and a second or two on every lease.
"""

import hashlib
import hmac
import json
import logging
import os
import stat
import subprocess
import threading
import time

try:
    from orchestrator import browser_pool as bp
except ImportError:  # running from orchestrator/ directly
    import browser_pool as bp  # type: ignore

KIND_PHONE = "phone-data"
KIND_BROWSER = "browser-profile"
KINDS = (KIND_PHONE, KIND_BROWSER)

GIB = 1024 ** 3
MIN_MASTER_BYTES = 32
KEY_BYTES = 64
WARN_LEVELS = (80, 95)

# Android needs exec on /data (app native libraries); Chrome's profile does not.
MOUNT_OPTIONS = {
    KIND_PHONE: "nodev,nosuid",
    KIND_BROWSER: "nodev,nosuid,noexec",
}

_TOKEN_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


class VolumeError(bp.LeaseError):
    """A refused or failed volume operation. `status` is the HTTP status to answer with."""


# --------------------------------------------------------------------------
# Keys
# --------------------------------------------------------------------------

def hkdf_sha256(ikm, info, length=KEY_BYTES, salt=b""):
    """RFC 5869 HKDF with SHA-256 (stdlib only)."""
    if length > 255 * 32:
        raise ValueError("HKDF length too large")
    prk = hmac.new(salt or bytes(32), ikm, hashlib.sha256).digest()
    out, block, counter = b"", b"", 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        out += block
        counter += 1
    return out[:length]


def _check_kind(kind):
    if kind not in KINDS:
        raise VolumeError("bad_kind", f"unknown volume kind (use one of {', '.join(KINDS)})", 404)
    return kind


def derive_key(master, owner_tok, kind):
    """The LUKS passphrase for one owner's volume of one kind."""
    _check_kind(kind)
    return hkdf_sha256(master, info=f"orch-volume:{owner_tok}:{kind}".encode("utf-8"), length=KEY_BYTES)


def load_master_key(path):
    """Read the master key file, refusing anything another user could read or swap.

    Error messages name the variable and the problem, never the content.
    """
    if not path:
        raise VolumeError("volume_key_missing", "ORCH_VOLUME_MASTER_KEY_FILE is not set", 503)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise VolumeError("volume_key_missing", "ORCH_VOLUME_MASTER_KEY_FILE does not exist", 503) from None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise VolumeError("volume_key_insecure", "ORCH_VOLUME_MASTER_KEY_FILE must be a regular file, not a link", 503)
    if st.st_mode & 0o077:
        raise VolumeError("volume_key_insecure", "ORCH_VOLUME_MASTER_KEY_FILE must be mode 0600 (owner only)", 503)
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as fh:
        data = fh.read()
    if len(data) < MIN_MASTER_BYTES:
        raise VolumeError("volume_key_insecure",
                          f"ORCH_VOLUME_MASTER_KEY_FILE must hold at least {MIN_MASTER_BYTES} bytes", 503)
    return data


def mapper_name(owner_tok, kind):
    """Device-mapper name: a digest, so /dev/mapper does not list owners by name."""
    digest = hashlib.sha256(f"{owner_tok}:{kind}".encode("utf-8")).hexdigest()[:20]
    return f"orchv-{digest}"


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def subprocess_runner(argv, input=None):
    """The real runner. `input` (bytes) goes to stdin; output is captured as text."""
    proc = subprocess.run(list(argv), input=input, capture_output=True, timeout=300)
    out = proc.stdout.decode("utf-8", "replace") if isinstance(proc.stdout, bytes) else proc.stdout
    err = proc.stderr.decode("utf-8", "replace") if isinstance(proc.stderr, bytes) else proc.stderr
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)


def luks_format_argv(target):
    return ["cryptsetup", "luksFormat", "--type", "luks2", "--batch-mode",
            "--pbkdf", "pbkdf2", "--pbkdf-force-iterations", "1000",
            "--key-file=-", target]


def luks_open_argv(device, name):
    return ["cryptsetup", "open", "--type", "luks2", "--key-file=-", device, name]


def luks_test_key_argv(target):
    """Check a key against a LUKS header without activating anything (no device-mapper needed)."""
    return ["cryptsetup", "open", "--test-passphrase", "--type", "luks2", "--key-file=-", target]


def mount_namespace_problem(readlink=os.readlink):
    """Why mounts made here would be invisible to dockerd, or None.

    Docker resolves a bind-mount source in the daemon's (the host's) mount
    namespace. A service started with PrivateTmp/ProtectSystem/ProtectHome has
    a private namespace: a volume mounted there is not seen by dockerd, which
    would bind the EMPTY directory underneath and let the container write
    /data or the profile, unencrypted and unlimited, onto the host disk.
    """
    try:
        mine, host = readlink("/proc/self/ns/mnt"), readlink("/proc/1/ns/mnt")
    except OSError as exc:
        return (f"cannot compare mount namespaces ({type(exc).__name__}); per-owner volumes need root "
                "in the host mount namespace")
    if mine != host:
        return ("the orchestrator runs in a private mount namespace (systemd PrivateTmp/ProtectSystem/"
                "ProtectHome?); volumes it mounts would be invisible to dockerd")
    return None


def _mapper_device_exists(name):
    return os.path.exists(f"/dev/mapper/{name}")


# --------------------------------------------------------------------------
# The manager
# --------------------------------------------------------------------------

class VolumeManager:
    """Create, open, close, delete and measure per-owner volumes under one root.

    `store_get/store_put/store_delete` persist a small registry (size, open
    state, last usage, the alert level reached) keyed `<token>:<kind>`; it holds
    no owner ids and no key material. `lease_check(token, kind)` answers "is
    this volume leased right now?"; without one, delete refuses (fail closed).
    """

    def __init__(self, root, master_key_file, *, phone_quota_gb=8.0, browser_quota_gb=1.0, quota_file="",
                 host_min_free_gb=10.0, runner=None, is_mounted=None, mapper_exists=None, statvfs=None,
                 logger=None, store_get=None, store_put=None, store_delete=None, lease_check=None):
        self.root = os.path.abspath(root) if root else ""
        self.master_key_file = master_key_file
        self.defaults = {KIND_PHONE: float(phone_quota_gb), KIND_BROWSER: float(browser_quota_gb)}
        self.quota_file = quota_file or ""
        self.host_min_free_gb = float(host_min_free_gb)
        self.runner = runner or subprocess_runner
        self.is_mounted = is_mounted or os.path.ismount
        self.mapper_exists = mapper_exists or _mapper_device_exists
        self.statvfs = statvfs or os.statvfs
        self.log = logger or logging.getLogger("orchestrator.volumes")
        mem = {}
        self.store_get = store_get or mem.get
        self.store_put = store_put or mem.__setitem__
        self.store_delete = store_delete or (lambda k: mem.pop(k, None))
        self.lease_check = lease_check
        self._lock = threading.RLock()

    def __repr__(self):  # never show the key file's content (it is not held here anyway)
        return f"<VolumeManager root={self.root!r}>"

    # -- paths ----------------------------------------------------------------
    def _token(self, owner):
        tok = bp.owner_token(owner)
        if not tok:
            raise VolumeError("owner_required", "a volume needs an owner", 400)
        return tok

    def paths(self, owner, kind):
        return self.paths_for_token(self._token(owner), kind)

    def paths_for_token(self, tok, kind):
        _check_kind(kind)
        if not self.root:
            raise VolumeError("volume_root_required", "ORCH_VOLUME_ROOT is not set", 500)
        tok = str(tok or "")
        if not tok or len(tok) > 64 or tok[0] in ".-" or any(c not in _TOKEN_OK for c in tok):
            raise VolumeError("bad_owner", "owner does not map to a directory under the volume root", 400)
        owner_dir = os.path.join(self.root, tok)
        if os.path.dirname(owner_dir) != self.root:
            raise VolumeError("bad_owner", "owner does not map to a directory under the volume root", 400)
        return {
            "token": tok,
            "kind": kind,
            "dir": owner_dir,
            "image": os.path.join(owner_dir, f"{kind}.img"),
            "mount": os.path.join(owner_dir, f"{kind}.mnt"),
            "mapper": mapper_name(tok, kind),
        }

    @staticmethod
    def _private_dir(path):
        """Create `path` 0700, or check it is a plain directory (not a link) and tighten it."""
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            os.mkdir(path, 0o700)
            os.chmod(path, 0o700)  # mkdir honours umask; be explicit
            return
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise VolumeError("volume_path_unsafe", "a volume path is a link or not a directory", 500)
        if st.st_mode & 0o077:
            os.chmod(path, 0o700)

    def _prepare_dirs(self, p):
        parent = os.path.dirname(self.root)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, mode=0o700, exist_ok=True)
        self._private_dir(self.root)
        self._private_dir(p["dir"])
        if os.path.realpath(p["dir"]) != os.path.join(os.path.realpath(self.root), p["token"]):
            raise VolumeError("volume_path_unsafe", "owner directory resolves outside the volume root", 500)

    @staticmethod
    def _image_state(path):
        """None if absent; otherwise the lstat, after refusing links and hard links."""
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            raise VolumeError("volume_path_unsafe", "volume image is a link or not a regular file", 500)
        if st.st_nlink != 1:
            raise VolumeError("volume_path_unsafe", "volume image has another name (hard link)", 500)
        return st

    # -- quotas ---------------------------------------------------------------
    def _quota_overrides(self):
        if not self.quota_file:
            return {}
        try:
            with open(self.quota_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                raise ValueError("top level must be an object")
            return data
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            self.log.warning("ORCH_VOLUME_QUOTA_FILE unreadable (%s); using default quotas", type(exc).__name__)
            return {}

    def quota_bytes_for_token(self, tok, kind):
        _check_kind(kind)
        gb = self.defaults[kind]
        for key, entry in self._quota_overrides().items():
            if key != tok and bp.owner_token(key) != tok:
                continue
            if not isinstance(entry, dict) or kind not in entry:
                continue
            value = entry[kind]
            if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value <= 4096:
                gb = float(value)
            else:
                self.log.warning("ORCH_VOLUME_QUOTA_FILE: bad %s quota for owner token %s; using default", kind, tok)
        return int(gb * GIB)

    def quota_bytes(self, owner, kind):
        return self.quota_bytes_for_token(self._token(owner), kind)

    # -- host guard -------------------------------------------------------------
    def _committed_bytes(self):
        """Bytes existing sparse images may still claim: apparent size minus allocated."""
        total = 0
        try:
            owners = os.listdir(self.root)
        except OSError:
            return 0
        for name in owners:
            d = os.path.join(self.root, name)
            if os.path.islink(d) or not os.path.isdir(d):
                continue
            for kind in KINDS:
                try:
                    st = os.lstat(os.path.join(d, f"{kind}.img"))
                except OSError:
                    continue
                if stat.S_ISREG(st.st_mode):
                    total += max(0, st.st_size - st.st_blocks * 512)
        return total

    def _host_free_bytes(self):
        target = self.root if os.path.isdir(self.root) else os.path.dirname(self.root) or "/"
        sv = self.statvfs(target)
        return sv.f_bavail * sv.f_frsize

    def host_status(self):
        free = self._host_free_bytes()
        uncommitted = free - self._committed_bytes()
        return {"host_free_gb": round(free / GIB, 2), "host_uncommitted_gb": round(uncommitted / GIB, 2),
                "min_free_gb": self.host_min_free_gb}

    def check_host_capacity(self, new_bytes):
        after = self._host_free_bytes() - self._committed_bytes() - int(new_bytes)
        if after < self.host_min_free_gb * GIB:
            self.log.warning("refusing a new %.1f GiB volume: host would have %.1f GiB uncommitted "
                             "(ORCH_HOST_MIN_FREE_GB=%s)", new_bytes / GIB, after / GIB, self.host_min_free_gb)
            raise VolumeError("host_disk_low", "the host is low on disk space; no new volumes until it is freed", 507)

    # -- registry ---------------------------------------------------------------
    def _rec(self, tok, kind):
        return dict(self.store_get(f"{tok}:{kind}") or {})

    def _save(self, tok, kind, rec):
        rec.update({"owner_token": tok, "kind": kind})
        self.store_put(f"{tok}:{kind}", rec)

    # -- commands -------------------------------------------------------------
    def _run(self, argv, key=None, check=True):
        res = self.runner(argv, input=key)
        if check and res.returncode != 0:
            # stderr from these tools never echoes stdin; still, keep it short.
            detail = (res.stderr or "").strip().splitlines()[-1:] or [""]
            raise VolumeError("volume_command_failed",
                              f"{argv[0]} {argv[1] if len(argv) > 1 else ''} failed (exit {res.returncode}): "
                              f"{detail[0][:200]}", 500)
        return res

    def _loops_for(self, image):
        res = self._run(["losetup", "-j", image], check=False)
        loops = []
        for line in (res.stdout or "").splitlines():
            dev = line.split(":", 1)[0].strip()
            if dev.startswith("/dev/loop"):
                loops.append(dev)
        return loops

    def _attach_loop(self, image):
        existing = self._loops_for(image)
        if existing:
            return existing[0]
        res = self._run(["losetup", "--find", "--show", image])
        dev = (res.stdout or "").strip().splitlines()[-1:]
        if not dev or not dev[0].startswith("/dev/loop"):
            raise VolumeError("volume_command_failed", "losetup did not name a loop device", 500)
        return dev[0]

    def _teardown(self, p):
        """umount, luksClose, detach every loop on the image. Best effort, in that order."""
        if self.is_mounted(p["mount"]):
            self._run(["umount", p["mount"]])
        if self.mapper_exists(p["mapper"]):
            self._run(["cryptsetup", "close", p["mapper"]])
        if os.path.lexists(p["image"]):
            for dev in self._loops_for(p["image"]):
                self._run(["losetup", "-d", dev], check=False)

    @staticmethod
    def _create_image(path, size):
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        try:
            os.fchmod(fd, 0o600)
            os.ftruncate(fd, int(size))  # sparse: blocks are allocated as the owner writes
        finally:
            os.close(fd)

    def _master(self):
        return load_master_key(self.master_key_file)

    # -- lifecycle ------------------------------------------------------------
    def open_volume(self, owner, kind):
        return self.open_token(self._token(owner), kind)

    def open_token(self, tok, kind):
        """Mount the owner's volume (creating it on first use) and return where.

        Idempotent: an already-mounted volume is returned without re-mounting,
        but only after this owner's key is checked against its header again, so
        even a mis-routed path cannot hand an open volume to another owner.
        """
        p = self.paths_for_token(tok, kind)
        with self._lock:
            self._prepare_dirs(p)
            st = self._image_state(p["image"])
            if os.path.lexists(p["mount"]):
                self._private_dir(p["mount"])
            master = self._master()
            if st is not None and self.is_mounted(p["mount"]):
                key = derive_key(master, tok, kind)
                try:
                    res = self._run(luks_test_key_argv(p["image"]), key=key, check=False)
                finally:
                    del key, master
                if res.returncode != 0:
                    self.log.warning("refused an open volume: key mismatch token=%s kind=%s", tok, kind)
                    raise VolumeError("volume_key_mismatch", "this volume does not belong to the requesting owner", 403)
                return self._opened(p, created=False, size=st.st_size)
            created = st is None
            if created:
                size = self.quota_bytes_for_token(tok, kind)
                self.check_host_capacity(size)
                self._create_image(p["image"], size)
            else:
                size = st.st_size
            key = derive_key(master, tok, kind)
            try:
                if created:
                    self._run(luks_format_argv(p["image"]), key=key)
                loop = self._attach_loop(p["image"])
                if not self.mapper_exists(p["mapper"]):
                    self._run(luks_open_argv(loop, p["mapper"]), key=key)
                if created:
                    self._run(["mkfs.ext4", "-q", "-m", "0", "-L", kind[:16], f"/dev/mapper/{p['mapper']}"])
                self._private_dir(p["mount"])
                self._run(["mount", "-t", "ext4", "-o", MOUNT_OPTIONS[kind],
                           f"/dev/mapper/{p['mapper']}", p["mount"]])
                os.chmod(p["mount"], 0o700)  # the filesystem's root, now that it is mounted
            except Exception:
                try:
                    self._teardown(p)
                except Exception:  # noqa: BLE001 - report the first failure, not the cleanup's
                    self.log.exception("cleanup after a failed open left something behind kind=%s token=%s",
                                       kind, tok)
                if created:
                    try:
                        os.unlink(p["image"])
                    except OSError:
                        pass
                raise
            finally:
                del key, master
            if created:
                self.log.info("volume created token=%s kind=%s size_gb=%.2f", tok, kind, size / GIB)
            return self._opened(p, created=created, size=size)

    def _opened(self, p, *, created, size):
        rec = self._rec(p["token"], p["kind"])
        rec.setdefault("created_at", time.time())
        rec.update({"size_bytes": int(size), "open": True, "opened_at": time.time()})
        self._save(p["token"], p["kind"], rec)
        return {"token": p["token"], "kind": p["kind"], "mount": p["mount"], "image": p["image"],
                "mapper": p["mapper"], "size_bytes": int(size), "created": created}

    def close_volume(self, owner, kind):
        return self.close_token(self._token(owner), kind)

    def close_token(self, tok, kind):
        """Unmount and lock the volume. Keeps the image. True if anything was open."""
        p = self.paths_for_token(tok, kind)
        with self._lock:
            was_open = self.is_mounted(p["mount"]) or self.mapper_exists(p["mapper"])
            if self.is_mounted(p["mount"]):
                try:
                    self._usage(p)  # keep the last reading for the usage endpoint
                except OSError:
                    pass
            self._teardown(p)
            rec = self._rec(tok, kind)
            if rec:
                rec.update({"open": False, "closed_at": time.time()})
                self._save(tok, kind, rec)
            return was_open

    def delete_volume(self, owner, kind):
        return self.delete_token(self._token(owner), kind)

    def delete_token(self, tok, kind):
        """Destroy the owner's volume. Refused while leased; explicit call only."""
        p = self.paths_for_token(tok, kind)
        if self.lease_check is None or self.lease_check(tok, kind):
            raise VolumeError("lease_active", "release the lease before deleting its volume", 409)
        with self._lock:
            self._teardown(p)
            st = self._image_state(p["image"])
            existed = st is not None
            if existed:
                # Wipe the keyslots first: the bytes left on disk are then
                # unrecoverable even with the master key.
                self._run(["cryptsetup", "luksErase", "--batch-mode", p["image"]], check=False)
                os.unlink(p["image"])
            for path in (p["mount"], p["dir"]):
                try:
                    if os.path.isdir(path) and not os.path.islink(path):
                        os.rmdir(path)
                except OSError:
                    pass  # not empty: the other kind's volume is still there
            self.store_delete(f"{tok}:{kind}")
            if existed:
                self.log.warning("volume deleted token=%s kind=%s", tok, kind)
            return existed

    # -- usage ----------------------------------------------------------------
    def _usage(self, p):
        sv = self.statvfs(p["mount"])
        size = sv.f_blocks * sv.f_frsize
        used = (sv.f_blocks - sv.f_bfree) * sv.f_frsize
        pct = (100.0 * used / size) if size else 0.0
        rec = self._rec(p["token"], p["kind"])
        rec.update({"last_used_bytes": int(used), "last_fs_bytes": int(size), "last_sampled_at": time.time()})
        self._alert(p, rec, pct)
        self._save(p["token"], p["kind"], rec)
        return used, size, pct

    def _alert(self, p, rec, pct):
        reached = max((lvl for lvl in WARN_LEVELS if pct >= lvl), default=0)
        warned = int(rec.get("warned_pct") or 0)
        if reached > warned:
            self.log.warning("volume usage crossed %d%% token=%s kind=%s used_pct=%.1f",
                             reached, p["token"], p["kind"], pct)
        if reached != warned:
            rec["warned_pct"] = reached  # dropping back re-arms the lower alert

    def usage(self, owner, kind):
        return self.usage_token(self._token(owner), kind)

    def usage_token(self, tok, kind):
        p = self.paths_for_token(tok, kind)
        with self._lock:
            st = self._image_state(p["image"])
            limit = st.st_size if st is not None else self.quota_bytes_for_token(tok, kind)
            out = {"kind": kind, "exists": st is not None, "open": False, "limit_bytes": int(limit),
                   "size_bytes": int(limit), "used_bytes": None, "used_pct": None}
            if st is None:
                return out
            if self.is_mounted(p["mount"]):
                used, fs_size, pct = self._usage(p)
                out.update({"open": True, "used_bytes": int(used), "used_pct": round(pct, 2)})
            else:
                rec = self._rec(tok, kind)
                if rec.get("last_used_bytes") is not None and rec.get("last_fs_bytes"):
                    out.update({"used_bytes": rec["last_used_bytes"],
                                "used_pct": round(100.0 * rec["last_used_bytes"] / rec["last_fs_bytes"], 2),
                                "sampled_at": rec.get("last_sampled_at")})
            return out

    def sample_open(self):
        """Measure every mounted volume (fires the 80/95 % alerts). Returns the readings."""
        out = []
        try:
            owners = sorted(os.listdir(self.root))
        except OSError:
            return out
        for tok in owners:
            for kind in KINDS:
                try:
                    p = self.paths_for_token(tok, kind)
                except VolumeError:
                    continue
                if self.is_mounted(p["mount"]):
                    try:
                        out.append(self.usage_token(tok, kind))
                    except (OSError, VolumeError):
                        self.log.exception("usage sample failed kind=%s", kind)
        return out
