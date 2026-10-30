# Multi-user isolation: browsers, phones, tickets and the relay

Two owners must never share a browser, a profile, a proxy exit or a phone, and
neither may reach the other's container. This page explains how the
orchestrator enforces that, how to run it, and what it does not do.

## The model

The orchestrator leases **phones** (Redroid automation, Cuttlefish camera) and
**browsers** (the `chrome` pool) per owner, and it is the **router** for every
command and byte between an OSLO session and its container:

```
OSLO session ──ticket──▶ orchestrator relay ──▶ that owner's browser / phone
                         (owner + lease check,       (loopback / private net,
                          rate limit, audit,          never published)
                          kill switch)
```

- A lease is refused across owners, never silently reassigned. A full pool
  answers `409 at_capacity`; it never evicts somebody else.
- Every lease has an explicit `expires_at`. An expired lease is reaped: its
  browser stops and its tickets die. Its profile stays.
- Releasing a browser stops it and keeps the profile, so the owner's sessions
  survive. Deleting a profile is its own call (`DELETE /profiles/<owner>`),
  needs `{"confirm": "<owner>"}`, and is refused while the owner holds a lease.
- Callers never see a container address. `POST /sessions` returns a
  **ticket** and relay URLs; the internal DevTools port and URL stay inside.

## Tickets

A ticket (`ort_…`) is a short-lived bearer bound to one owner and one lease.

- Minted on `POST /sessions` and `POST /tickets` (orchestrator token required).
  `POST /tickets` only mints for the caller's **own** current lease; naming
  another owner's `lease_id` is `403`.
- Lifetime `ORCH_TICKET_TTL_SECONDS` (default 300, max 3600), never longer
  than the lease.
- Revocable: `POST /tickets/revoke {ticket | ticket_id}`. Releasing a lease
  revokes all its tickets. `POST /sessions/<owner>/kill` revokes them and drops
  every open stream for that lease at once (the kill switch).
- Stored as SHA-256 digests, so the state database never holds a usable
  bearer, and a revocation survives a restart.
- Presented as `X-Orch-Ticket: <ticket>`, or `Authorization: Bearer <ticket>`,
  or `?ticket=` for browser WebSockets that cannot set headers (the Watch
  page). The access log redacts `ticket=` values.

## The relay

| Route | For | Notes |
|---|---|---|
| `WS /relay/cdp/<lease_id>` | the owner's browser (DevTools) | Playwright `connectOverCDP(url, {headers: {"X-Orch-Ticket": t}})`. The Watch screencast travels on this socket. The path's lease must equal the ticket's. |
| `GET/POST/PUT/DELETE /relay/phone/<path>` | the owner's phone Control API | `<path>` is the Control API path (`device/input`, `device/screenshot/base64`, `ui/command`, …). `config` and `..` are refused. |
| `WS /relay/phone/stream` | the phone's screen and input (Watch / take-over) | Frames out at `ORCH_PHONE_FPS`; `{type, x, y, text, keycode, …}` input events in. |

On every message the relay checks the ticket is live and the lease is still
held by the ticket's owner, applies the per-lease rate limit
(`ORCH_RELAY_RATE`/`ORCH_RELAY_BURST`; a CDP command over the limit gets a
CDP error reply, an HTTP call gets `429`), and writes one audit line per
command (`AUD` label):

```
relay owner=<owner token> lease=<lease id> kind=cdp verb=Page.navigate ms=1.84 ok=true
```

Never payloads, URLs, cookies or keystrokes. The owner appears as its owner
token (`<slug>-<8 hex>`), the same token OSLO uses for cookie jars.

The container is resolved from the ticket only. An `instance_id`, `api_url`,
`adb_connect` or `X-Cloud-Phone-Adb` in a relayed request is ignored, and the
caller's headers are never forwarded.

### Containers accept the orchestrator only

