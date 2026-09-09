# GApps / Play Services (Redroid phones)

**Status:** required for the automation pool · **Runtime:** Redroid only · **Cuttlefish:** out of scope

Spoof props in `config/device-profiles/` (`ro.com.google.gmsversion`) are **not** GApps.

Lab failure this replaces: empty `/opt/gapps/gapps.zip` → no Play Store / GMS, while `/health` still looked fine.

## Where GApps lives

| Image | GApps |
|---|---|
| Redroid OCI VMs (`./cloud-phone deploy-redroid`, orchestrator `purpose=automation`) | **Install here** |
| Cuttlefish golden (RTMP / camera HAL) | **Do not install** — ingest-only |

Decision write-up: [`RUNTIME-SPLIT.md`](./RUNTIME-SPLIT.md).

## Operator supply (never commit the zip)

| Name | Role |
|---|---|
| `GAPPS_ZIP` | Absolute path to MindTheGapps / NikGApps / OpenGApps **arm64** zip |
| `GAPPS_ZIP_URL` | Optional HTTPS URL **you** host; script refuses a 0-byte download |
| `/opt/gapps/gapps.zip` | Host drop path if env is unset |
| `REDROID_IMAGE` | Optional pre-baked tag that already contains Play (still run `gapps-check`) |

Do **not** commit proprietary zips. Do **not** trust historical SourceForge/GitHub URLs — they 404'd and produced the empty zip. Re-checked 2026-09-09: still dead, see [What is actually blocking](#what-is-actually-blocking-measured-2026-09-09).

ARM64 + Android version must match the Redroid tag (default guest is Android 11). Mismatch is a warning unless `--require-sdk-match`.

## Bake it into the image (preferred)

`gapps-install` pushes an extracted zip into a **running** container. That is
fine for a one-off probe and wrong for the fleet, for two reasons that are easy
to miss until Play misbehaves:

- Redroid's `/system` is a read-only image layer, so the push does not survive
  `docker rm` or a golden-image redeploy. The phone silently reverts to no Play.
- `adb install` cannot make GmsCore a **privileged** app. It has to sit in
  `priv-app` with a `privapp-permissions` whitelist before first boot, or it
  installs, loses its privileged permissions, and Play sign-in fails looking
  exactly like a network fault.

So bake:

```bash
# Plan the build with no Docker and no device (this is what CI exercises)
./cloud-phone gapps-bake --zip /opt/gapps/MindTheGapps-11.0.0-arm64.zip --dry-run

# Build for the Ampere fleet, then put it on a phone VM without a registry
./cloud-phone gapps-bake --zip /opt/gapps/MindTheGapps-11.0.0-arm64.zip \
    --ship-to ubuntu@10.0.1.127 --ssh-key ~/.ssh/android_arm_cloud_phone_oci

# Or publish it, if you have registry credentials on the phones
./cloud-phone gapps-bake --zip … --tag iad.ocir.io/<ns>/redroid-gapps:11 --push
```

**Architecture is the trap.** The fleet is OCI `VM.Standard.A1.Flex` — Ampere,
arm64. Every prebuilt `*_mindthegapps` image on Docker Hub is **amd64-only**
(checked 2026-09-07: `whojk/redroid` and `tva2002/redroid` publish `amd64` only),
so the tempting shortcut of pulling a community image yields a phone that
cannot run Play at all. The official `redroid/redroid` base *is* multi-arch, so
the bake derives from it and you supply the arm64 zip. An amd64 zip is refused
unless you also ask for `--platform linux/amd64`.

You do **not** need an Ampere machine to produce the Ampere image. The bake is a
`COPY` plus one small `RUN`, so qemu emulation builds it on any x86 host at
negligible cost:

```bash
docker run --privileged --rm tonistiigi/binfmt --install arm64   # once per host
./cloud-phone gapps-bake --zip … --save /tmp/redroid-gapps.tar
```

Without that registration buildx does not fail cleanly — it dies inside the
`RUN` with `exec format error`, which reads like a broken Dockerfile. The
builder therefore checks `docker buildx inspect` for the target platform first
and refuses with the `binfmt` command rather than starting a build that cannot
finish.

The generated Dockerfile copies each partition the zip carries to its real
mount point — MindTheGapps ships `system/product/...`, which must land at
`/product`, **not** `/system/product`, or the guest ends up with two copies of
Play Services on different partitions. It also restates the redroid
`ENTRYPOINT` in full, because adding `ro.setupwizard.mode=DISABLED` replaces it
and dropping `androidboot.hardware=redroid` stops the guest booting.

