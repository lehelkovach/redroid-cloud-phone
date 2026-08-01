# Agent ↔ cloud phone access

**Companion product plan (APK / real device):** sibling  
`osl-oc-agent/docs/MOBILEIO-COMPANION-PLAN.md`.  
**Viewer wire / host IPs:** [`MOBILEIO-VIEWER-WIRE.md`](./MOBILEIO-VIEWER-WIRE.md).  
**HTTP API:** [`API_REFERENCE.md`](./API_REFERENCE.md).

This file is the **control-plane access** cheat sheet for OSLO `mobile.*` tools.

---

## Roles

| Component | Default ports | Role |
|---|---|---|
| Phone VM (Redroid/Cuttlefish) | ADB `5555`, Control API `8080` | Device + input/screenshot/install |
| Orchestrator (optional) | `8090` | One-phone-per-user session lease |
| OSLO agent | `8091` (prod site) | Calls Control API via `CLOUD_PHONE_API_URL` |

---

## Env (agent host)

```bash
export CLOUD_PHONE_API_URL=http://127.0.0.1:18080   # after SSH tunnel
# export CLOUD_PHONE_ORCH_URL=http://127.0.0.1:18090
# export CLOUD_PHONE_TOKEN=…   # if API auth enabled
```

Tunnel example (from cloud agent / laptop):

```bash
ssh -N -L 18080:127.0.0.1:8080 ubuntu@<PHONE_VM_IP>
```

Live host table: see [`MOBILEIO-VIEWER-WIRE.md`](./MOBILEIO-VIEWER-WIRE.md) (IPs rotate — verify before dogfood).

---

## OSLO smoke

```bash
cd ../osl-oc-agent
node scripts/mobile_phone_smoke.mjs
```

---

## vs companion APK / USB device

| Path | Where documented |
|---|---|
| Cloud ADB HTTP | this repo |
| USB/`adb` on laptop → `MOBILEIO=adb` | `osl-oc-agent` companion plan P1 |
| On-device APK bridge | companion plan P2 |

Do not put APK source in this repo — keep Redroid as the disposable lab control plane.
