# AGENTS.md

## Cursor Cloud specific instructions

### Overview

This is an Android ARM Cloud Phone infrastructure project with two Python/Flask services:

- **Control API** (`api/server.py`, port 8080): REST API for Android device control via ADB (screenshots, input, proxy, GPS, anti-detection, app management, async jobs).
- **Orchestrator** (`orchestrator/server.py`, port 8090): Fleet management service that provisions cloud phone instances and relays commands to the Control API.

An **Agent API** (`api/agent_api.py`, port 8080) provides an LLM-agent-friendly alternative interface.

### Running services locally

Both services run without an actual ADB/Cuttlefish device. The Control API reports `"status": "degraded"` when ADB is unavailable, but all non-ADB endpoints (identity generation, proxy/location state, config, job queue) work fully.

```bash
source .venv/bin/activate

# Control API (port 8080)
API_HOST=127.0.0.1 API_PORT=8080 python api/server.py

# Orchestrator (port 8090, mock mode — no OCI needed)
ORCH_DEPLOY_MODE=mock ORCH_MOCK_API_URL=http://127.0.0.1:8080 ORCH_HOST=127.0.0.1 ORCH_PORT=8090 python orchestrator/server.py
```

### Running tests

```bash
source .venv/bin/activate

# Unit tests (no external deps, fast)
python -m pytest tests/test_orchestrator_unit.py -v

# Integration test (spins up mock Control API + orchestrator subprocess)
python tests/test_orchestrator_integration.py

# E2E test (full login-flow operation through mock stack)
python tests/test_orchestrator_e2e.py
```

The `test_agent_api.py` and `test_connectivity.py` tests require a live Cuttlefish device and are not runnable in the cloud agent VM.

### Gotchas

- `python3.12-venv` system package is required to create the virtualenv. The update script handles venv creation.
- The project has no linter config (no flake8/ruff/pylint setup). There is no `pyproject.toml` or `setup.py`.
- The `cloud-phone` CLI (`./cloud-phone`) is a bash wrapper. `api-run` and `orchestrator-run` subcommands start the services.
- No `.env` file is loaded automatically; copy `.env.example` to `.env` if needed and export vars manually.
