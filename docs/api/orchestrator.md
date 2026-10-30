# Orchestrator API reference

The orchestrator (`orchestrator/server.py`) keeps a pool of phones, gives each
user one phone at a time, relays calls to the phone's Control API and runs
procedures. Start it with `./cloud-phone orchestrator-run`, or install
`systemd/cloud-phone-orchestrator.service`. It also leases per-owner browsers
(the `chrome` pool) and is the authenticated relay for every browser and phone
command: see [isolation.md](../isolation.md).

Base URL: `http://<host>:8090` (`ORCH_HOST`, `ORCH_PORT`; default
`127.0.0.1:8090`). A wildcard or public bind, or a missing `ORCH_API_TOKEN`,
makes it refuse to start (see isolation.md for the overrides).

**State.** With `ORCH_STATE_DB` set, instances, leases, phone and browser
sessions, tenancy and ticket digests persist across restarts. Without it they
are in memory. Operations and procedure results are always in memory. VMs it
created in `oci` mode keep running either way.

## Authentication and health

The orchestrator has two tokens, one for each direction:

| Variable | Direction | Meaning |
|---|---|---|
| `ORCH_API_TOKEN` | Inbound | What callers of the orchestrator must send |
| `ORCH_CONTROL_API_TOKEN` | Outbound | What the orchestrator sends to phones. Must equal the phones' `API_TOKEN`. Falls back to `ORCH_API_TOKEN`. |

Every endpoint except `GET /health` and `/relay/*` needs
`Authorization: Bearer <ORCH_API_TOKEN>`. `/relay/*` takes a session ticket
instead (below). A rejected call gets `401` with
`code: "auth_required"` or `"auth_invalid"`, in the same shape as the Control
API.

`GET /health` is open and reports whether *your* token would be accepted:

```json
{"status": "ok", "auth": {"required": true, "presented": true, "ok": true},
 "default_purpose": "automation", "default_runtime": "redroid",
 "deploy_mode": "mock", "runtime": "mock",
 "instances": 1, "sessions": 1, "max_instances": 5, "pool": {...}}
```

`status` is `ok` or `unauthorized`.

Before running an operation or a procedure on a phone, the orchestrator calls
that phone's `/health`. If the phone reports that the orchestrator's token is
rejected, the run fails before the first step with an error naming
`ORCH_CONTROL_API_TOKEN`. A `401`/`403` from a phone during a relay raises the
same error.

JSON endpoints need `Content-Type: application/json`.

## Purpose and runtime

Requests that create or pick a phone accept `purpose` or `runtime`:

| Value | Resolves to |
|---|---|
| no value, `automation`, `play`, `mobile`, `phone`, `gapps`, `redroid`, `default` | `automation` on Redroid |
| `camera`, `ingest`, `stream`, `rtmp`, `webrtc`, `cuttlefish`, `video`, `mic` | `camera` on Cuttlefish |
| `runtime: "redroid"` / `"cuttlefish"` | Wins over `purpose` |

Anything else is a `400`. The two pools never share instances.

## Sessions (one phone per user)

### `POST /sessions`

```json
{"owner_user_id": "alice", "purpose": "automation", "ttl_seconds": 3600, "provision": true}
```

`owner` and `user_id` are accepted as aliases for `owner_user_id`; `ttl` for
`ttl_seconds` (minimum 10). With `provision: false` it only reuses an idle
instance.

The orchestrator renews the user's existing session if it is still valid and
for the same runtime. Otherwise it picks an idle instance of the right
runtime, or provisions one, and leases it to the user.

```json
{"success": true, "created": true, "session": {
  "owner_user_id": "alice", "instance_id": "...", "api_url": "http://127.0.0.1:8080",
  "adb_connect": "127.0.0.1:5555", "runtime": "redroid", "purpose": "automation",
  "ttl_seconds": 3600, "name": "...", "gapps": true, "ingest": null}}
```

`201` when a session was created, `200` when renewed. For `camera` sessions,
`ingest` tells you where to publish:

```json
"ingest": {"rtmp_url": "rtmp://<host>/live/cam", "rtmp_app": "live", "stream_key": "cam", "host": "<host>"}
```

The host is taken from the instance's `api_url`.

Errors: `400` for a missing owner or bad purpose; `409` when the phone is in
use, the pool or instance limit is reached, or the phone is "not clean" (see
below); `500` for other failures (for example a failed deploy).

**Tenant isolation.** An instance remembers the last owner who leased it. It
is refused to a different owner unless a reset was recorded after that use.
No endpoint records a reset yet, so in practice an instance serves only the
first owner who held it until the orchestrator restarts. Other owners get
`409 phone not clean`.

### Other session endpoints

