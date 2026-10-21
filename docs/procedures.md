# Procedures

A procedure is a list of steps. Each step names an action from one shared
vocabulary, and a **surface adapter** decides how that action reaches the
world. The code is in `orchestrator/procedures.py`; the HTTP endpoints are in
[api/orchestrator.md](api/orchestrator.md#procedures).

| Surface | Backend | Enabled when |
|---|---|---|
| `mobile` | The phone's Control API | Always |
| `web` | A cloud browser driver you run; steps are POSTed to `<url>/web/step` | `ORCH_WEB_DRIVER_URL` is set |
| `chrome` | A bridge to a user's own browser tab; steps are POSTed to `<url>/chrome/step` | `ORCH_CHROME_BRIDGE_URL` is set |
| `console` | Shell commands **on the orchestrator host** | `ORCH_ENABLE_CONSOLE=1` |

The same `{"action": "type", "text": "..."}` step runs on any surface, so a
login written for a phone can run in a browser without a rewrite. This repo
does not include the `web` driver or the `chrome` bridge.

## Actions

`open` · `tap` · `tap_label` · `type` · `key` · `swipe` · `wait` · `read` ·
`screenshot` · `shell` · `install` · `submit` · `purchase`

Each adapter supports a subset:

| Surface | Supported actions |
|---|---|
| `mobile` | `open`, `tap`, `tap_label`, `type`, `key`, `swipe`, `wait`, `read`, `screenshot`, `shell`, `install`, `submit` |
| `web` | `open`, `tap`, `tap_label`, `type`, `key`, `wait`, `read`, `screenshot`, `submit`, `purchase` |
| `chrome` | `open`, `tap`, `tap_label`, `type`, `wait`, `read`, `screenshot` |
| `console` | `shell`, `wait`, `read` |

Ask a running orchestrator what it has wired:

```bash
curl -s $ORCH/procedures/surfaces -H "Authorization: Bearer $ORCH_API_TOKEN"
```

### Step fields on `mobile`

| Action | Fields | Control API call |
|---|---|---|
| `open` | `package` (or `target`) | `POST /apps/<package>/start` |
| `tap` | `x`, `y` | `POST /device/input` |
| `tap_label` | `label` (or `target`) | `POST /device/input` with `type: tap_label` |
| `type` | `text` | `POST /device/input` with `type: text` |
| `key` | `keycode` (default 66, Enter) | `POST /device/input` |
| `swipe` | `x1`, `y1`, `x2`, `y2`, `duration_ms` (300) | `POST /device/input` |
| `wait` | `duration_ms` | none; the orchestrator sleeps |
| `read` | | `POST /device/ui` |
| `screenshot` | | `POST /device/screenshot/base64` (see [ROADMAP.md](../ROADMAP.md): that route only accepts GET) |
| `shell` | `command` | `POST /adb/shell` |
| `install` | `path` | `POST /apps/install` (see ROADMAP.md: no such route exists) |
| `submit` | | Enter key |

Any step may set its own `surface`, overriding the procedure's default.

## Rules the runner enforces

**Validate before executing.** Every step is checked against its adapter
before step 1 runs. An unknown action, an unknown or unwired surface, an
action the surface does not support, or a missing required field (`open`
without a target, `tap_label` without a label, `shell` without a command,
`install` without a path, a negative `wait`) fails the whole procedure.

**Sensitive steps need approval.** `install`, `submit` and `purchase` are
refused unless the request has `"approve": true`. The run ends with status
`needs_approval`.

**Stop at the first failure.** A step that returns `success: false` or raises
ends the run with `status: "failed"`, `failed_index` and `error`.

## Run one

```bash
curl -s -X POST $ORCH/procedures \
  -H "Authorization: Bearer $ORCH_API_TOKEN" -H 'Content-Type: application/json' -d '{
  "surface": "mobile",
  "sync": true,
  "steps": [
    {"action": "open", "package": "com.android.settings"},
    {"action": "wait", "duration_ms": 800},
    {"action": "tap_label", "label": "Network"}
  ]
}'
```

Without `sync`, the call returns `202` with a `procedure_id`; poll
`GET /procedures/<id>`. Use `POST /procedures/validate` to check a procedure
without running it.

If the request has no `steps`, but has `app_package` (or `url`) and `login`,
the orchestrator builds a login procedure: open, type the username, move to
the password field (Tab, or `tap_label` on `login.password_label`), type the
password and press Enter.

A cross-surface example, reading a code on the phone and typing it into a
browser:

```json
[{"action": "read", "surface": "mobile"},
 {"action": "type", "text": "123456", "surface": "web"}]
```

## Addressing by label

Prefer `tap_label` to coordinates, which break when the layout changes. The
Control API's `GET /device/ui` returns labelled elements with tap points.
When several elements match, clickable elements beat exact text matches,
because a form's caption ("Email") often matches better than the input next
to it, and tapping the caption does nothing.

## Follow-up rules (`orchestrator/rules.py`)

`rules.py` contains pure functions for time-based follow-ups and a swipe
budget. They are not exposed over HTTP; only the tests call them today.

- `plan_followups(matches, now, template, delay_s=3600, already_messaged=(), max_per_run=3, approve=False)`
  returns message intents for matches older than `delay_s`, oldest first,
  skipping anyone already messaged, at most `max_per_run`. Each intent has
  `needs_approval: true` unless `approve=True`. The template must contain
  `{name}` or `{id}`. Nothing is sent.
- `SwipeBudget(max_swipes=20, max_likes=10)` counts swipes and likes and
  raises once either limit is reached.

The end-to-end test `tests/test_mobile_e2e_scenario.py` uses these against a
simulated phone and a fabricated dating app (`com.example.mockdating`).
