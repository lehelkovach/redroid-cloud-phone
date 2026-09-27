# Screen stream — live phone video for the Watch page

**Status:** route A (MJPEG) is **live** in this tree. Route B (H.264) has a
server side and an offline ladder, but has **not** been run against a phone.

The OSLO Watch page (`osl-oc-agent`) used to poll `GET /device/screenshot` at
about 2 fps during take-over. That endpoint runs one `adb exec-out screencap -p`
per request (0.3–0.6 s of PNG on a live phone), so it could never look like
video. This document is the contract for the two stream endpoints that replace
the poll, and the note that keeps getting lost:

> **"VNC" in this repo is bookkeeping, not RFB.** `api/viewport.py` records the
> rectangle the Control API advertises (size, `VNC_PORT`, client count, frame
> count) so `[VNC]` log lines can prove a human and the agent looked at the
> same pixels. Nothing listens on `:5900`. `/vnc/status`, `/vnc/attach` and
> `/vnc/detach` read and write that record. The endpoints below are what
> actually moves frames — and they tick the same record, so a stream watcher
> shows up as a viewport client and every part increments `frames`.

## Route A — `GET /device/stream.mjpeg` (live)

Server-side MJPEG over `multipart/x-mixed-replace`. A browser renders it with
nothing but an `<img>`, which is what the Watch page already does for the
browser surface:

```html
<img src="http://phone:8080/device/stream.mjpeg?fps=10">
```

Each part is one `screencap -p`, re-encoded to JPEG through Pillow
(`api/requirements.txt`). Without Pillow the parts are the raw PNGs — browsers
sniff each part, so an `<img>` still renders, each frame is just larger.

| Query | Default | Range | Meaning |
|---|---|---|---|
| `fps` | `VNC_FPS` (30) | 1 … `VNC_FPS` | Frame-rate **cap**. `screencap` alone takes 0.3–0.6 s, so expect 2–4 fps of *fresh* frames from a Redroid guest; the cap only stops a fast host from spinning. |
| `quality` | `STREAM_JPEG_QUALITY` (70) | 10 … 95 | JPEG quality. |
| `frames` | 0 | 0 … 100000 | Stop after N parts. 0 = until the client disconnects. Handy for `curl` smoke checks and the offline ladder. |

Headers: `Content-Type: multipart/x-mixed-replace; boundary=cloudphoneframe`,
`Cache-Control: no-cache, no-store`, `X-Accel-Buffering: no` (so an nginx in
front relays instead of buffering).

Behaviour:

- **Auth:** `API_TOKEN` bearer, same `require_auth` as every device endpoint;
  a missing token is a 401 with `code: auth_required` before any `screencap`.
- **Per-phone routing:** `X-Cloud-Phone-Adb` selects the ADB serial, exactly
  as on `/device/screenshot`.
- **Disconnect:** the WSGI server closes the generator when the socket goes
  away; the `screencap` loop stops and the viewport client is released.
- **Failure:** `STREAM_MAX_FAILURES` (10) consecutive failed captures end the
  stream with `reason=capture_failed` rather than spinning on a rebooting phone.
- **Logs:** `[VNC] stream start kind=mjpeg …`, one `viewport frame` DEBUG tick
  per part (size in bytes, never pixels), `[VNC] stream stop … reason=…`.
  Frame bytes are never logged.

Smoke check against a live phone (the sandbox cannot reach one):

```bash
curl -N -H "Authorization: Bearer $API_TOKEN" http://phone:8080/device/stream.mjpeg
curl -N -H "Authorization: Bearer $API_TOKEN" "http://phone:8080/device/stream.mjpeg?frames=3" | head -c 400
curl -s -H "Authorization: Bearer $API_TOKEN" http://phone:8080/vnc/status   # clients / frames move
```

### Through the orchestrator

`GET /phones/<id>/stream.mjpeg` on the orchestrator relays the phone's stream
chunk by chunk (`requests` `stream=True`, Flask generator response, no
buffering) with the same query knobs. It carries `ORCH_CONTROL_API_TOKEN` to
the phone and demands `ORCH_API_TOKEN` from the caller, like every other
`/phones/<id>/…` route. Closing the client closes the upstream socket, which
ends the phone's loop.

```bash
curl -N -H "Authorization: Bearer $ORCH_API_TOKEN" \
  "http://<ORCH_IP>:8090/phones/<ID>/stream.mjpeg?fps=8"
```

`ORCH_STREAM_READ_TIMEOUT` (60 s) is the per-chunk read timeout, so a quiet
phone is not mistaken for a dead one.

### Serving notes

- The Control API runs under Flask's threaded server (`server.py`,
  `threaded=True`, see `systemd/control-api*.service`), so each watcher holds
  one thread for the life of the stream. That is fine for a handful of Watch
  pages per phone. If it is ever moved to gunicorn, use `--worker-class gthread`
  or `gevent`; a `sync` worker is blocked for the whole stream.
