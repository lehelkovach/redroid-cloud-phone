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

**Secret naming (important):** the `KSG_*` secrets (`KSG_DEV_VM_*`) belong to a *different*
`KSG` project service (not this repo) — do **not** use them for the cloud phone. The cloud-phone service uses
secrets prefixed `WAYDROID_`, `REDROID_`, or `CLOUD_PHONE_` (e.g. a `CLOUD_PHONE_DEV_VM_SSH_KEY`
/ `_HOST` / `_USER` / `_APP_DIR`). As of this writing none of those are set yet, so direct SSH to
the cloud-phone dev box isn't possible from the agent until they are added. The shared `OCI_*`
secrets (tenancy/compartment/user/fingerprint/region/`OCI_PRIVATE_KEY_B64`) DO work and are how
the fleet is discovered/managed (see below).

If/when a cloud-phone SSH key secret is provided, note that secrets are stored as a SINGLE LINE
(newlines stripped); rebuild PEM line breaks before use or ssh/ssh-keygen will reject it:

```bash
python3 - <<'PY'
import os, re, textwrap
raw = os.environ["CLOUD_PHONE_DEV_VM_SSH_KEY"].strip()  # whichever cloud-phone key secret exists
m = re.search(r"-----BEGIN ([A-Z ]+?)-----(.*?)-----END \1-----", raw, re.S)
label, body = m.group(1).strip(), re.sub(r"\s+", "", m.group(2))
open("/tmp/dev_key","w").write(f"-----BEGIN {label}-----\n" + "\n".join(textwrap.wrap(body,70)) + f"\n-----END {label}-----\n")
import os as o; o.chmod("/tmp/dev_key",0o600)
PY
```

Other caveats:

- App services on the phone VMs bind to localhost / are VCN-restricted, so reach them over SSH
  (`ssh ... curl http://127.0.0.1:<port>/health`) or an SSH tunnel — not directly via the public IP.
- GitHub Actions auto-deploy (`.github/workflows/deploy-dev.yml`) uses its own **repository Actions
  secrets** `DEV_DEPLOY_HOST/USER/SSH_KEY/APP_DIR` (separate from the Cursor env secrets above). The
  workflow's "Check deploy is configured" step prints which are visible.

To test the running control plane against a real device VM once one exists, the repo's existing
scripts take the target explicitly:
`PUBLIC_IP=<ip> python tests/test_connectivity.py` and
`python tests/test_agent_api.py --api-url http://<host>:8080`.

### Viewing / controlling the phone UI (scrcpy over ADB)

The previous/working setup views and controls the Android UI with **`scrcpy` over ADB** (not
WebRTC or VNC). ADB is the device control path (the Control API also wraps ADB; `ADB_CONNECT`
defaults to `127.0.0.1:5555`). Typical flow from a laptop:

```bash
# ADB/5555 is not opened publicly by default — tunnel it over SSH to the VM host:
ssh -i <cloud-phone-key> -L 5555:127.0.0.1:5555 ubuntu@<host>
# then, locally:
adb connect 127.0.0.1:5555
scrcpy            # mirror + control (keyboard/mouse/touch)
```

So the SSH key only authenticates the VM host login; the Android UI itself is reached via
ADB+scrcpy (tunnelled), and programmatically via the Control API (`:8080`).

### OCI access + finding the Android emulator instance

The injected `OCI_*` secrets (`OCI_TENANCY_OCID`, `OCI_USER_OCID`, `OCI_FINGERPRINT`,
`OCI_REGION`, `OCI_COMPARTMENT_OCID`, `OCI_PRIVATE_KEY_B64`) authenticate as the tenancy's
API user and allow read/manage of compute. Build `~/.oci/config` from them
(`base64 -d` the key into `key_file`), then e.g.:

```bash
pip install oci-cli   # not in the update script; installing it downgrades `click` in the venv
oci compute instance list -c "$OCI_COMPARTMENT_OCID" --all --output table
oci compute instance list-vnics --instance-id <ocid> --query 'data[0]."public-ip"' --raw-output
```

Non-obvious facts verified via the OCI API:

- The `KSG_DEV_VM_*` dev VM (`147.224.250.240`) is instance
  `[REDACTED]-dev-...` and runs a *different* app (containerized API + ArangoDB), not Android.
