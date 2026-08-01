# Agent access — OCI Redroid cloud phone

## Live instance (this session)

| Field | Value |
|-------|--------|
| OCI name | `cloud-phone-agent-6c58` |
| Public IP | `129.146.55.133` |
| Image | `cloud-phone-gapps-v1` (Redroid 11 + Play/GMS) |
| Shape | VM.Standard.A1.Flex 4 OCPU / 24GB |
| Control API | `http://129.146.55.133:8080` |
| ADB | `129.146.55.133:5555` |
| SSH | `ssh -i ~/.ssh/oci_console ubuntu@129.146.55.133` |

> Older `cloud-phone-dev` (`129.146.70.170`) is running but its SSH key is
> `cloud-phone-dev-agent` (not in this Cursor environment). Prefer the
> agent-accessible VM above, or inject the private key as a secret.

## Boot gotcha (critical)

Redroid needs kernel binder/ashmem. On Oracle Ubuntu ARM:

```bash
sudo modprobe binder_linux devices="binder,hwbinder,vndbinder"
sudo modprobe ashmem_linux
sudo mkdir -p /dev/binderfs && sudo mount -t binder binder /dev/binderfs
sudo docker restart redroid
adb connect 127.0.0.1:5555
```

`redroid-binder.service` was enabled on `cloud-phone-agent-6c58` so this
survives reboot.

## Networking

Public `:8080` currently **resets** from outside the VCN (security list). From a
Cloud Agent / laptop use an SSH tunnel:

```bash
ssh -i ~/.ssh/oci_console -N -L 18080:127.0.0.1:8080 ubuntu@129.146.55.133
export CLOUD_PHONE_API_URL=http://127.0.0.1:18080
node scripts/mobile_phone_smoke.mjs   # in osl-oc-agent
```

To expose the API publicly, add an ingress rule for TCP 8080 (and optionally
5555) on the phone subnet's security list / NSG.

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
