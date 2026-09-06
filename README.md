# Android ARM Cloud Phone (OCI ARM64)

Two runtimes, two jobs. Do not merge them. Canonical write-up: [`docs/RUNTIME-SPLIT.md`](docs/RUNTIME-SPLIT.md).
Lab hosts / ports / token **names**: [`docs/OPS-ORCHESTRATOR.md`](docs/OPS-ORCHESTRATOR.md) and sibling
`osl-oc-agent/.AGENT/CLOUD-PHONE-ORCH.md` (orchestrator **`129.146.105.26:8090`**).

| Runtime | Job |
|---|---|
| **Redroid** (Docker) | **Default orchestrator pool** — GApps / Play phones for `mobile.*` automation |
| **Cuttlefish** (KVM) | On-demand camera/mic ingest: nginx-rtmp, FFmpeg bridge, v4l2-class sinks |

Virtual camera HAL on Redroid/Waydroid **failed** (ABI/VNDK). That is why ingest stays on Cuttlefish. We are **not** retrying HAL-on-container. Waydroid stays parked.

## Redroid — GApps automation pool (default spawn)

```bash
./cloud-phone deploy-redroid --name redroid-source --ocpus 2 --memory 8
GAPPS_ZIP=/path/to/MindTheGapps-arm64.zip ./cloud-phone gapps-install --name redroid
./cloud-phone gapps-check --adb 127.0.0.1:5555
COMPARTMENT_ID=<ocid> ./cloud-phone create-golden <IP> cloud-phone-redroid-gapps-v1 redroid

# Orchestrator reuses idle Redroid VMs; POST /sessions defaults to this pool
REDROID_GOLDEN_IMAGE_ID=<ocid> ORCH_DEPLOY_MODE=oci ./cloud-phone orchestrator-run
# POST /sessions  {"owner_user_id":"alice"}
# POST /sessions  {"owner_user_id":"alice","purpose":"camera"}   # Cuttlefish ingest instead
```

Never commit a GApps zip. Empty `/opt/gapps/gapps.zip` is rejected. Details: [`docs/GAPPS.md`](docs/GAPPS.md).

## Cuttlefish — ingest (spawn only when a camera stream is needed)

```bash
./cloud-phone deploy --name cuttlefish-source --ocpus 4 --memory 24
./cloud-phone verify-ingest --vm <OCI_PUBLIC_IP>
COMPARTMENT_ID=<ocid> ./cloud-phone create-golden <OCI_PUBLIC_IP> cloud-phone-cuttlefish-v1 cuttlefish
CUTTLEFISH_GOLDEN_IMAGE_ID=<ocid> ./cloud-phone deploy-fleet --platform cuttlefish --count 2 --verify-ingest
```

Do **not** bake Play/GMS into the Cuttlefish golden.

## After machine wipe (quick path)

```bash
git clone <your-repo-url> android-arm-cloud-phone
cd android-arm-cloud-phone
cp .env.example .env

python3 -m venv .venv
source .venv/bin/activate
pip install -r api/requirements.txt -r orchestrator/requirements.txt

# Automation phones (no KVM):
./cloud-phone redroid-up --dry-run --json --name phone-1

# Ingest host (needs /dev/kvm):
./cloud-phone deploy --name cuttlefish-source --ocpus 4 --memory 24
./cloud-phone verify-ingest --vm <OCI_PUBLIC_IP>
```

## Tests

```bash
./cloud-phone test                 # all offline suites — no device, no OCI
./cloud-phone test --coverage      # + coverage, fails under 60% in CI
./cloud-phone test --list
./cloud-phone test --suite ladder-e2e
```

Includes the dual-pool ladder (Redroid vs Cuttlefish) and a mobile e2e scenario
(proxy → signup → capped swipe) against a simulated phone.
[`docs/TESTING.md`](docs/TESTING.md) · [`docs/LOGGING.md`](docs/LOGGING.md)
(`[ADB]` commanders, `[APM]` Appium, `[CMD]` commandlets, `[VNC]` viewports).

## Procedures — one script, four surfaces

A procedure is a list of steps in one shared vocabulary; a surface adapter
decides how each step reaches the world (`mobile`, `web`, `chrome`, `console`).
Unsupported actions fail validation before step 1 touches a device, and
`install` / `submit` / `purchase` stay approval-gated.

```bash
curl -s $ORCH/procedures/surfaces | jq
curl -X POST $ORCH/procedures -d '{"sync":true,"steps":[...]}'
```

Details: [`docs/PROCEDURES.md`](docs/PROCEDURES.md).

## Project structure

```text
android-arm-cloud-phone/
├── cloud-phone
├── api/
├── orchestrator/          # default purpose=automation → Redroid pool
├── orchestrator/procedures.py
├── orchestrator/rules.py
├── api/cloudphone_logging.py
├── docker/redroid-compose.yml
├── scripts/redroid-up.sh
├── scripts/install-gapps-redroid.sh
├── scripts/install-redroid-cloud-phone.sh
├── scripts/deploy-redroid-oci.sh
├── scripts/run-tests.sh
├── scripts/lib/log.sh
├── systemd/redroid-*.service
├── systemd/cuttlefish-*.service
└── docs/RUNTIME-SPLIT.md
```

## Documentation

- `docs/RUNTIME-SPLIT.md` — why two images; orchestrator pool
- `docs/GAPPS.md` — Play install on Redroid only
- `docs/PROCEDURES.md` — step vocabulary, surfaces, approval gates
- `docs/AUTH-AND-HEALTH.md` — the three tokens, and why `healthy` used to lie
- `docs/OPS-ORCHESTRATOR.md` — lab IP/port/token **names**
- `docs/TESTING.md` — offline suites, coverage, dual-pool ladder R0–R4
- `docs/LOGGING.md` — label scheme and filtering
- `docs/DEPLOYMENT.md` — Cuttlefish ingest deploy
- `docs/CUTTLEFISH_PHASE1.md` / `CUTTLEFISH_PHASE2_RTMP_BRIDGE.md` / `CUTTLEFISH_OCI_GOLDEN_IMAGE.md`
- `docs/CLEANROOM_BOOTSTRAP.md`
- `FUTURE_CONSIDERATIONS_CAMERA_STACK.md` — parked HAL-on-container notes
