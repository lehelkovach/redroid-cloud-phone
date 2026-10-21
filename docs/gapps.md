# Google Play (GApps) on Redroid

Redroid images do not include Google Play Store or Google Play services. Apps
that need them, and anything that signs in with a Google account, need a GApps
package installed into the phone. This applies to Redroid phones only. The
Cuttlefish ingest hosts are kept without GApps (see
[architecture.md](architecture.md)).

The device profiles in `config/device-profiles/` set properties such as
`ro.com.google.gmsversion`. Setting those does **not** install Play; only the
packages below count.

## Supply your own zip

This repo does not ship or download a GApps package. You provide one, for
example from MindTheGapps, NikGApps or OpenGApps, that matches:

- the guest's Android version (the default image is
  `redroid/redroid:11.0.0-latest`, Android 11), and
- the CPU architecture of the host (ARM64 on OCI A1 instances).

Do not commit the zip. `.gitignore` excludes `gapps.zip` and `*.apk`.

| Setting | Meaning |
|---|---|
| `GAPPS_ZIP` | Path to the zip |
| `--zip PATH` | Same, as a flag (takes precedence) |
| `GAPPS_ZIP_URL` | HTTPS URL to download from when the zip file does not exist yet |
| `/opt/gapps/gapps.zip` | Path used when neither is set (`GAPPS_DIR` changes the directory) |
| `GAPPS_MIN_BYTES` | Optional minimum zip size |

## Commands

```bash
# Check a zip without a device (also used by the tests)
./scripts/install-gapps-redroid.sh --check-zip /path/to/gapps.zip

# Install into a running phone over ADB
GAPPS_ZIP=/path/to/gapps.zip ./cloud-phone gapps-install --adb 127.0.0.1:5555

# Check that Play packages are present
./cloud-phone gapps-check --adb 127.0.0.1:5555
```

`gapps-install` and `gapps-check` wrap `scripts/install-gapps-redroid.sh`
(`gapps-check` adds `--validate-only`). The ADB serial defaults to
`ADB_CONNECT` or `127.0.0.1:5555`. `--name` is accepted but not used; the
script works only over ADB. `--dry-run` prints the steps without changing the
phone.

The installer refuses a zip that is missing, empty (0 bytes), has no `.apk`
entries, or has no entry named like `GmsCore`, `Phonesky`, `vending` or
`GoogleServicesFramework`.

To install, it extracts the zip, runs `adb root` and `adb remount`, pushes any
`system/`, `product/`, `system_ext/` and `vendor/` directories it finds, falls back to
`adb install -r -g` for each APK, reboots the guest and waits for ADB.
`--require-sdk-match` prints the device's SDK level next to the zip name; it
does not compare them.

On OCI, `deploy-redroid` copies `GAPPS_ZIP` to `/opt/gapps/gapps.zip` on the
host and the installer runs this step automatically once the phone answers on
ADB. See [deploy-oci.md](deploy-oci.md).

## Check it worked

These packages must be installed (`pm path` finds them):

| Package | What | Required |
|---|---|---|
| `com.google.android.gms` | Google Play services | Yes |
| `com.android.vending` | Play Store | Yes |
| `com.google.android.gsf` | Google Services Framework | Reported, not required |

The Control API reports the same check on `GET /health` and `GET /status`:

```json
"gapps": {"gms": true, "play_store": true, "gsf": true, "ready": true}
```

`ready` is true when both `gms` and `play_store` are present. The orchestrator
marks Redroid instances with `"gapps": true` in sessions and `/instances`;
that flag comes from the instance's purpose, not from a check on the phone.

`POST /apps/com.android.vending/start` returns 404 with
`code: "not_launchable"` when Play Store is not installed.
