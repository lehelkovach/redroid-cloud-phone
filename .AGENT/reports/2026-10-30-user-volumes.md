# Per-owner, size-limited, encrypted volumes for phones and browsers

- **Date:** 2026-10-30
- **Asked by:** the owner (founder requirements, relayed by a coordinating agent session)
- **Question:** Can each user's phone storage and browser profile be bound to the user, size-limited, encrypted with a per-user key, and never handed to another user?
- **Scope:** `orchestrator/user_volumes.py` (new), `orchestrator/server.py`, `orchestrator/state_store.py`, `scripts/redroid-up.sh`, `docker/redroid*-compose.yml`, tests, docs. Branch `user-volumes`. No live host touched.
- **Method:** wrote tests first against a fake host (mocked losetup/cryptsetup/mkfs/mount), then the code; ran the full offline CI as `.github/workflows/ci.yml` does; ran the opt-in real-kernel test in this sandbox.
- **Verdict:** Yes, behind two flags that default off: the logic is built and tested offline (73 new tests, two-owner gate extended, negative controls), and on a real kernel the LUKS2 key separation and the ext4 size limit were shown. A full LUKS open/mount was not possible here (no device-mapper). Enabling it on the shipped systemd unit is blocked until a privileged helper exists (see "Deployment finding").
- **Follow-up:** run `ORCH_REAL_VOLUME_TEST=1` and a Redroid lease on a host with dm_crypt; build the privileged helper; ROADMAP "Other planned work".

## What was built

`orchestrator/user_volumes.py`, `VolumeManager`:

| Function | Does |
|---|---|
| `paths()` / `paths_for_token()` | `ORCH_VOLUME_ROOT/<owner token>/<kind>.img` and `.mnt`; mapper `orchv-<20 hex>` (a digest, no owner name). Rejects bad tokens, unknown kinds, symlinked root/owner dir/mount, symlinked or hard-linked images. |
| `open_volume()` / `open_token()` | First use: host guard, sparse image (`O_EXCL|O_NOFOLLOW`, 0600, `ftruncate`), `cryptsetup luksFormat --type luks2 --pbkdf pbkdf2 --pbkdf-force-iterations 1000 --key-file=- <img>`, `losetup --find --show`, `cryptsetup open --key-file=-`, `mkfs.ext4 -q -m 0`, `mount -t ext4 -o nodev,nosuid[,noexec]`. Later: loop + open + mount. Already mounted: no remount, but the owner's key is re-checked with `cryptsetup open --test-passphrase`. Any failure tears down; a failed *create* removes the image, a failed *open* never does. |
| `close_volume()` | Samples usage, `umount`, `cryptsetup close`, `losetup -d` for every loop on the image. Keeps the image. |
| `delete_volume()` | Refused while leased (and refused if no lease check is wired: fail closed); closes, `cryptsetup luksErase`, unlinks, removes empty dirs, drops the registry row. |
| `usage()` / `sample_open()` | statvfs on the mount; 80 %/95 % warnings once each, re-armed when usage drops. |
| `check_host_capacity()` / `host_status()` | free space minus the unallocated part of every existing image minus the new image must stay above `ORCH_HOST_MIN_FREE_GB`, else `507 host_disk_low`. |
| `derive_key()` / `hkdf_sha256()` / `load_master_key()` | RFC 5869 HKDF-SHA256 (stdlib; checked against RFC test case 1), info `orch-volume:<token>:<kind>`, 64-byte key. Master file must be a regular file, not a link, mode 0600, at least 32 bytes. |
| `mount_namespace_problem()` | Detects a private mount namespace (see below). |

All privileged commands go through one `runner(argv, input=None)`. Keys travel only as `input` (stdin).

`orchestrator/server.py`:

- Flags `ORCH_PHONE_DATA` (`instance`|`per_user`), `ORCH_BROWSER_PROFILE` (`dir`|`volume`) and `ORCH_VOLUME_ROOT`, `ORCH_VOLUME_MASTER_KEY_FILE`, `ORCH_QUOTA_PHONE_GB`, `ORCH_QUOTA_BROWSER_GB`, `ORCH_VOLUME_QUOTA_FILE`, `ORCH_HOST_MIN_FREE_GB`, `ORCH_VOLUME_SAMPLE_SECONDS`.
- Phones (`_acquire_per_user_instance`, `_attach_phone_data`, `_detach_phone_data`, `_reap_phone_volumes`): the volume is opened only after a slot is known to exist; a new Redroid container is provisioned with `redroid-up.sh --data-dir <mount>`; an idle instance is stopped (`--down`, which also drops its per-instance volume) and restarted on the new owner's volume; switching to another owner's volume calls `record_reset`; the tenancy guard (`lease_decision`) still runs, plus an explicit check that `/data` really is this owner's attached volume. Release and expiry stop the container and close the volume. With the flag off, an instance that last ran on a per-user volume is refused. Raw `POST /instances/<id>/lease` is refused while the flag is on.
- Browsers: the profile directory is the mounted `browser-profile` volume; `_stop_browser` (release and expiry) closes it; a failed Chrome start closes it.
- Endpoints `GET /volumes/<owner>`, `DELETE /volumes/<owner>/<kind>`; `DELETE /profiles/<owner>` deletes the volume in volume mode; `/health.volumes`; `volume_config_refusal()` at start; a sampler thread.
- State DB namespace `volumes`: size, open state, last usage, alert level per `<token>:<kind>`. No owner ids, no keys.

## Tests

