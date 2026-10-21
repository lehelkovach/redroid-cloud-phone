# Architecture

## Components

| Component | Code | Runs where | Job |
|---|---|---|---|
| Control API | `api/server.py` (+ `ui_control.py`, `viewport.py`, `cloudphone_logging.py`) | Each phone host, port 8080 | HTTP wrapper around `adb` for one guest (or several, selected per request) |
| Orchestrator | `orchestrator/server.py` (+ `procedures.py`, `runtimes.py`, `rules.py`) | Anywhere that can reach the phones, port 8090 | Pool of phones, per-user sessions, relays, procedures |
| Redroid phone | `docker/redroid-compose.yml`, `scripts/redroid-up.sh` | Docker on the phone host | Android 11 in a privileged container, ADB on 5555 |
| Cuttlefish phone | `scripts/cuttlefish-phase1-setup.sh`, `systemd/cuttlefish-launch.service` | KVM on the phone host | Android VM started with `launch_cvd`, WebRTC on 8443 |
| Ingest | `config/nginx-rtmp.conf`, `scripts/cuttlefish-rtmp-bridge.sh` | Cuttlefish hosts | RTMP in on 1935, FFmpeg split into camera and mic feeds |
| Deploy tooling | `scripts/deploy-*.sh`, `scripts/*golden*.sh`, `systemd/` | Your machine and the hosts | Create OCI instances, install stacks, capture and launch golden images |

The orchestrator talks only to Control APIs. Agents can talk to either.

## Decision: two runtimes, two jobs

**Status:** accepted.

**Context.** The project needs two kinds of phone:

1. Automation phones that run Play Store apps and sign in with Google
   accounts. Many of them, cheap, quick to start.
2. Phones whose camera and microphone show a live feed published from OBS.

**Decision.** Use Redroid for the first and Cuttlefish for the second, with
separate golden images and separate orchestrator pools.

| | Redroid | Cuttlefish |
|---|---|---|
| Runs as | Privileged Docker container on the host kernel | KVM virtual machine (`/dev/kvm`) |
| Google Play | Yes, operator-supplied GApps zip ([gapps.md](gapps.md)) | No |
| Virtual camera/mic | No | Yes, via the RTMP bridge |
| Default size on OCI | 2 OCPU / 8 GB | 4 OCPU / 24 GB |
| Orchestrator purpose | `automation` (the default) | `camera` |

**Consequences.**

- A request with no `purpose` gets a Redroid phone. `purpose: "camera"` gets a
  Cuttlefish host and never reuses a Redroid phone. Each pool has its own
  limit (`ORCH_MAX_REDROID_INSTANCES`, `ORCH_MAX_CUTTLEFISH_INSTANCES`).
- Cuttlefish golden images are built for KVM and ingest only. Putting Play on
  them would mean a second, different bake (zip, privileged APKs, device
  certification), and an image that does both made each job wait on the
  other.
- Device-profile properties such as `ro.com.google.gmsversion` are not GApps.
  Play is present only when its packages are installed.

## Decision: no virtual camera HAL in containers

**Status:** accepted. Revisit only with a full platform rebuild (see below).

**Context.** The first attempt streamed a virtual camera into Redroid (and
Waydroid) by building an Android camera HAL and loading it in the container,
with `v4l2loopback` devices on the host.

**What went wrong.**

- **ABI/VNDK mismatch at runtime.** Camera components built separately loaded
  against incompatible libraries and linker namespaces. Symptoms included
  linker failures and crashes in strong-pointer code.
- **Mixed library state.** Copying system and vendor libraries into the
  container to satisfy dependencies let linking succeed while leaving the
  runtime inconsistent.
- **Incomplete integration.** Provider service, implementation libraries,
  VINTF manifest entries, init services and SELinux contexts were never
  coherent for that image.

**Decision.** Do not attach cameras to Redroid. `docker/redroid-compose.yml`
and `redroid-up.sh` mount no `/dev/video*` devices. Live camera and mic input
goes to Cuttlefish instead. Waydroid is parked.

**What a working container camera would need**, if this is revisited:

- Build the camera stack from the exact Android branch and toolchain of the
  runtime image, not from unrelated AOSP tags or host binaries.
- Ship the provider service, `impl` libraries, VINTF entries, init service
  definitions, SELinux contexts and namespace rules together.
- Present stable front and back logical cameras with correct facing metadata.
  An "external camera" provider makes apps treat the feed as a removable USB
  camera.
- Feed frames in a fixed format (`yuv420p`), at supported sizes and rates,
  with stable timestamps and reconnect handling.
- Add checks before deploy (ABI and dependency checks on the provider) and
  after (`dumpsys media.camera` count and facing, an app-level capture test
  for both cameras, a mic capture test).

A full platform rebuild (LXC-style or full AOSP) is more likely to get the
HAL right than inserting binaries into a container image, at a much higher
build cost.

**Related facts.**

- `v4l2loopback` is a host kernel module and does not need nested
  virtualisation.
- Cuttlefish does need a usable `/dev/kvm`. Check with `test -e /dev/kvm`,
  `ls -l /dev/kvm`, or `kvm-ok` if installed. Without it, Cuttlefish does not
  run regardless of the RTMP setup.

The original notes are in
[archive/FUTURE_CONSIDERATIONS_CAMERA_STACK.md](archive/FUTURE_CONSIDERATIONS_CAMERA_STACK.md).

## Decision: fail closed on reused phones

**Status:** accepted, partly implemented.

A Redroid phone keeps everything in `/data`: signed-in sessions, cookies,
saved credentials, downloads, keyboard history. Releasing a session frees the
slot but does not wipe the data. The orchestrator therefore records the last
owner of each instance and refuses to lease it to anyone else unless a reset
has been recorded since. Wiping and recording resets is not implemented yet,
so today an instance serves one owner. See [ROADMAP.md](../ROADMAP.md).

## Decision: health reports the caller's view

`/health` stays open (no token) on both services so systemd and Docker probes
work. To keep that from hiding auth problems, it reports whether the caller's
token would be accepted (`auth`, `usable`, `status: "unauthorized"`), and the
orchestrator checks a phone's `/health` for its own token before running
anything. The orchestrator uses separate inbound (`ORCH_API_TOKEN`) and
outbound (`ORCH_CONTROL_API_TOKEN`) tokens, so the phones' token can differ
from the one agents use. Background:
[archive/AUTH-AND-HEALTH.md](archive/AUTH-AND-HEALTH.md).
