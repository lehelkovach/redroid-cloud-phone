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
| 2026-10-30 | [multi-user-isolation](2026-10-30-multi-user-isolation.md) | Can the orchestrator lease browsers per owner, persist leases, and route every browser/phone command through a ticketed relay? | Yes, behind flags, in mock/local mode: 56 new tests incl. a two-owner gate, restart, real Chromium and a negative control; relay adds <1 ms per CDP message on one host. Per-owner encryption keys and a real Docker run not done |
| 2026-10-30 | [gapps-phone-first-boot](2026-10-30-gapps-phone-first-boot.md) | Does the GApps image boot on OCI A1, install apps, and drive real apps through the Control API and OSLO's tools? | Yes: boots in ~60 s, APK by URL + SHA-256 works, 31/31 real-app steps (3 runs + after reboot), OSLO tools 24/24; five bugs fixed; Play sign-in left to the owner |
| 2026-10-29 | [gapps-and-app-install](2026-10-29-gapps-and-app-install.md) | Which way of getting Play onto the phone fits, and can it be proven here? | Redroid + MindTheGapps 13 arm64 baked at build time; image build verified, no Android booted (sandbox has no binder/KVM) |
| 2026-10-21 | [docs-audit](2026-10-21-docs-audit.md) | Are the docs usable by a person who has never seen the project? | They were not: agent-session notes, duplicated and contradictory pages, lab addresses in examples. Rewritten in PR #19; three owner decisions remain |
