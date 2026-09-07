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

Do **not** commit proprietary zips. Do **not** trust historical SourceForge/GitHub URLs — they 404'd and produced the empty zip.

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
it, because that needs an arm64 Docker host and an operator-supplied zip.

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