- **Browsers**: DevTools listens on `127.0.0.1:<port>` on the orchestrator
  host (`local` runtime) or is published as `-p 127.0.0.1:<port>:9222`
  (`docker` runtime). Nothing off the host can reach it, and only the
  orchestrator holds the address.
- **Phones**: the Control API already requires `API_TOKEN`, which only the
  orchestrator holds (`ORCH_CONTROL_API_TOKEN`). Put phones on a private
  network and allow TCP 8080 from the orchestrator host only (OCI security
  list / NSG). Then set `ORCH_REQUIRE_TICKETS=1` so the old unticketed
  `/phones/<id>/*` routes (which route by a caller-supplied instance id) answer
  `403`. That flag is off by default because OSLO's current direct route uses
  them.

### Latency (measured 2026-10-30, loopback, `scripts/relay-latency.py`)

| Path | Direct p50 | Relayed p50 | Added |
|---|---|---|---|
| CDP round trip, real Chromium (`Browser.getVersion`, n=300) | 0.16 ms | 0.25 ms | +0.09 ms |
| CDP round trip, fake DevTools (n=300) | 0.11 ms | 0.34 ms | +0.23 ms |
| Browser screen frame, real Chromium (`Page.captureScreenshot`, n=30) | 36.0 ms | 35.0 ms | within noise |
| Phone frame over HTTP relay (n=300) | 2.3 ms | 5.2 ms | +2.9 ms |

A pure relay costs well under a millisecond per CDP message on one host; the
screenshot itself dominates a Watch frame. The HTTP phone path pays one extra
loopback HTTP request per call. Across hosts, add one network hop (OSLO to
orchestrator) to every message. If that ever makes CDP chatter too slow, the
later option is a short-lived signed **direct** ticket the browser container
verifies itself; it is not built.

## Browsers (the `chrome` pool)

`POST /sessions {"owner_user_id": "...", "purpose": "browser", "ttl_seconds": 3600, "proxy": {"id": "...", "upstream": "http://user:pass@host:port"}}`
(`purpose` may be `browser`, `web`, `chrome`; or `runtime: "chrome"`).

Each lease gets:

- **A profile directory** `ORCH_PROFILE_ROOT/<owner token>`, mode 0700 (the
  root too), created on first lease and reused on every later one. A symlinked
  or non-directory profile is refused. Only this directory is mounted into the
  owner's container.
- **An internal DevTools port** from `ORCH_CHROME_CDP_PORT_BASE`, never
  returned.
