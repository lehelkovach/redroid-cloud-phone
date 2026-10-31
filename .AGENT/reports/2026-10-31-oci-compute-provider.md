# OCI Compute phone provider: one A1.Flex instance per phone, capped

- **Date:** 2026-10-31
- **Asked by:** the founder's build order, via the OSLO coordinator ("OCI provisioning for the cloud-phone orchestrator")
- **Question:** Can the orchestrator spin up a redroid phone as its own OCI Compute instance on demand, authenticated as the orchestrator instance, hand it out in the session shape OSLO already consumes, and be incapable of running up an unbounded bill?
- **Scope:** new `orchestrator/providers/oci_compute.py`; `orchestrator/server.py` (`_provision_instance`, `delete_instance`, `create_session`/`create_instance` error mapping, `/health`, `main()` start-up refusal and boot reconcile, owner plumbed from `_acquire_user_session`); `scripts/run-tests.sh` (suite `oci-provider`); `tests/test_oci_compute_provider.py` (new); `orchestrator/requirements-oci.txt`; `docs/configuration.md`, `docs/api/orchestrator.md`, `docs/deploy-oci.md`, `CHANGELOG.md`. Branch `oci-compute-provider` from `master` 26540fe. Not touched: `runtimes.py`, `browser_pool.py`, `user_volumes.py`, the relay.
- **Method:** read the orchestrator's provisioning paths and the OSLO `RedroidProvider` contract; read-only `oci` CLI in the tenancy (one region, the running instances, the existing dynamic group and policy, the phone host's VNIC, `oci compute image create` / `instance-agent command` parameter names); wrote the tests against a fake SDK, then the code; ran `PYTHON=<py3.12 venv> ./scripts/run-tests.sh --coverage --fail-under 60` and `bash -n` over the scripts. No OCI write was run: this session's permissions allow list/get only.
- **Verdict:** Built and gated, default off. With `ORCH_PHONE_PROVIDER=oci` a `POST /sessions` that finds no idle phone launches one `VM.Standard.A1.Flex` (2 OCPU / 12 GB by default) from `ORCH_OCI_IMAGE_ID` on `ORCH_OCI_SUBNET_ID`, waits for `RUNNING` and `/health`, and returns the usual session with `api_url = http://<private ip>:8080`. Two caps are checked before any API call (2 phones alive, 4 launch attempts an hour). Not run against OCI: the image, the IAM policy and the first live launch are the founder's (OSLO `deploy/oci/`, `docs/OCI-PHONE-FLEET.md`).
- **Follow-up:** OSLO branch `oci-ops-scripts` (operator scripts and the fleet runbook). No PR opened, per instructions.

## What the provider does, in order

| Step | Where | Refusal |
|---|---|---|
| Caps: phones alive < `ORCH_OCI_MAX_INSTANCES`; launch attempts in the last hour < `ORCH_OCI_MAX_LAUNCHES_PER_HOUR` | `_check_caps()`, before the SDK is even imported | `429 oci_cap` with both counters |
| Count the attempt (a failing launch spends budget: a crash loop stops itself after four tries) | `launch()` | |
| Auth: `InstancePrincipalsSecurityTokenSigner`; `auto` falls back to `~/.oci/config` on a box without a metadata service; `instance_principal` forces it | `_signer_and_config()` | `500 oci_auth`, naming both failures, never a key |
| `launch_instance`: shape `VM.Standard.A1.Flex`, `shape_config`, `source_details` image, one VNIC on the subnet with `assign_public_ip=False`, freeform tags `oslo-owner` (slug of the owner id), `oslo-role=phone`, `oslo-created` (UTC) | `launch_details()` | `502 oci_error` with `service_code` / `service_status` |
| Wait `RUNNING` (`ORCH_OCI_RUNNING_TIMEOUT`, 300 s); `TERMINATED`/`TERMINATING` during boot is an error, not a wait | `_wait_running()` | `504 oci_timeout`, instance terminated |
| Private IP from the attached VNIC | `_private_ip()` | `502 oci_error` |
| Wait `GET http://<ip>:8080/health` → `status: ok` (`ORCH_OCI_HEALTH_TIMEOUT`, 300 s) | `_wait_health()` | `504 oci_unhealthy` with the last answer, instance terminated; `terminated: false` in the body if that also failed |
| `terminate(ocid)`: `terminate_instance(preserve_boot_volume=False)` | `DELETE /instances/<id>` | `502 oci_error`; the record stays |
| Boot: `list_instances` filtered to `oslo-role=phone`; known OCIDs adopted, orphans older than `ORCH_OCI_ORPHAN_TTL` (2 h) terminated, younger kept and counted | `reconcile_oci_phones()` in `main()` | logged, not fatal |

Nothing retries. The orchestrator's own pool check (`ORCH_MAX_REDROID_INSTANCES`) runs first and `/pool` now reports the tighter of the two caps, so OSLO's `RedroidProvider.refreshCapacity()` sees the real limit.

## What was measured, and what was not

- 35 tests, all offline, against a fake module with the SDK's shape (`core.ComputeClient`, `core.models.*`, `exceptions.ServiceError`, `auth.signers`, `config.from_file`): launch payload, no secret in payload or record, custom shape config, AD lookup, both caps (including the budget returning after an hour), terminate freeing a slot, caps before import, config refusals, both waits giving up and terminating, a dying instance, failed termination after a failed launch, service errors on launch and terminate, missing SDK, all four auth selections, reconcile (adopted / terminated / kept / untagged untouched / undated orphan), and the server wiring (default off byte-for-byte, session with owner tag, 409 pool-full vs 429 provider cap, 502 with service code, delete, delete failure, boot reconcile, start-up refusals, camera path untouched).
- Read-only in the tenancy: one region (us-phoenix-1); the phone host, orchestrator host and prod host are `VM.Standard.A1.Flex`; the phone host's VNIC is attached to one private subnet; the existing dynamic group matches one instance id and the existing policy grants only `instance-agent-command-execution-family` to it.
- Not done: no `launch_instance`, `image create` or IAM write. The real SDK was not imported (not installed in the test venv; `orchestrator/requirements-oci.txt` adds it where wanted). Whether Run Command on the Ubuntu hosts runs as root or as `ocarun` was not checked.

## Decisions left to the founder

- The caps. 2 / 4 are deliberately tight; raise `ORCH_OCI_MAX_INSTANCES` with the number of owners expected at once.
- Image refresh cadence: a phone boots with whatever `/data` the image carried; after Play sign-in or an app install on the source host, make a new image and change `ORCH_OCI_IMAGE_ID`.
- Whether to stop the phone host before imaging (consistent) or image it running (no downtime, small risk of a half-written `/data`).
