# Logging

Every line names the subsystem that produced it, so one unified log can be
filtered by origin instead of guessed at from message wording.

```text
2026-09-04 09:16:28.792 [ORC] [INFO ] Procedure cda4d94 finished status=done
2026-09-04 09:16:28.791 [ADB] [WARN ] failed: adb -s 127.0.0.1:5555 shell pm path
```

`LOG_FORMAT=json` switches to one JSON object per line for shipping:

```json
{"ts":"2026-09-04 09:16:28.792","type":"ORC","level":"INFO","logger":"orchestrator","msg":"..."}
```

## Labels

| Label | Origin |
|---|---|
| `SYS` | System / CLI |
| `API` | Control API |
| `ORC` | Orchestrator |
| `ADB` | adb commands |
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
| `LOG_FORMAT` | `text` (default) or `json` |
| `LOG_FILE` | Also write to this path; a bad path warns, it does not crash the service |
| `ORCH_LOG_LEVEL` | Orchestrator override |

## Python

```python
from cloudphone_logging import configure

logger = configure("control_api", log_type="API")
logger.info("started")
adb_logger = logger.bind("ADB")   # same sink, different label
```

## Shell

```bash
source "$(dirname "$0")/lib/log.sh"
LOG_TYPE=RDR
log_info "container started"
```

Shell logs go to **stderr** so `--json` stdout stays machine-readable — that is
what lets the orchestrator parse `redroid-up.sh --json` while still logging.

The label list is duplicated in `api/cloudphone_logging.py` and
`scripts/lib/log.sh`; `tests/test_logging.py` fails if the two drift apart.

## Filtering

```bash
grep '\[ADB\]' unified.log            # one subsystem
grep -E '\[(WARN|ERROR)' unified.log  # problems only
jq -r 'select(.type=="ORC")' < unified.jsonl
```
