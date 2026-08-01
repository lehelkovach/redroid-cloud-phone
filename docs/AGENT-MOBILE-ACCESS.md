# Agent access — OCI Redroid cloud phone

Full topology: [`docs/OCI-LIVE-TOPOLOGY.md`](./OCI-LIVE-TOPOLOGY.md).

## Live instances (this session)

| Field | Phone | Orchestrator |
|-------|--------|--------------|
| OCI name | `cloud-phone-agent-6c58` | `cloud-phone-orch-6c58` |
| Public IP | `129.146.55.133` | `129.146.105.26` |
| Private IP | `10.0.1.127` | `10.0.1.123` |
| Image / role | Redroid 11 + Play/GMS + Control API | Orchestrator (`:8090`, mock mode) |
| Shape | VM.Standard.A1.Flex 4 OCPU / 24GB | VM.Standard.A1.Flex 1 OCPU / 6GB |
| Control API | `http://127.0.0.1:8080` (tunnel) | registers `http://10.0.1.127:8080` |
| ADB | host `:5555` → container `:5554` (adbd) | n/a |
| SSH | `ssh -i ~/.ssh/oci_console ubuntu@129.146.55.133` | `ssh -i ~/.ssh/oci_console ubuntu@129.146.105.26` |

> Older `cloud-phone-dev` (`129.146.70.170`) is running but its SSH key is
> `cloud-phone-dev-agent` (not in this Cursor environment). Prefer the
> agent-accessible VM above, or inject the private key as a secret.
>
> **Cuttlefish is not what these VMs run.** A1.Flex has no `/dev/kvm`. See topology doc.

## Boot gotcha (critical)

Redroid needs kernel binder/ashmem. On Oracle Ubuntu ARM:

```bash
sudo modprobe binder_linux devices="binder,hwbinder,vndbinder"
sudo modprobe ashmem_linux
sudo mkdir -p /dev/binderfs && sudo mount -t binder binder /dev/binderfs
sudo docker restart redroid
adb connect 127.0.0.1:5555
adb devices   # must show "device" — "connected to" alone is not enough
```

`redroid-binder.service` was enabled on `cloud-phone-agent-6c58` so this
survives reboot.

**Control API ADB target:** use `ADB_CONNECT=127.0.0.1:5555` (docker-proxy on the
host). A drop-in pinning `172.17.0.2:5554` makes `/health` report the wrong
target and screenshots fail with `device not found` even when Play launch
occasionally succeeds.

## Networking

Public `:8080` currently **resets** from outside the VCN (security list). From a
Cloud Agent / laptop use an SSH tunnel:

```bash
ssh -i ~/.ssh/oci_console -N -L 18080:127.0.0.1:8080 ubuntu@129.146.55.133
ssh -i ~/.ssh/oci_console -N -L 18090:127.0.0.1:8090 ubuntu@129.146.105.26
export CLOUD_PHONE_API_URL=http://127.0.0.1:18080
export CLOUD_PHONE_ORCH_URL=http://127.0.0.1:18090
node scripts/mobile_phone_smoke.mjs   # in osl-oc-agent
```

Host iptables on the phone REJECT everything except SSH (+ VCN `:8080` for the
orchestrator). Do not expect public `:8080` to work without opening that rule.

Tools: `mobile.health|screenshot|tap|swipe|type|key|home|back|launch|close|apps|shell|focus`.

## Gmail signup path (status)

**Installed on `cloud-phone-agent-6c58`:** Play Store (`com.android.vending`),
GMS/GSF, **Firefox** (`org.mozilla.firefox`). Gmail app APK is *not* required
for account create — signup is web (`accounts.google.com/signup`) in Firefox.

### What automation can do

1. `mobile.launch` Play → **SIGN IN** (or open Firefox with the signup URL)
2. Drive name → birthday/gender with screenshot + tap/type (+ Gemini for coords)
3. Stop at **phone SMS / CAPTCHA** and hand off

### What still needs a human (cannot VLM-solve)

- Google **phone verification SMS** (your number + code)
- Any **CAPTCHA** / bot check Google inserts
- Play payment / later IPRoyal card approval (separate flow)

### Preferred create-account entry

```bash
# Tunnel API first
ssh -i ~/.ssh/oci_console -N -L 18080:127.0.0.1:8080 ubuntu@129.146.55.133
export CLOUD_PHONE_API_URL=http://127.0.0.1:18080

# Open signup in Firefox (more reliable than WebView Browser Tester)
adb -s 127.0.0.1:5555 shell \
  'am start -a android.intent.action.VIEW -d "https://accounts.google.com/signup/v2/webcreateaccount?flowName=GlifWebSignIn&flowEntry=SignUp" -p org.mozilla.firefox'

# Or from osl-oc-agent:
node scripts/mobile_gmail_signup_assist.mjs
```

Play Store `UnauthenticatedMainActivity` → SIGN IN also reaches Google create-
account, but often resolves into `org.chromium.webview_shell`, whose address bar
steals `input text`. Prefer Firefox.

After account exists: vault as private `LoginCredential`, then use the mailbox
for IPRoyal email OTP / job-site bootstrap.

## API note

This golden image ships `/opt/cloud-phone-api/server.py` (routes under
`/device/*`, `/apps/*/start`, `/adb/shell`), which differs slightly from
`api/agent_api.py` in-repo. The Node client tolerates both.

## mobileio dogfood

See `docs/MOBILEIO-DOGFOOD.md` for the morning checklist (sessions, swipe, mock Tinder, IPRoyal).
