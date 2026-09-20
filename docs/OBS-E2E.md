# OBS → cloud phone camera: end-to-end

The camera/mic stream lands on the **Cuttlefish** ingest host, not on Redroid
(camera HAL on Redroid/Waydroid failed; see [`RUNTIME-SPLIT.md`](./RUNTIME-SPLIT.md)).
Everything below runs on, or against, a Cuttlefish host deployed with
`./cloud-phone deploy` (or from the Cuttlefish golden image).

```
OBS (your desk) ──rtmp://<host>/live/cam──▶ nginx-rtmp ──rtmp://127.0.0.1/live/cam──▶ ffmpeg (bridge) ──▶ front sink
                                              :1935                                    decode → raw yuv420p   ──▶ back sink
                                                                                       nothing else survives  ──▶ mic sink (PCM)
```

## What "clean" means

The bridge (`scripts/cuttlefish-rtmp-bridge.sh`, mode `clean`, the default)
decodes the H.264 that OBS sends and writes the **decoded yuv420p planes and
nothing else**:

| Junk that enters | Where it lives | Why it cannot reach a sink |
|---|---|---|
| `encoder=obs-output module (libobs …)`, titles, comments | FLV onMetaData | `-map_metadata -1` and raw sinks have no metadata section at all |
| `Server=NGINX RTMP …`, `RtmpSampleAccess`, `displayWidth` | nginx's onMetaData | same |
| `x264 - core 164 …` banner | H.264 SEI user-data NAL | the decoder discards SEI; only pixels are re-emitted |
| EXIF / XMP from image sources in OBS | never in H.264; OBS already rasterised it | there is no container to carry it |
| `Lavf` / `Lavc` writer tags | added by ffmpeg muxers | `-bitexact`; rawvideo / y4m / v4l2 have no writer tag |
| encoder re-timing (dup/drop) | ffmpeg CFR default | `-fps_mode passthrough`: one decoded frame in, one frame out |

There is no re-encode, no scale and no fps conversion unless you ask for it
(`--video-width/--video-height/--video-fps`). Every packet is flushed as it
is written, so a FIFO/v4l2 consumer receives each frame the moment it is
decoded and a stop never leaves a torn frame.

Sink URI decides the raw container:

| Sink URI | Format | Notes |
|---|---|---|
| `/dev/video10` | `v4l2` | v4l2loopback device; the usual Linux virtual camera |
| `file:/path`, `/path` | `rawvideo` | pure frames, `w*h*1.5` bytes each |
| `pipe:/path`, `fifo:/path` | `rawvideo` | named pipe (created if missing) |
| `udp://…`, `tcp://…`, `unix://…` | `yuv4mpegpipe` | one ASCII header + `FRAME` markers, no tool identity; start the reader **before** the bridge attaches (a late reader never sees the header) |

Raw video over loopback UDP is 10–40 MB/s. The reader must request a big
socket buffer, e.g. `udp://127.0.0.1:23000?buffer_size=4194304&fifo_size=5000000&overrun_nonfatal=1`,
and the kernel must allow it: the installer writes
`/etc/sysctl.d/90-cloud-phone-rtmp-bridge.conf` (`net.core.rmem_max=16777216`).
With Ubuntu's default cap (212992) packets are dropped and the y4m demuxer
loses sync after the first hole. For an injector on the same host a FIFO or
v4l2loopback sink has none of this.

Mic: `pcm_s16le` raw (`s16le`) on every sink kind, source rate/channels
unless `--audio-rate/--audio-channels` are set. Override with
`--video-format` / `--audio-format` (`rawvideo|y4m|nut|v4l2|mpegts`,
`s16le|wav|nut|mpegts`).

The Cuttlefish camera injector that consumes the sinks is still pluggable
(`--front-cmd/--back-cmd/--mic-cmd` with `{FRONT_URI}` `{BACK_URI}`
`{MIC_URI}`); the bridge makes no assumption about it.

## OBS settings

Settings → Stream:

| Field | Value |
|---|---|
| Service | Custom… |
| Server | `rtmp://<ingest-host-public-ip>/live` |
| Stream Key | `cam` |

Settings → Output (Output Mode: Advanced → Streaming):

| Field | Value | Why |
|---|---|---|
| Encoder | x264 (or any H.264 hardware encoder) | nginx-rtmp / FLV carry H.264 |
| Rate control | CBR, 4000–8000 Kbps at 720p30 | steady bitrate, predictable decode |
| Keyframe interval | 1 s | nginx `wait_key` hands the bridge a keyframe first; short GOP = fast attach |
| Profile | main or high | either decodes fine; avoid B-frames for latency: x264 options `bframes=0` or tune `zerolatency` |
| Audio | AAC, 160 Kbps, 48 kHz | becomes raw PCM on the mic sink |

Settings → Video: set the canvas and output resolution to what the phone
camera should present (e.g. 1280×720 @ 30 fps). Settings → Advanced → Video:
Color Format **NV12** or **I420**, Color Space 709, Range Limited: the bridge
then converts nothing (yuv420p in, yuv420p out).

