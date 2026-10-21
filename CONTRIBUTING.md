# Contributing

Thanks for helping. This page covers setting up, running the tests, and what
a change should include.

## Set up

```bash
git clone https://github.com/lehelkovach/redroid-cloud-phone.git
cd redroid-cloud-phone
python3 -m venv .venv && . .venv/bin/activate
pip install -r api/requirements.txt -r orchestrator/requirements.txt coverage
```

The offline tests need no phone, Docker, OCI account or GApps zip.

## Run the tests

```bash
./cloud-phone test                              # all offline suites
./cloud-phone test --coverage --fail-under 60   # what CI runs (plus --verbose)
./cloud-phone test --list                       # list suites
./cloud-phone test --suite procedures           # one suite
PYTHON=/usr/bin/python3 ./cloud-phone test      # pick the interpreter
```

`./cloud-phone test` runs `scripts/run-tests.sh`. Each suite's output goes to
`.test-reports/<suite>.log`; with `--coverage` you also get
`coverage.txt`, `coverage.xml` and `htmlcov/` there. Coverage is measured over
`api/` and `orchestrator/`.

The default coverage floor is 80% (`COVERAGE_FAIL_UNDER`), but CI runs with
`--fail-under 60`. Plain `./cloud-phone test --coverage` can therefore fail
locally on a tree that passes CI; pass `--fail-under 60` to match CI.

### What CI does

`.github/workflows/ci.yml` runs on pushes to `master` and `cursor/**`, pull
requests to `master`, and manual dispatch. On Python 3.12 it:

1. installs both requirements files and `coverage`,
2. runs `bash -n` on `scripts/*.sh`, `scripts/lib/*.sh` and `cloud-phone`,
3. runs `./scripts/run-tests.sh --coverage --fail-under 60 --verbose`,
4. uploads `.test-reports/`.

`tests/test_tenant_isolation.py` and `tests/test_ingest_endpoints.py` use
`pytest` and are not run by `run-tests.sh` or CI. Run them by hand:

```bash
pip install pytest
python -m pytest tests/test_tenant_isolation.py tests/test_ingest_endpoints.py
```

### Test rungs

Tests are organised as a ladder. Put a new test on the lowest rung that would
catch the bug.

| Rung | Kind | Covers | Suites |
|---|---|---|---|
| R0 | Unit | GApps zip checks (including the empty-zip refusal), purpose to runtime mapping, deploy script flags, orchestrator helpers, labelled logging, UI command translation | `test_gapps_zip`, `test_gapps_health`, `test_orchestrator_unit`, `test_scripts_contract`, `test_logging`, `test_ui_control` |
| R1 | Component | Default pool is Redroid, camera pool is Cuttlefish; `/health` reports `gapps.ready` from `pm path`; UI commandlets; Appium 501; viewport logs. Flask `test_client` with ADB patched. | `test_runtime_pool`, `test_control_api` |
| R2 | Process integration | A real orchestrator process talking HTTP to a fake Control API | `test_orchestrator_integration`, `test_orchestrator_e2e` |
| R3 | Dual-pool end to end | Two fake phones: automation gets Redroid with GApps, camera gets Cuttlefish without; leases, 409s and release; `verify-redroid-phone.sh --require-gapps` | `test_ladder_e2e` |
| R4 | Live | A real Control API | `test_live`, `test_agent_api`, `test_connectivity` |

Also offline: `test_procedures`, `test_procedure_api`, `test_user_sessions`
and `test_mobile_e2e_scenario` (a simulated phone and a fabricated app).

`--rung N` runs a single suite for that rung, not the whole rung: 0 runs
`logging`, 1 `runtime-pool`, 2 `orchestrator-integration`, 3 `ladder-e2e`,
and 4 runs only the live `agent-api` suite.

R0 to R3 must not call Docker or `oci`. Fake Control APIs live in
`tests/fixtures/fake_control.py` and `tests/fixtures/fake_phone.py`; process
helpers in `tests/harness.py`.

### Live tests

Against a running Control API:

```bash
./cloud-phone test --live --suite live --api-url http://<host>:8080
REQUIRE_GAPPS=1 ./cloud-phone test --live --suite live --api-url http://<host>:8080
./cloud-phone test --live --suite agent-api --api-url http://<host>:8080
```

`--suite live` runs `tests/test_live.py` with `CLOUD_PHONE_LIVE=1`.
The `agent-api` suite runs `tests/test_agent_api.py`, which takes
`--api-token` when run directly:

```bash
python3 tests/test_agent_api.py --api-url http://<host>:8080 --api-token "$API_TOKEN"
```

`tests/test_connectivity.py` checks TCP ports 22, 1935, 4723, 5555, 5900 and
8080 and ADB on a deployed host: `PUBLIC_IP=<host> python3 tests/test_connectivity.py`
(optionally `SSH_USER`, `SSH_KEY`). Nothing in this repo listens on 4723
(Appium) or 5900 (VNC), so expect those two to fail.
`run-tests.sh --list` shows a `connectivity` suite, but the runner does not
execute it.

## Making a change

- Keep the two runtimes separate: no cameras on Redroid, no Play on
  Cuttlefish ([docs/architecture.md](docs/architecture.md)).
- Shell scripts must pass `bash -n`. New scripts should source
  `scripts/lib/log.sh` and accept `--help`; a script with a `--dry-run` mode
  is much easier to test.
- New log labels go in both `api/cloudphone_logging.py` and
  `scripts/lib/log.sh`; `tests/test_logging.py` fails if they differ.
- New environment variables go in [docs/configuration.md](docs/configuration.md)
  and, if users need them, `.env.example`.
- New or changed endpoints go in [docs/api/](docs/api/).
- Never commit tokens, keys, IP addresses of your own hosts, or GApps zips.

## Reporting bugs

Open a GitHub issue with what you ran, what you expected, and what happened,
including the `/health` output and relevant `GET /logs` lines with tokens
removed. For security problems, see [SECURITY.md](SECURITY.md).
