# Agent entry — redroid-cloud-phone

**Stack-wide ops (IPs, ports, secret names):** sibling
[`../osl-oc-agent/.AGENT/CLOUD-PHONE-ORCH.md`](../osl-oc-agent/.AGENT/CLOUD-PHONE-ORCH.md)
when that repo is checked out next to this one.

**This repo:** [`docs/OPS-ORCHESTRATOR.md`](../docs/OPS-ORCHESTRATOR.md) (listen
env), [`docs/RUNTIME-SPLIT.md`](../docs/RUNTIME-SPLIT.md) (Redroid vs Cuttlefish),
[`docs/AGENT_COORDINATION.md`](../docs/AGENT_COORDINATION.md) (HTTP API).

Never print `ORCH_API_TOKEN` / `API_TOKEN` values. Record **names** only.

## 2026-10-19 — what changed and what is still unproven

- Control API grew device control (`/device/clock|time|timezone|settings`),
  container control (`/container`, needs `CONTAINER_NAME`), install-from-URL
  (`/apps/install_url`, `/apps/<pkg>/info`) and a pushed frame stream
  (`/device/stream.mjpeg`). All TDD, adb/docker/download patched:
  `device-control`, `install-url`, `stream` suites. `docs/API_REFERENCE.md` § Device control.
- Cuttlefish ingest is now measurable end to end: `tools/ingest_probe.py` +
  `scripts/cuttlefish-ingest-e2e.sh` + `ingest-probe` suite (RTMP loopback runs
  offline). `docs/CUTTLEFISH_INGEST_E2E.md`. Guest rungs I4/I5 need an injector
  bound to the bridge sinks — not built.
- Lab status: `phone-lab-probe.yml` on 2026-10-19 (run 39220149944): every
  port on both phone VMs refused from a GitHub runner, same as 2026-10-11.
  Nothing in this repo has run against a live phone since. Founder: OCI console.
- Camera HAL on Redroid: `docs/REDROID-CAMERA-HAL-RESEARCH.md`. Try Redroid 14
  + droidpool patches first; do not retry on the 11 image by copying libs.

- 2026-10-19: `POST|GET|DELETE /device/browser/devtools` (`api/server.py`,
  relay in `api/devtools_relay.py`, 4 tests in `tests/test_control_api.py`,
  `docs/API_REFERENCE.md` § Browser DevTools): forward + verify + relay the
  phone browser's CDP socket and return `cdp_url` — the path for an app with
  no APK (Airbnb is not on Aptoide). Needs a Chromium on the image (stock
  Chrome listens on `chrome_devtools_remote` without a flag). UNVERIFIED on a
  phone: the lab is unreachable.

## Release rule (stack-wide, 2026-10-19)

Every build, merge and release cut of this repo ends with (1) the test ladder
run green on the deployed target before the Control API / orchestrator takes
traffic (`./cloud-phone test` offline suites → `scripts/cuttlefish-ingest-e2e.sh`
/ phone-lab probes where a phone is reachable), and (2) a live conversation
with the running OSLO that uses the new endpoint through its mobile tools
(`osl-oc-agent/scripts/live/oslo_sanity.mjs --plan mobile`, or the
`workflow_battery.mjs` `device_control` / `mobile_onboarding` workflows),
with the reply, trace and logs read. A rung that could not run is reported
UNVERIFIED, never green.
