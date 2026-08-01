# Agent Runbook — redroid-cloud-phone

Policy lives in root `AGENTS.md`. Active work lives in an issue or PR.

## Start of a session

1. Read `AGENTS.md`, the task/PR, and (if in the +8 env)
   `osl-oc-agent/.AGENT/handoffs/CURSOR-ENV-HANDOFF.md`.
2. Inspect branch, working tree, recent commits — preserve unrelated work.
3. Confirm acceptance criteria and verification commands.
4. Resume from PR/issue evidence. Read a task-specific handoff only if one exists.

## Verify (narrow → broad)

Locally runnable (control plane; no KVM/Cuttlefish in Cloud VMs):

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r api/requirements.txt -r orchestrator/requirements.txt
python tests/run_tiers.py
```

Targeted suites from the tier map in `AGENTS.md` (e.g.
`python -m tests.test_launch_config`, `python tests/test_orchestrator_integration.py`).
Live-device / OCI / Appium steps are expected SKIP here.

## End of a session

1. Record what was and was not verified (including expected SKIPs).
2. Commit coherent work; update the PR with state, blockers, next action.
3. If no PR yet and continuity is needed, copy
   `.AGENT/handoffs/HANDOFF-TEMPLATE.md` → `.AGENT/handoffs/<issue>-<task>.md`.

Do not append shared action logs or run-once queues.
