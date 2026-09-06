# Testing — TDD ladder

Two runtimes (Redroid+GApps automation vs Cuttlefish ingest) are proven by a
**runged ladder**, not a single `unittest discover` dump. Offline suites never touch Docker, OCI, or a proprietary Play zip.
CI runs `./cloud-phone test --coverage --fail-under 60`. Procedure and
auth suites from the runtime-split track are included alongside the
dual-pool ladder.

```bash
./cloud-phone test                 # all offline suites
./cloud-phone test --coverage      # + line coverage (CI fail-under 60)
./cloud-phone test --suite ladder-e2e
./cloud-phone test --list
./cloud-phone test --live          # + live Control API suites
PYTHON=/usr/bin/python3 ./cloud-phone test
```

Logs: `.test-reports/<suite>.log`. Filter labeled `[ADB]` / `[CMD]` / `[APM]` / `[VNC]`
lines from those reports. See [`LOGGING.md`](./LOGGING.md).

## Rungs

| Rung | Kind | What must stay green | How |
|---|---|---|---|
| **R0** | Unit | GApps zip (incl. empty-zip refuse), purpose→runtime mapping, deploy `--platform` contracts, orchestrator helpers, labeled logging (incl. APM/CMD/VNC), VLM box parse + CPMS role bind | `test_gapps_zip`, `test_gapps_health`, `test_orchestrator_unit`, `test_scripts_contract`, `test_logging`, `test_ui_control`, `test_vlm_boxes` |
| **R1** | Component | Default pool is Redroid; camera is a separate Cuttlefish pool; Control API `/health` reports `gapps.ready` from `pm path`, not spoof props; UI commandlets + Appium 501 + VNC viewport logs | Flask `test_client` + patched ADB (`test_runtime_pool`, `test_control_api`) |
| **R2** | Process integration | A real orchestrator process talks HTTP to one fake Control API: health, tap, screenshot, jobs, Play login, `/ui` `/appium` `/vnc` `/logs`. Mock agent deploys a phone, Gemini-shaped VLM boxes an ADB screenshot, POSTs tap+type (submit gated) | `test_orchestrator_integration`, `test_orchestrator_e2e`, `test_agent_vlm_fill` |
| **R3** | Dual-pool e2e | Two fake phones at once. Default session → Redroid with `gapps.ready`. `purpose=camera` → Cuttlefish ingest, **no** GApps. Play launch hits only Redroid. Lease/409/release reuse the automation pool. `verify-redroid-phone.sh --require-gapps` passes Redroid and fails Cuttlefish. Verbose CMD/APM/VNC log ring is populated. | `test_ladder_e2e` |
| **R4** | Live | Real Control API. Skipped unless `CLOUD_PHONE_LIVE=1` (optional `REQUIRE_GAPPS=1`). | `test_live`; also `tests/test_agent_api.py --api-url …`, `PUBLIC_IP=… tests/test_connectivity.py` |

## Adding a test

Put it on the **lowest rung that would catch the bug**.

- Pure function / zip / CLI flag → R0
- Orchestrator pool rules without a subprocess → R1
- HTTP across process boundary → R2 (mock-agent VLM fill lives here; fake Gemini, no Docker)
- Redroid vs Cuttlefish isolation → R3
- Needs a booted guest → R4 (skip offline)

Do not call Docker or `oci` from R0–R3. Fake Control APIs live in
`tests/fixtures/fake_control.py`. Process helpers live in `tests/harness.py`.

## Live GApps bake (R4)

After `./cloud-phone deploy-redroid` and an operator zip:

```bash
CLOUD_PHONE_LIVE=1 REQUIRE_GAPPS=1 CLOUD_PHONE_API_URL=http://<IP>:8080 \
  ./cloud-phone test --rung 4 --live
```

Live Gemini (R4, skipped offline): `CLOUD_PHONE_LIVE=1` plus `GEMINI_API_KEY`.
The offline mock-agent suite uses a fake VLM; it never calls Google.
