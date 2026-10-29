# Control API reference

The Control API (`api/server.py`) runs on each phone host and turns HTTP
requests into `adb` commands against one Android guest. Start it locally with
`./cloud-phone api-run`; on deployed hosts it runs as
`control-api-redroid.service` or `control-api.service`.

Base URL: `http://<host>:8080` (`API_HOST`, `API_PORT`).

## Conventions

**Authentication.** When `API_TOKEN` is set, send it on every request:

```
Authorization: Bearer <API_TOKEN>
```

The raw token without the `Bearer ` prefix is also accepted. When `API_TOKEN`
is empty, no endpoint checks anything. Four endpoints never require a token:
`GET /health`, `GET /logs`, `GET /appium/status`, `GET /vnc/status`.

A rejected request gets `401` with a `WWW-Authenticate: Bearer` header:

```json
{"success": false, "error": "Unauthorized", "code": "auth_required", "auth_required": true}
```

`code` is `auth_required` when no token was sent and `auth_invalid` when it
was wrong.

**Request bodies.** JSON endpoints need `Content-Type: application/json`.
Without it Flask returns `415 Unsupported Media Type` before the handler runs.

**Choosing the phone.** Commands go to `ADB_CONNECT` (default
`127.0.0.1:5555`). A request may name a different ADB serial in the
`X-Cloud-Phone-Adb` header (letters, digits and `._:[]-`, under 128
characters). The orchestrator uses this to drive several phones through one
Control API.

**Success flags.** Most responses carry `success`. Several endpoints report
`success: true` without checking the `adb` result; they are marked below.

**Example.**

```bash
curl -s -X POST http://127.0.0.1:8080/device/input \
  -H "Authorization: Bearer $API_TOKEN" -H 'Content-Type: application/json' \
  -d '{"type":"tap","x":640,"y":360}'
```

## Health and status

### `GET /health` (no auth)

Always `200`. Tries to connect ADB, checks Play packages, and reports whether
*this caller* could drive the phone.

```json
{
  "status": "healthy",
  "adb_connected": true,
  "adb_target": "127.0.0.1:5555",
  "auth": {"required": true, "presented": true, "ok": true},
  "usable": true,
  "runtime": "redroid",
  "gapps": {"gms": true, "play_store": true, "gsf": true, "ready": true},
  "appium": {"url": "http://127.0.0.1:4723", "ready": false, "backend": "adb"},
  "vnc": {"protocol": "rfb", "port": 5900, "width": 1280, "height": 720, "fps": 30, "clients": 0, "frames": 0, "runtime": "redroid"},
  "state": {"proxy": {...}, "location": {...}, "connected": true, "last_adb_error": "", "ui_backend": "adb"}
}
```

| `status` | Meaning |
|---|---|
| `healthy` | ADB is connected and your token is accepted |
| `degraded` | Your token is accepted, but ADB is not connected |
| `unauthorized` | Your token would be rejected; the phone itself may be fine |

`usable` is `adb_connected AND auth.ok`. `runtime` is `CLOUD_PHONE_RUNTIME`.

### `GET /status`

```json
{"connected": true,
 "device": {"model": "...", "android_version": "11", "sdk_version": "30"},
 "battery": "100", "screen_state": "on",
 "proxy": {...}, "location": {...}, "gapps": {...}}
```

## Input and screen

### `POST /device/input`

| `type` | Fields (defaults) | Effect |
|---|---|---|
| `tap` (default) | `x` (500), `y` (500) | `input tap x y` |
| `tap_label` | `label` | Reads the UI tree and taps the best match (see `/device/ui`) |
| `swipe` | `x1` (100), `y1` (100), `x2` (500), `y2` (500), `duration` ms (300) | `input swipe` |
| `text` | `text` | `input text`; spaces become `%s` |
| `key` | `keycode` (4 = Back) | `input keyevent` |

