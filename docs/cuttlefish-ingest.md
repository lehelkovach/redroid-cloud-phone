# Cuttlefish camera and mic ingest

A Cuttlefish host is a phone that can receive a live stream. You publish video
and audio from OBS over RTMP, nginx-rtmp receives it, and an FFmpeg bridge
normalises it and splits it into front camera, back camera and microphone
feeds. This runtime does not carry Google Play; use Redroid for that (see
[architecture.md](architecture.md)).

```text
OBS --RTMP--> nginx-rtmp :1935 (app "live", key "cam")
                  |
                  v  rtmp://127.0.0.1/live/cam
        cuttlefish-rtmp-bridge.sh (FFmpeg)
           |               |               |
     front sink       back sink        mic sink
   udp://127.0.0.1:23000  :23001         :23010
           |               |               |
     optional injector commands (you provide)
```

**Current state:** the bridge produces the three feeds and the verify script
checks that they decode. Wiring those feeds into the Cuttlefish guest's camera
and microphone is not done in this repo; the bridge accepts your own
"injector" commands for that step. See [ROADMAP.md](../ROADMAP.md).

## What you must supply

| Item | Why | Check |
|---|---|---|
| `/dev/kvm` on the host | Cuttlefish runs the guest as a KVM virtual machine | `test -e /dev/kvm && ls -l /dev/kvm` |
| Cuttlefish host tools (`launch_cvd`, `cvd`) | Not vendored or installed by this repo | `command -v launch_cvd cvd` |
| A Cuttlefish guest image for ARM64 | Not vendored or downloaded by this repo | `launch_cvd` boots it |

Whether an OCI `VM.Standard.A1.Flex` instance exposes `/dev/kvm` is not
something this repo establishes. Check it on your instance before you invest
in the rest. The installer stops with an error if `/dev/kvm` is missing.

Install the host tools with your preferred supported method (distribution
packages or Debian packages built from the AOSP `android-cuttlefish`
project). `cuttlefish-launch.service` runs
`launch_cvd --daemon --instance_name=<name> --base_instance_num=1 --start_webrtc=true --webrtc_sig_server_port=<port>`
as root with no image path arguments, so put the guest image where
`launch_cvd` looks by default for the root user.

Sizing: the scripts warn below 4 OCPU / 24 GB and use that as the default.

## Deploy

```bash
./cloud-phone deploy --name cuttlefish-source --ocpus 4 --memory 24
```

Reuse an existing instance with `--to-instance <IP>`. Other flags (see
`--help`): `--image-id`, `--instance-name` (default `cvd-arm64-1`),
`--webrtc-port` (default 8443), `--rtmp-url`, `--front-sink`, `--back-sink`,
`--mic-sink`, `--skip-tools-check`, `--dry-run`. OCI prerequisites and
security list rules are in [deploy-oci.md](deploy-oci.md).

`scripts/install-cuttlefish-cloud-phone.sh` (run as root on the host):

1. Installs nginx with `libnginx-mod-rtmp`, FFmpeg, adb, `qemu-kvm` and
   networking tools with `apt-get`.
2. Replaces `/etc/nginx/nginx.conf` with `config/nginx-rtmp.conf` and disables
   the stock `nginx.service`.
3. Copies scripts to `/opt/cloud-phone-scripts/` and the Control API to
   `/opt/cloud-phone-api/`.
4. Installs and enables `nginx-rtmp.service`, `cuttlefish-launch.service`,
   `cuttlefish-rtmp-bridge.service`, `control-api.service` and
   `cuttlefish-cloud-phone.target`, and writes `/etc/default/cuttlefish-cloud-phone`.
5. Fails if `/dev/kvm` is missing, or if `launch_cvd`/`cvd` are missing
   (unless `--skip-tools-check`), then starts the target.

The deploy script then runs the runtime validation once.

## Ports on a Cuttlefish host

| Port | Service | Exposure |
|---|---|---|
| TCP 1935 | nginx-rtmp, app `live` | All interfaces. **Any source may publish.** Playback only from 127.0.0.1 |
| 127.0.0.1:8081 | nginx HTTP: `/health`, `/stat`, `/stream-status` | Loopback only |
| TCP 8080 | Control API (`control-api.service`) | `0.0.0.0`, no token unless you add one |
| TCP 8443 | Cuttlefish WebRTC signalling | As configured |
| TCP+UDP 15550-15599 | Cuttlefish | As configured |
| UDP 23000, 23001, 23010 | Bridge sinks (front, back, mic) | 127.0.0.1 |

