# Roadmap

This page lists planned work and known problems. Nothing here has a date.

## Next: live camera and mic through Cuttlefish

Today the RTMP bridge splits an OBS stream into front, back and mic feeds and
the verify script checks they decode, but the feeds do not reach the guest's
camera or microphone. Steps to finish it:

1. Confirm `/dev/kvm` and a booting ARM64 Cuttlefish guest on the chosen OCI
   shape, and document the exact host-tools and guest-image versions used.
2. Pick an injection method for the Cuttlefish build in use and wire it in
   through `--front-cmd`, `--back-cmd` and `--mic-cmd` of
   `scripts/cuttlefish-rtmp-bridge.sh`.
3. Extend `cuttlefish-phase1-validate.sh` to check front and back facing in
   `dumpsys media.camera`, and add an app-level capture test for both cameras
   and for the mic.
4. Give Cuttlefish hosts the same token setup as Redroid hosts (an
   `EnvironmentFile` for `control-api.service`) and the correct ADB serial.
5. Capture a Cuttlefish golden image and run it through the orchestrator with
   `purpose: "camera"` end to end.

## Other planned work

- Reset (wipe `/data`) an instance between owners and record it, so released
  phones can return to the shared pool. The lease guard already waits for
  this.
- A systemd unit and persistent state for the orchestrator.
- Run the pytest-only tests in CI.

## Known issues

These are verified against the current code. None are fixed yet.

### Deployment and systemd

- `systemd/redroid-cloud-phone.target` has `Wants=control-api.service` (the
  Cuttlefish unit, not installed on Redroid hosts) instead of
  `control-api-redroid.service`. The Redroid Control API is still pulled in
  through its own `WantedBy=` because the installer enables it.
- The units set `CLOUD_PHONE_CONFIG=/etc/cloud-phone/config.json`, but
  `api/server.py` reads `CONFIG_FILE`. The setting has no effect (the default
  happens to be the same path).
- `control-api-redroid.service` hard-codes `ADB_CONNECT=127.0.0.1:5555` and
  runs `adb connect 127.0.0.1:5555` before start. Installing with a different
  `--adb-port` leaves the Control API pointed at the wrong port unless
  `ADB_CONNECT` is added to `/etc/default/redroid-cloud-phone`.
- `control-api.service` (Cuttlefish) also uses `127.0.0.1:5555`, has no
  `EnvironmentFile`, and the validate script falls back to `127.0.0.1:6520`
  for the Cuttlefish guest.
- `control-api.service` and `cuttlefish-cloud-phone.target` link to the old
  repository URL (`lehelkovach/android-arm-cloud-phone`).
- No installer sets `API_TOKEN`, so deployed Control APIs start without
  authentication on `0.0.0.0:8080`.
- `scripts/deploy-from-golden.sh` and `scripts/deploy-golden-fleet.sh` default
  to `--platform redroid`. `deploy-golden-fleet.sh` takes `GOLDEN_IMAGE_ID`
  before the platform-specific variables, so a Cuttlefish image in
  `GOLDEN_IMAGE_ID` with no `--platform` is launched as a Redroid host. Its
  header comment still says `--platform cuttlefish (required value)`.
- The archived clean-room guide (step 6) runs `deploy-fleet` without
  `--platform` and so hits the problem above.
- `create-golden-image.sh` treats `SSH_KEY_FILE` as a private key, while the
  deploy scripts treat it as the public key.
- `prepare-golden-image.sh` does not wipe the Redroid data volume, so
  signed-in accounts and app data are copied into golden images.
- `verify-redroid-phone.sh` sends no token, so it fails once `API_TOKEN` is
  set (`--wait-check` and `--run-tests` ignore that failure).
- The deploy scripts hard-code `SSH_USER=ubuntu`; the `SSH_USER` and
  `OCI_PROFILE` entries in `.env.example` are not read by them.
- `api/Dockerfile` is not documented or used by any script. Inside a
  container it cannot reach ADB on the host's `127.0.0.1` without host
  networking.
- The Redroid installer never checks for binder support in the host kernel.

### Control API

- `DELETE /proxy` and `DELETE /location` call `request.get_json()` and answer
  `415` unless the request has a JSON content type.
- `POST /device/input`, `POST /device/screen`, `POST /location` and the
  anti-detection endpoints report `success: true` without checking the `adb`
  result.
- `/opt/cloud-phone-scripts/proxy-control.sh` and `anti-detection.sh` are
  referenced but not in the repo.
- `GET /logs` is unauthenticated even when `API_TOKEN` is set.
- `/vnc/*` and `/appium/*` describe services that are not installed; Appium
  sessions always return `501`.

### Orchestrator and procedures

- No endpoint records a reset, so an instance is refused to every owner after
  its first one (`409 phone not clean`) until the orchestrator restarts.
- In `redroid` deploy mode every container shares one Control API URL; the
  phone is chosen by the `X-Cloud-Phone-Adb` header, and the Control API's
  in-memory state (proxy, location, `connected`) is shared between them. The
  first ADB port (`ORCH_REDROID_ADB_PORT_BASE`, 5555) collides with a phone
  started by hand on the default port.
- The `mobile` procedure adapter sends `screenshot` as `POST` to
  `/device/screenshot/base64` (GET only) and `install` to `/apps/install`
  (no such route).
- `/health` reports `"runtime": "cuttlefish"` in `oci` mode even though the
  default pool there is Redroid.
- Operation and procedure records keep request bodies, including login
  passwords, and `_run_operation` logs the payload and login steps
  unredacted.
- The `/phones/<id>/...` relays do not check leases.

### Tests and CI

- `tests/test_tenant_isolation.py` and `tests/test_ingest_endpoints.py` need
  `pytest`, which is not installed in CI, and `run-tests.sh` does not run
  them.
- `--rung N` runs one suite, not the whole rung, and `--rung 4` runs the
  `agent-api` suite only, not `tests/test_live.py`.
- `run-tests.sh --list` shows a `connectivity` suite that the runner never
  executes.
- The default coverage floor in `run-tests.sh` is 80%, but the suites cover
  about 69% and CI uses 60%.
- `flask` appears in both `api/requirements.txt` and
  `orchestrator/requirements.txt`; `gunicorn` is listed but unused, and
  `pytest` is not listed.

### Documentation pointers in code

Some code comments and unit files still name old document paths:

| Old path | Now |
|---|---|
| `docs/RUNTIME-SPLIT.md` | [docs/architecture.md](docs/architecture.md) |
| `docs/GAPPS.md` | [docs/gapps.md](docs/gapps.md) |
| `docs/LOGGING.md` | [docs/logging.md](docs/logging.md) |
| `docs/CUTTLEFISH_PHASE1.md`, `docs/CUTTLEFISH_PHASE2_RTMP_BRIDGE.md` | [docs/cuttlefish-ingest.md](docs/cuttlefish-ingest.md) (originals in `docs/archive/`) |
| `docs/OPS-ORCHESTRATOR.md` | [docs/api/orchestrator.md](docs/api/orchestrator.md) (original in `docs/archive/`) |

`.github/workflows/phone-lab-probe.yml` still has the maintainer's lab host
addresses as input defaults.