Returns `{"success": true, "type": "<type>"}` without checking the `adb`
result. `tap_label` returns `{"success", "type", "label", "x", "y", "matched"}`,
or `success: false` with up to 12 `available` labels when nothing matched.

### `POST /device/screen`

Body `{"action": "on" | "off" | "toggle" | "unlock"}` (default `toggle`).
Sends wake/sleep/power key events; `unlock` also swipes up. Always reports
`success: true`.

### `GET /device/screenshot`

PNG image (`image/png`). `500` with `{"error": ...}` on failure.

### `GET /device/screenshot/base64`

`{"success": true, "image_base64": "..."}`, or `500`.

### `GET /device/ui` (also `POST`)

Runs `uiautomator dump` and returns elements that have a label or resource ID,
with the centre of their bounds as the tap point:

```json
{"success": true, "count": 2, "elements": [
  {"label": "Email", "resource_id": "com.example:id/email", "clickable": true,
   "focused": false, "password": false, "x": 540, "y": 812, "bounds": [40, 760, 1040, 864]}
]}
```

`500` with `success: false` if the dump fails. When matching a label,
clickable elements win over exact text, and among clickable ones an exact
match wins over a partial one.

### `GET /device/focus`

`{"success": true, "focus": "mCurrentFocus=Window{... com.example/.MainActivity}"}`.

## UI commandlets

A second input interface with percentage coordinates and named keys. It
returns the exact `adb` commands it ran.

### `POST /ui/command`

| Field | Meaning |
|---|---|
| `action` (or `type`) | `tap`, `long_press`, `swipe`, `text`/`type`, `key` |
| `x`, `y` | Pixels, or a float from 0 to 1, or a string like `"50%"` |
| `xp`, `yp` | Percent of screen width/height |
| `x1`, `y1`, `x2`, `y2` (or `x1p` ... `y2p`) | Swipe start and end |
| `duration` | ms; `swipe` default 300, `long_press` default 800 |
| `text` | For `text` |
| `key` or `keycode` | Number, or a name: `back`, `home`, `menu`, `recents`, `enter`, `tab`, `space`, `del`, `escape`, `up`, `down`, `left`, `right`, `power`, `volume_up`, `volume_down`, `search` |
| `backend` | `adb` (default `UI_BACKEND`) or `appium` |

```json
{"success": true, "backend": "adb", "size": {"width": 1280, "height": 720},
 "commands": ["input tap 640 360"],
 "results": [{"cmd": "input tap 640 360", "ok": true, "stdout": "", "stderr": ""}],
 "w3c": {"actions": [...]}}
```

`400` for an invalid command, `501` when `backend` is `appium`.

### Shortcuts

`POST /ui/tap`, `POST /ui/swipe`, `POST /ui/text`, `POST /ui/key` take the
same body with `action` preset.

### `GET /ui/screen`

Screenshot as base64 plus `width`, `height` and `backend`.

## Apps

