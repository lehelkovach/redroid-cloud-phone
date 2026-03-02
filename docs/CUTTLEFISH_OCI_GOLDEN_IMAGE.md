# Cuttlefish OCI + Golden Image Workflow

This workflow keeps the same ingest composition you used before:

- OBS RTMP publish to nginx-rtmp on the OCI host
- ffmpeg bridge on host normalizes stream and splits front/back video plus mic audio feeds
- Android runtime is Cuttlefish

## 1) Deploy Cuttlefish Stack on OCI ARM64

Use the deployment script to either create a new beefy instance or reuse an existing one.

Create new instance (recommended baseline for Cuttlefish):

```bash
./scripts/deploy-cuttlefish-oci.sh \
  --name cuttlefish-source \
  --ocpus 4 \
  --memory 24
```

Reuse existing dev instance:

```bash
./scripts/deploy-cuttlefish-oci.sh \
  --to-instance <OCI_PUBLIC_IP>
```

If Cuttlefish host tools are already preinstalled in your image, this brings up:

- `cuttlefish-cloud-phone.target`
- `cuttlefish-launch.service`
- `cuttlefish-rtmp-bridge.service`
- `nginx-rtmp.service`

## 2) Validate runtime and bridge

```bash
ssh -i ~/.ssh/android_arm_cloud_phone_oci ubuntu@<OCI_PUBLIC_IP> \
  '/opt/cloud-phone-scripts/cuttlefish-phase1-validate.sh --local --instance-name cvd-arm64-1 --webrtc-port 8443'
```

Run bridge test:

```bash
ssh -i ~/.ssh/android_arm_cloud_phone_oci ubuntu@<OCI_PUBLIC_IP> \
  '/opt/cloud-phone-scripts/test-cuttlefish-rtmp-bridge.sh --local'
```

## 3) Prepare and create a new golden image

Prepare on the instance:

```bash
ssh -i ~/.ssh/android_arm_cloud_phone_oci ubuntu@<OCI_PUBLIC_IP> \
  'sudo /opt/cloud-phone-scripts/prepare-golden-image.sh --platform cuttlefish'
```

Create image from local machine:

```bash
COMPARTMENT_ID=<ocid> \
./scripts/create-golden-image.sh <OCI_PUBLIC_IP> cloud-phone-cuttlefish-v1 cuttlefish
```

## 4) Deploy from golden image

```bash
GOLDEN_IMAGE_ID=<new_image_ocid> \
COMPARTMENT_ID=<ocid> \
SUBNET_ID=<ocid> \
AVAILABILITY_DOMAIN=<ad> \
./scripts/deploy-from-golden.sh \
  --platform cuttlefish \
  --name cuttlefish-prod-1 \
  --ocpus 4 \
  --memory 24 \
  --wait-check
```

Deploy multiple devices from one golden image:

```bash
GOLDEN_IMAGE_ID=<new_image_ocid> \
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

## Notes

- `deploy-from-golden.sh` in this repo is now cuttlefish-only.
- Cuttlefish sizing is materially higher than container-first Android runtimes; keep at least `4 OCPU / 24GB` unless you have validated lower.
- OBS still publishes RTMP exactly as before: `rtmp://<IP>/live` with stream key `cam`.
- Release gate command: `./scripts/verify-cuttlefish-ingest.sh --vm <OCI_PUBLIC_IP>`
