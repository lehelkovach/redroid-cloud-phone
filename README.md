# redroid-cloud-phone

Self-hosted Android phones on Linux cloud VMs (built for Oracle Cloud ARM64
instances), driven over HTTP by scripts, automation tools and LLM agents. Each
phone host runs a small **Control API** that wraps `adb`: tap, type, swipe,
screenshots, app management and a labelled UI tree. An optional
**orchestrator** hands out phones from a pool, one per user, and runs
multi-step procedures against them. It is for developers who want phones they
control end to end, rather than a hosted device farm.

## How it fits together

```text
  your agent / script
          |  HTTP + Bearer token
          v
  Orchestrator :8090  ---- optional: pool, sessions, procedures
          |  HTTP + Bearer token
          v
  Control API :8080   ---- one per phone host, wraps adb
          |  adb (TCP 5555 / Cuttlefish adb)
          v
  Android guest: Redroid container (Docker)  or  Cuttlefish VM (KVM)

  OBS --RTMP :1935--> nginx-rtmp --> FFmpeg bridge --> camera/mic sinks
                     (Cuttlefish hosts only)
```

## Two runtimes

The project uses two Android runtimes for two jobs. See
[docs/architecture.md](docs/architecture.md) for why.

| Use | Runtime | Needs | Ports |
|---|---|---|---|
| Automation phone with Google Play | Redroid (`redroid/redroid:11.0.0-latest`, privileged Docker container) | Docker with the compose plugin, binder support in the host kernel, a GApps zip you supply | 5555 ADB, 8080 Control API |
| Phone that receives a live camera/mic stream from OBS | Cuttlefish (`launch_cvd`) | `/dev/kvm`, Cuttlefish host tools and guest image you supply, nginx with the RTMP module, FFmpeg | 1935 RTMP, 8080 Control API, 8443 WebRTC, 15550-15599 TCP/UDP |

The orchestrator sends requests with no `purpose` (or `purpose: "automation"`)
to Redroid, and `purpose: "camera"` to Cuttlefish.

## Requirements

- Linux host. The OCI scripts target ARM64 Ubuntu 24.04 or 22.04 on
  `VM.Standard.A1.Flex`. Redroid also publishes x86_64 images, so a local x86_64
  Linux machine works for trying things out.