- The Android/cloud-phone compute instances (`cloud-phone-gapps-test`, `redroid-camera-build`,
  `waydroid-test-1`, …) are authorized with a **different SSH key that is NOT in the injected
  secrets**, so you cannot `ssh`/run commands on them with `KSG_DEV_VM_KEY`. The OCI API still
  lets you list/start/stop/inspect them. Their control-plane/ADB ports are restricted to the VCN
  (not reachable from outside), so probe them via SSH from on-box, not over the public IP.
- **Canonical dev instance: `redroid-camera-build`** (4 OCPU / 24 GB, public IP `152.70.146.56`,
  OCID `ocid1.instance.oc1.phx.anyhqljrgmifkaqctzrsdrfcqb7v52rrmxcxidwmq2bspmvo7f2ljurhasta`).
  It is the newest build (2026-02-15) and the only one matching the README's Cuttlefish baseline.
  Point `DEV_DEPLOY_HOST` / the dev-server secrets at this instance.
- The older/duplicate phone instances were stopped (not terminated, so they can be restarted) to
  leave a single dev box: two `cloud-phone-gapps-test` (`137.131.52.136`, `129.146.109.119`) and
  `waydroid-test-1` (`161.153.55.58`). A separate release instance is expected later (e.g. deployed
  from a golden image).

### Orchestrator as an independent proxy (multi-instance routing)

Per `docs/AGENT_COORDINATION.md`, the orchestrator (`orchestrator/server.py`, :8090) is meant to
run as its **own standalone service** — separate from any phone instance — and proxy/route to one
or more per-phone Control APIs (`api/server.py`, :8080). It already supports this: register phones
via `POST /instances`, then route by id through `GET|POST /phones/<id>/{status,health,input,screenshot,jobs}`,
with `Authorization: Bearer $ORCH_API_TOKEN`. `ORCH_DEPLOY_MODE=oci` lets it provision phones
on demand via `scripts/deploy-from-golden.sh`; `mock` routes to `ORCH_MOCK_API_URL`.

`systemd/orchestrator.service` runs it standalone (working dir `/opt/cloud-phone-orchestrator`,
its own venv, port 8090, token + max-instances via env / `EnvironmentFile`). It is deliberately
**not** part of `cuttlefish-cloud-phone.target` (that target is the per-phone stack:
cuttlefish-launch + nginx-rtmp + rtmp-bridge + control-api). Deploy the orchestrator on a control
host (or the dev box) and register the phone instances it should manage.

Launch config + fleet fan-out (added):

- `orchestrator/launch_config.py` defines a per-instance startup config (`instance_id`, `proxy`,
  `device_identity`, fire-and-forget `startup_tasks`, `labels`, open `extra`) and renders it to
  cloud-init user-data. `POST /instances` accepts `{"launch_config": {...}}`; in OCI mode the
  config is delivered via `deploy-from-golden.sh --user-data-file`, in mock mode it is applied to
  the target Control API. See `config/launch-config.example.json`.
- The Control API applies it at boot from `LAUNCH_CONFIG_FILE` (default `/etc/cloud-phone/launch.json`)
  and via `POST /launch-config/apply` / `GET /launch-config` (sets proxy, enqueues startup tasks).
- Async multi-phone control: `POST /fleet/operations` (targets `instance_ids` or all) dispatches an
  operation to many phones concurrently; poll `GET /fleet/operations/<id>` for per-instance status.

### Auto-deploy on push to `dev`

There is **no** CI/CD auto-deploy configured anywhere in the repo (no `.github/workflows` on any
historical branch, no webhook). The only historical deploy was manual: `ssh <host> 'cd <app> && git pull'`
plus `systemctl restart`. `.github/workflows/deploy-dev.yml` adds a gated GitHub Actions workflow
that hot-loads the dev server on push to `dev`; it no-ops until these repo secrets are set:
`DEV_DEPLOY_HOST`, `DEV_DEPLOY_USER`, `DEV_DEPLOY_SSH_KEY`, `DEV_DEPLOY_APP_DIR`
(optional `DEV_DEPLOY_PORT`, `DEV_DEPLOY_RESTART_CMD`). A `dev` branch must also exist.

### Tests

There is no pytest/CI config or linter configured. Run tests directly from the repo root
(so `from orchestrator import server` resolves):

- Unit: `python -m unittest tests.test_orchestrator_unit -v`
- Integration: `python tests/test_orchestrator_integration.py`
- E2E login flow: `python tests/test_orchestrator_e2e.py`
- `tests/test_agent_api.py` and `tests/test_connectivity.py` require a live Control API /
  a deployed VM (real device) and are not runnable in this VM.
