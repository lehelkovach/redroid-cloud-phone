# Procedures

A procedure is a list of steps. A step names an action from one shared
vocabulary; a **surface adapter** decides how that action reaches the world.

| Surface | Backend | Notes |
|---|---|---|
| `mobile` | Control API → adb | Redroid phones, Cuttlefish guests |
| `web` | cloud browser driver | `ORCH_WEB_DRIVER_URL` |
| `chrome` | the user's own tab, via the extension bridge | `ORCH_CHROME_BRIDGE_URL` |
| `console` | host shell | `ORCH_ENABLE_CONSOLE=1` |

The same `{"action": "type", "text": "..."}` runs on any of them, so a login
written against a phone replays in a browser tab without a rewrite.

## Actions

`open` · `tap` · `tap_label` · `type` · `key` · `swipe` · `wait` · `read` ·
`screenshot` · `shell` · `install` · `submit` · `purchase`

Adapters implement a subset and declare it, so an unsupported combination fails
validation rather than silently doing nothing:

```bash
curl -s $ORCH/procedures/surfaces | jq
```

`console` cannot `tap`. `chrome` cannot `purchase` or `submit` — the helper
proposes a fill and the person clicks. Only `web` may `purchase`, and only with
approval.

## Two rules the callers depend on

**Validate before executing.** An unsupported action fails the whole procedure
before step 1 touches a device. A half-applied procedure on a logged-in phone is
worse than one that never started.

**Sensitive steps need approval.** `install`, `submit`, and `purchase` are gated
on every surface, including ones that could do them silently.

## Running one

```bash
curl -X POST $ORCH/procedures -H 'Content-Type: application/json' -d '{
  "surface": "mobile",
  "sync": true,
  "steps": [
    {"action": "open", "package": "com.example.app"},
    {"action": "wait", "duration_ms": 800},
    {"action": "tap_label", "label": "Email"},
    {"action": "type", "text": "someone@example.com"}
  ]
}'
```

| Endpoint | |
|---|---|
| `POST /procedures` | Run; `sync:true` blocks, otherwise poll |
| `GET /procedures/<id>` | Status, per-step timings, `failed_index` |
| `POST /procedures/validate` | Check without executing |
| `GET /procedures/surfaces` | What each wired surface can do |

A failure halts the run and reports which step broke:

```json
{"status": "failed", "failed_index": 0, "error": "network unreachable (no egress proxy)"}
```

## Cross-surface

A step may override the surface, which is how a procedure reads a code on the
phone and types it into the browser:

```json
[{"action": "read", "surface": "mobile"},
 {"action": "type", "text": "123456", "surface": "web"}]
```

## Addressing by label

`tap_label` beats coordinates, which break on any layout change. The Control API
serves `GET /device/ui` from `uiautomator dump`, returning labels with tap
centres. Matching prefers *clickable* elements over exact text, because a form's
caption ("Email") often matches better than the input beside it while tapping it
does nothing.

## Follow-up rules

Procedures are straight lines; engagement is not. `orchestrator/rules.py` holds
the time-based part — "message a match an hour later" — as pure functions that
return **intents**, never sends:

```python
rules.plan_followups(matches, now, "hey {name}", delay_s=3600)
# [{"to": "grace", "text": "hey Grace", "waited_s": 3601, "needs_approval": true}]
```

Policy encoded there: one message per match ever, nothing before the delay, a
per-run cap, a template that must personalize, and approval before send.
`SwipeBudget` caps automated swiping for the same reason — a runaway loop is the
abuse case.

See [`TESTING.md`](./TESTING.md) for the end-to-end scenario that exercises all
of this against a simulated phone.
