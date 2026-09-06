# Testing — TDD ladder

Two runtimes (Redroid+GApps automation vs Cuttlefish ingest) are proven by a
**runged ladder**, not a single `unittest discover` dump. Offline rungs never
touch Docker, OCI, or a proprietary Play zip.

```bash
./cloud-phone test                 # R0–R3
./cloud-phone test --rung 3        # dual-pool e2e only
./cloud-phone test --list
./cloud-phone test --live          # + R4 against a real Control API
PYTHON=/usr/bin/python3 ./cloud-phone test
```

Logs: `.test-reports/r<N>-<suite>.log`. The runner sets `CLOUD_PHONE_VERBOSE=1`
and prints labeled `[ADB]` / `[CMD]` / `[APM]` / `[VNC]` excerpts after each
suite (`--quiet` hides them). See [`LOGGING.md`](./LOGGING.md).

## Rungs

| Rung | Kind | What must stay green | How |
|---|---|---|---|
| **R0** | Unit | GApps zip (incl. empty-zip refuse), purpose→runtime mapping, deploy `--platform` contracts, orchestrator helpers, labeled logging (incl. APM/CMD/VNC) | `test_gapps_zip`, `test_gapps_health`, `test_orchestrator_unit`, `test_scripts_contract`, `test_logging`, `test_ui_control` |
| **R1** | Component | Default pool is Redroid; camera is a separate Cuttlefish pool; Control API `/health` reports `gapps.ready` from `pm path`, not spoof props; UI commandlets + Appium 501 + VNC viewport logs | Flask `test_client` + patched ADB (`test_runtime_pool`, `test_control_api`) |
| **R2** | Process integration | A real orchestrator process talks HTTP to one fake Control API: health, tap, screenshot, jobs, Play login, `/ui` `/appium` `/vnc` `/logs` | `test_orchestrator_integration`, `test_orchestrator_e2e` |
| **R3** | Dual-pool e2e | Two fake phones at once. Default session → Redroid with `gapps.ready`. `purpose=camera` → Cuttlefish ingest, **no** GApps. Play launch hits only Redroid. Lease/409/release reuse the automation pool. `verify-redroid-phone.sh --require-gapps` passes Redroid and fails Cuttlefish. Verbose CMD/APM/VNC log ring is populated. | `test_ladder_e2e` |
| **R4** | Live | Real Control API. Skipped unless `CLOUD_PHONE_LIVE=1` (optional `REQUIRE_GAPPS=1`). | `test_live`; also `tests/test_agent_api.py --api-url …`, `PUBLIC_IP=… tests/test_connectivity.py` |

## Adding a test

Put it on the **lowest rung that would catch the bug**.

- Pure function / zip / CLI flag → R0
- Orchestrator pool rules without a subprocess → R1
- HTTP across process boundary → R2
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

Empty `/opt/gapps/gapps.zip` must still fail R0.