- Python 3 with `venv` (CI uses Python 3.12).
- `adb` (Android platform tools) on the machine that runs the Control API.
- For Redroid: Docker and `docker compose`, and **binder support in the host
  kernel** (binderfs or the `binder_linux` module). The scripts in this repo do
  not set binder up. Check with `grep binder /proc/filesystems`; see
  [docs/troubleshooting.md](docs/troubleshooting.md#redroid-container-starts-then-exits).
- For OCI deploys: the `oci` CLI, an SSH key pair, and a compartment and
  subnet. See [docs/deploy-oci.md](docs/deploy-oci.md).

## Quickstart: one local phone

This runs a Redroid phone on your machine, puts the Control API in front of it,
and taps the screen. Full walkthrough: [docs/getting-started.md](docs/getting-started.md).

```bash
git clone https://github.com/lehelkovach/redroid-cloud-phone.git
cd redroid-cloud-phone
python3 -m venv .venv && . .venv/bin/activate
pip install -r api/requirements.txt -r orchestrator/requirements.txt

# 1. Start a phone (container "phone-1", ADB on 127.0.0.1:5555)
./cloud-phone redroid-up --name phone-1 --adb-port 5555
adb connect 127.0.0.1:5555
adb -s 127.0.0.1:5555 shell getprop sys.boot_completed   # repeat until it prints 1

# 2. Start the Control API with a token (binds 127.0.0.1:8080 by default)
export API_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
./cloud-phone api-run
```

In a second terminal, with the same `API_TOKEN` exported:

```bash
curl -s http://127.0.0.1:8080/health -H "Authorization: Bearer $API_TOKEN"
# "status": "healthy" and "usable": true mean the phone is reachable and your token works

curl -s -X POST http://127.0.0.1:8080/device/input \
  -H "Authorization: Bearer $API_TOKEN" -H 'Content-Type: application/json' \
  -d '{"type":"tap","x":640,"y":360}'
```

### Add the orchestrator (mock mode)

Mock mode creates no machines. Every "instance" points at the Control API on
`http://127.0.0.1:8080`, so you can try sessions and procedures against the
phone you just started.

```bash
export ORCH_API_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
export ORCH_CONTROL_API_TOKEN="$API_TOKEN"   # what the orchestrator sends to the Control API
ORCH_DEPLOY_MODE=mock ./cloud-phone orchestrator-run
```

```bash
curl -s -X POST http://127.0.0.1:8090/sessions \
  -H "Authorization: Bearer $ORCH_API_TOKEN" -H 'Content-Type: application/json' \
  -d '{"owner_user_id":"alice"}'
# returns {"session": {"instance_id": "...", ...}}

curl -s -X POST http://127.0.0.1:8090/phones/<instance_id>/input \
  -H "Authorization: Bearer $ORCH_API_TOKEN" -H 'Content-Type: application/json' \
  -d '{"type":"tap","x":640,"y":360}'
```

Stop the phone with `./cloud-phone redroid-down --name phone-1`. This also
deletes the phone's data volume.

## Deploy to Oracle Cloud

`./cloud-phone deploy-redroid` creates an ARM64 instance and installs Redroid,
the Control API and systemd units. `./cloud-phone deploy` does the same for a
Cuttlefish ingest host. You can then capture golden images and launch fleets
from them. Read [docs/deploy-oci.md](docs/deploy-oci.md) and
[SECURITY.md](SECURITY.md) first: the default install exposes ADB and an
unauthenticated Control API on public ports.

## The `cloud-phone` command

| Command | What it runs |
|---|---|
| `redroid-up`, `redroid-down` | `scripts/redroid-up.sh` (local Redroid container) |
| `api-run` | `python3 api/server.py` (Control API) |
| `orchestrator-run` | `python3 orchestrator/server.py` |
| `gapps-install`, `gapps-check` | `scripts/install-gapps-redroid.sh` |
| `verify-phone` | `scripts/verify-redroid-phone.sh` |
| `deploy-redroid` | `scripts/deploy-redroid-oci.sh` |
| `deploy` | `scripts/deploy-cuttlefish-oci.sh` |
| `verify-ingest` | `scripts/verify-cuttlefish-ingest.sh` |
| `prepare-golden`, `create-golden` | `scripts/prepare-golden-image.sh`, `scripts/create-golden-image.sh` |
| `deploy-golden`, `deploy-fleet` | `scripts/deploy-from-golden.sh`, `scripts/deploy-golden-fleet.sh` |
| `test` | `scripts/run-tests.sh` (offline test suites) |

The script-backed commands take `--help`. Two exceptions: `create-golden`
takes positional arguments (`<instance-ip> [image-name] [redroid|cuttlefish]`),
and `api-run` / `orchestrator-run` are configured only through environment
variables ([docs/configuration.md](docs/configuration.md)).

## Documentation

| Topic | Page |
|---|---|
| Local setup, step by step | [docs/getting-started.md](docs/getting-started.md) |
| Deploy on Oracle Cloud, golden images, fleets | [docs/deploy-oci.md](docs/deploy-oci.md) |
| Cuttlefish camera/mic ingest (OBS, RTMP) | [docs/cuttlefish-ingest.md](docs/cuttlefish-ingest.md) |
| Google Play (GApps) on Redroid | [docs/gapps.md](docs/gapps.md) |
| Every environment variable | [docs/configuration.md](docs/configuration.md) |
| Control API reference | [docs/api/control-api.md](docs/api/control-api.md) |
| Orchestrator API reference | [docs/api/orchestrator.md](docs/api/orchestrator.md) |
| Procedures (multi-step scripts) | [docs/procedures.md](docs/procedures.md) |
| Log format and filtering | [docs/logging.md](docs/logging.md) |
| Design decisions | [docs/architecture.md](docs/architecture.md) |
| Troubleshooting | [docs/troubleshooting.md](docs/troubleshooting.md) |
| Security model and reporting | [SECURITY.md](SECURITY.md) |
| Running tests, contributing | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Notes for coding agents | [AGENTS.md](AGENTS.md) |
| Older notes | [docs/archive/](docs/archive/) |

## Status

This is an early-stage project. The offline test suites run in CI; the
deploy scripts and live phones are tested by hand. Known bugs and planned work
are listed in [ROADMAP.md](ROADMAP.md).

## Responsible use

The Control API includes endpoints that change how the device identifies
itself (`/device/identity`, `/device/antidetect`), and the orchestrator ships
rules for automated swiping and follow-up messages. They are documented
factually in [docs/api/control-api.md](docs/api/control-api.md) and
[docs/procedures.md](docs/procedures.md).

> **OWNER-TODO:** state the intended uses of this project, the uses you do not
> support, and that users must follow the terms of service of the apps and
> services they automate and the laws that apply to them.

## License

> **OWNER-TODO:** no license has been chosen yet and there is no `LICENSE`
> file. Until one is added, default copyright applies and others have no
> permission to use, copy or modify this code. Pick a license and add a
> `LICENSE` file at the repository root.
