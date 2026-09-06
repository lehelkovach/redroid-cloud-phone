# Testing

```bash
./cloud-phone test                     # every offline suite
./cloud-phone test --coverage          # + coverage, fails under 60%
./cloud-phone test --suite mobile-e2e --verbose
./cloud-phone test --list
./cloud-phone test --live --api-url http://127.0.0.1:8080
```

Offline suites touch no device, no Docker, and no OCI, so they run in CI and in
a cloud agent. Logs land in `.test-reports/<suite>.log`; coverage HTML in
`.test-reports/htmlcov/`.

## Suites

| Suite | Covers |
|---|---|
| `logging` | Label format, JSON mode, shell/Python parity |
| `control-api` | `/health` GApps reporting, per-request ADB routing |
| `procedures` | Step vocabulary, capabilities, approval gates, cross-surface runs |
| `procedure-api` | `POST /procedures`, `/validate`, `/surfaces` |
| `orchestrator` | Step normalization, leases, instance caps |
| `sessions` | Playwright-like acquire / renew / 409 / release |
| `gapps` | Zip validation (incl. the empty-zip failure), Redroid launcher dry run |
| `mobile-e2e` | Full ladder: proxy → signup → capped swipe → match → follow-up rule |
| `orchestrator-integration` / `-e2e` | Orchestrator against a mock Control API |
| `agent-api`, `connectivity` | **Live only** — need a real Control API |

## The mobile e2e scenario

`tests/test_mobile_e2e_scenario.py` drives `tests/fixtures/fake_phone.py`, an
in-process Control API backed by a small Android-ish state machine. It is a
*simulator*, not a stub: it refuses taps on the wrong screen, will not load the
app until an egress proxy is configured, and will not message someone who was
never a match.

The ladder it proves:

1. **Proxy first** — the app refuses to load without egress; configuring a
   residential proxy changes the apparent IP.
2. **Signup** — the shared `login_procedure` fills email/password with fake
   credentials and submits.
3. **Swiping under budget** — `rules.SwipeBudget` caps swipes and likes; the
   run stops at the cap, not at the end of the deck.
4. **Match** — the deck deterministically matches on every second like.
5. **Follow-up after an hour** — `rules.plan_followups` uses a virtual clock,
   returns *intents*, and marks them `needs_approval`. The test asserts nothing
   is sent without approval.

The app is `com.example.mockdating`, fabricated for this fixture. There is no
real account and no unattended outreach: one message per match ever, capped per
run, personalized template required, approval gate on send. Pointing this at a
live dating service is out of scope.

## Adding a suite

Add the module to `OFFLINE_SUITES` in `scripts/run-tests.sh`. Keep it offline —
patch `run_adb` (see `tests/test_control_api.py`) or use the fake phone rather
than reaching for a device.

## Coverage

The floor is 60% over `api/` and `orchestrator/`, enforced in CI. It is a
ratchet, not a target: raise `--fail-under` when a suite lands, never lower it
to make a red run green.
