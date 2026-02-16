# Deployment Guide (Cuttlefish-Only)

This repository uses a single deployment model:

- OCI ARM64 instance
- Cuttlefish runtime
- nginx-rtmp ingest
- FFmpeg bridge for front/back camera feeds and mic feed

## Prerequisites

Set OCI environment variables locally:

```bash
export COMPARTMENT_ID="ocid1.compartment..."
export SUBNET_ID="ocid1.subnet..."
export AVAILABILITY_DOMAIN="ABxx:REGION-AD-1"
export SSH_KEY_FILE="$HOME/.ssh/redroid_oci.pub"
```

## 1) Deploy to new OCI instance

```bash
./scripts/deploy-cuttlefish-oci.sh \
  --name cuttlefish-source \
  --ocpus 4 \
  --memory 24
```

Recommended baseline: `4 OCPU / 24GB RAM`.

## 2) Verify ingest path (video + audio)

```bash
./scripts/verify-cuttlefish-ingest.sh --vm <OCI_PUBLIC_IP>
```

This executes:

- runtime validation (`cuttlefish-phase1-validate.sh`)
- RTMP bridge A/V checks (`test-cuttlefish-rtmp-bridge.sh`)

## 3) Prepare and create golden image

On source instance:

```bash
ssh -i ~/.ssh/redroid_oci ubuntu@<OCI_PUBLIC_IP> \
  'sudo /opt/redroid-scripts/prepare-golden-image.sh --platform cuttlefish'
```

From local machine:

```bash
COMPARTMENT_ID=<ocid> \
./scripts/create-golden-image.sh <OCI_PUBLIC_IP> cloud-phone-cuttlefish-v1 cuttlefish
```

## 4) Deploy from golden image

Single instance:

```bash
GOLDEN_IMAGE_ID=<image_ocid> \
COMPARTMENT_ID=<ocid> \
SUBNET_ID=<ocid> \
AVAILABILITY_DOMAIN=<ad> \
./scripts/deploy-from-golden.sh \
  --platform cuttlefish \
  --name phone-1 \
  --ocpus 4 \
  --memory 24 \
  --wait-check
```

Fleet:

```bash
GOLDEN_IMAGE_ID=<image_ocid> \
COMPARTMENT_ID=<ocid> \
SUBNET_ID=<ocid> \
AVAILABILITY_DOMAIN=<ad> \
./scripts/deploy-golden-fleet.sh \
  --platform cuttlefish \
  --count 5 \
  --name-prefix phone \
  --parallel 2 \
  --verify-ingest
```