Network: TCP **1935** must be open on the OCI security list / host firewall
for the source IP you stream from. Nothing else needs to be exposed (the
stat/health HTTP endpoint listens on 127.0.0.1:8081 only).

## End-to-end test procedure

1. **Host side is green without OBS.** Runs a synthetic OBS (lossless x264 of a
   deterministic pattern plus OBS-style junk metadata) through nginx-rtmp
   and the real bridge, then proves the pixel-copy contract:

   ```bash
   ./cloud-phone bridge-test --vm <OCI_PUBLIC_IP>      # or --local on the host
   ```

   Passes only if every captured frame is byte-identical to the source
   pattern, frames are contiguous (no drops/dups), the raw file is an exact
   multiple of the frame size, the UDP y4m path works, and no fingerprint
   (`obs-output`, `x264 - core`, `Lavf`, `NGINX RTMP`, …) survives. This is
   also part of `./cloud-phone verify-ingest`.

2. **Start streaming in OBS** with the settings above.

3. **Check the live session.** The check waits for a publisher on `live/cam`,
   reports what OBS is sending (codec, size, fps, audio, every metadata tag
   that rode in), confirms the bridge unit is attached, then taps the same
   stream with its own clean worker for a few seconds into a scratch dir
   (the production bridge and its sinks are untouched):

   ```bash
   ./cloud-phone obs-check --vm <OCI_PUBLIC_IP> --snapshot ./obs-frame.png
   ```

   Expect:

   - `PASS: OBS is publishing on 'cam'`
   - `PASS: incoming video: h264 1280x720 yuv420p @ 30/1 fps`
   - `INFO: metadata riding in with the stream …` — this is the junk list; it must not appear again below
   - `PASS: bridge unit cuttlefish-rtmp-bridge.service is active` and `bridge attached a worker`
   - `PASS: tap: N raw yuv420p frames, exact multiple of … bytes`
   - `PASS: tap: frame count … consistent with 30/1 fps`
   - `PASS: framed output carries none of: obs-output, libobs, x264, Lavf/Lavc, NGINX RTMP`
   - `PASS: tap: … ms of raw s16le PCM`
   - `PASS: snapshot written` — open `obs-frame.png`; it is the exact frame data the phone camera will get.

4. **Stop/start OBS** and run step 3 again. The bridge keeps its worker (and
   the sinks) attached across OBS sessions, so the injector never sees EOF;
   nginx simply resumes feeding it when OBS reconnects. On the host:

   ```bash
   tail -f /tmp/cuttlefish-bridge/bridge.log
   grep ^frame= /tmp/cuttlefish-bridge/progress.txt | tail -1     # frame counter, updates every second
   curl -s http://127.0.0.1:8081/stat | grep -c '<publishing/>'    # 1 while OBS streams
   ```

5. **Phone side** (once your injector is wired to the sinks): open the
   camera app on the Cuttlefish device, or `adb shell dumpsys media.camera`,
   and compare with the snapshot from step 3.

## Bridge lifecycle (what the log lines mean)

| Log line | Meaning |
|---|---|
| `Stream detected. Starting ffmpeg worker #N` | a publisher appeared; worker attached (~1 s: minimal input probe, keyframe first) |
| `Restarting worker #N (stall: publisher live … no frames for 10s)` | OBS is publishing but nothing decodes; worker restarted (`--stall-timeout`) |
| `Restarting worker #N (idle: no publisher for Ns)` | only with `--idle-timeout N`; by default the worker stays attached while OBS is stopped |
| `ffmpeg worker #N ended` | nginx closed the session (nginx restart) |
| `ERROR: ffmpeg worker #N exited with code …` | look at `/tmp/cuttlefish-bridge/ffmpeg-stream-N.log` |

Systemd knobs live in `/etc/default/cuttlefish-cloud-phone`:
`BRIDGE_MODE` (`clean`|`encode`), `BRIDGE_IDLE_TIMEOUT`, `BRIDGE_STALL_TIMEOUT`,
`FRONT_SINK_URI`, `BACK_SINK_URI`, `MIC_SINK_URI`.

## Troubleshooting

- **`no publisher on 'cam' after 120s`** — OBS is not reaching nginx: wrong
  server/key, port 1935 blocked, or `nginx-rtmp.service` down
  (`curl 127.0.0.1:8081/health` on the host).
- **`tap captured no frames`** while OBS is publishing — look at
  `/tmp/obs-e2e/tap.log`; the usual cause is an unsupported codec (OBS set to
  AV1/HEVC: switch to H.264).
- **UDP y4m reader gets nothing** — the reader must be running before the
  bridge attaches its worker (the y4m header is sent once per worker). Start
  the reader, then `systemctl restart cuttlefish-rtmp-bridge`.
- **Frame count too low** — check the host CPU while streaming; clean mode
  is one decode per stream, but 1080p60 lossless sources are heavy. Use
  `--video-width/--video-height` to pin a size only if the injector needs it.
- **Old bridge behaviour** (libx264 → MPEG-TS on every sink): `BRIDGE_MODE=encode`.
  Still strips metadata and the x264 SEI, but MPEG-TS/x264 add their own
  headers; use only for a consumer that must have TS.
