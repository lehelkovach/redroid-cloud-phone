# Getting started

This guide runs everything on one Linux machine: a Redroid phone in Docker,
the Control API in front of it, and the orchestrator in mock mode. Nothing here
touches a cloud account. To deploy on Oracle Cloud, see
[deploy-oci.md](deploy-oci.md).

## 1. Check the host

You need:

| Tool | Check | Notes |
|---|---|---|
| Python 3 with venv | `python3 -m venv --help` | CI uses 3.12 |
| adb | `adb version` | Ubuntu: `sudo apt-get install android-tools-adb` |
| Docker + compose plugin | `docker compose version` | `redroid-up` calls `docker compose` |
| Binder in the host kernel | `grep binder /proc/filesystems` | Required by Redroid, see below |

### Binder

Redroid runs Android's userspace directly on your host kernel, and Android
needs the kernel's binder IPC driver. **The scripts in this repo do not set it
up.** If `grep binder /proc/filesystems` prints nothing, the container will
start and then fail to boot Android.

On Ubuntu the driver usually ships as the `binder_linux` module in the
`linux-modules-extra-$(uname -r)` package. The exact steps depend on your
kernel; follow the host setup notes of the
[Redroid project](https://github.com/remote-android/redroid-doc) for your
distribution. After loading it, check again:

```bash
grep binder /proc/filesystems     # expect a line containing "binder"
```

## 2. Install the Python dependencies

```bash
git clone https://github.com/lehelkovach/redroid-cloud-phone.git
cd redroid-cloud-phone
python3 -m venv .venv
. .venv/bin/activate
pip install -r api/requirements.txt -r orchestrator/requirements.txt
```

`.env.example` lists the settings the project knows about. Nothing loads `.env`
automatically. Copy it, edit it, and export it into your shell if you want to
use it:

```bash
cp .env.example .env
set -a; . ./.env; set +a
```

Note that `.env.example` sets `API_HOST=0.0.0.0`, which makes the Control API
listen on all interfaces. For local work, leave `API_HOST` unset so it binds
to `127.0.0.1`. It also sets `API_TOKEN=` and `ORCH_API_TOKEN=` to empty
values, so fill them in, or export your tokens after loading the file.

## 3. Start a phone

```bash
./cloud-phone redroid-up --name phone-1 --adb-port 5555
```

This runs `docker compose -f docker/redroid-compose.yml -p phone-1 up -d`.
The container is privileged, uses the image `redroid/redroid:11.0.0-latest`,
publishes ADB on host port 5555 (all interfaces) and keeps `/data` in a Docker
volume. Change the image with `--image` or `REDROID_IMAGE`. Screen size comes
from `REDROID_WIDTH`, `REDROID_HEIGHT`, `REDROID_FPS` and `REDROID_DPI`
(defaults 1280, 720, 30, 240).

Wait for Android to boot:

```bash
adb connect 127.0.0.1:5555
adb -s 127.0.0.1:5555 shell getprop sys.boot_completed   # prints 1 when ready
```

To see what the script would do without Docker:

```bash
./cloud-phone redroid-up --dry-run --json --name phone-1
```

## 4. Start the Control API

Always set a token. With `API_TOKEN` empty, every endpoint, including
`/adb/shell`, is open to anyone who can reach the port.

```bash
export API_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
./cloud-phone api-run
```

It listens on `127.0.0.1:8080` and talks to `127.0.0.1:5555` by default
(`API_HOST`, `API_PORT`, `ADB_CONNECT`). All settings are in
[configuration.md](configuration.md).

## 5. Drive the phone

In a second terminal, export the same `API_TOKEN`, then:

```bash
API=http://127.0.0.1:8080
AUTH="Authorization: Bearer $API_TOKEN"

# Is the phone reachable, and does my token work?
curl -s $API/health -H "$AUTH"

# Tap the centre of a 1280x720 screen
curl -s -X POST $API/device/input -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"type":"tap","x":640,"y":360}'

# Same tap using percentages, through the UI commandlet endpoint
curl -s -X POST $API/ui/tap -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"xp":50,"yp":50}'

# Save a screenshot
curl -s $API/device/screenshot -H "$AUTH" -o screen.png

# List on-screen elements with their labels and tap points
curl -s $API/device/ui -H "$AUTH"

# Tap an element by its label
curl -s -X POST $API/device/input -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"type":"tap_label","label":"Settings"}'
```

`/health` answers `status: "healthy"` when ADB is connected and your token is
accepted, `degraded` when ADB is not connected, and `unauthorized` when your
token would be rejected. JSON endpoints need the `Content-Type:
application/json` header; without it Flask answers 415. The full list of
endpoints is in [api/control-api.md](api/control-api.md).

## 6. Try the orchestrator

The orchestrator hands out phones per user and relays calls to their Control
API. In mock mode it creates no containers or VMs. Each instance it "creates"
points at `ORCH_MOCK_API_URL` (default `http://127.0.0.1:8080`), which is the
Control API from step 4.

```bash
export ORCH_API_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
export ORCH_CONTROL_API_TOKEN="$API_TOKEN"
ORCH_DEPLOY_MODE=mock ./cloud-phone orchestrator-run     # listens on 127.0.0.1:8090
```

In another terminal:

```bash
ORCH=http://127.0.0.1:8090
OAUTH="Authorization: Bearer $ORCH_API_TOKEN"

curl -s $ORCH/health
curl -s -X POST $ORCH/sessions -H "$OAUTH" -H 'Content-Type: application/json' \
  -d '{"owner_user_id":"alice"}'
curl -s $ORCH/pool -H "$OAUTH"

# Use the instance_id from the session
curl -s -X POST $ORCH/phones/<instance_id>/input -H "$OAUTH" \
  -H 'Content-Type: application/json' -d '{"type":"tap","x":640,"y":360}'

# Run a short procedure and wait for the result
curl -s -X POST $ORCH/procedures -H "$OAUTH" -H 'Content-Type: application/json' -d '{
  "sync": true,
  "instance_id": "<instance_id>",
  "steps": [
    {"action": "tap", "x": 640, "y": 360},
    {"action": "wait", "duration_ms": 500},
    {"action": "key", "keycode": 3}
  ]
}'

curl -s -X DELETE $ORCH/sessions/alice -H "$OAUTH"
```

Two things to know:

- Without `ORCH_STATE_DB` the orchestrator keeps everything in memory, and
  restarting it forgets all instances and sessions. Set `ORCH_STATE_DB` to a
  file to keep leases across restarts (procedure results are never kept). It
  refuses to start without `ORCH_API_TOKEN`; see [isolation.md](isolation.md)
  for browsers, tickets and the relay.
- Once a user has held an instance, it is not given to a different user until
  a reset is recorded, and no endpoint records one yet. In practice a second
  user gets `409 phone not clean` from the same instance. Restart the
  orchestrator to clear this while testing. See [ROADMAP.md](../ROADMAP.md).

A `purpose: "camera"` session works in mock mode too and includes an `ingest`
block with an RTMP URL, but nothing is listening for RTMP unless you point
`ORCH_MOCK_CAMERA_API_URL` at a real Cuttlefish host.

### Local Redroid mode

With `ORCH_DEPLOY_MODE=redroid`, the orchestrator starts a real container for
each new automation instance by running
`scripts/redroid-up.sh --name orchestrated-phone-<port> --adb-port <port> --json`.
Ports start at `ORCH_REDROID_ADB_PORT_BASE` (default 5555) and go up by one.
All these containers share one Control API (`ORCH_REDROID_API_URL`, default
`ORCH_MOCK_API_URL`). The orchestrator selects the phone by sending its ADB
serial in the `X-Cloud-Phone-Adb` header. Stop the phone from step 3 first, or
set the port base higher, so the first port does not collide.

## 7. Add Google Play (optional)

Redroid images do not include Play Store or Play services. You supply a GApps
zip that matches the guest's Android version and CPU architecture:

```bash
GAPPS_ZIP=/path/to/gapps.zip ./cloud-phone gapps-install --adb 127.0.0.1:5555
./cloud-phone gapps-check --adb 127.0.0.1:5555
```

See [gapps.md](gapps.md).

## 8. Clean up

```bash
./cloud-phone redroid-down --name phone-1
```

This runs `docker compose ... down -v`, which removes the container **and its
data volume**. Anything installed or signed in on the phone is gone.

## Next steps

- Run the tests: [CONTRIBUTING.md](../CONTRIBUTING.md)
- Deploy to Oracle Cloud: [deploy-oci.md](deploy-oci.md)
- Lock it down before exposing any port: [SECURITY.md](../SECURITY.md)
