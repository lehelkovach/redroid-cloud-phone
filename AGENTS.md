# AGENTS.md

## Cursor Cloud specific instructions

### What runs locally vs. what does not

This repo is an Android ARM "Cloud Phone" platform. Most of it (Cuttlefish Android,
`nginx-rtmp`, the FFmpeg camera/mic bridge, golden-image/fleet deploys) only runs on a
provisioned **OCI ARM64 host with KVM** and cannot run in this VM. The locally runnable
and testable part is the **Python control plane**: the Control API (`api/server.py`,
port 8080) and the Orchestrator (`orchestrator/server.py`, port 8090). Both are Flask
apps that hold all state in memory (no database).

### Setup / dependencies

- Python 3 with a virtualenv at `.venv` (system `python3-venv` must be present to create it).
- Dependencies are installed from `api/requirements.txt` and `orchestrator/requirements.txt`
  by the startup update script. Activate with `source .venv/bin/activate` before running anything.

### Running the services (dev mode)

Run from the repo root with the venv active. The `cloud-phone` CLI wraps these:
`./cloud-phone api-run` and `./cloud-phone orchestrator-run`.

- Control API: `API_HOST=127.0.0.1 python api/server.py` (defaults to bind `0.0.0.0:8080`).
- Orchestrator: `ORCH_DEPLOY_MODE=mock ORCH_MOCK_API_URL=http://127.0.0.1:8080 python orchestrator/server.py`
  (defaults to `0.0.0.0:8090`). Keep `ORCH_DEPLOY_MODE=mock` locally; `oci` mode needs real
  OCI credentials + a `GOLDEN_IMAGE_ID`.
- If `API_TOKEN` / `ORCH_API_TOKEN` are set, send `Authorization: Bearer <token>`. They are
  empty by default, so local curl needs no auth (orchestrator `/health` never requires auth).

### Non-obvious caveats

- **`adb` device ops always fail here.** The Control API's `/health` reports `degraded` and
  device endpoints error with "No such file or directory: 'adb'" because there is no `adb`
  binary and no real Cuttlefish device in this VM. This is expected — the services themselves
  are healthy. Endpoints that don't touch `adb` work fully (e.g. `POST /device/identity/generate`,
  `/config`, `/proxy` GET, orchestrator instance/lease management).
- To exercise the full orchestrator login-automation flow end to end without a real device,
  use the bundled tests, which spin up their own in-process **mock** Control API representing a device.

### Tests

There is no pytest/CI config or linter configured. Run tests directly from the repo root
(so `from orchestrator import server` resolves):

- Unit: `python -m unittest tests.test_orchestrator_unit -v`
- Integration: `python tests/test_orchestrator_integration.py`
- E2E login flow: `python tests/test_orchestrator_e2e.py`
- `tests/test_agent_api.py` and `tests/test_connectivity.py` require a live Control API /
  a deployed VM (real device) and are not runnable in this VM.
