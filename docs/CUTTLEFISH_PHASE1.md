# Cuttlefish Phase 1 (OCI ARM64 PoC)

Cuttlefish is the **ingest** runtime (virtual camera/mic). GApps/Play phones are **Redroid** — see [`RUNTIME-SPLIT.md`](./RUNTIME-SPLIT.md). Do not bake Play into this image.

This phase proves Cuttlefish can run on OCI ARM64 with:

- host virtualization enabled (`/dev/kvm`)
- Android boot and ADB connectivity
- WebRTC endpoint exposed
- camera service visible from Android (`dumpsys media.camera`)

Phase 1 is intentionally limited to platform validation and camera enumeration gates. Frame injection and stream quality checks are handled in later phases.

For RTMP ingest bridge implementation, continue with [`docs/CUTTLEFISH_PHASE2_RTMP_BRIDGE.md`](docs/CUTTLEFISH_PHASE2_RTMP_BRIDGE.md).

## Recommended OCI Shape

- `VM.Standard.A1.Flex`
- Start with `4 OCPU / 24 GB RAM` for a stable first pass
- Ubuntu 22.04 or 24.04 image
- 100 GB boot volume minimum

## Cost Planning (Quick Estimate)

Use OCI calculator for exact numbers in your region. As a rough estimate:

- `4 OCPU / 24 GB` running 24/7: low double-digit to low triple-digit USD/month depending on region/pricing model
- stop instance outside active testing windows to reduce cost

## Security Rules

Open ingress from your trusted IPs only:

- `TCP 22` (SSH)
- `TCP 8443` (WebRTC signaling; configurable)
- `TCP 15550-15599` (Cuttlefish control/forwarding range)
- `UDP 15550-15599` (media channels)

## Install Cuttlefish Host Tools

If `launch_cvd` and `cvd` are missing, install host tools first on the OCI VM.

Example baseline:

```bash
sudo apt-get update
sudo apt-get install -y git curl unzip adb qemu-kvm bridge-utils dnsmasq iptables iproute2
```

Then install Cuttlefish host packages using your preferred supported method (distro packages or AOSP-built debs). Confirm:

```bash
command -v launch_cvd
command -v cvd
```

## Phase 1 Scripts

### 1) Setup and Launch

```bash
chmod +x ./scripts/cuttlefish-phase1-setup.sh
./scripts/cuttlefish-phase1-setup.sh --vm <OCI_PUBLIC_IP> --instance-name cvd-arm64-1 --webrtc-port 8443
```

Local mode:

```bash
./scripts/cuttlefish-phase1-setup.sh --local
```

### 2) Validate Gates

```bash
chmod +x ./scripts/cuttlefish-phase1-validate.sh
./scripts/cuttlefish-phase1-validate.sh --vm <OCI_PUBLIC_IP> --instance-name cvd-arm64-1 --webrtc-port 8443
```

Local mode:

```bash
./scripts/cuttlefish-phase1-validate.sh --local
```

## Expected PASS Conditions

- `cvd` and `adb` available
- instance listed in `cvd fleet`
- `adb get-state` returns `device`
- `sys.boot_completed=1` (may require extra wait on first boot)
- host listens on configured WebRTC port
- `dumpsys media.camera` reports non-zero camera devices

## Troubleshooting

### `/dev/kvm` missing

- verify OCI shape supports virtualization
- check kernel module availability and host virtualization settings

### `launch_cvd` missing

This repository does not vendor Cuttlefish host binaries. Install host tools using the distro/AOSP method on the VM, then rerun setup.

### ADB not connecting

- confirm `cvd fleet` includes your instance
- re-run `adb connect <serial>`
- inspect launch logs on VM:

```bash
ls -lah /tmp/*launch*.log
```

### camera count is zero

Phase 1 only validates base camera service visibility. Continue to Phase 2 to wire deterministic front/back virtual camera sources and stream injection.
