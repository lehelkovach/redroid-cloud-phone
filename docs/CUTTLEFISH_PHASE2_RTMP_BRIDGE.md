# Cuttlefish Phase 2: OBS RTMP Bridge

Phase 2 adds RTMP ingest for OBS while keeping camera injection pluggable.
Operator walkthrough, OBS settings and the live end-to-end procedure:
[`OBS-E2E.md`](./OBS-E2E.md).

Pipeline:

- OBS publishes RTMP to `rtmp://<host>/live/cam`
- nginx-rtmp (`config/nginx-rtmp.conf`) accepts the publish and lets local subscribers play it
- `cuttlefish-rtmp-bridge.sh` subscribes, decodes once, and writes **raw yuv420p frames** to the front and back sinks and **raw PCM** to the mic sink
- optional injector commands consume those sinks for Cuttlefish camera mapping

## Clean mode (default): pixel-to-pixel copy

The bridge does not re-encode. It decodes and emits the decoded planes, and
strips everything that is not pixel data:

- `-map_metadata -1 -map_chapters -1 -sn -dn -bitexact`: no container tags
  (OBS `encoder=obs-output …`, nginx `Server=NGINX RTMP`), no chapters, no
  data/subtitle streams, no `Lavf/Lavc` writer tags
- decode → `rawvideo`: the x264 user-data SEI and any other NAL side data
  are gone because only pixels are re-emitted
- `-fps_mode passthrough`: no frame duplication or dropping; one decoded
  frame in, one frame out (a `--video-fps N` switches to CFR on purpose)
- no `scale` unless `--video-width/--video-height` are given
- `flush_packets=1` on every sink: a frame reaches the sink the moment it
  is decoded; a stop never leaves a torn frame
- minimal input probe (`-probesize 32 -analyzeduration 0`): attaches about
  1 s after OBS starts instead of discarding the first 3–5 s while ffmpeg
  analyses the stream

Sink URI → raw container: `/dev/videoN` → `v4l2`; `file:`/`pipe:`/`fifo:`
→ `rawvideo`; `udp://`/`tcp://`/`unix://` → `yuv4mpegpipe` (framed, no tool
identity; start the reader first, and give it a large socket buffer:
`?buffer_size=4194304&fifo_size=5000000&overrun_nonfatal=1`, with
`net.core.rmem_max` raised as the installer does). Mic → `s16le`.
`--video-format` / `--audio-format` override.

`--mode encode` keeps the previous libx264 → MPEG-TS behaviour (now with the
SEI NAL units and metadata stripped) for consumers that must have TS.

## Lifecycle

nginx-rtmp keeps a subscriber attached when the publisher goes away, and
feeds it again when OBS reconnects. The bridge relies on that: one worker,
sinks stay open across OBS stop/start, the injector never sees EOF. The
monitor reads ffmpeg's `-progress` frame counter every second and

- restarts the worker when nginx reports a live publisher but no frames
  arrive for `--stall-timeout` (default 10 s)
- optionally detaches after `--idle-timeout` seconds with no publisher
  (default 0 = stay attached)

An idle ffmpeg blocked in the RTMP read ignores its first SIGINT; the stop
sequence sends a second one and, because every packet was already flushed,
loses nothing.

Fixed on the way: the old script passed HTTP-only `-reconnect*` options
to the RTMP input, which ffmpeg 6.x rejects ("Option reconnect not found"),
and then masked the non-zero exit as "ended normally".

## Quick start

```bash
./scripts/cuttlefish-rtmp-bridge.sh --dry-run        # prints the resolved ffmpeg argv
./scripts/cuttlefish-rtmp-bridge.sh                  # default UDP y4m sinks, PCM mic
```

v4l2loopback + FIFO example:

```bash
./scripts/cuttlefish-rtmp-bridge.sh \
  --rtmp-url rtmp://127.0.0.1/live/cam \
  --front-sink /dev/video10 \
  --back-sink pipe:/run/cloud-phone/back.yuv \
  --mic-sink pipe:/run/cloud-phone/mic.pcm
```

Pin geometry only if the injector needs it (this adds scale/pad and CFR):

```bash
./scripts/cuttlefish-rtmp-bridge.sh --video-width 1280 --video-height 720 --video-fps 30
```

## Optional injector commands

Attach per-camera injector commands through placeholders `{FRONT_URI}`
`{BACK_URI}` `{MIC_URI}` `{RTMP_URL}` `{LOG_DIR}`:

```bash
./scripts/cuttlefish-rtmp-bridge.sh \
  --front-cmd "my_front_injector --input {FRONT_URI}" \
  --back-cmd "my_back_injector --input {BACK_URI}" \
  --mic-cmd "my_mic_injector --input {MIC_URI}"
```

Use `--dry-run` first to inspect resolved commands.

## Tests

Offline (CI, no ffmpeg required for the dry-run contracts; the local
pixel-copy run is skipped without ffmpeg):

```bash
./cloud-phone test --suite rtmp-bridge
```

Run-level, synthetic OBS (lossless x264 of a deterministic pattern plus
OBS-style junk metadata) through nginx-rtmp and the real bridge:

```bash
./cloud-phone bridge-test --local            # on the host
./cloud-phone bridge-test --vm <OCI_PUBLIC_IP>
```

It passes only if every captured frame is byte-identical to the source
pattern, frames are contiguous, the raw file is an exact multiple of the
frame size, the UDP y4m path delivers, and no input fingerprint survives.
`./cloud-phone verify-ingest` runs it after the Phase 1 runtime checks.

With real OBS streaming: `./cloud-phone obs-check --vm <OCI_PUBLIC_IP> --snapshot ./obs-frame.png`
(see [`OBS-E2E.md`](./OBS-E2E.md)).

## Notes

- Phase 2 validates ingest and the clean split to front/back/mic sinks. The
  final front/back camera mapping depends on the Cuttlefish camera injection
  backend chosen on the VM; the sinks are already in the form (raw yuv420p,
  raw PCM) such a backend consumes.
