# Cuttlefish Cloud Phone (OCI ARM64)

This repository is now focused on a single stack:

- Cuttlefish Android on OCI ARM64
- OBS RTMP ingest via `nginx-rtmp`
- FFmpeg bridge to Cuttlefish front/back camera sinks and mic sink
- Golden image deployment for multi-device fleets

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

## Project structure (current)

```text
redroid-cloud-phone/
├── cloud-phone
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
- `FUTURE_CONSIDERATIONS_CAMERA_STACK.md`
