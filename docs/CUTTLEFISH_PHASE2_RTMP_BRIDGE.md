# Cuttlefish Phase 2: OBS RTMP Bridge

Phase 2 adds RTMP ingest support for OBS while keeping camera injection pluggable.

Pipeline:

- OBS publishes RTMP to `rtmp://<host>/live/cam`
- `cuttlefish-rtmp-bridge.sh` ingests that RTMP stream
- bridge normalizes frames (`yuv420p`, fixed fps/resolution)
- bridge duplicates output to front/back sink URIs and a mic sink URI
- optional injector commands consume those sink URIs for Cuttlefish camera mapping

## Why this bridge design

- You can keep OBS on RTMP only.
- WebRTC remains optional for device viewing/control, not ingest.
- Front/back injection commands differ by Cuttlefish build and host setup, so the script avoids hardcoded assumptions.

## Quick start

Start bridge with default UDP front/back sinks:

```bash
chmod +x ./scripts/cuttlefish-rtmp-bridge.sh
./scripts/cuttlefish-rtmp-bridge.sh
```

Custom sink example:

```bash
./scripts/cuttlefish-rtmp-bridge.sh \
  --rtmp-url rtmp://127.0.0.1/live/cam \
  --front-sink "udp://127.0.0.1:23000?pkt_size=1316" \
  --back-sink "udp://127.0.0.1:23001?pkt_size=1316" \
  --mic-sink "udp://127.0.0.1:23010?pkt_size=1316" \
  --video-width 1280 \
  --video-height 720 \
  --video-fps 30
```

## Optional injector commands

You can attach per-camera injector commands through placeholders:

- `{FRONT_URI}`
- `{BACK_URI}`
- `{MIC_URI}`
- `{RTMP_URL}`
- `{LOG_DIR}`

Example pattern:

```bash
./scripts/cuttlefish-rtmp-bridge.sh \
  --front-cmd "my_front_injector --input {FRONT_URI}" \
  --back-cmd "my_back_injector --input {BACK_URI}" \
  --mic-cmd "my_mic_injector --input {MIC_URI}"
```

Use `--dry-run` first to inspect resolved commands.

## Run-level test

Use the included test script to validate stream flow end-to-end:

```bash
chmod +x ./scripts/test-cuttlefish-rtmp-bridge.sh
./scripts/test-cuttlefish-rtmp-bridge.sh --local
```

Remote OCI VM:

```bash
./scripts/test-cuttlefish-rtmp-bridge.sh --vm <OCI_PUBLIC_IP>
```

The test verifies:

- ffmpeg/ffprobe availability
- nginx-rtmp health endpoint
- bridge receives RTMP and writes front/back + mic sink outputs
- sink outputs contain decodable video streams
- mic sink contains decodable audio stream

## Notes

- Phase 2 validates ingest and split distribution, which is the core requirement for OBS RTMP input.
- Final non-detectable front/back camera mapping behavior depends on the Cuttlefish camera injection backend chosen on your VM.

For a single end-to-end command (runtime + ingest):

```bash
./scripts/verify-cuttlefish-ingest.sh --vm <OCI_PUBLIC_IP>
```
