# Auth and health — why a working phone read as a dead one

**Status:** fixed in this tree · **Applies to:** `api/server.py`, `orchestrator/server.py`

The lab looked dead for two weeks while `adbd` was stable and `screencap` returned
valid PNGs the whole time. Nothing was broken on the device. Four faults masked each
other, and three of them were about *auth*, not Android.

## The masking chain

| # | Fault | What an operator saw |
|---|---|---|
| 1 | `/health` is unauthenticated; every device endpoint needs a bearer | `status: healthy` while every call answered 401 |
| 2 | One env var (`ORCH_API_TOKEN`) served both inbound auth and outbound calls to phones | No way to express "the phone's token differs" — the mismatch surfaced mid-run |
| 3 | Agent-side `launchApp()` retried an alternate route on *any* failure | A 401 came back as `non-JSON 404: <!doctype html>` |
| 4 | `POST /apps/<pkg>/start` reported `success: true, activity: "No activity found"` | Launching a Play Store that GApps never installed read as success — this is what hid the missing GMS |

## The contract now

`/health` stays **open and 200** on both services — systemd `ExecStartPost` and the
Docker `HEALTHCHECK` probe it without a token, and taking that away would break them.
What changed is that it stops answering for a caller it is about to reject:

```jsonc
// GET /health with a token every device endpoint will refuse
{
  "status": "unauthorized",        // not "healthy"
  "adb_connected": true,           // the phone itself is fine
  "usable": false,                 // connected AND authorized
  "auth": { "required": true, "presented": false, "ok": false }
}
```

`status` is the *caller's* verdict, so the three states are distinguishable:

| `status` | Means |
|---|---|
| `healthy` | ADB is up and this caller can drive it |
| `degraded` | this caller is authorized; the device is not reachable |
| `unauthorized` | the device may be perfectly fine; **your token is wrong** |

401s carry a machine-readable reason so no client can mistake one for a missing route:

```jsonc
{ "success": false, "error": "Unauthorized", "code": "auth_required", "auth_required": true }
```

`code` is `auth_required` when nothing was sent and `auth_invalid` when the token was
wrong, with `WWW-Authenticate: Bearer` on both.

## Tokens (three, not one)

| Variable | Service | Direction |
|---|---|---|
| `API_TOKEN` | Control API (`api/server.py`) | inbound — what a phone demands |
| `ORCH_API_TOKEN` | Orchestrator | inbound — what the orchestrator demands of agents |
| `ORCH_CONTROL_API_TOKEN` | Orchestrator | **outbound** — what the orchestrator sends to phones; must equal the phones' `API_TOKEN` |

`ORCH_CONTROL_API_TOKEN` falls back to `ORCH_API_TOKEN` when unset, which is the old
single-value behaviour and fine for a one-token lab. Set it explicitly as soon as the
orchestrator and the phones have different owners.

A mismatch now fails **before the first step runs**, naming the variable:

```text
phone at http://10.0.1.7:8080 reports status=unauthorized for this token —
ORCH_CONTROL_API_TOKEN must match API_TOKEN on the phone
```

## No false greens on launch

`cmd package resolve-activity` exits 0 and prints `No activity found` for a package
that is not installed, so its output is matched against `<package>/` rather than
trusted. An unlaunchable package is a **404** with `code: not_launchable`; a resolved
activity whose `am start` errors is a **502**. Neither reports success.

`GET /device/focus` reads `dumpsys window` unpiped and filters host-side: a pipe
inside `adb shell` closes early, kills the upstream dumpsys, and returns `""`, which
reads as a failed device rather than a bad command.

## Checking it

```bash
# Is the phone up, and can *I* drive it?
curl -s http://PHONE:8080/health | jq '{status, usable, auth, gapps}'

# Distinguish "wrong token" from "no route"
curl -si http://PHONE:8080/status | head -1        # 401 + code: auth_required
```

Covered by `tests/test_control_api.py` (`AuthTests`, `AppLaunchTests`, `FocusTests`)
and `tests/test_procedure_api.py` (`TokenMismatchTests`, which drives a
token-protected fake phone through the orchestrator).
