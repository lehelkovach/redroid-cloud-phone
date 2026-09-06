# Logging

Every line names the subsystem that produced it, so one unified log can be
filtered by origin instead of guessed at from message wording.

```text
2026-09-06 13:40:12.430 [CMD] [INFO ] commandlet tap backend=adb cmds=['input tap 640 360']
2026-09-06 13:40:12.431 [APM] [INFO ] w3c action=tap size=1280x720 payload={'actions': [...]}
2026-09-06 13:40:12.432 [VNC] [INFO ] viewport 1280x720 :5900 clients=0 frames=3 runtime=redroid
2026-09-06 13:40:12.433 [ADB] [DEBUG] ok ms=12 cmd=adb -s 127.0.0.1:5555 shell input tap 640 360
```

`LOG_FORMAT=json` switches to one JSON object per line for shipping.
`CLOUD_PHONE_VERBOSE=1` (also set by `./cloud-phone test`) promotes the process
to DEBUG so ADB argv, Appium W3C payloads, and VNC frame ticks all show up.

Passwords and tokens are redacted (`***`) before a payload is logged.

## Labels

| Label | Origin |
|---|---|
| `SYS` | System / CLI |
| `API` | Control API |
| `ORC` | Orchestrator |
| `ADB` | adb commanders (`adb -s … shell …`) |
| `CMD` | UI commandlets (`/ui/command`, tap/swipe/text/key) |
| `APM` | Appium / W3C actions (`/appium/status`, `backend=appium`) |
| `VNC` | VNC / RFB viewports (`/vnc/status`, attach, frames) |
| `RDR` | Redroid container |
| `CVD` | Cuttlefish launch |
| `GAP` | GApps install |
| `NGX` | nginx-rtmp |
| `FFM` | FFmpeg bridge |
| `DKR` | Docker |
| `LCT` | Android logcat |
| `TST` | Test harness |

An unknown label degrades to `SYS` rather than corrupting the column.

## Env

| Variable | Effect |
|---|---|
| `LOG_LEVEL` | `DEBUG` / `INFO` / `WARN` / `ERROR` (default `INFO`) |
| `CLOUD_PHONE_VERBOSE` | `1` forces DEBUG and INFO-level ADB commanders |
| `LOG_FORMAT` | `text` (default) or `json` |
| `LOG_FILE` | Also write to this path; a bad path warns, it does not crash the service |
| `ORCH_LOG_LEVEL` | Orchestrator override |
| `APPIUM_URL` | Appium server (default `http://127.0.0.1:4723`) |
| `UI_BACKEND` | `adb` (default) or `appium` |
| `VNC_PORT` | RFB port advertised on `/vnc/status` (default `5900`) |
| `VNC_WIDTH` / `VNC_HEIGHT` | Viewport size (defaults follow `REDROID_WIDTH`/`HEIGHT`) |

## Read the ring

```bash
curl http://127.0.0.1:8080/logs?type=CMD,APM,VNC
curl http://127.0.0.1:8090/phones/<id>/logs?type=ADB,CMD,APM,VNC
```

## Python

```python
from api.cloudphone_logging import configure

logger = configure("control_api", log_type="API")
logger.info("started")
logger.bind("ADB").debug("ok cmd=adb shell input tap 10 20")
logger.bind("CMD").info("commandlet tap")
logger.bind("APM").info("w3c action=tap")
logger.bind("VNC").info("viewport 1280x720 :5900")
```

## Shell

```bash
source "$(dirname "$0")/lib/log.sh"
LOG_TYPE=VNC
log_info "viewport attach 1280x720 :5900"
```

Shell logs go to **stderr** so `--json` stdout stays machine-readable.

The label list is duplicated in `api/cloudphone_logging.py` and
`scripts/lib/log.sh`; `tests/test_logging.py` fails if the two drift apart.

## Filtering

```bash
grep '\[APM\]' unified.log
grep -E '\[(CMD|VNC)\]' unified.log
grep -E '\[(WARN|ERROR)' unified.log
jq -r 'select(.type=="VNC")' < unified.jsonl
```