- Every watcher costs one `screencap` per frame on the guest. Two watchers at
  10 fps is two capture loops; there is no fan-out. Route B fixes that.

## Route B — `GET /device/stream.h264` (server side only, unverified)

Real-time video needs the guest's encoder, not `screencap`. The server runs

```text
adb exec-out screenrecord --output-format=h264 --size WxH --bit-rate N --time-limit T -
```

and relays the raw **Annex-B H.264** byte stream as an HTTP chunked response
with `Content-Type: video/h264`. `screenrecord` refuses more than 180 s per
run, so the generator chains runs: when one exits, the next starts at once and
opens with fresh SPS/PPS, which every Annex-B decoder resynchronises on. The
watcher sees one continuous stream. Disconnecting kills the recorder.

| Query | Default | Meaning |
|---|---|---|
| `width` / `height` | device size | `--size`; scale down for bandwidth |
| `bitrate` | `STREAM_H264_BITRATE` (4 Mbit/s) | `--bit-rate` |
| `limit` | `STREAM_H264_TIME_LIMIT` (180) | seconds per recorder run, max 180 |
| `runs` | 0 | stop after N recorder runs (0 = unbounded) |

Relay: `GET /phones/<id>/stream.h264` on the orchestrator, same as MJPEG.

**Why HTTP chunked and not a WebSocket.** The task sketch said "H.264 over
WebSocket" because that is what ws-scrcpy does. A one-way byte stream does not
need a WebSocket: `fetch()` exposes the response body as a `ReadableStream`,
carries the `Authorization` header (a browser `WebSocket` cannot, so the token
would have had to move into the URL), and rides through the existing
`/phones/<id>/…` relay and any nginx in front with no `Upgrade` handling. It
also keeps Flask dependency-free (`flask-sock` / `simple-websocket` would be new
and would change how the service is served).

**Decoder choice for the Watch page (follow-up in `osl-oc-agent`).**

1. **WebCodecs `VideoDecoder`** (Chrome, Edge, Safari 16.4+, Firefox 130+).
   Split the byte stream on Annex-B start codes (`00 00 00 01` / `00 00 01`),
   keep the latest SPS (NAL type 7) and PPS (8), configure the decoder with
   `codec: "avc1.<profile><compat><level>"` derived from the SPS and
   `avc: { format: "annexb" }`, feed each access unit as an `EncodedVideoChunk`
   (`type: "key"` for IDR, NAL type 5), and draw each `VideoFrame` to a
   `<canvas>`. Latency is one frame. About 150 lines, no library.
2. **Fallback:** [jmuxer](https://github.com/samirkumardas/jmuxer) wraps the
   same Annex-B stream into fragmented MP4 for MSE on a `<video>` element.
   Simpler to wire but buffers a few frames.
3. **Not chosen:** Broadway.js (WASM software decoder, high CPU, baseline
   profile only) and `scrcpy-server` (needs the jar pushed into the guest and
   its own control socket; `screenrecord` is already in every Android image).

The Watch page's pointer/typing layer does not change: only the `<img>` becomes
a `<canvas>` fed by the decoder, and taps still go to `/ui/command`.

**Not yet verified on a live phone:** whether Redroid's software encoder
(`OMX.google.h264.encoder` / `c2.android.avc.encoder`) keeps up at the device
size, and whether `exec-out` delivers `screenrecord`'s stdout unbuffered on
this image. First check:

```bash
curl -N -H "Authorization: Bearer $API_TOKEN" \
  "http://phone:8080/device/stream.h264?runs=1&limit=5&width=640&height=360" > /tmp/five.h264
ffprobe /tmp/five.h264            # should report h264, 640x360
```

## Tests (offline, `./cloud-phone test`)

| Rung | Suite | Proves |
|---|---|---|
| R0 | `tests/test_screen_stream.py` | multipart shape, JPEG/PNG fallback, fps pacing on a fake clock, failure cut-off, `on_stop` on generator close, H.264 run chaining and recorder kill |
| R1 | `tests/test_control_api.py` `ScreenStreamTests` / `H264StreamTests` | 401 without a token, ≥2 parts from a fake `screencap`, stop on disconnect releases the viewport client, `fps=999` clamps to `VNC_FPS`, `X-Cloud-Phone-Adb` reaches the capture, `screenrecord` killed on close |
| R2 | `tests/test_orchestrator_integration.py` | the orchestrator relays the fake phone's multipart parts unbuffered, 401s without `ORCH_API_TOKEN`, and the `[VNC]` ring shows start/stop |

None of them sleep: `screen_stream._SLEEP` / `_CLOCK` are patched.
