# Agent Instructions for redroid-cloud-phone

## Before working

- Read this file, the repository README, the relevant issue or task, and any more-specific
  `AGENTS.md` files in the directory tree.
- Inspect the current branch and working tree. Preserve unrelated work.
- Confirm the task's acceptance criteria, owned files, and verification commands.

## Workflow

- Use one branch or worktree per task. Keep changes small enough to review.
- Use the repository's existing conventions and dependencies before introducing new ones.
- Record active work, ownership, and acceptance criteria in the issue or task tracker.
- Record architectural decisions in `docs/decisions/` when the repository uses ADRs.
- Put implementation evidence, commands run, residual risks, and the next action in the
  pull request or task record.

## Continuity

- Use `.AGENT/RUNBOOK.md` for stable startup and verification guidance that does not
  belong in this file.
- If work must pass to another session before a pull request exists, copy
  `.AGENT/handoffs/HANDOFF-TEMPLATE.md` to a uniquely named task file.
- Never coordinate concurrent agents through one shared queue or append-only action log.
  Git branches, worktrees, issues, commits, and pull requests are the durable record.

## Safety

- Never discard unrelated changes or rewrite history without explicit authorization.
- Never print or commit credentials, tokens, personal data, or machine-local settings.
- Run the narrowest relevant checks first, then broader checks required by the repository.
- State exactly what was and was not verified.

## Repository specifics

### Purpose

- Android ARM64 cloud phones on OCI with two deliberately separate runtimes: Redroid
  (Docker) as the default GApps/Play automation pool, and Cuttlefish (KVM) only for
  on-demand camera/mic RTMP ingest. A Flask Control API runs on each phone host; a Flask
  orchestrator allocates sessions across both pools. See `docs/RUNTIME-SPLIT.md`.

### Commands

- Setup: `cp .env.example .env`, then
  `pip install -r api/requirements.txt -r orchestrator/requirements.txt` (add `coverage`
  for coverage runs). CI uses Python 3.12. No Makefile, pyproject, linter, or type checker.
- Tests (offline, no device/Docker/OCI): `./cloud-phone test` (wraps
  `scripts/run-tests.sh`). Options: `--list`, `--suite NAME`, `--coverage`, `--verbose`,
  `--live --api-url URL`.
- CI gate (`.github/workflows/ci.yml`): `bash -n` on `scripts/*.sh scripts/lib/*.sh
  cloud-phone`, then `./scripts/run-tests.sh --coverage --fail-under 60 --verbose`.
- Run locally: `./cloud-phone api-run` (Control API), `./cloud-phone orchestrator-run`.
  `./cloud-phone help` lists the deploy, golden-image, Redroid, and GApps commands.
- Env var names: `API_TOKEN`, `ORCH_API_TOKEN`, `ORCH_CONTROL_API_TOKEN`,
  `ORCH_DEPLOY_MODE` (`mock` default, `redroid`, `oci`), `COMPARTMENT_ID`, `SUBNET_ID`,
  `REDROID_GOLDEN_IMAGE_ID`, `CUTTLEFISH_GOLDEN_IMAGE_ID`, `GAPPS_ZIP`; full list in
  `.env.example`.

### Layout

- `cloud-phone`: the CLI entry point that dispatches to `scripts/`.
- `api/`: Control API (`server.py`, `ui_control.py`, `viewport.py`, `Dockerfile`).
- `orchestrator/`: session/pool orchestrator (`server.py`, `procedures.py`, `rules.py`,
  `runtimes.py`).
- `scripts/`: deploy, golden-image, GApps, and verify scripts, plus `run-tests.sh` and
  `lib/log.sh`.
- `tests/`: unittest suites; fake phone/control fixtures live in `tests/fixtures/`.
- `systemd/`, `docker/`, `config/`: unit files, Redroid compose, nginx-rtmp, profiles.
- `docs/`: runtime split, auth tokens, testing ladder, procedures, and deployment.

### Gotchas

- Tests are plain `unittest`, not pytest. Without Flask installed, 9 of the 15 suites fail
  on import. Reports go to `.test-reports/` (gitignored).
- There are three distinct tokens. `ORCH_CONTROL_API_TOKEN` must equal the phones'
  `API_TOKEN` and falls back to `ORCH_API_TOKEN` when unset (`docs/AUTH-AND-HEALTH.md`).
- Never commit token values, GApps zips, `.env`, keys, or OCI config (see `.gitignore`).
  Keep Play/GMS out of the Cuttlefish golden image.
- A CI failure within 2-3 seconds with `BlobNotFound` means the GitHub Actions budget is
  blocked, not that the code is broken (comment in `ci.yml`).
