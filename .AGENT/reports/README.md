# Reports

Audits, diagnoses, measurements and reviews that a coding-agent session
produced for a person to read later: what was asked, what was looked at, what
was found, and what it means. Read this directory before measuring something
again. Convention source: `agent-repo-boilerplate`
`templates/default/.AGENT/reports/README.md`.

## Rules

- One report per file, named `YYYY-MM-DD-<slug>.md`.
- Start with the header block below, then the body.
- Say what was read, measured or run, and what was not.
- Never include tokens, host addresses, key material or a third party's
  personal data. Name a secret only by its variable name.
- A report is evidence, not policy. A rule it yields goes into `AGENTS.md`,
  `ROADMAP.md` or the relevant page under `docs/`, with a link back.
- Do not rewrite an old report; a follow-up is a new report that links back.
- Add a row to the index below in the same change, newest first.

## Header block

```markdown
# <Title>

- **Date:** YYYY-MM-DD
- **Asked by:** <who asked, or "self-initiated">
- **Question:** <one sentence>
- **Scope:** <files, hosts, time window>
- **Method:** <read the code / measured on <host> / ran <command>>
- **Verdict:** <one or two sentences>
- **Follow-up:** <pull request, issue, or "none">
```

## Index

| Date | Report | Question | Verdict |
|---|---|---|---|
| 2026-10-21 | [docs-audit](2026-10-21-docs-audit.md) | Are the docs usable by a person who has never seen the project? | They were not: agent-session notes, duplicated and contradictory pages, lab addresses in examples. Rewritten in PR #19; three owner decisions remain |
