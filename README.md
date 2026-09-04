# Android ARM Cloud Phone

Two runtimes, two jobs. Do not merge them. Canonical write-up: [`docs/RUNTIME-SPLIT.md`](docs/RUNTIME-SPLIT.md).

| Runtime | Job |
|---|---|
| **Redroid** (Docker) | GApps / Play phones for `mobile.*` automation (Playwright-like containers) |
| **Cuttlefish** (KVM) | Virtual camera + mic ingest: nginx-rtmp, FFmpeg bridge, v4l2-class sinks |

Virtual camera HAL on Redroid/Waydroid **failed** (ABI/VNDK). That is why this repo forked to Cuttlefish for ingest. We are **not** retrying HAL-on-container. Waydroid stays parked.

## Redroid — GApps phones

```bash
./cloud-phone redroid-up --name phone-1 --adb-port 5555
GAPPS_ZIP=/path/to/MindTheGapps-arm64.zip ./cloud-phone gapps-install --name phone-1
./cloud-phone gapps-check --adb 127.0.0.1:5555

# Orchestrator (one phone per owner; provision:true starts another container)
ORCH_DEPLOY_MODE=redroid ORCH_REDROID_DRY_RUN=1 ./cloud-phone orchestrator-run
# POST /sessions  {"owner_user_id":"alice","purpose":"play","provision":true}
```

Never commit a GApps zip. Empty `/opt/gapps/gapps.zip` is rejected. Details: [`docs/GAPPS.md`](docs/GAPPS.md).

## Cuttlefish — ingest (camera / mic)

```bash
./cloud-phone deploy --name cuttlefish-source --ocpus 4 --memory 24
./cloud-phone verify-ingest --vm <OCI_PUBLIC_IP>
COMPARTMENT_ID=<ocid> ./cloud-phone create-golden <OCI_PUBLIC_IP> cloud-phone-cuttlefish-v1 cuttlefish
GOLDEN_IMAGE_ID=<image_ocid> ./cloud-phone deploy-fleet --count 5 --parallel 2 --verify-ingest
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

# Phones (no KVM required):
./cloud-phone redroid-up --dry-run --json --name phone-1

# Ingest host (needs /dev/kvm):
./cloud-phone deploy --name cuttlefish-source --ocpus 4 --memory 24
./cloud-phone verify-ingest --vm <OCI_PUBLIC_IP>
```

## Project structure

```text
android-arm-cloud-phone/
├── cloud-phone
├── api/
├── orchestrator/
├── docker/redroid-compose.yml
├── scripts/redroid-up.sh
├── scripts/install-gapps-redroid.sh
├── systemd/redroid-container.service
├── systemd/cuttlefish-*.service
└── docs/RUNTIME-SPLIT.md
```

## Documentation

- `docs/RUNTIME-SPLIT.md` — why two images
- `docs/GAPPS.md` — Play install on Redroid only
- `docs/DEPLOYMENT.md` — Cuttlefish ingest deploy
- `docs/CUTTLEFISH_PHASE1.md` / `CUTTLEFISH_PHASE2_RTMP_BRIDGE.md` / `CUTTLEFISH_OCI_GOLDEN_IMAGE.md`
- `docs/API_REFERENCE.md` / `docs/AGENT_COORDINATION.md`
- `FUTURE_CONSIDERATIONS_CAMERA_STACK.md` — parked HAL-on-container notes