| Suite | Tests | What |
|---|---|---|
| `tests/test_user_volumes.py` (`user-volumes`) | 64 | Key derivation (RFC vector, stable, differs per owner and kind), master-key file checks, path/traversal/symlink/hard-link refusals, exact create/open/close/delete command sequences, key on stdin never in argv, A's volume never opened with B's key, mis-routed paths refused by the key (open and closed), failed-create cleanup, delete refused while leased, quotas from defaults/env/file (and a broken file), host guard incl. sparse accounting, 80/95 % alerts once, flags-off = old behaviour, per-user phone lease/release/expiry/next-owner/second-check/rollback/raw-lease/507/missing key/restart/redroid argv, browser volume lease/expiry/delete/507/failed start, usage endpoint scoped and authenticated, `/health` without owners or key path, start-up refusals incl. mount namespace. |
| `tests/test_isolation_gate.py` (`isolation-gate`) | +5 | `PerUserVolumeGate`: A and B mount four different volumes, each opened with its own owner's key; after a restart from the state DB every lease maps to the same volume and renewals touch nothing; new owner C gets freshly formatted volumes and nothing of A's is opened. `GateCatchesASharedVolume`: the detector fires on a manager that maps everyone to one volume; a manager that only mis-routes paths is stopped by the key (403, then 500). |
| `tests/test_user_volumes_real.py` (`user-volumes-real`) | 3 | Opt-in (`ORCH_REAL_VOLUME_TEST=1`), root. See below. |
| `tests/test_scripts_contract.py` | +1 | `redroid-up.sh --data-dir` binds, rejects relative/`..`/`:` paths; both compose files take `REDROID_DATA_SOURCE`. |

The existing isolation suites (`isolation`, `isolation-gate`'s original 22, and the pytest-only `tests/test_tenant_isolation.py`, 8/8) pass unchanged.

## CI (run here exactly as `.github/workflows/ci.yml`)

`bash -n` on every script: clean. `./scripts/run-tests.sh --coverage --fail-under 60 --verbose` (Python 3.11 venv; the new and isolation suites also pass on Python 3.12, which CI uses):

| | Before (origin/master 90ddf88) | After |
|---|---|---|
| Suites | 20 passed | 22 passed, 0 failed |
| unittest tests | 339 | 412 |
| Total coverage (`api/*`, `orchestrator/*`) | 76 % | 79 % (floor 60) |
| `orchestrator/server.py` | 74 % | 78 % |
| `orchestrator/user_volumes.py` | — | 92 % |

## Real-kernel test in this sandbox

Root, `/dev/loop*` present, `cryptsetup` 2.7.0 installed for the run (`cryptsetup-bin`); the kernel has no device-mapper (`/proc/misc` has no `device-mapper`, `cryptsetup open` fails with "Cannot initialize device-mapper").

- `RealLuksHeader`: **ran, passed.** A 64 MiB sparse image formatted LUKS2 with owner A's derived key through the module's own `luksFormat` argv; `--test-passphrase` accepts A's phone-data key and rejects owner B's key and A's browser-profile key; the image stays sparse.
- `RealExt4Limit`: **ran, passed.** ext4 on a 64 MiB loop-mounted image with the browser mount options: writing past it fails with `ENOSPC`; the host file never exceeds 64 MiB. This is the limit mechanism the encrypted volume relies on, shown without LUKS.
- `RealFullVolume` (manager create, mount, ENOSPC through LUKS, close, reopen with data kept, B's key refused, delete): **skipped**, no device-mapper. Unverified on a real kernel.

## Deployment finding: the shipped unit cannot run this yet

`systemd/cloud-phone-orchestrator.service` runs as `cloudphone` with `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome` and `PrivateTmp`. Two consequences:

1. losetup, cryptsetup and mount need root; they fail there.
2. The service gets a private mount namespace. A volume mounted inside it is invisible to dockerd, which resolves bind sources in the host namespace and would bind the empty mount-point directory beneath, so Android would write `/data` (or Chrome its profile) unencrypted and unlimited onto the host disk.

Point 2 is now a start-up refusal: with a flag on and a container runtime in use (`redroid` phones or `docker` browsers), the orchestrator exits unless `/proc/self/ns/mnt` equals `/proc/1/ns/mnt` (and refuses when it cannot read them). The unit is unchanged; flags stay off. Intended fix, not built: a narrow privileged helper (the `runner` seam is where it plugs in) running in the host namespace.

## What it means for dropping `privileged`

Redroid containers run `privileged: true`, which exposes every host device node, including the decrypted `/dev/dm-*` of every *other* owner whose volume is open at that moment. Per-owner keys protect volumes at rest and while closed; between volumes open at the same time on one host, isolation still rests on the container boundary, which `privileged` largely removes. So per-user volumes raise the value of the separate experiment to drop `privileged` (or at least restrict the device cgroup), and the bind mount of one directory as `/data` is the shape that experiment needs. Closing volumes on release keeps the exposure to concurrently leased owners only.

## Not done, said plainly

- No real Redroid container was started on a per-user volume; Android booting on a bind-mounted ext4 `/data` with `nodev,nosuid` is unverified.
- Full LUKS open/mount/ENOSPC on a real kernel: skipped here (no dm).
- Raising a quota does not grow an existing image; profiles under `ORCH_PROFILE_ROOT` are not migrated.
- The master key is one secret on the host; whoever reads it derives every owner's key. No key rotation.
- The privileged helper, and running enabled under the shipped systemd unit.
- Concurrent acquires for the same owner are not serialised (pre-existing for phones).
