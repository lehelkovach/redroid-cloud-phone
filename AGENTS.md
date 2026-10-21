# Notes for coding agents

Start with [README.md](README.md) and [docs/architecture.md](docs/architecture.md).

## Layout

- `api/server.py`: Control API (Flask, port 8080). `api/agent_api.py` is legacy and not deployed.
- `orchestrator/server.py`: orchestrator (Flask, port 8090); `procedures.py`, `runtimes.py`, `rules.py`.
- `scripts/`: Bash, all reached through `./cloud-phone <command>`. Shared logging in `scripts/lib/log.sh`.
- `systemd/`, `docker/`, `config/`: what the installers put on hosts.
- `tests/`: unittest suites run by `scripts/run-tests.sh`; fakes in `tests/fixtures/`.

## Before you push

```bash
pip install -r api/requirements.txt -r orchestrator/requirements.txt coverage
./cloud-phone test --coverage --fail-under 60
for s in scripts/*.sh scripts/lib/*.sh cloud-phone; do bash -n "$s"; done
```

Offline tests must not call Docker, `oci` or a real phone.

## Conventions

- Keep the runtimes separate: Redroid gets GApps and no cameras; Cuttlefish
  gets the camera ingest and no GApps.
- Log through `api/cloudphone_logging.py` (Python) or `scripts/lib/log.sh`
  (shell) with a label from the shared list; pass payloads through `redact()`.
- Document new environment variables in `docs/configuration.md` and new
  endpoints in `docs/api/`.
- Refer to tokens by variable name (`API_TOKEN`, `ORCH_API_TOKEN`,
  `ORCH_CONTROL_API_TOKEN`). Keep token values, host addresses and GApps
  zips out of the repo, logs and commit messages.
- Known bugs are listed in [ROADMAP.md](ROADMAP.md); check there before
  "fixing" behaviour that looks wrong.
- Write every audit, diagnosis or measurement you make to
  `.AGENT/reports/YYYY-MM-DD-<slug>.md` and index it in
  [`.AGENT/reports/README.md`](.AGENT/reports/README.md), so the owner and later
  sessions can read it without the chat.