Then host it exactly like a vanilla phone — `REDROID_IMAGE` is already read by
`docker/redroid-compose.yml`, and `deploy-redroid-oci.sh` takes
`--redroid-image`:

```bash
REDROID_IMAGE=redroid-gapps:11.0.0-arm64 \
  docker compose -f docker/redroid-compose.yml -p phone-1 up -d
./cloud-phone gapps-check   # baking does not self-certify
```

Baking does not remove the validation step. `gapps-check` is the same package
check that guards a runtime install, and a baked image still has to prove
`com.google.android.gms` and `com.android.vending` are present on a booted
guest.

**Not yet built or booted.** The recipe and its refusals are covered offline by
`tests/test_gapps_image.py`; no arm64 image has been produced or started from
it. One input is missing, and it is the zip.

### What is actually blocking (measured 2026-09-09)

The zip has to come from the operator because the channels this repo used to
name no longer serve one. Checked from an unrestricted network, so this is
supply, not egress:

| Channel | State |
|---|---|
| `github.com/MindTheGapps/11.0.0-arm64` | Repo exists, **empty** (`size: 0`), last push 2023-09-22 |
| `api.github.com/repos/MindTheGapps/vendor_gapps/releases` | **0 releases**, no assets |
| `sourceforge.net/projects/mindthegapps/files/11.0/arm64/` | **404** |
| `mindthegapps.com` | 200, but it is now an unrelated SEO blog — not a provenance you should bake into a fleet image |

This is why `--zip` / `GAPPS_ZIP` are operator inputs with a size and layout
check rather than a URL the script trusts. Drop a verified arm64 Android 11 zip
at `/opt/gapps/`, and the bake runs on any x86 host under qemu (above).

The phone VMs themselves are **not** the blocker, and the older notes implying
the lab is gone are wrong. The OCI compartment (listed 2026-09-09 with tenancy
admin) holds `cloud-phone-agent-6c58` and `cloud-phone-dev` (both
`VM.Standard.A1.Flex`, 4 OCPU / 24 GB, RUNNING), `cloud-phone-orch-6c58`
(1/6, RUNNING), `redroid-camera-build` (4/24, RUNNING), plus two STOPPED
`cloud-phone-gapps-test` VMs (2/8). Capacity is arm64 and live; what is missing
is a way in. Port 22 is open on all of them and rejects both stack keys, the
Control API ports are closed from outside, and the OCI *Run Command* plugin —
enabled by API on the two phone hosts on 2026-09-09 — leaves commands
`ACCEPTED` and never executes them, so the on-host `oracle-cloud-agent` is not
functioning either. Regaining shell needs an operator decision: a key-recovery
boot-volume attach, or a fresh VM (which bills — the tenancy is already ~36 A1
OCPUs, past the always-free allowance).

## Commands

```bash
# Validate zip layout without a device (CI)
./scripts/install-gapps-redroid.sh --check-zip /path/to/gapps.zip

# Install into a running Redroid container
GAPPS_ZIP=/opt/gapps/gapps.zip ./scripts/install-gapps-redroid.sh --name redroid

# Confirm Play packages
./scripts/install-gapps-redroid.sh --validate-only --adb 127.0.0.1:5555
```

CLI wrappers: `./cloud-phone gapps-install` · `./cloud-phone gapps-check`.

Installer refuses:

- missing zip
- **empty** zip (the 0-byte lab file)
- zip with no `*.apk` / no GmsCore|Phonesky|vending names

## Validate

Packages that must show up in `pm path`:

- `com.google.android.gms` (Play Services)
- `com.android.vending` (Play Store)
- `com.google.android.gsf` (framework; warn if missing)

Control API `GET /health` reports `gapps: { gms, play_store, gsf, ready }` when ADB is connected. Orchestrator `/pool` reports Redroid vs Cuttlefish members; only Redroid is expected to have `gapps.ready`.

## Historical Redroid scripts

`install-gapps.sh` / `fix-play-services.sh` were deleted in the Cuttlefish slim. Recover from git `0028cb4` for archaeology only — they targeted `docker exec redroid` + broken download URLs. This tree’s installer is `scripts/install-gapps-redroid.sh`.
