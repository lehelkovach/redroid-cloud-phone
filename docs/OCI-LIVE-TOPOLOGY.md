# OCI live topology (cloud phone)

Verified 2026-08-01 from Cursor Cloud agent against the tenancy.

## Access

| Item | Value |
|------|--------|
| Repo | https://github.com/lehelkovach/redroid-cloud-phone (**public**) |
| SSH key (agent) | `~/.ssh/oci_console` → `ubuntu@…` |
| Phone VCN | `10.0.1.0/24` (`waydroid-subnet`, PHX-AD-1) |

## Running instances (phone-related)

| Name | Public IP | Private IP | Shape | Role |
|------|-----------|------------|-------|------|
| `cloud-phone-agent-6c58` | `129.146.55.133` | `10.0.1.127` | A1.Flex 4/24 | **Live Redroid phone** + Control API `:8080` |
| `cloud-phone-orch-6c58` | `129.146.105.26` | `10.0.1.123` | A1.Flex 1/6 | **Orchestrator** `:8090` (registers phone over VCN) |
| `cloud-phone-dev` | `129.146.70.170` | (same subnet) | A1.Flex 4/24 | Older phone; SSH key **not** in this agent env |
| `redroid-camera-build` | `152.70.146.56` | | A1.Flex | Legacy camera build host |

## Interface path (what works now)

Public `:8080` / `:8090` on the phone are **REJECT**ed by host iptables (SSH-only from the internet). Use SSH tunnels:

```bash
# Control API (phone)
ssh -i ~/.ssh/oci_console -N -L 18080:127.0.0.1:8080 ubuntu@129.146.55.133

# Orchestrator
ssh -i ~/.ssh/oci_console -N -L 18090:127.0.0.1:8090 ubuntu@129.146.105.26

export CLOUD_PHONE_API_URL=http://127.0.0.1:18080
export CLOUD_PHONE_ORCH_URL=http://127.0.0.1:18090

curl -sS $CLOUD_PHONE_API_URL/health
curl -sS $CLOUD_PHONE_ORCH_URL/health
curl -sS $CLOUD_PHONE_ORCH_URL/instances

# one-phone-per-user session
curl -sS -X POST $CLOUD_PHONE_ORCH_URL/sessions \
  -H 'content-type: application/json' \
  -d '{"owner_user_id":"dogfood-user-1","ttl_seconds":3600}'
```

Inside the VCN, the orchestrator reaches the phone at `http://10.0.1.127:8080`
(host iptables allows `10.0.1.0/24` → `:8080`).

Agent tools (osl-oc-agent): `mobile.health|screenshot|tap|swipe|type|…` against
`CLOUD_PHONE_API_URL`.

## Runtime reality (important)

- **Live phones are Redroid 11**, not Cuttlefish. Stock **VM.Standard.A1.Flex has no `/dev/kvm`**, so Cuttlefish cannot run on these shapes.
- Cuttlefish needs a **KVM-capable** shape (typically bare-metal Ampere `BM.Standard.A1.160`). Do **not** expect `./cloud-phone deploy` cuttlefish install to succeed on A1.Flex.
- Repo code/docs are Cuttlefish-oriented; the provisioned fleet still runs Redroid + Control API.

## Redroid ADB gotchas (verified)

1. **binderfs** must be mounted (`redroid-binder.service`).
2. **`adbd` often stops** after restart — `docker exec redroid setprop ctl.start adbd` (unit `redroid-adbd.service` installed on agent phone).
3. **Port mismatch:** on this image `adbd` listens on container **`:5554`**, while something else occupies `:5555`. Host publish must be `-p 5555:5554` (launcher patched on the agent phone). If published port flaps, point Control API at the docker bridge: `ADB_CONNECT=172.17.0.2:5554`.
4. Control API `/health` can report `adb_connected:true` while `adb devices` is offline — always check `adb devices -l` / a real `shell`/`screenshot`.

## Screenshot API hotfix

Live `/opt/cloud-phone-api/server.py` screenshot handlers were rewritten to
`adb exec-out screencap -p` (the prior pull/`>` subprocess path always 500'd).

## Orchestrator deploy notes

On `cloud-phone-orch-6c58`:

- Code: `/opt/cloud-phone-orchestrator` (venv + `server.py`)
- Env: `/etc/cloud-phone/orchestrator.env`
  - `ORCH_DEPLOY_MODE=mock`
  - `ORCH_REGISTER_API_URLS=http://10.0.1.127:8080`
- Unit: `orchestrator.service` (user `cloudphone`)

`ORCH_DEPLOY_MODE=oci` (provision-on-demand from golden image) still needs
`GOLDEN_IMAGE_ID` + OCI deploy script wiring — not enabled on this VM yet.

## Cuttlefish next step (blocked on shape)

To actually launch Cuttlefish:

1. Confirm capacity/cost for `BM.Standard.A1.160` (or other KVM Ampere).
2. `./cloud-phone deploy --name cuttlefish-source …` from this repo with
   `COMPARTMENT_ID` / `SUBNET_ID` / `AVAILABILITY_DOMAIN` set.
3. Point orchestrator `ORCH_REGISTER_API_URLS` (or `oci` mode) at that host.

Until then, dogfood mobileio against **Redroid** on `cloud-phone-agent-6c58` via the orchestrator above.
