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

- `ORCH_DEPLOY_MODE=static|mock|redroid|oci`
- **Loaded pool (2026-10-20):** `ORCH_POOL_FILE` (or `ORCH_POOL_JSON`) lists the
  phones the lab already has — one entry each, `config/pool.example.json` —
  and they are registered as ready instances at boot (`[pool] loaded N static
  phone(s)` in the log) and leased **first** in every mode. `static` never
  provisions past them: an exhausted pool is a 409 *"pool exhausted"* that
  names the way out (release, add a phone, or switch to `redroid`/`oci` to
  scale up). Today's lab is one Redroid host and one Cuttlefish host for one
  user; scale-up is the same orchestrator with the mode changed.
- **Restart-safe:** `ORCH_STATE_FILE` keeps instances, leases, tenancy and
  sessions; a restart restores live leases (static entries are re-read from
  the pool file, which is the source of truth for them).
- `GET /pool` reports, per runtime, `total / leased / idle / static / max`
  and `members` (id, name, host, leased, lease owner) — never a token.
- Idle reuse **per runtime** (Redroid vs Cuttlefish). Never mix.
- Goldens: `REDROID_GOLDEN_IMAGE_ID`, `CUTTLEFISH_GOLDEN_IMAGE_ID`
- **Host install:** `sudo scripts/install-orchestrator.sh [--pool pool.json]`
  places the code under `/opt/cloud-phone-orchestrator`, the pool under
  `/etc/cloud-phone/pool.json`, the env under `/etc/default/cloud-phone-orchestrator`
  (`config/orchestrator.env.example`), and enables
  `systemd/cloud-phone-orchestrator.service` on **:8090**. Tests:
  `tests/test_orchestrator_static_pool.py`.

## Is the lab alive? (`scripts/fleet_doctor.py`, `phone-lab-probe.yml`, `phone-lab-ops.yml`)

`python3 scripts/fleet_doctor.py` (needs `oci` + `~/.oci/config`) reads the two
lab VMs' OCI state and addresses, the subnet's security-list ingress rules and
TCP probes, and prints one finding per blocker with its fix; `--facts` evaluates
saved facts offline (`tests/test_fleet_doctor.py`). **2026-10-20, measured:**
both lab VMs (and 18 of the 24 instances in the tenancy) were **STOPPED at the
OCI level** — that, not a security list, was "the lab is unreachable" since at
least 2026-10-11. Started by API the same day; SSH then answered on both, but
`:8080`/`:5555` on the phone host and `:8090` on the orchestrator did not —
the units are not up after the reboot and need an on-host repair. The subnet
security list also has **no ingress rule for :8090**, so OSLO prod (10.0.1.151)
could never reach the orchestrator even when it ran (needs TCP 8090 from
10.0.0.0/16). The osl-oc-agent workflow **Phone lab ops** (`phone-lab-ops.yml`,
`mode=diagnose|repair`) does the on-host work from a runner over SSH with the
prod deploy key — which the phone VMs do **not** currently accept
(`Permission denied (publickey)`), so a phone-VM key secret or OCI Run Command
(IAM: a dynamic group for the three instances + `use
instance-agent-command-execution-family`) is the prerequisite.

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
