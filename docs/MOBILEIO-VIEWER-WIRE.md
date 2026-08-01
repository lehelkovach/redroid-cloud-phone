# MobileIO viewer wire (app ↔ Redroid)

Pairs with `osl-oc-agent` Watch (`CloudPhoneWatchBackend`).

## Live topology (dogfood)

| Host | Role |
|------|------|
| `129.146.55.133` | Redroid + Control API `:8080` (ADB `127.0.0.1:5555`) |
| `129.146.105.26` | Orchestrator `:8090` (mock mode) |
| `129.153.118.145` | knowshowgo.com agent (`oc-agent`) |

## Screenshot JSON

`GET /device/screenshot/base64` returns **both**:

- `image_base64` (repo / agent clients)
- `image` (older deploys)

Raw PNG: `GET /device/screenshot`.

## Auth + firewall

- Set `API_TOKEN` on `control-api.service` (Bearer).
- Allowlist app VM only:  
  `iptables -I INPUT 1 -s 129.153.118.145 -p tcp --dport 8080 -j ACCEPT`
- Agent env: `CLOUD_PHONE_API_URL=http://129.146.55.133:8080` + `CLOUD_PHONE_API_TOKEN`.
- Do **not** leave `CLOUD_PHONE_WATCH=1` on forever — Watch flips to mobile on first `mobile.*` tool.

## GApps / Play

Post-scrub image has **no** Play/GMS; `/opt/gapps/gapps.zip` was empty. Restore a real
arm64 Android 11 GApps package before Play login / Tinder install.

## Mock Tinder

See `fixtures/mock-tinder/README.md` (HTTP + `org.chromium.webview_shell`).
