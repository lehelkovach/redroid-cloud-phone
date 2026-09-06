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
