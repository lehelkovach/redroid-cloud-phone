# Multi-user isolation: browser pool, persisted leases, ticketed relay

- **Date:** 2026-10-30
- **Asked by:** the founder, via the OSLO coordinator ("two OSLO users never share a browser, cookies, proxy exit, vault entries, memory or a phone. Make it so with test coverage"; then "the orchestrator must also be the ROUTER for all commands and I/O")
- **Question:** Can the orchestrator lease browsers per owner as it leases phones, keep every lease across a restart, and route every browser and phone command through an authenticated relay so no owner can reach another's container?
- **Scope:** `orchestrator/` (new `state_store.py`, `browser_pool.py`, `relay.py`, `proxy_relay.py`; `server.py`, `runtimes.py`), `systemd/cloud-phone-orchestrator.service`, `config/orchestrator.env.example`, `docs/isolation.md`, `docs/api/orchestrator.md`, `docs/configuration.md`, tests. Branch `isolation-browser-runtime` from `master`. PR #20's branch and files (`api/app_install.py`, the GApps scripts) were not touched.
- **Method:** test-first in mock runtime; `./cloud-phone test --coverage --fail-under 60` (17 suites, 0 failed, 73 % lines); the new files also under pytest (56 passed); one real-Chromium relay test with the Playwright Chromium build; `scripts/relay-latency.py` for the latency numbers.
- **Verdict:** Built and gated, off the live path. Nothing changes for a running orchestrator except two start-up refusals (no token, open bind), which are the requested policy and are flagged below as an upgrade step.
- **Follow-up:** OSLO branch `isolation` (osl-oc-agent) consumes this API behind `OSLO_BROWSER_LEASE=orchestrator` and `OSLO_PHONE_ROUTE=relay`. No PR opened, per instructions.

## What was there

- State in five dicts; a restart forgot every lease, and the tenancy guard
  forgot who had Android data on which phone.
- Default bind `0.0.0.0:8090`, and an empty `ORCH_API_TOKEN` meant no auth.
- No systemd unit.
- `/phones/<instance_id>/*` relays route by a caller-supplied instance id,
  with only the orchestrator token as a check.
- No browser pool: OSLO drives one shared Chrome.

## What was built

1. **Persistence** (`state_store.py`, `ORCH_STATE_DB`): write-through SQLite
   for instances, phone leases and sessions, tenancy, browser sessions, and
   ticket digests. Proxy credentials and bearers are never written.
2. **`chrome` pool** (`browser_pool.py`, `purpose: browser|web|chrome` or
   `runtime: chrome`): per-owner profile directory (0700, symlink refused,
   same owner token as OSLO's jars, verified by shared vectors), per-lease
   loopback DevTools port never returned, per-lease proxy id and upstream with
   a per-lease authenticating CONNECT relay for credentialed upstreams. Runtimes
   `mock`, `local` (Chromium per lease), `docker` (argv tested; Docker not run).
   Cross-owner requests `403`, capacity `409` without eviction, explicit
   `expires_at`, release keeps the profile, `DELETE /profiles/<owner>` with a
   confirmation and refused while leased.
3. **Tickets and the relay** (`relay.py`): `ort_` tickets bound to owner and
   lease, TTL capped by the lease, revocable, digests persisted. `WS
   /relay/cdp/<lease>`, `/relay/phone/<path>`, `WS /relay/phone/stream`. On
   every message: ticket live, lease still that owner's, per-lease token
   bucket, one audit line (owner token, lease, verb, ms, ok). Kill switch
   `POST /sessions/<owner>/kill`. Forged `instance_id`/`api_url`/ADB headers
   ignored. `?ticket=` redacted from the access log.
4. **Start-up policy and unit**: loopback default; wildcard/public refused
   without `ORCH_ALLOW_PUBLIC_BIND=1`; no token refused (loopback dev override
   `ORCH_ALLOW_NO_TOKEN=1`). `systemd/cloud-phone-orchestrator.service`
   (manual install).
5. **Encryption at rest**: `ORCH_PROFILE_ENCRYPTION=luks|fscrypt` verifies and
   refuses; tested with fake mountinfo/sysfs/fscrypt output.

## Tests (all in `./cloud-phone test`)

- `tests/test_isolation.py` (34): owner-token parity with OSLO; profile mode,
  tightening, symlink refusal, `..`; delete touches one owner; LUKS/fscrypt
  checks; docker argv mounts one profile and publishes loopback only; state
  round trip and file mode; ticket expiry, revocation, wrong lease/kind, lease
  gone, digests only; rate limiter per lease; audit fields fixed; start-up
  policy matrix; unit file content; per-lease proxy relay authenticates with
  its own credential and fails with another's; access-log redaction.
- `tests/test_isolation_gate.py` (22): the two-owner gate through a live
  orchestrator: separate leases and profiles, no container address in any
  response; A logs in and B is not; B's ticket cannot attach to A's browser or
  phone stream; cross-owner lease and ticket requests `403`; capacity refuses
  without evicting; forged addresses ignored on the phone relay; `config` and
  traversal not relayed; revoked and expired tickets dropped mid-stream; kill
  switch drops only that owner's streams; release revokes tickets and keeps the
  profile; delete is explicit; rate limit per lease; audit has one line per
  command and no payloads; per-lease egress (owner or direct, credential never
  on disk); restart keeps every mapping and revocation; `ORCH_REQUIRE_TICKETS`;
  a **real-process restart** that also proves both start-up refusals; a
  **real-Chromium** check (skips without a browser binary); and the **negative
  control**: with one shared browser the same detector fires.

## Relay latency (loopback, mock mode, 2026-10-30)

| Path | Direct p50 | Relayed p50 | Added |
|---|---|---|---|
| CDP round trip, real Chromium (n=300) | 0.16 ms | 0.25 ms | +0.09 ms |
| CDP round trip, fake DevTools (n=300) | 0.11 ms | 0.34 ms | +0.23 ms |
| Browser frame (`Page.captureScreenshot`, real Chromium, n=30) | 36.0 ms | 35.0 ms | within noise |
| Phone frame over the HTTP relay (n=300) | 2.3 ms | 5.2 ms | +2.9 ms |

A pure relay is not the bottleneck on one host. Across hosts each message
gains one network hop; if that hurts, the later option is a short-lived signed
direct ticket verified by the browser container (not built).

## Not done, and why

- **Per-owner encryption keys** for profiles: needs key custody per owner at
  lease time; only whole-root LUKS/fscrypt verification is implemented.
- **Docker runtime against a real Docker daemon**: argv is tested, the daemon
  path was not run (no Docker in this environment). No browser image is
  chosen or built.
- **Phones still reachable by the old routes** until `ORCH_REQUIRE_TICKETS=1`,
  and phone Control APIs are only as private as their network: the firewall
  rule (TCP 8080 from the orchestrator host only) is an operator step.
- **Credential re-supply after restart**: a credentialed upstream lives in
  memory; after a restart the owner's next acquire must pass it again (the
  running browser's relay is gone, so its egress fails closed until then).
- Operations and procedure results remain in memory.

## Upgrade step for a running orchestrator

Set `ORCH_API_TOKEN` and `ORCH_HOST` (private address) before running this
code, or it exits with status 2. Then set `ORCH_STATE_DB`. Then install the
unit. `ORCH_REQUIRE_TICKETS=1` only after OSLO routes phones through
`/relay/phone` (`OSLO_PHONE_ROUTE=relay`).
