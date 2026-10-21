# Security

This project gives remote, scriptable control of Android devices, including
an ADB shell in the guest. Treat every exposed port as a way into the phone
and, in some cases, into the host.

## What is exposed by default

| Port | Service | Default exposure | Authentication |
|---|---|---|---|
| 8080 | Control API | `127.0.0.1` when run with `api-run`; **`0.0.0.0` under the systemd units** | None unless `API_TOKEN` is set. The installers do not set it. |
| 8090 | Orchestrator | `0.0.0.0` | None unless `ORCH_API_TOKEN` is set |
| 5555 | Redroid ADB | **All interfaces** (Docker port mapping) | None. ADB has no authentication here. |
| 1935 | RTMP ingest (Cuttlefish) | All interfaces | None. `allow publish all` in `config/nginx-rtmp.conf`. |
| 8443, 15550-15599 | Cuttlefish WebRTC and control | As configured by `launch_cvd` | See Cuttlefish documentation |
| 127.0.0.1:8081 | nginx status and health | Loopback only | None |

### What an unauthenticated Control API allows

With `API_TOKEN` empty, anyone who can reach port 8080 can:

- run any shell command in the guest (`POST /adb/shell`, `POST /jobs`),
- install APKs (`POST /adb/install`) and push or pull files (`/adb/push`,
  `/adb/pull`),
- change Android settings, the proxy, the location and device identifiers,
- read screenshots and the on-screen UI tree,
- point the API at any other ADB serial it can reach, using the
  `X-Cloud-Phone-Adb` header.

The deployed Control API runs as `root` on the host (`User=root` in the
systemd units). `POST /config` writes a JSON file at `CONFIG_FILE`.

Even with a token set, `GET /health`, `GET /logs`, `GET /appium/status` and
`GET /vnc/status` stay open. `/logs` returns recent log lines, which include
request paths and ADB commands.

### Orchestrator

- With `ORCH_ENABLE_CONSOLE=1`, the `console` procedure surface runs shell
  commands **on the orchestrator host** for any authenticated caller.
- Login operations and procedures keep the request body, including passwords,
  in memory, and return it from `GET /operations/<id>` and
  `GET /procedures/<id>`. Some log lines include the unredacted operation
  payload and login steps.
- Any authenticated caller can use the `/phones/<id>/...` relays for any
  instance; leases are not checked there.

### Golden images

`prepare-golden-image.sh` stops services and clears logs, caches, cloud-init
state and shell history. It does **not** wipe the Redroid `/data` volume. A
Google account signed in on the source phone, app data and cookies are copied
into every instance launched from the image. So is
`/etc/default/redroid-cloud-phone`, including `API_TOKEN` if you set it.

## Recommended setup

1. Set `API_TOKEN` on every phone host and `ORCH_API_TOKEN` on the
   orchestrator. Use long random values, e.g.
   `python3 -c 'import secrets; print(secrets.token_hex(32))'`.
2. Set `ORCH_CONTROL_API_TOKEN` to the phones' `API_TOKEN`, and keep
   `ORCH_API_TOKEN` different from it.
3. In the OCI security list (or NSG) and any host firewall, allow 22, 8080,
   8090, 5555, 1935 and 8443 only from addresses you trust. Prefer closing
   5555 entirely and using `adb` over an SSH tunnel.
4. Do not enable `ORCH_ENABLE_CONSOLE` on a shared orchestrator.
5. Sign out of accounts and clear app data on a source phone before capturing
   a golden image, unless every instance is meant to share that account.
6. Never commit tokens, `.env`, SSH keys, OCI credentials or GApps zips.
   `.gitignore` covers the common file names.

## Reporting a vulnerability

> **OWNER-TODO:** add a private contact for security reports (an email
> address, or enable GitHub private vulnerability reporting for this
> repository) and state how quickly you aim to respond.

Until then, please do not open a public issue with exploit details. Open an
issue that says you have a security report and ask the maintainer for a
private channel.
