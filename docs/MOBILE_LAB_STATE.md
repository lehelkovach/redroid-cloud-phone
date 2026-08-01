# Mobile lab: what is actually running

Verified 2026-08-23 by SSH'ing the OCI instances and driving the device. This
file records measured state, not intent. Where it contradicts the README or the
`CUTTLEFISH_*.md` docs, this file is the one that was checked against a
running device.

## Headline

The working lab is **Redroid in Docker**, not Cuttlefish. Cuttlefish cannot run
on these VMs at all: there is no `/dev/kvm`, no KVM kernel module in the OCI
`5.15.0-*-oracle` aarch64 kernel, and no `launch_cvd`/`cvd` binary or package
installed. OCI `VM.Standard.A1.Flex` (Ampere Altra) does not expose nested
virtualization, so a Cuttlefish guest would need software CPU emulation. Plan
around Redroid unless the host situation changes (see "What Cuttlefish would
require").

## Instances

| Display name | Role | Shape | Notes |
|---|---|---|---|
| `cloud-phone-agent-6c58` | phone + Control API + Appium | `VM.Standard.A1.Flex`, 4 vCPU / 23 GiB | Redroid container, Control API `:8080`, Appium `:4723` |
| `cloud-phone-orch-6c58` | orchestrator | `VM.Standard.A1.Flex` | `orchestrator.service` on `:8090` |
| `cloud-phone-dev` | older dev phone | `VM.Standard.A1.Flex` | running; not reachable with the `oci_console` key |
| `cloud-phone-gapps-test` (x2), `waydroid-test-1` | abandoned experiments | — | STOPPED; leave stopped |

Public IPs move when an instance is stopped and started. Resolve them rather
than trusting any IP written in a doc:

```bash
oci compute instance list --compartment-id "$OCI_COMPARTMENT_ID" --all \
  --query 'data[?contains("display-name", `cloud-phone`)].{name:"display-name",state:"lifecycle-state",id:id}' \
  --output table
oci compute instance list-vnics --instance-id <id> --query 'data[0]."public-ip"' --raw-output
```

## Phone VM services

All four were already up and healthy; nothing needed restarting:

| Unit | State | Port |
|---|---|---|
| `control-api.service` (`/opt/cloud-phone-api/server.py`) | active | `8080` |
| `redroid-container.service` (docker `redroid/redroid:11.0.0-latest`) | active (exited; container `Up`) | `5555` adb, `5900` VNC (localhost only) |
| `appium.service` | active | `4723` |
| `nginx-rtmp.service` | active | `1935`, `8081` |

Device: Android 11 (API 30), `redroid11_arm64`, 1280x720 @ 240dpi, 142 packages.

### adb is stable

`docs/MOBILEIO-DOGFOOD.md` in `osl-oc-agent` lists "screenshot / stable
`adb devices`" as **flaky — adbd often empty after connect**. That did not
reproduce: five consecutive `adb devices` calls all listed the device, and
screenshots returned valid 1280x720 PNGs every time. Both `127.0.0.1:5555` and
`emulator-5554` appear — the same device via two transports, which is cosmetic.

## Two things that make a healthy phone look broken

**1. The Control API requires a bearer token.** `API_TOKEN` is set through
`/etc/systemd/system/control-api.service.d/api-token.conf` (the value is also in
`/opt/cloud-phone-api/.api_token`, root-only). `GET /health` is unauthenticated,
so health looks fine while every device call returns `401 {"error":"Unauthorized"}`.
Callers must send `Authorization: Bearer <token>`.

**2. `:8080` is firewalled, and not by a security list.** `iptables` on the phone
VM allows `8080` only from the app VM `129.153.118.145/32` and from `10.0.1.0/24`.
Everything else is `REJECT`ed by the chain's final rule. `5555` (adb) is dropped
for any non-`10.0.0.0/8` source. So the orchestrator, reaching the phone over its
*public* IP, is also refused.

From anywhere else, tunnel over SSH instead of editing the firewall:

```bash
ssh -i ~/.ssh/oci_console -N -o ServerAliveInterval=20 \
  -L 18080:127.0.0.1:8080 -L 14723:127.0.0.1:4723 ubuntu@<PHONE_IP>
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:18080/device/ui
```

## Element-level UI reads work today

`uiautomator dump` runs fine on this image and returns the full hierarchy with
`text`, `resource-id`, `content-desc`, `bounds`, and the `clickable`/`focused`/
`password` flags. `GET /device/ui` wraps it (see `docs/API_REFERENCE.md`), so
blind coordinate tapping is no longer the only option.

**Appium + UiAutomator2 also works**, which was previously untested. The server
was running but had **zero drivers installed**, so it answered `GET /status`
while being unable to create a session — and the default driver refuses to
install against Appium 2.x. `scripts/setup-appium-uiautomator2.sh` fixes it:
pins `appium-uiautomator2-driver@4.2.9`, creates a minimal `ANDROID_HOME` whose
`platform-tools/adb` symlinks the system adb, and adds a systemd drop-in so the
service sees it. Verified: session created, `GET /session/<id>/source` returned
the element tree, and find-element-by-resource-id + read-text returned the
expected value.

### Which read path to use

| | `GET /device/ui` (uiautomator dump) | Appium UiAutomator2 |
|---|---|---|
| Extra infrastructure | none | `appium.service` + driver + `ANDROID_HOME` |
| Latency | ~1–2 s per dump | ~2–4 s session setup, then fast |
| Output | whole-screen snapshot | W3C element handles, per-element attributes |
| Selectors | match on the returned fields | id / xpath / UiSelector / accessibility id |
| Waiting | poll and re-dump | implicit waits, `waitForIdle` |
| Stateful | no | yes (session must be created and deleted) |

Start with `/device/ui`: it needs nothing new, is stateless, and one dump gives
the agent every label and tap point on screen. Reach for Appium when a flow
needs waits, scroll-into-view, or WebView context switching.

## Known gaps

- **No GMS / no Play Store.** `/opt/gapps/gapps.zip` is **0 bytes** and
  `/opt/gapps/extracted/` is empty, so `com.android.vending` is not installed
  and `pm list packages` shows no `gms`/`gsf`. Anything that needs a Google
  account on the device is blocked until a real gapps (arm64, Android 11 / API 30)
  package is supplied.
- **The deployed Control API lagged the repo.** The phone was running a build
  with no `ui_control` import and none of the `/ui/*`, `/jobs`, or
  `/device/identity` routes. `api/server.py` from this branch has been deployed;
  the previous file is kept as `/opt/cloud-phone-api/server.py.prerevive.<ts>`.
- **`cloud-phone-dev` is not reachable** with `~/.ssh/oci_console`; it wants a
  different key.

## What Cuttlefish would require

Cuttlefish needs KVM. On OCI that means either a bare-metal shape
(`BM.Standard.A1.160` — a much larger, always-on machine) or a different
provider that exposes nested virtualization on ARM. Both cost meaningfully more
than the current A1 VM. Until someone decides to pay for that, treat the
`CUTTLEFISH_*.md` documents as a design target and Redroid as the runtime.
