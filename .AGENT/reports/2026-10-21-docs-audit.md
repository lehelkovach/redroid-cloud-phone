# Documentation audit before the rewrite

- **Date:** 2026-10-21
- **Asked by:** owner ("the documentation is shitty from coding agent work. Need it clear and helpful for humans")
- **Question:** Can a developer who has never seen this repository set up a phone, drive it over HTTP and deploy it, from the docs alone?
- **Scope:** `master` at `9dcb2c5` (the state before PR #19): `README.md`, `docs/*.md`, `AGENTS.md`, `scripts/*.sh --help`, `api/server.py`, `orchestrator/server.py`.
- **Method:** read every page against the code it describes; ran the offline test suite and `bash -n` on every script; no phone, OCI instance or Cuttlefish host was reachable from the session, so nothing was verified live.
- **Verdict:** No. The pages were session notes (what an agent did one day) rather than documentation (what a person does now): several contradicted each other on ports, tokens and which runtime does what, examples carried the owner's lab IP, and no page explained the Redroid/Cuttlefish split or what the project is for. PR #19 replaces them. The code was not changed; what the code does wrong is listed in `ROADMAP.md` § Known issues.
- **Follow-up:** PR #19 (docs); the owner fills in the license, the responsible-use statement and the security contact; a security-defaults code PR after that.

## What was wrong

| Problem | Where | Effect on a reader |
|---|---|---|
| Pages written as agent progress notes ("Phase 1", "cleanroom bootstrap", "ops") with dates and session state | `docs/CUTTLEFISH_PHASE1.md`, `CUTTLEFISH_PHASE2_RTMP_BRIDGE.md`, `CLEANROOM_BOOTSTRAP.md`, `OPS-ORCHESTRATOR.md` | No page says what the project is or where to start |
| Two runtimes (Redroid for Play automation, Cuttlefish for camera ingest) never explained as a design | spread across pages | Readers try to attach a camera to Redroid, which the code refuses on purpose |
| Ports and tokens stated differently on different pages (8080 vs 8090, which token goes where) | README, API notes, scripts | First `curl` fails with 401 |
| The owner's lab IP in `.env.example` and workflow inputs | `.env.example`, `phone-lab-probe.yml` | Copied by every new user; a private address in a public repo |
| No API reference; endpoints discoverable only by reading Flask routes | `api/server.py`, `orchestrator/server.py` | Automation tools cannot be written from the docs |
| Security posture unstated: the default install exposes ADB and an unauthenticated Control API on public ports | install scripts | A deploy from the docs is open to the internet |
| No license, no contribution page, no security contact | repo root | Legally nobody may reuse the code; nobody knows where to report a hole |
| Identity-spoofing and auto-swiping endpoints undocumented | `/device/identity`, `/device/antidetect`, `orchestrator/rules.py` | Readers find them by surprise; the owner has not stated intended use |

## What PR #19 did

- New `README.md`: purpose, the two-runtime diagram, requirements, a local quickstart that ends in a tap, the `cloud-phone` command table, a docs index, status and responsible-use placeholders.
- New pages: `docs/getting-started.md`, `configuration.md` (every environment variable), `troubleshooting.md`, `api/control-api.md`, `api/orchestrator.md`, `deploy-oci.md` (golden images, fleets), `cuttlefish-ingest.md`, `gapps.md`, `procedures.md`, `logging.md`, `architecture.md` (decisions, including why no container camera HAL).
- `SECURITY.md`, `CONTRIBUTING.md`, `ROADMAP.md` (planned work and verified known issues), `AGENTS.md` (short notes for coding agents).
- Old pages moved to `docs/archive/`; the lab IP removed from `.env.example`.

## Left for the owner

1. **License.** None exists; default copyright applies.
2. **Responsible use** statement (the identity and swiping endpoints).
3. **Security contact** in `SECURITY.md`.
4. `phone-lab-probe.yml` still carries lab addresses as workflow defaults.
5. A code PR for secure defaults: bind the Control API to loopback, require a token, keep ADB off public interfaces.

## Not verified

Everything that needs a phone or a cloud instance: the quickstart on a real host, the OCI deploy, GApps install, the Cuttlefish ingest. The docs say so where it applies.
