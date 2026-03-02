# Clean-Room Bootstrap (Post-Wipe Recovery)

Use this runbook when starting from a fresh machine or fresh CI/agent workspace.

## 1) Clone and baseline config

```bash
git clone <your-repo-url> android-arm-cloud-phone
cd android-arm-cloud-phone
cp .env.example .env
```

Fill required values in `.env`:

- `COMPARTMENT_ID`
- `SUBNET_ID`
- `AVAILABILITY_DOMAIN`
- `SSH_KEY_FILE`

## 2) Local toolchain requirements

Install locally:

- `bash`, `git`, `python3`, `python3-venv`, `pip`
- `oci` CLI
- `ssh` / `scp`
- Optional for local validation: `ffmpeg`, `ffprobe`, `adb`

## 3) Control-plane virtual environment (for cloud agents/devs)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r api/requirements.txt -r orchestrator/requirements.txt
```

Run locally if needed:

```bash
./cloud-phone api-run
./cloud-phone orchestrator-run
```

## 4) OCI deploy from clean machine

```bash
./cloud-phone deploy --name cuttlefish-source --ocpus 4 --memory 24
```

Recommended baseline: `4 OCPU / 24GB`.

## 5) Validate release gates

```bash
./cloud-phone verify-ingest --vm <OCI_PUBLIC_IP>
```

This validates:

- Cuttlefish runtime and ADB readiness
- WebRTC/API surface availability checks
- RTMP ingest split to front/back video and mic audio sinks

## 6) Golden image and fleet rollout

```bash
COMPARTMENT_ID=<ocid> ./cloud-phone create-golden <OCI_PUBLIC_IP> cloud-phone-cuttlefish-v1 cuttlefish
GOLDEN_IMAGE_ID=<image_ocid> ./cloud-phone deploy-fleet --count 5 --parallel 2 --verify-ingest
```

## 7) Agent handoff checklist

- `.env.example` is up to date with required variables.
- Script defaults do not reference machine-specific absolute paths.
- `api/requirements.txt` and `orchestrator/requirements.txt` install in a clean venv.
- `./cloud-phone verify-ingest --vm <OCI_PUBLIC_IP>` passes on a freshly deployed node.

