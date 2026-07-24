# AGENTS.md

Canonical instructions for coding agents working in `redroid-cloud-phone`.

> **Environment handoff (canonical):** this repo is one of the `knowshowgo +8` Cursor
> environment repos. Read `osl-oc-agent/.AGENT/handoffs/CURSOR-ENV-HANDOFF.md` first.

## Repo role

Android ARM cloud phone on OCI ARM64 (Cuttlefish + OBS RTMP ingest + FFmpeg camera/mic bridge + golden-image fleet deploy).

## Commands

See `README.md` and the package/build manifest for the authoritative, current
commands (they are the source of truth; this file intentionally does not
duplicate them so they cannot drift).

## Branching model (gitflow)

- Branch from the integration branch; open PRs back into it (one logical change
  per branch). `main`/`master` holds released, tagged versions only.
- Keep `.AGENT/` records current as you work (append to `agent-action-log.md`).

## Agent conventions

- Operating scaffold lives in `.AGENT/` (`agent.md`, `agent-run.md`,
  `agent-action-log.md`, `handoffs/`). Canonical template: `agent-repo-boilerplate`.
- Cursor project rules live in `.cursor/rules/`.
- Never commit secret values; secrets are injected as env vars.
