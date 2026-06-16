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

### Branch + OCI deployment workflow (hot-load on push)

Intended promotion model for this project:

- The **`dev` branch is the integration branch** — merge new agent changes here. Pushing to
  `dev` is expected to hot-load the **dev** OCI server. Validate changes against the dev OCI
  server before promoting.
- The **`main`/release branch** is expected to hot-load the **release/production** OCI server.
- So the flow is: agent work → merge to `dev` → auto-deploy to dev OCI server → test there →
  promote to the release branch → auto-deploy to release OCI server.

Dev-server access is provided through injected secrets (names, not values):
`KSG_DEV_VM_HOST`, `KSG_DEV_VM_USER`, `KSG_DEV_VM_PORT`, `KSG_DEV_VM_KEY` (SSH private key),
`KSG_DEV_VM_APP_DIR`, `KSG_DEV_VM_BRANCH`, `KSG_DEV_VM_API_URL`. Connect read-only with, e.g.:

```bash
# KSG_DEV_VM_KEY is stored as a SINGLE LINE (newlines stripped) — rebuild PEM line breaks first,
# or ssh-keygen/ssh will reject it.
python3 - <<'PY'
import os, re, textwrap
raw = os.environ["KSG_DEV_VM_KEY"].strip()
m = re.search(r"-----BEGIN ([A-Z ]+?)-----(.*?)-----END \1-----", raw, re.S)
label, body = m.group(1).strip(), re.sub(r"\s+", "", m.group(2))
open("/tmp/dev_key","w").write(f"-----BEGIN {label}-----\n" + "\n".join(textwrap.wrap(body,70)) + f"\n-----END {label}-----\n")
import os as o; o.chmod("/tmp/dev_key",0o600)
PY
ssh -i /tmp/dev_key -p "$KSG_DEV_VM_PORT" -o StrictHostKeyChecking=no \
  "$KSG_DEV_VM_USER@$KSG_DEV_VM_HOST" "cd $KSG_DEV_VM_APP_DIR && git rev-parse --abbrev-ref HEAD"
```

Non-obvious gotchas / caveats (confirm before relying on these):

- The `KSG_DEV_VM_KEY` newline-stripping issue above is required every time; skipping it yields
  an "invalid format" error from ssh.
- App services on these VMs bind to localhost, so reach them over SSH
  (`ssh ... curl http://127.0.0.1:<port>/health`) or an SSH tunnel — not directly via the public IP.
- **Deploy target must be confirmed.** As observed during setup, the VM reachable via
  `KSG_DEV_VM_*` is currently running a *different* application (a containerized API + ArangoDB,
  checked out on `main`), and the cloud-phone control plane / RTMP / Cuttlefish stack is **not**
  deployed there. There is also no `dev`/`main` branch in this repo yet (only `master`) and no
  secrets for a separate release/production OCI server. Verify the intended cloud-phone dev/release
  servers and branch names before running cloud-phone deploys or tests against them.

To test the running control plane against a real device VM once one exists, the repo's existing
scripts take the target explicitly:
`PUBLIC_IP=<ip> python tests/test_connectivity.py` and
`python tests/test_agent_api.py --api-url http://<host>:8080`.

### Tests

There is no pytest/CI config or linter configured. Run tests directly from the repo root
(so `from orchestrator import server` resolves):

- Unit: `python -m unittest tests.test_orchestrator_unit -v`
- Integration: `python tests/test_orchestrator_integration.py`
- E2E login flow: `python tests/test_orchestrator_e2e.py`
- `tests/test_agent_api.py` and `tests/test_connectivity.py` require a live Control API /
  a deployed VM (real device) and are not runnable in this VM.