| Method and path | Response |
|---|---|
| `GET /sessions` | `{"count", "sessions": [...]}` |
| `GET /sessions/<owner_user_id>` | The session, or `404` if missing or expired |
| `DELETE /sessions/<owner_user_id>` | Releases the lease: `{"success", "session", "released"}`. The phone's data is not wiped. |

### `GET /pool`

```json
{"success": true, "pool": {
  "automation": {"runtime": "redroid", "total": 1, "leased": 1, "idle": 0, "max": 3},
  "camera": {"runtime": "cuttlefish", "total": 0, "leased": 0, "idle": 0, "max": 2}}}
```

## Instances

| Method and path | Body | Response |
|---|---|---|
| `GET /instances` | | List of instance records |
| `POST /instances` | `{"purpose"}` or `{"runtime"}` | `201` with the new record; `400` bad purpose; `409` when a limit is reached or a golden image ID is missing |
| `DELETE /instances/<id>` | | `redroid` mode: runs `redroid-up.sh --down` (deletes the container's data). `oci` mode: terminates the VM. `mock`: forgets it. |
| `POST /instances/<id>/lease` | `{"owner": "default", "ttl_seconds": 300}` | `{"success", "instance_id", "owner", "ttl_seconds"}`; `409` if already leased; `400` if TTL < 10 |
| `DELETE /instances/<id>/lease` | | `{"success", "instance_id"}` |

An instance record contains `id`, `name`, `api_url`, `adb_connect`, `mode`,
`runtime`, `purpose`, `gapps`, `instance_ocid`, `created_at`, `last_used`,
and depending on mode `container`, `image`, `public_ip`, `golden_image`.

How instances are provisioned depends on `ORCH_DEPLOY_MODE`:

| Mode | Automation (Redroid) | Camera (Cuttlefish) |
|---|---|---|
| `mock` | Record pointing at `ORCH_MOCK_API_URL` | Record pointing at `ORCH_MOCK_CAMERA_API_URL` or `ORCH_MOCK_API_URL` |
| `redroid` | `redroid-up.sh` container on the next ADB port; Control API `ORCH_REDROID_API_URL` | OCI VM from `CUTTLEFISH_GOLDEN_IMAGE_ID` (required) |
| `oci` | OCI VM from `REDROID_GOLDEN_IMAGE_ID` | OCI VM from `CUTTLEFISH_GOLDEN_IMAGE_ID` |

## Relay to a phone

These forward to the instance's Control API with `ORCH_CONTROL_API_TOKEN` and
the instance's ADB serial in `X-Cloud-Phone-Adb`. They return the Control
API's JSON. `404` if the instance is unknown. If the Control API answers
with an error status, the orchestrator answers `500`.

| Method and path | Control API call |
|---|---|
| `GET /phones/<id>/status` | `GET /status` |
| `GET /phones/<id>/health` | `GET /health` |
| `POST /phones/<id>/input` | `POST /device/input` (body passed through, `type` defaults to `tap`) |
| `GET /phones/<id>/screenshot` | `GET /device/screenshot/base64` |
| `POST /phones/<id>/jobs` | `POST /jobs` (returns `202`) |
| `GET /phones/<id>/jobs/<job_id>` | `GET /jobs/<job_id>` |
| `POST /phones/<id>/ui` | `POST /ui/command` |
| `GET /phones/<id>/appium` | `GET /appium/status` |
| `GET /phones/<id>/vnc` | `GET /vnc/status` |
| `GET /phones/<id>/logs` | `GET /logs` (query string passed through) |

The relays do not check that the caller holds a lease on the instance.

## Operations (older step runner)

### `POST /operations`

Either a login:

```json
{"operation": "login", "app_package": "com.example.app",
 "login": {"username": "...", "password": "...",
           "password_tap": {"x": 540, "y": 900}, "submit_tap": {"x": 540, "y": 1100}}}
```

or explicit steps with actions `start_app` (`package`), `input_text` (`text`),
`key` (`keycode`, default 66), `tap` (`x`, `y`), `sleep_ms` (`duration`):

```json
{"steps": [{"action": "start_app", "package": "com.example.app"},
           {"action": "sleep_ms", "duration": 800},
           {"action": "tap", "x": 540, "y": 900}],
 "purpose": "automation", "instance_id": "optional"}
```

Returns `202 {"operation_id", "status": "queued"}`. Poll
`GET /operations/<op_id>` for `status` (`queued`, `running`, `done`,
`failed`), `result` and `error`. The stored operation includes the request
body as sent, including any password.

## Procedures

The newer step runner, with one action vocabulary across surfaces. Full
description: [procedures.md](../procedures.md).

| Method and path | Body | Response |
|---|---|---|
| `GET /procedures/surfaces` | | `{"surfaces": {"mobile": [...], ...}, "actions": [...], "sensitive": [...]}` |
| `POST /procedures/validate` | `{"steps", "surface", "approve", "instance_id"}` | `{"valid": true, "steps", "count"}`; `400 {"valid": false, "error"}`; `200 {"valid": false, "needs_approval": true}` |
| `POST /procedures` | `{"steps", "surface": "mobile", "sync": false, "approve": false, "instance_id", "purpose", "runtime"}` or a login shortcut `{"app_package" or "url", "login": {"username", "password", "password_label"}}` | `202 {"procedure_id", "status": "queued"}`, or with `sync: true` the finished record |
| `GET /procedures/<id>` | | The record |

A procedure record:

```json
{"id": "...", "kind": "procedure", "status": "done", "error": null,
 "payload": {...},
 "result": {"instance": {...}, "status": "done", "failed_index": null,
            "steps": [{"index": 0, "action": "tap", "surface": "mobile",
                       "status": "ok", "duration_ms": 3, "result": {...}}]}}
```

`status` is `queued`, `running`, `done`, `failed` or `needs_approval`.

## Logs

`GET /logs?type=ORC,CMD&n=200` returns the orchestrator's own recent log
lines, in the same format as the Control API's `/logs`. Unlike the Control
API, this endpoint requires the token.

## Browsers, tickets and the relay

Full description in [isolation.md](../isolation.md).

### `POST /sessions` with `purpose: "browser"`

```json
{"owner_user_id": "slack:U0EXAMPLE", "purpose": "browser", "ttl_seconds": 3600,
 "proxy": {"id": "owner-resi", "upstream": "http://user:pass@proxy.example:8000"},
 "lease_id": "bl-... (optional: renew this lease; another owner's is 403)"}
```

`201` (new) / `200` (renewed):

```json
{"success": true, "created": true,
 "session": {"lease_id": "bl-…", "owner_user_id": "…", "owner_token": "slack-U0EXAMPLE-1a2b3c4d",
             "purpose": "browser", "runtime": "chrome", "chrome_runtime": "docker",
             "profile_dir": "/var/lib/cloud-phone-orchestrator/profiles/slack-U0EXAMPLE-1a2b3c4d",
             "proxy_id": "owner-resi", "proxy_upstream": "http://proxy.example:8000", "egress": "owner",
             "acquired_at": 0, "expires_at": 0, "ttl_seconds": 3600, "renewals": 0},
 "ticket": "ort_…", "ticket_id": "…", "ticket_expires_at": 0,
 "relay": {"cdp_ws_url": "ws://<orchestrator>/relay/cdp/bl-…"}}
```

Refusals: `400 owner_required`, `403 forbidden` (another owner's lease),
`409 at_capacity`, `409 proxy_changed`, `503 profile_not_encrypted`.

`GET /sessions/<owner>?purpose=browser` and `DELETE /sessions/<owner>?purpose=browser`
read and release it (release keeps the profile: `"profile_kept": true`).
`GET /sessions` lists `browser_sessions` next to the phone `sessions`.

A phone `POST /sessions` now also returns `ticket`, `ticket_id`,
`ticket_expires_at` and `relay: {phone_api_url, phone_stream_url}`, and the
session carries a `lease_id` and `expires_at`.

### Tickets

| Endpoint | Body | Result |
|---|---|---|
| `POST /tickets` | `{owner_user_id, purpose?, lease_id?, ttl_seconds?}` | A fresh ticket for that owner's own current lease. `403` for another owner's `lease_id`, `404` if the owner holds none. |
| `POST /tickets/revoke` | `{ticket}` or `{ticket_id}` | `{revoked: bool}` |
| `POST /sessions/<owner>/kill` | `{purpose?}` | Kill switch: revokes every ticket for the lease and drops its open streams. `{tickets_revoked, streams_dropped}` |

### Profiles

| Endpoint | Notes |
|---|---|
| `GET /profiles/<owner>` | `{owner_token, profile_dir, exists, mode}` |
| `DELETE /profiles/<owner>` | Body `{"confirm": "<owner>"}` (`400` otherwise). `409 lease_active` while leased. |

### Relay (ticket auth, no orchestrator token)

| Route | Notes |
|---|---|
| `WS /relay/cdp/<lease_id>` | DevTools of the ticket's browser. Close codes: 4401 ticket, 4410 killed, 4502 browser gone. Over the rate limit a command gets `{"id": n, "error": {"code": -32000, "message": "rate limited by orchestrator relay"}}`. |
| `GET/POST/PUT/DELETE /relay/phone/<path>` | Control API of the ticket's phone; `429` over the limit; `config` and `..` refused. |
| `WS /relay/phone/stream` | Screen frames `{"type": "frame", "seq": n, "frame": {...}}` out; input events in. |

With `ORCH_REQUIRE_TICKETS=1` the older `/phones/<instance_id>/*` routes
answer `403 tickets_required`.
