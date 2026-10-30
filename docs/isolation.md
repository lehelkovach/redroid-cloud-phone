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

- **No per-owner keys.** Both options use one key for the whole profile root.
  Per-owner fscrypt protectors (one directory policy per owner, unlocked only
  while that owner holds a lease) would mean the orchestrator holding or
  receiving each owner's key at lease time; that key management is not built.
- **Nothing is encrypted by the orchestrator itself**, and nothing is set up
  automatically. The tests exercise the checks with fake `mountinfo`, sysfs and
  `fscrypt` output; no real LUKS volume or fscrypt policy was created here.
- A compromised orchestrator host can read every open profile. Isolation here
  is between tenants of one trusted host, not against its operator.

## Tests

`tests/test_isolation.py` (units) and `tests/test_isolation_gate.py` (the
two-owner gate through a live orchestrator in mock mode, a real-process
restart, an optional real-Chromium check, and a negative control that proves
the detector fires on a shared browser). Both run in `./cloud-phone test`.
