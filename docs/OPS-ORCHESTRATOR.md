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
