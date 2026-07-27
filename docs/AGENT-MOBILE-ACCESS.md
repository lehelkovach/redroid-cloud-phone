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

## Gmail signup path (next)

1. `mobile.launch { package: "com.android.vending" }` (Play — already has GMS)
2. Sign in / Create account UI via screenshot + tap/type (and vision when needed)
3. Phone SMS → `user.ask` for Captain's number + code
4. Vault new Gmail as `LoginCredential`
5. Use mailbox for IPRoyal verify email (desktop bootstrap)

Play Store currently opens `UnauthenticatedMainActivity` until a Google account
exists — that is the intended create-account entry.

## API note

This golden image ships `/opt/cloud-phone-api/server.py` (routes under
`/device/*`, `/apps/*/start`, `/adb/shell`), which differs slightly from
`api/agent_api.py` in-repo. The Node client tolerates both.
