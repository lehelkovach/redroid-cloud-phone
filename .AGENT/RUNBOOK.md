# Agent Runbook

Use this file for short, stable operational guidance shared across sessions. Repository
policy belongs in the nearest `AGENTS.md`; active work belongs in an issue or task.

## Start of a session

1. Read the applicable `AGENTS.md` files and the current task.
2. Inspect the branch, working tree, recent commits, and related pull request.
3. Confirm acceptance criteria, owned file scope, and verification commands.
4. Resume from task or PR evidence. Read a task-specific handoff only when one exists.

## End of a session

1. Run and record the relevant verification.
2. Commit coherent work when the workflow permits it.
3. Update the issue or pull request with state, evidence, blockers, and next action.
4. If no issue or pull request exists yet and continuity is needed, create one uniquely
   named file under `.AGENT/handoffs/` from the template.

Do not add run-once queues, global activity logs, or live messaging configuration here.
