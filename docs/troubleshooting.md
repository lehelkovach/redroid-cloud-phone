# Troubleshooting

Start with the phone's own report:

```bash
curl -s http://<host>:8080/health -H "Authorization: Bearer $API_TOKEN"
```

Look at `status`, `adb_connected`, `auth` and `state.last_adb_error`. For more
detail, run the service with `CLOUD_PHONE_VERBOSE=1` and read
`GET /logs?type=ADB` (see [logging.md](logging.md)). On a deployed host:

```bash
sudo systemctl status redroid-cloud-phone.target
sudo journalctl -u control-api-redroid.service -n 100 --no-pager
```

## Redroid

### Redroid container starts then exits

Or: the container runs but `sys.boot_completed` never becomes `1`.

The most common cause is missing binder support in the host kernel. Redroid
needs it and this repo does not set it up.

```bash
grep binder /proc/filesystems        # empty output means no binder support
docker logs <container-name>         # e.g. phone-1
```

Enable binder for your kernel (on Ubuntu usually the `binder_linux` module
from `linux-modules-extra-$(uname -r)`; see the
[Redroid project](https://github.com/remote-android/redroid-doc) for your
distribution), then restart the container:

```bash
./cloud-phone redroid-down --name phone-1    # note: deletes the data volume
./cloud-phone redroid-up --name phone-1 --adb-port 5555
```

On a deployed host: `sudo systemctl restart redroid-cloud-phone.target`.

### `docker not found` or `docker compose` fails

`redroid-up.sh` needs Docker and the compose v2 plugin (`docker compose`, not
`docker-compose`). On Ubuntu: `sudo apt-get install docker.io docker-compose-v2`.

### `/health` says `degraded`

Your token is fine but ADB is not connected.

- Is the phone booted? `adb -s 127.0.0.1:5555 shell getprop sys.boot_completed`
- Is the Control API pointed at the right serial? Check `adb_target` in the
  response and `ADB_CONNECT`. On Redroid hosts, if you changed `ADB_PORT`, add
  `ADB_CONNECT=127.0.0.1:<port>` to `/etc/default/redroid-cloud-phone`.
- Is `adb` installed where the Control API runs? `state.last_adb_error` shows
  `No such file or directory: 'adb'` if not.

### Play Store missing, or `POST /apps/com.android.vending/start` returns 404

The response has `code: "not_launchable"`: the package is not installed.
Redroid has no Play by default; install a GApps zip ([gapps.md](gapps.md)) and
check `gapps.ready` in `/health`.

### `gapps-install` refuses the zip

It rejects missing files, empty (0-byte) files, zips with no `.apk` entries,
and zips with nothing named like `GmsCore`, `Phonesky` or `vending`. Check
with `./scripts/install-gapps-redroid.sh --check-zip <zip>`.

## Authentication and requests

### `/health` says `unauthorized`

The phone may be fine; your token is wrong or missing. Compare the token you
send with `API_TOKEN` on that host.

### `401` with `code: auth_required` or `auth_invalid`

`auth_required`: you sent no token. `auth_invalid`: you sent the wrong one.
Send `Authorization: Bearer <token>`.

### `415 Unsupported Media Type`

The endpoint reads JSON and the request has no `Content-Type:
application/json` header. Add `-H 'Content-Type: application/json'`. For
`DELETE /proxy` and `DELETE /location`, also send `-d '{}'`.

### `verify-phone` fails with `unexpected health status: unauthorized`

`verify-redroid-phone.sh` does not send a token, so it fails once `API_TOKEN`
is set. Check the phone with `curl` and your token instead.

## Orchestrator

### `409` with `phone not clean`

The idle instance was last leased by a different owner and has not been
reset. No endpoint records resets yet, so this instance will keep refusing
other owners. Restart the orchestrator (this forgets all instances), or in
mock mode simply use the same `owner_user_id` while testing.

### `ORCH_CONTROL_API_TOKEN must match API_TOKEN on the phone`

The orchestrator's outbound token is rejected by the phone. Set
`ORCH_CONTROL_API_TOKEN` to the phone's `API_TOKEN`. If unset, it falls back
to `ORCH_API_TOKEN`.

### `409` with `pool full` or `Instance limit reached`

Raise `ORCH_MAX_REDROID_INSTANCES`, `ORCH_MAX_CUTTLEFISH_INSTANCES` or
`ORCH_MAX_INSTANCES`, or delete instances with `DELETE /instances/<id>`.

### The orchestrator forgot my instances

All state is in memory and is lost on restart. VMs created in `oci` mode keep
running; find and terminate them in the OCI console or with the `oci` CLI.

### `camera purpose needs a Cuttlefish OCI golden`

In `redroid` mode, a `purpose: "camera"` request needs
`CUTTLEFISH_GOLDEN_IMAGE_ID` and the OCI settings, because there is no local
ingest host.

## OCI deploys

### `COMPARTMENT_ID, SUBNET_ID, and AVAILABILITY_DOMAIN are required`

Export them (see [deploy-oci.md](deploy-oci.md)), or pass `--to-instance <IP>`
to reuse an existing instance.

### `SSH private key not found`

The deploy scripts use `SSH_KEY_FILE` (the `.pub` file) and expect the
private key at the same path without `.pub`.

### `create-golden` cannot SSH to the host

`create-golden-image.sh` uses `SSH_KEY_FILE` as the private key. Run it with
`SSH_KEY_FILE=~/.ssh/android_arm_cloud_phone_oci` (no `.pub`).

### `Instance with IP ... not found`

`create-golden-image.sh` looks the instance up by public IP in
`COMPARTMENT_ID`. Check both.

## Cuttlefish

### `/dev/kvm is missing`

The instance does not expose hardware virtualisation, and Cuttlefish cannot
run on it. Check with `test -e /dev/kvm`. Use a shape or host that provides
KVM.

### `launch_cvd/cvd not found`

This repo does not install the Cuttlefish host tools. Install them, then rerun
the installer, or pass `--skip-tools-check` to install the rest first. See
[cuttlefish-ingest.md](cuttlefish-ingest.md#what-you-must-supply).

### ADB to the Cuttlefish guest does not connect

- `cvd fleet` should list your instance (default `cvd-arm64-1`).
- Connect to the serial it reports: `adb connect <serial>`.
- Read the launch log: `/tmp/<instance-name>-launch.log`.
- `control-api.service` uses `ADB_CONNECT=127.0.0.1:5555`. If the guest's
  serial differs, override it with `sudo systemctl edit control-api.service`.

### `dumpsys media.camera` reports no cameras

The runtime check only looks for camera devices. Feeding the bridge output
into the guest's cameras needs injector commands you provide
([cuttlefish-ingest.md](cuttlefish-ingest.md#the-bridge)).

### OBS cannot connect

- TCP 1935 must be open in the security list for OBS's address.
- On the host: `systemctl status nginx-rtmp.service` and
  `curl -s http://127.0.0.1:8081/health` (expects `OK`).
- Bridge logs: `sudo journalctl -u cuttlefish-rtmp-bridge.service -n 100 --no-pager`.

## Tests and CI

### `coverage below 80%`

`./cloud-phone test --coverage` uses a floor of 80% by default; CI uses 60%.
Run `./cloud-phone test --coverage --fail-under 60` to match CI.

### A CI run fails in a few seconds with no steps

The comment at the top of `.github/workflows/ci.yml` notes that a run failing
in 2-3 seconds with `steps=0` and `BlobNotFound` logs is a GitHub Actions
budget block, not a test failure. Check Settings > Billing > Budgets.