- **A proxy id and upstream** from the request (on the OSLO side, from the
  owner's own vault or connector). No upstream means `egress: "direct"`. An
  upstream with credentials gets a **per-lease authenticating relay** on
  `ORCH_CHROME_RELAY_BIND` (Chrome cannot send proxy credentials itself); the
  credential lives in memory only and dies with the lease. The database stores
  the upstream with the userinfo removed. Asking to change a lease's proxy is
  refused; release first (cookies travel with the exit that earned them).

Runtimes (`ORCH_CHROME_RUNTIME`): `mock` (no process; default with
`ORCH_DEPLOY_MODE=mock`), `local` (one Chromium per lease, `ORCH_CHROME_BIN`),
`docker` (one container per lease from `ORCH_CHROME_IMAGE` on
`ORCH_CHROME_NETWORK`, `--cap-drop ALL`, `no-new-privileges`).

## Persistence

`ORCH_STATE_DB` names a SQLite file (0600). Instances, phone leases and
sessions, tenancy records, browser sessions and ticket digests are written
through on every change and reloaded at start, so after a restart every lease
maps to the same owner, revoked tickets stay revoked and the profile
directories are found again. Unset, state is in memory (the old behaviour) and
the orchestrator logs a warning. Proxy credentials and bearer tokens are never
written: after a restart an owner's next acquire supplies its upstream again.
Operations and procedure results are still in memory.

## Start-up policy and the systemd unit

- `ORCH_HOST` defaults to `127.0.0.1`. A wildcard (`0.0.0.0`, `::`) or public
  address is refused unless `ORCH_ALLOW_PUBLIC_BIND=1`. Private addresses
  (10/8, 172.16/12, 192.168/16, 100.64/10, link-local, fc00::/7) are accepted.
- No `ORCH_API_TOKEN` means the process exits with status 2, unless it binds
  loopback and `ORCH_ALLOW_NO_TOKEN=1` (local development).
- `systemd/cloud-phone-orchestrator.service` runs it as `cloudphone`, loopback,
  `ORCH_STATE_DB` and `ORCH_PROFILE_ROOT` under
  `/var/lib/cloud-phone-orchestrator` (0700), the token from
  `/etc/cloud-phone/orchestrator.env` (template:
  `config/orchestrator.env.example`). Installing it is a manual step.

**Upgrade note.** The old default bind was `0.0.0.0` with no token required.
An orchestrator started the old way (`./cloud-phone orchestrator-run` with no
`ORCH_HOST`/`ORCH_API_TOKEN`) now refuses to start. Set `ORCH_API_TOKEN` and
`ORCH_HOST` (the host's private address) before upgrading a running one.

## Encryption at rest for profiles

`ORCH_PROFILE_ENCRYPTION` makes the orchestrator **verify** encryption before
it creates or reuses a profile, and refuse (`503 profile_not_encrypted`) if
the check fails:

| Mode | Set up by the operator | Checked | Protects against | Does not protect against |
|---|---|---|---|---|
| `none` (default) | nothing | nothing (reported in `/health`) | — | anything at rest |
| `luks` | put `ORCH_PROFILE_ROOT` on a LUKS (dm-crypt) volume, e.g. a block volume formatted with `cryptsetup luksFormat`, opened at boot | the mount holding the root is a `dm-*` device whose `dm/uuid` starts with `CRYPT-` | a stolen disk, a detached block volume, a boot-volume snapshot | anyone on the running host with the service's or root's rights (the volume is open) |
| `fscrypt` | `fscrypt encrypt` the profile root; subdirectories inherit the policy | `fscrypt status <root>` says it is encrypted | as LUKS, per directory tree, on ext4/f2fs without repartitioning | the same, while the protector is unlocked |

Not done, said plainly:

- **No per-owner keys** with these two modes: both use one key for the whole
  profile root. Per-owner keys are what `ORCH_BROWSER_PROFILE=volume` adds;
  see [Per-owner volumes](#per-owner-volumes-phone-data-and-browser-profiles).
- **Nothing is encrypted by the orchestrator itself**, and nothing is set up
  automatically. The tests exercise the checks with fake `mountinfo`, sysfs and
  `fscrypt` output; no real LUKS volume or fscrypt policy was created here.
- A compromised orchestrator host can read every open profile. Isolation here
  is between tenants of one trusted host, not against its operator.

## Per-owner volumes: phone /data and browser profiles

Off by default. Two flags move each owner's storage onto volumes that belong
to the **owner**, not to a container or an instance:

| Flag | Default | On |
|---|---|---|
| `ORCH_PHONE_DATA` | `instance` (one Docker volume per container, shared over time by whoever leases it, guarded by the tenancy check) | `per_user`: the owner's `phone-data` volume is bind-mounted as the Redroid container's `/data` for the lease |
| `ORCH_BROWSER_PROFILE` | `dir` (`ORCH_PROFILE_ROOT/<owner token>`) | `volume`: the owner's `browser-profile` volume is the Chrome profile |

Each volume (`orchestrator/user_volumes.py`) is:

- **The owner's.** `ORCH_VOLUME_ROOT/<owner token>/<kind>.img`, mounted at
  `<kind>.mnt` while leased (root, owner directory and mount point 0700, image
  0600). The owner token is the one used everywhere else; owners that would
  map outside the root, symlinked or hard-linked paths are refused.
- **Size-limited.** A sparse image of the owner's quota with ext4 inside: when
  the quota is full the owner gets `ENOSPC`, and the host file never grows past
  the quota. Defaults `ORCH_QUOTA_PHONE_GB=8`, `ORCH_QUOTA_BROWSER_GB=1`;
  per-owner overrides in `ORCH_VOLUME_QUOTA_FILE` (JSON, keyed by owner id or
  owner token, values in GiB: `{"avery-example": {"phone-data": 16}}`). An
  override applies when the image is created; growing an existing image is not
  built.
- **Encrypted with a key per owner.** LUKS2 (aes-xts, cryptsetup defaults).
  The key is HKDF-SHA256(master, info=`orch-volume:<owner token>:<kind>`), so
  every owner and every kind has its own key and owner B's key cannot open
  owner A's image. It reaches cryptsetup on stdin (`--key-file=-`), never in
  argv, the environment, the state database, a log line or `/health`. An
  already-open volume is handed out only after the requesting owner's key is
  checked against its header again.
- **Never handed to another owner.** A new owner's first lease creates a new
  image. On a phone, switching an instance's `/data` from one owner's volume to
  another's (or away from its per-instance volume) is recorded as a reset, and
  the tenancy guard still runs as a second check; if the switch did not happen,
  the lease is refused (`409 phone not clean`).
- **Opened only while leased.** Release and expiry stop the container, unmount
  the volume and close it (`umount`, `cryptsetup close`, `losetup -d`). The
  image stays. Deleting it is its own call (`DELETE /volumes/<owner>/<kind>`,
  or `DELETE /profiles/<owner>` for the browser volume), needs
  `{"confirm": "<owner>"}`, is refused while leased, and wipes the LUKS
  keyslots (`luksErase`) before removing the file.

The PBKDF is PBKDF2 at its minimum on purpose: the passphrase is a 512-bit
derived key, not something a person typed, and the argon2id default would cost
about 1 GiB of memory and a second or two on every lease.

### Host guard and usage alerts

- A new image is refused with **`507 host_disk_low`** when the free space on
  `ORCH_VOLUME_ROOT`'s filesystem, minus what every existing image may still
  claim (images count as fully allocated though they are sparse), minus the new
  image, would drop below `ORCH_HOST_MIN_FREE_GB` (default 10). Reopening an
  existing volume is not refused: its size is already counted.
- `GET /health` gains `volumes: {enabled, phone_data, browser_profile, root,
  host_free_gb, host_uncommitted_gb, min_free_gb}`, with no owner in it.
- `GET /volumes/<owner>` (orchestrator token) returns that owner's
  `used_bytes`, `used_pct` and `limit_bytes` per kind; closed volumes report the
  reading taken when they were closed.
- A warning is logged once when a volume crosses 80 % and once at 95 %
  (`volume usage crossed 80% token=<owner token> kind=…`); falling back under a
  level re-arms it. A sampler thread (`ORCH_VOLUME_SAMPLE_SECONDS`, default
  300) measures open volumes and reaps lapsed phone and browser leases.

### Enabling

The orchestrator must be able to run `losetup`, `cryptsetup`, `mkfs.ext4`,
`mount` and `umount`, **as root, in the host's mount namespace**. The shipped
systemd unit runs as `cloudphone` with `NoNewPrivileges`, `ProtectSystem`,
`ProtectHome` and `PrivateTmp`, and so cannot: a volume mounted in a private
namespace is invisible to dockerd, which would bind the empty directory beneath
it and let Android write `/data` unencrypted onto the host disk. The
orchestrator refuses to start with the flags on when its mount namespace is
not PID 1's (or it cannot tell) and a container runtime is in use. A narrow
privileged helper is the intended fix and is not built; until then this is for
a host where the orchestrator runs as root without those sandbox options.

1. Install `cryptsetup` (`cryptsetup-bin` is enough) and check `dm_crypt` and
   loop devices are available (`grep device-mapper /proc/misc`, `ls /dev/loop-control`).
2. Create the master key, readable by the service user only, and back it up
   offline. Losing it loses every owner's data:
   `sudo install -m 0600 /dev/null /etc/cloud-phone/volume-master.key && sudo sh -c 'head -c 64 /dev/urandom > /etc/cloud-phone/volume-master.key'`.
   The orchestrator refuses a key file that is a link, readable by group or
   others, or shorter than 32 bytes. Never print it or put it in the
   environment file.
3. Set `ORCH_VOLUME_ROOT` (default `/var/lib/cloud-phone-orchestrator/volumes`),
   `ORCH_VOLUME_MASTER_KEY_FILE`, and optionally the quota variables.
4. Turn on `ORCH_BROWSER_PROFILE=volume` first (browsers restart cleanly; old
   profiles under `ORCH_PROFILE_ROOT` are not migrated, so owners sign in
   again). Then `ORCH_PHONE_DATA=per_user`, which needs
   `ORCH_DEPLOY_MODE=redroid` (an `oci` phone keeps `/data` on its own VM; the
   orchestrator refuses to start with that combination). Existing phone
   instances are switched to the leasing owner's volume on their next lease;
   their per-instance volume is removed by `redroid-up.sh --down`.
5. Unknown flag values, a missing or loose key file, or a relative root stop
   the process at start with exit status 2.

With `ORCH_PHONE_DATA=per_user` the raw `POST /instances/<id>/lease` answers
`409 use_sessions`: phones are leased through `POST /sessions`, which mounts
the volume. Camera (Cuttlefish) leases are unchanged.

### Rolling back

1. Release every lease (or wait for expiry) so every volume is closed.
2. Phones: `DELETE /instances/<id>` every Redroid instance that ran with
   per-user volumes, then set `ORCH_PHONE_DATA=instance`. With the flag off,
   an instance that last ran on a per-user volume is refused (`409 phone not
   clean`, "delete and reprovision"), because its tenancy record no longer
   describes its per-instance `/data`.
3. Browsers: set `ORCH_BROWSER_PROFILE=dir`; owners get (or reuse) a
   directory under `ORCH_PROFILE_ROOT`.
4. The images stay under `ORCH_VOLUME_ROOT` until deleted with
   `DELETE /volumes/<owner>/<kind>` (works with the flags off). Keep the master
   key until they are gone.

### What it does not protect against

- **Anyone with root on the running host** while a volume is open: an open
  volume is a decrypted block device (`/dev/mapper/orchv-…`).
- **A privileged container.** The Redroid containers run `privileged: true`,
  which exposes the host's device nodes, including every open owner's
  decrypted `/dev/dm-*`. Per-owner keys protect volumes at rest and closed
  volumes; between open volumes on one host, isolation still rests on the
  container boundary. Dropping `privileged` (a separate experiment) matters
  more once several owners' volumes are open at once.
- The master key is one secret on the host: whoever reads it can derive every
  owner's key. It is not a per-owner secret held by the owner.

## Tests

`tests/test_isolation.py` (units) and `tests/test_isolation_gate.py` (the
two-owner gate through a live orchestrator in mock mode, a real-process
restart, an optional real-Chromium check, and a negative control that proves
the detector fires on a shared browser). Both run in `./cloud-phone test`.

Per-owner volumes: `tests/test_user_volumes.py` (command sequences, keys,
paths, quotas, host guard, alerts and the server wiring, against a fake host
in `tests/fixtures/fake_volumes.py`), `PerUserVolumeGate` and its negative
control in `tests/test_isolation_gate.py`, and
`tests/test_user_volumes_real.py`, which runs only with
`ORCH_REAL_VOLUME_TEST=1` as root and skips each part the host cannot do (the
full LUKS open needs device-mapper).
