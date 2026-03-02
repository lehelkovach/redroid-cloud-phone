# Android ARM Cloud Phone (OCI ARM64)

This repository is now focused on a single stack:

- Cuttlefish Android on OCI ARM64
- OBS RTMP ingest via `nginx-rtmp`
- FFmpeg bridge to Cuttlefish front/back camera sinks and mic sink
- Golden image deployment for multi-device fleets
- Control API and orchestrator for multi-phone automation

## Updates and notes

- Runtime is Cuttlefish-only; legacy runtime-specific assumptions were removed.
- Control-plane support is included (`api/` + `orchestrator/`) and deployable from repo.
- Naming is now generalized for runtime swap flexibility (`android-arm-cloud-phone`).
- Default SSH key naming is `~/.ssh/android_arm_cloud_phone_oci(.pub)`.
- Clean-machine recovery is documented in `docs/CLEANROOM_BOOTSTRAP.md`.

## Canonical commands

```bash
# Deploy fresh OCI instance and install cuttlefish stack
./cloud-phone deploy --name cuttlefish-source --ocpus 4 --memory 24

# Verify runtime + ingest (video+audio)
./cloud-phone verify-ingest --vm <OCI_PUBLIC_IP>

# Create golden image from configured source
COMPARTMENT_ID=<ocid> ./cloud-phone create-golden <OCI_PUBLIC_IP> cloud-phone-cuttlefish-v1 cuttlefish

# Deploy one from golden
GOLDEN_IMAGE_ID=<image_ocid> ./cloud-phone deploy-golden --name phone-1 --wait-check

# Deploy many from same golden image
GOLDEN_IMAGE_ID=<image_ocid> ./cloud-phone deploy-fleet --count 5 --parallel 2 --verify-ingest
```

## After machine wipe (quick path)

```bash
git clone <your-repo-url> android-arm-cloud-phone
cd android-arm-cloud-phone
cp .env.example .env

# Optional: local control-plane venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r api/requirements.txt -r orchestrator/requirements.txt

# Deploy and validate
./cloud-phone deploy --name cuttlefish-source --ocpus 4 --memory 24
./cloud-phone verify-ingest --vm <OCI_PUBLIC_IP>
```

## Project structure (current)

```text
android-arm-cloud-phone/
├── cloud-phone
├── api/
│   ├── server.py
│   └── requirements.txt
├── orchestrator/
│   ├── server.py
│   └── requirements.txt
├── scripts/
│   ├── deploy-cuttlefish-oci.sh
│   ├── install-cuttlefish-cloud-phone.sh
│   ├── deploy-from-golden.sh
│   ├── deploy-golden-fleet.sh
│   ├── create-golden-image.sh
│   ├── prepare-golden-image.sh
│   ├── cuttlefish-phase1-setup.sh
│   ├── cuttlefish-phase1-validate.sh
│   ├── cuttlefish-rtmp-bridge.sh
│   ├── test-cuttlefish-rtmp-bridge.sh
│   └── verify-cuttlefish-ingest.sh
├── systemd/
│   ├── cuttlefish-cloud-phone.target
│   ├── cuttlefish-launch.service
│   ├── cuttlefish-rtmp-bridge.service
│   ├── control-api.service
│   └── nginx-rtmp.service
└── docs/
    ├── DEPLOYMENT.md
    ├── CUTTLEFISH_PHASE1.md
    ├── CUTTLEFISH_PHASE2_RTMP_BRIDGE.md
    └── CUTTLEFISH_OCI_GOLDEN_IMAGE.md
```

## Documentation

- `docs/DEPLOYMENT.md`
- `docs/CUTTLEFISH_PHASE1.md`
- `docs/CUTTLEFISH_PHASE2_RTMP_BRIDGE.md`
- `docs/CUTTLEFISH_OCI_GOLDEN_IMAGE.md`
- `docs/API_REFERENCE.md`
- `docs/AGENT_COORDINATION.md`
- `docs/CLEANROOM_BOOTSTRAP.md`
- `FUTURE_CONSIDERATIONS_CAMERA_STACK.md`