| Method and path | Body | Response |
|---|---|---|
| `GET /apps` | `?scope=third_party` (default), `system` or `all` | `{"success", "scope", "packages": [...], "count"}`; `400 invalid_scope`, `502` when `pm` fails |
| `GET /apps/<package>` | | `{"success", "installed": true, "version_name", "version_code", ...}`; `404 {"installed": false}`; `400` for an invalid name |
| `POST /apps/install` | See [Install an app](#install-an-app) | Installs an APK, split set or bundle by host path or HTTPS URL + SHA-256 |
| `GET /apps/play/status` | | What stands between this phone and Play: `{"gapps", "google_account", "gsf_android_id", "uncertified_registration_url", "ready", "owner_action"?}` |
| `POST /apps/play/install` | `{"package", "wait"?, "timeout"?}` | Install from Play by package name; **requires the owner's sign-in**. See below |
| `POST /apps/<package>/start` | | See below |
| `POST /apps/<package>/stop` | | `am force-stop`; `{"success", "message"}` |
| `POST /apps/<package>/clear` | | `pm clear`; `{"success", "message"}` |
| `DELETE /apps/<package>/uninstall` | | `adb uninstall`; `{"success", "message"}` |

`POST /apps/<package>/start` resolves the launcher activity and runs
`am start`:

- `200 {"success": true, "activity": "pkg/.Main"}`, or
  `{"success": true, "package": ..., "via": "monkey"}` if it fell back to `monkey`.
- `502` when the activity was found but `am start` reported an error.
- `404 {"code": "not_launchable"}` when the package has no launchable activity
  (usually: not installed).

### Install an app

`POST /apps/install` takes exactly one source:

| Body | Meaning |
|---|---|
| `{"path": "/opt/cloud-phone/apks/app.apk", "sha256"?: "..."}` | A file on the Control API host. Must resolve (after symlinks) inside `APP_INSTALL_DIRS`, else `403 path_not_allowed`. `sha256`, when given, is checked |
| `{"paths": ["base.apk", "split_config.arm64_v8a.apk"]}` | A split set, installed with `adb install-multiple` |
| `{"url": "https://...", "sha256": "..."}` | Downloaded, then checked. `sha256` is **required** (`400 checksum_required`) |
| `{"urls": [{"url", "sha256"}, ...]}` | A split set from URLs, each with its own `sha256` |

A file that is a zip with top-level `.apk` entries and no `AndroidManifest.xml`
(`.apks`, `.xapk`, `.apkm`) is unpacked and installed with `install-multiple`;
OBB files in it are listed in `obb_not_installed`, not pushed.

Optional booleans: `replace` (default `true`, `-r`), `grant_permissions`
(`-g`), `downgrade` (`-d`). `expected_package` makes the call fail unless that
package is installed afterwards. Raw `adb` flags are not accepted.

URLs must be `https://` (also after redirects), must not resolve to a private,
loopback or link-local address unless `APP_INSTALL_ALLOW_PRIVATE=1`, and may
not exceed `APP_INSTALL_MAX_BYTES`. URLs are logged and echoed as
scheme://host/path only, so a signed URL's query string never reaches the logs.

| Status | `code` | When |
|---|---|---|
| `200` | | `{"success": true, "method": "install"\|"install-multiple", "files", "new_packages", "sources", "package"?, "verified"?}` |
| `400` | `invalid_body`, `checksum_required`, `invalid_checksum`, `invalid_package`, `too_many_files`, `insecure_url`, `invalid_url` | Bad request; nothing downloaded |
| `403` | `path_not_allowed`, `private_address` | |
| `404` | `not_found` | No file at `path` |
| `413` | `too_large` | |
| `422` | `checksum_mismatch`, `not_an_apk` | The file was deleted; nothing was installed. The actual digest is not returned |
| `502` | `install_failed` (with `install_error`, e.g. `INSTALL_FAILED_VERSION_DOWNGRADE`), `download_failed`, `package_not_found_after_install` | |

```bash
curl -s -X POST $API/apps/install -H "Authorization: Bearer $API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.org/app.apk","sha256":"<64 hex>","expected_package":"org.example.app"}'
```

### Install from Google Play

`POST /apps/play/install {"package": "com.example.app"}` drives the Play
Store app on the phone: it opens `market://details?id=<package>`, taps
**Install** and waits until `pm path` finds the package. It only works after
the phone's owner has registered the device and signed in to Play on it
([GAPPS-AND-APP-INSTALL.md](../GAPPS-AND-APP-INSTALL.md#play-certification-and-sign-in-the-owners-steps)).

- `409 gapps_missing` when Play services or the Play Store are not installed.
- `409 play_sign_in_required` with `owner_action` when no Google account is
  signed in on the phone.
- Otherwise `202 {"job_id", "type": "play_install"}`; poll `GET /jobs/<id>`.
  With `"wait": true` it runs in the request and answers `200` or `502`.
- Job result codes: `already_installed` (success), `paid_app` (never buys),
  `play_sign_in_required`, `not_available`, `install_button_not_found`,
  `install_timeout`, `play_not_launchable`.
- `timeout` is 10 to 3600 seconds (default `PLAY_INSTALL_TIMEOUT`, 600).

It reads the English Play Store labels. It reports whether a Google account
exists, never which.

## ADB passthrough

These give full control of the guest. Protect them with `API_TOKEN` and a
firewall (see [SECURITY.md](../../SECURITY.md)).

| Method and path | Body | Response |
|---|---|---|
| `POST /adb/shell` | `{"command": "...", "timeout": 30}` | `{"success", "stdout", "stderr"}`; `400` without `command` |
| `POST /adb/push` | multipart: `file`, optional `path` (default `/sdcard/<filename>`) | `{"success", "path", "message"}` |
| `POST /adb/pull` | `{"path": "/sdcard/x"}` | `{"success": true, "path", "content_base64"}`, or `404` |
| `POST /adb/install` | multipart: `file` (APK), optional `options` (default `-r`) | `{"success", "message"}`; 120 s timeout |

```bash
curl -s -X POST $API/adb/install -H "Authorization: Bearer $API_TOKEN" \
  -F file=@app.apk -F options="-r -g"
```

## Android settings

| Method and path | Body | Response |
|---|---|---|
| `GET /settings/<namespace>/<key>` | | `{"namespace", "key", "value"}` |
| `PUT /settings/<namespace>/<key>` | `{"value": "..."}` | `{"success", "message"}` |

`namespace` must be `system`, `secure` or `global` (`400` otherwise).

## Network proxy

| Method and path | Body |
|---|---|
| `GET /proxy` | Returns the stored proxy state |
| `POST /proxy` | `{"enabled": true, "type": "http" \| "socks5" \| "transparent", "host", "port", "username", "password"}` |
| `DELETE /proxy` | Disables the proxy. Send `Content-Type: application/json` and `{}`, or Flask returns 415. |

- `http`: `settings put global http_proxy host:port`.
- `socks5`: runs `/opt/cloud-phone-scripts/proxy-control.sh` if present,
  otherwise sets `persist.proxy.socks5.host`/`port` properties.
- `transparent`: requires `proxy-control.sh`, otherwise `500`.

`proxy-control.sh` is not part of this repo. Returns
`{"success", "message", "proxy"}`; `400` if `enabled` without `host`/`port`.

## Location

| Method and path | Body |
|---|---|
| `GET /location` | Returns the stored location |
| `POST /location` | `{"enabled": true, "latitude", "longitude", "altitude": 0, "accuracy": 10}` |
| `DELETE /location` | Disables. Same 415 note as `DELETE /proxy`. |

Enabling turns on `mock_location`, sends a `MOCK_LOCATION` broadcast, allows
mock location for the shell, sends `adb emu geo fix` (emulators only) and
opens a `geo:` intent. It always reports `success: true`. Whether apps see the
new location depends on the guest; this is not verified by the tests.

## Device identity and anti-detection

These endpoints change the properties and identifiers the guest reports to
apps. Device profiles are `.prop` files in `PROFILES_DIR`
(`config/device-profiles/`: `samsung-galaxy-s21`, `google-pixel-6`,
`oneplus-9-pro`).

| Method and path | Body | Effect |
|---|---|---|
| `GET /device/identity` | | Current `model`, `brand`, `fingerprint`, `android_id`, `serial`, `debuggable`, `qemu_detected`, plus the stored spoofing state |
| `POST /device/identity` | `{"profile": "<name>" \| "random", "generate_ids": true, "custom": {...}}` | `setprop` for each line of the profile; sets `android_id`, `ro.serialno`, `ro.boot.serialno` and `gsm.sim.imei` from generated or custom IDs. Returns the IDs and `properties_applied`. |
| `GET /device/identity/profiles` | | `{"profiles": [...], "current"}` |
| `POST /device/identity/generate` | | New random IMEI, serial, MAC addresses, Android ID and advertising ID. Returned, not applied. |
| `POST /device/antidetect` | `{"profile", "hide_root": true, "hide_emulator": true, "spoof_battery": true}` | Applies the profile (via `/opt/cloud-phone-scripts/anti-detection.sh` if present, which is not in this repo, otherwise as `POST /device/identity`). `hide_emulator` sets `ro.kernel.qemu`, `ro.hardware.virtual_device`, `ro.boot.qemu`, `qemu.hw.mainkeys` to `0`. `hide_root` sets `ro.debuggable=0`, `ro.secure=1`, `ro.boot.verifiedbootstate=green`, `ro.boot.flash.locked=1`. `spoof_battery` sets a random level from 45 to 85, not charging. |
| `GET /device/antidetect/status` | | Checks four properties and returns `checks`, `score` and `detection_risk` |
| `POST /device/antidetect/reset` | | Resets the battery and sets `ro.debuggable=1` |

Each step is reported as `success: true` without checking whether `setprop`
took effect. Android normally refuses to change `ro.*` properties after boot,
so check with `GET /device/identity` rather than trusting the response.

## Jobs (asynchronous)

### `POST /jobs`

```json
{"type": "adb_shell", "payload": {"command": "pm list packages"}}
```

| `type` | `payload` |
|---|---|
| `adb_shell` | `command`, `timeout` |
| `device_input` | Same body as `POST /device/input` |
| `screen` | `action` |
| `screenshot` | none |
| `app_start`, `app_stop`, `app_clear`, `app_uninstall` | `package` |
| `play_install` | `package`, `timeout` (same as `POST /apps/play/install`) |

Returns `202 {"job_id", "status": "queued", "type"}`.

### `GET /jobs/<job_id>`

```json
{"id": "...", "type": "adb_shell", "status": "done", "created_at": 0, "updated_at": 0,
 "result": {...}, "error": null}
```

`status` is `queued`, `running`, `done` or `failed`. Jobs live in memory, are
dropped after `JOB_TTL_SECONDS`, and at most `JOB_MAX` are kept. `404` for an
unknown or expired job.

## Configuration file

| Method and path | Effect |
|---|---|
| `GET /config` | Contents of `CONFIG_FILE` plus `runtime_state` |
| `POST /config` | Merges the JSON body into `CONFIG_FILE` and saves it. Nothing else is applied. |

## Appium

`GET /appium/status` (no auth) returns `{"url", "ready", "backend"}`. `ready`
is true only if the `appium` Python package is importable; it is not in
`api/requirements.txt`. `POST /appium/session` always returns `501`: Appium
sessions are not implemented. Nothing in this repo installs an Appium server.

## Viewport ("VNC")

No VNC server is started. These endpoints track a viewport size and a client
count for logging.

| Method and path | Response |
|---|---|
| `GET /vnc/status` (no auth) | `{"protocol": "rfb", "port", "width", "height", "fps", "clients", "frames", "runtime"}` |
| `POST /vnc/attach` | `201` with the same object, `clients` incremented |
| `POST /vnc/detach` | `{"success": true, "clients"}` |

## Logs

`GET /logs?type=CMD,ADB&n=200` (no auth) returns recent log lines from memory:

```json
{"count": 1, "logs": [{"ts": "2026-01-01 12:00:00.000", "type": "CMD", "level": "INFO", "logger": "control_api", "msg": "..."}]}
```

`type` is a comma-separated list of labels (see [logging.md](../logging.md));
`n` defaults to 200. The buffer holds `LOG_RING_SIZE` lines (default 4000).

## Legacy: `api/agent_api.py`

`api/agent_api.py` is a separate, older Flask app with routes such as
`/screen/screenshot` and `/input/tap`. No script, systemd unit or Dockerfile
starts it, and it is not documented here. `api/Dockerfile` builds an image of
the `api/` directory that runs `server.py`; it is not used by the deploy
scripts, and from inside a container it cannot reach ADB on the host's
`127.0.0.1` unless the container uses host networking.
