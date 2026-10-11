# Orchestrator ops (code-side)

**Live lab IPs, Cursor secret names, OSLO env:** sibling
`osl-oc-agent/.AGENT/CLOUD-PHONE-ORCH.md` (canonical). Do not fork that table
here when an address changes — update the OSLO card and the fleet file.

This file is what the **redroid-cloud-phone** process actually reads.

## Listen

| Process | Env | Default | Auth env |
|---|---|---|---|
| Orchestrator `orchestrator/server.py` | `ORCH_HOST` / `ORCH_PORT` | `0.0.0.0` / **8090** | `ORCH_API_TOKEN` Bearer; `/health` open |
| Control API `api/server.py` | `API_HOST` / `API_PORT` | `0.0.0.0` / **8080** | `API_TOKEN` Bearer when set |
| Redroid systemd drop-in | `/etc/default/redroid-cloud-phone` | — | `API_TOKEN=` |

Run: `./cloud-phone orchestrator-run` · `./cloud-phone api-run`.
There is **no** in-repo systemd unit for the orchestrator yet; phone Control API
is `control-api-redroid.service` / `control-api.service`.

## Client (OSLO) names

Map guest auth onto one OSLO secret so `LiveMobileEnv` can hit both hops:

| OSLO | Must equal |
|---|---|
| `CLOUD_PHONE_ORCH_URL` | `http://<orch-ip>:8090` |
| `CLOUD_PHONE_API_URL` | `http://<phone-ip>:8080` (or SSH tunnel) |
| `CLOUD_PHONE_API_TOKEN` | `ORCH_API_TOKEN` **and** `API_TOKEN` (same value unless the client is split) |

## Pool

- `ORCH_DEPLOY_MODE=mock|redroid|oci`
- Idle reuse **per runtime** (Redroid vs Cuttlefish). Never mix.
- Goldens: `REDROID_GOLDEN_IMAGE_ID`, `CUTTLEFISH_GOLDEN_IMAGE_ID`

Never commit token values. `.env.example` leaves `ORCH_API_TOKEN=` / `API_TOKEN=` empty.

## Is the lab alive? (`phone-lab-probe.yml`)

An agent sandbox cannot reach the phone VMs, and on 2026-10-11 neither could
the OSLO prod box: `:22`, `:8080` and `:8090` on both `cloud-phone-agent-6c58`
and `cloud-phone-orch-6c58` refused within 8 s. Before trusting any ladder,
dispatch **Phone lab probe (read-only)** from Actions. It reports, in order:

1. TCP from the runner to `:22` / `:8080` / `:5555` on the phone VM and
   `:22` / `:8090` on the orchestrator VM;
2. `/health` on the phone host's loopback (`adb_connected`, `usable`, `gapps`);
3. the GApps rung: `pm path com.android.vending` / `com.google.android.gms`;
4. the Google-account rung: `dumpsys account` emails of type `com.google`;
5. the app rung: `pm path <package>` (input `package`, default `com.tinder`).

Steps 2–5 need an `OCI_SSH_PRIVATE_KEY` repository secret whose public half
the VMs were launched with; without it the run says so and step 1 still
stands. Nothing is written and no token is printed.
