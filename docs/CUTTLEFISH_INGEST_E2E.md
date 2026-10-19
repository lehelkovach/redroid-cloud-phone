# Cuttlefish ingest, end to end: the signal in is the signal out

`scripts/test-cuttlefish-rtmp-bridge.sh` proved that bytes flow through
nginx-rtmp and the bridge and that the sinks decode. It never proved that what
the **guest's camera and mic** receive is the stream that was published: a
bridge that duplicated the wrong input, dropped audio, or froze on the first
frame still "had a video stream". This ladder makes the signal measurable.

## Pieces

| Piece | What it does |
|---|---|
| `tools/ingest_probe.py` | `clip` writes a looping mp4 whose video is solid colour blocks (red, lime, blue, yellow, magenta, cyan; one per second) and whose audio is one pure tone (1000 Hz). `frame-color` reads the centre of any frame ffmpeg can decode and names the nearest palette colour. `tone` runs a Goertzel detector over candidate frequencies. `verify` judges the bridge's three sinks; `verify-video` / `verify-audio` judge one file (a guest screencap, a guest `tinycap` wav). Pure Python + ffmpeg; no numpy (the VM has none). |
| `tests/test_ingest_probe.py` | R0 pure analysis (palette, expected colour at *t*, Goertzel on synthesized samples); R1 the generated clip classifies frame by frame and its tone is found, a frozen frame and silence are rejected; R2 **loopback**: ffmpeg itself listens on a local RTMP port as a stand-in for nginx-rtmp, the clip is published to it in real time, and the received transport stream passes the same judgement. Suite `ingest-probe` in `./cloud-phone test`. |
| `scripts/cuttlefish-ingest-e2e.sh` | The live rungs on a Cuttlefish host (local or `--vm`). |

## Live rungs

```bash
./scripts/cuttlefish-ingest-e2e.sh --local --duration 20          # on the host
./scripts/cuttlefish-ingest-e2e.sh --vm <OCI_PUBLIC_IP>            # over ssh
./scripts/cuttlefish-ingest-e2e.sh --local --dry-run               # print the plan
```

| Rung | Proves | Fails when |
|---|---|---|
| I0 | ffmpeg/ffprobe and the probe tool answer | tooling missing |
| I1 | nginx-rtmp `/health` on `:8081` | WARN only; the loop still publishes to `--rtmp-url` |
| I2 | clip generated; bridge started to file sinks; clip published in a loop for `--duration` s | publish exits non-zero |
| I3 | front and back sinks cycle through the palette; mic sink carries the tone | a sink is frozen (one colour), black, silent, or the wrong tone |
| I4 | the guest's camera preview shows a palette colour (camera app opened, `screencap`, centre classified) | no injector is bound to the sinks (`--front-cmd/--back-cmd`), the preview is black, or the camera app is absent |
| I5 | the guest's mic carries the tone (`tinycap` 3 s from the default input, pulled, Goertzel) | no mic injector, `tinycap` missing (not a userdebug build), silence |

`--skip-guest` stops after I3. `--keep-artifacts` leaves `/tmp/cf-ingest-e2e/*`
(sink `.ts`, `sinks.json`, `guest-camera.png/json`, `guest-mic.wav/json`).

## Status 2026-10-19

- R0–R2 green offline in CI (`ingest-probe`, 13 tests, including the RTMP loopback).
- I0–I3 run anywhere the bridge runs; **I4/I5 need the camera/mic injector
  commands bound to the bridge sinks**, which `docs/CUTTLEFISH_PHASE2_RTMP_BRIDGE.md`
  leaves to the Cuttlefish build on the VM (`--front-cmd` / `--mic-cmd`). Until an
  injector exists, I4/I5 fail honestly with "is an injector bound to the sinks?".
- Not run live on 2026-10-19: both phone VMs refused every port from this
  sandbox, from a GitHub runner and (per `docs/MOBILE-TINDER-TEST-PLAN.md`) from
  the prod box. See `docs/REDROID-CAMERA-HAL-RESEARCH.md` for whether the
  Cuttlefish hop can be removed altogether.