`control-api.service` has no `EnvironmentFile`. To set `API_TOKEN`, add a
drop-in with `sudo systemctl edit control-api.service`:

```ini
[Service]
Environment=API_TOKEN=<long-random-string>
```

then `sudo systemctl restart control-api.service`. The unit also sets
`ADB_CONNECT=127.0.0.1:5555`; check the guest's ADB serial with `cvd fleet`
and override `ADB_CONNECT` in the same drop-in if it differs (the validate
script falls back to `127.0.0.1:6520`).

## Publish from OBS

In OBS, Settings > Stream:

- Service: Custom
- Server: `rtmp://<HOST_IP>/live`
- Stream key: `cam`

An orchestrator session with `purpose: "camera"` returns the same address in
its `ingest` block (`rtmp_url`, `rtmp_app`, `stream_key`).

## Verify

From your machine:

```bash
./cloud-phone verify-ingest --vm <HOST_IP>
```

This runs two scripts over SSH (`--ssh-user`, default `ubuntu`; `--ssh-key`,
default `~/.ssh/android_arm_cloud_phone_oci`):

1. `cuttlefish-phase1-validate.sh`: `cvd` and `adb` present, instance listed
   in `cvd fleet`, `adb get-state` is `device`, `sys.boot_completed=1`, the
   WebRTC port is listening, and `dumpsys media.camera` reports cameras.
2. `test-cuttlefish-rtmp-bridge.sh`: `ffmpeg`/`ffprobe` present, nginx
   `/health` answers, a generated test stream (`--duration`, default 20 s)
   goes through the bridge, and the front, back and mic sinks contain
   decodable video and audio.

Run either one directly with `--local` on the host or `--vm <IP>` remotely.

## The bridge

`scripts/cuttlefish-rtmp-bridge.sh` is what `cuttlefish-rtmp-bridge.service`
runs. Its defaults come from `/etc/default/cuttlefish-cloud-phone`
(`RTMP_URL`, `FRONT_SINK_URI`, `BACK_SINK_URI`, `MIC_SINK_URI`).

```bash
./scripts/cuttlefish-rtmp-bridge.sh --dry-run          # print the resolved commands

./scripts/cuttlefish-rtmp-bridge.sh \
  --rtmp-url rtmp://127.0.0.1/live/cam \
  --front-sink "udp://127.0.0.1:23000?pkt_size=1316" \
  --back-sink "udp://127.0.0.1:23001?pkt_size=1316" \
  --mic-sink "udp://127.0.0.1:23010?pkt_size=1316" \
  --video-width 1280 --video-height 720 --video-fps 30
```

Other options: `--video-bitrate` (4M), `--audio-rate` (44100),
`--audio-channels` (2), `--audio-bitrate` (128k), `--log-dir`
(`/tmp/cuttlefish-bridge`), `--probe-interval`, `--retry-delay`.

To feed the guest, attach your own commands with `--front-cmd`, `--back-cmd`
and `--mic-cmd`. The placeholders `{FRONT_URI}`, `{BACK_URI}`, `{MIC_URI}`,
`{RTMP_URL}` and `{LOG_DIR}` are replaced before the command runs:

```bash
./scripts/cuttlefish-rtmp-bridge.sh \
  --front-cmd "my_front_injector --input {FRONT_URI}" \
  --mic-cmd "my_mic_injector --input {MIC_URI}"
```

## Golden images and fleets

```bash
COMPARTMENT_ID=<ocid> SSH_KEY_FILE=~/.ssh/android_arm_cloud_phone_oci \
  ./cloud-phone create-golden <HOST_IP> cloud-phone-cuttlefish-v1 cuttlefish

./cloud-phone deploy-golden --platform cuttlefish --image-id <image_ocid> \
  --name cf-1 --wait-check

CUTTLEFISH_GOLDEN_IMAGE_ID=<image_ocid> \
  ./cloud-phone deploy-fleet --platform cuttlefish --count 2 --parallel 2 --verify-ingest
```

For Cuttlefish, `prepare-golden-image.sh` also runs
`cvd stop --clear_instance_dirs` and removes Cuttlefish temp files. Always pass
`--platform cuttlefish`; both deploy commands default to `redroid`.

## Release checklist

- `./cloud-phone verify-ingest --vm <HOST_IP>` passes on a freshly deployed host.
- On the host, `systemctl status cuttlefish-cloud-phone.target` shows all units running.
- `API_TOKEN` is set for `control-api.service`, and ports 1935, 8080 and 8443
  are reachable only from addresses you trust.
