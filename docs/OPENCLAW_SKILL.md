# OpenClaw Skill Deployment Guide

This guide turns a deployed Cuttlefish cloud phone into an OpenClaw-usable
mobile device skill. The skill gives an agent controlled access to a cloud
Android session through the Control API and, optionally, an RTMP camera/audio
feed through OBS or another publisher.

Use this for authorized QA, research, livestream operations, app automation,
and owned-account workflows. Do not use it for fraud, impersonation without
consent, spam, fake engagement, or bypassing verification systems.

## Architecture

```text
OpenClaw agent
  -> OpenClaw skill adapter
  -> Control API :8080 or Orchestrator :8090
  -> ADB
  -> Cuttlefish Android device

OBS / ffmpeg / media generator
  -> nginx-rtmp :1935
  -> cuttlefish-rtmp-bridge
  -> front/back camera sinks + mic sink
```

Use the **Control API** when a skill talks to one phone directly. Use the
**Orchestrator** when a skill needs leasing, routing, or fleet operations across
multiple phones.

## 1. Deploy a Cuttlefish phone

Set OCI and SSH environment values:

```bash
cp .env.example .env
export COMPARTMENT_ID="ocid1.compartment..."
export SUBNET_ID="ocid1.subnet..."
export AVAILABILITY_DOMAIN="ABxx:REGION-AD-1"
export SSH_KEY_FILE="$HOME/.ssh/android_arm_cloud_phone_oci.pub"
```

Deploy a fresh host:

```bash
./cloud-phone deploy --name cuttlefish-source --ocpus 4 --memory 24
```

Recommended baseline is 4 OCPU and 24 GB RAM with KVM available on the target
host. Local Cursor/CI VMs usually cannot run Cuttlefish unless they expose
`/dev/kvm` and have the Cuttlefish host tools installed.

## 2. Verify runtime and ingest

Run the full release gate:

```bash
./cloud-phone verify-ingest --vm <OCI_PUBLIC_IP>
```

This runs:

```bash
./scripts/cuttlefish-phase1-validate.sh --vm <OCI_PUBLIC_IP>
./scripts/test-cuttlefish-rtmp-bridge.sh --vm <OCI_PUBLIC_IP>
```

Expected pass conditions:

- `cvd` and `adb` are available on the host.
- Android has booted.
- WebRTC port is listening.
- Camera service reports devices.
- nginx-rtmp health responds on `127.0.0.1:8081/health`.
- Synthetic RTMP input produces front/back video sinks and mic audio sink.

## 3. OBS / RTMP camera input

In OBS, configure:

- Server: `rtmp://<OCI_PUBLIC_IP>/live`
- Stream key: `cam`

The bridge consumes:

```text
rtmp://127.0.0.1/live/cam
```

For non-OBS tests, publish a repeating generated stream:

```bash
ffmpeg -re \
  -f lavfi -i testsrc2=size=1280x720:rate=30 \
  -f lavfi -i sine=frequency=880:sample_rate=44100 \
  -c:v libx264 -preset ultrafast -tune zerolatency \
  -pix_fmt yuv420p -g 60 -keyint_min 60 \
  -c:a aac -ar 44100 -b:a 128k \
  -f flv rtmp://<OCI_PUBLIC_IP>/live/cam
```

On the Cuttlefish host, the installed systemd stack is:

```bash
sudo systemctl status cuttlefish-cloud-phone.target
sudo systemctl status nginx-rtmp.service
sudo systemctl status cuttlefish-rtmp-bridge.service
sudo systemctl status control-api.service
```

## 4. Direct Control API skill

The Control API runs on port `8080` on each phone host.

Set an API token in production:

```bash
export API_TOKEN="replace-me"
```

Requests include:

```text
Authorization: Bearer <API_TOKEN>
```

### Minimal health check

```bash
curl http://<PHONE_IP>:8080/health
```

### Core tool endpoints

| Skill action | HTTP call | Body |
| --- | --- | --- |
| Health | `GET /health` | none |
| Status | `GET /status` | none |
| Screenshot | `GET /device/screenshot/base64` | none |
| Tap | `POST /device/input` | `{"type":"tap","x":540,"y":1200}` |
| Swipe | `POST /device/input` | `{"type":"swipe","x1":540,"y1":1800,"x2":540,"y2":600,"duration":300}` |
| Type text | `POST /device/input` | `{"type":"text","text":"hello"}` |
| Key | `POST /device/input` | `{"type":"key","keycode":4}` |
| Start app | `POST /apps/<package>/start` | none |
| Stop app | `POST /apps/<package>/stop` | none |
| List apps | `GET /apps` | none |
| Shell | `POST /adb/shell` | `{"command":"getprop ro.build.version.release","timeout":30}` |
| Async job | `POST /jobs` | `{"type":"screenshot","payload":{}}` |
| Poll job | `GET /jobs/<job_id>` | none |

Common keycodes:

- Back: `4`
- Home: `3`
- Enter: `66`
- Tab: `61`

## 5. OpenClaw skill manifest example

Ready-to-use artifacts are included under `skills/openclaw/`:

- `skills/openclaw/cloud_android_phone.yaml`
- `skills/openclaw/cloud_android_phone.py`
- `skills/openclaw/README.md`

Adapt the field names to your OpenClaw runtime if it uses a different manifest
schema. The important part is that each tool maps to a deterministic HTTP call
with bounded timeouts.

```yaml
name: cloud_android_phone
description: Authorized cloud Android device control through the Cuttlefish Control API.
config:
  base_url:
    type: string
    description: "Control API base URL, e.g. http://1.2.3.4:8080"
  token:
    type: string
    secret: true
    required: false
tools:
  - name: android_health
    description: Check Control API and ADB/device health.
    method: GET
    path: /health
    timeout_seconds: 10

  - name: android_screenshot
    description: Return the current screen as a base64 PNG.
    method: GET
    path: /device/screenshot/base64
    timeout_seconds: 30

  - name: android_tap
    description: Tap absolute screen coordinates.
    method: POST
    path: /device/input
    timeout_seconds: 10
    body:
      type: tap
      x: "{{x}}"
      y: "{{y}}"
    parameters:
      x: {type: integer, minimum: 0}
      y: {type: integer, minimum: 0}

  - name: android_swipe
    description: Swipe from one absolute coordinate to another.
    method: POST
    path: /device/input
    timeout_seconds: 10
    body:
      type: swipe
      x1: "{{x1}}"
      y1: "{{y1}}"
      x2: "{{x2}}"
      y2: "{{y2}}"
      duration: "{{duration}}"
    parameters:
      x1: {type: integer, minimum: 0}
      y1: {type: integer, minimum: 0}
      x2: {type: integer, minimum: 0}
      y2: {type: integer, minimum: 0}
      duration: {type: integer, default: 300, minimum: 1}

  - name: android_type_text
    description: Type text into the focused field.
    method: POST
    path: /device/input
    timeout_seconds: 15
    body:
      type: text
      text: "{{text}}"
    parameters:
      text: {type: string}

  - name: android_key
    description: Send an Android keycode.
    method: POST
    path: /device/input
    timeout_seconds: 10
    body:
      type: key
      keycode: "{{keycode}}"
    parameters:
      keycode: {type: integer}

  - name: android_start_app
    description: Launch an installed Android package.
    method: POST
    path: /apps/{{package}}/start
    timeout_seconds: 20
    parameters:
      package: {type: string}

  - name: android_shell
    description: Run an ADB shell command. Restrict this tool for trusted agents only.
    method: POST
    path: /adb/shell
    timeout_seconds: 30
    body:
      command: "{{command}}"
      timeout: "{{timeout}}"
    parameters:
      command: {type: string}
      timeout: {type: integer, default: 30, minimum: 1, maximum: 120}
```

## 6. Minimal Python HTTP adapter

If OpenClaw expects callable Python functions, wrap the HTTP API:

```python
import requests


class CloudAndroidPhone:
    def __init__(self, base_url, token=None, timeout=30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.headers = {"Content-Type": "application/json"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def _request(self, method, path, **kwargs):
        resp = requests.request(
            method,
            f"{self.base_url}{path}",
            headers=self.headers,
            timeout=self.timeout,
            **kwargs,
        )
        resp.raise_for_status()
        return resp.json()

    def health(self):
        return self._request("GET", "/health")

    def screenshot(self):
        return self._request("GET", "/device/screenshot/base64")

    def tap(self, x, y):
        return self._request("POST", "/device/input", json={"type": "tap", "x": x, "y": y})

    def type_text(self, text):
        return self._request("POST", "/device/input", json={"type": "text", "text": text})

    def key(self, keycode):
        return self._request("POST", "/device/input", json={"type": "key", "keycode": keycode})

    def start_app(self, package):
        return self._request("POST", f"/apps/{package}/start")
```

## 7. Fleet/orchestrator skill

The orchestrator runs on port `8090` and routes to registered phone instances.
It supports mock mode for local tests and OCI mode for golden-image deployment.

Start locally for development:

```bash
export ORCH_DEPLOY_MODE=mock
export ORCH_MOCK_API_URL=http://127.0.0.1:8080
export ORCH_API_TOKEN="replace-me"
./cloud-phone orchestrator-run
```

Useful endpoints:

| Skill action | HTTP call |
| --- | --- |
| Orchestrator health | `GET /health` |
| Create/register instance | `POST /instances` |
| List instances | `GET /instances` |
| Lease instance | `POST /instances/<id>/lease` |
| Release lease | `DELETE /instances/<id>/lease` |
| Phone health | `GET /phones/<id>/health` |
| Phone status | `GET /phones/<id>/status` |
| Phone screenshot | `GET /phones/<id>/screenshot` |
| Phone input | `POST /phones/<id>/input` |
| Phone async job | `POST /phones/<id>/jobs` |

Example lease + tap flow:

```bash
ORCH=http://<ORCH_IP>:8090
TOKEN=replace-me

curl -s "$ORCH/instances" -H "Authorization: Bearer $TOKEN"

curl -X POST "$ORCH/instances/<ID>/lease" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"owner":"openclaw-agent-1","ttl_seconds":900}'

curl -X POST "$ORCH/phones/<ID>/input" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"type":"tap","x":540,"y":1200}'
```

## 8. Agent operating loop

A reliable OpenClaw loop should follow this pattern:

1. Check `/health`.
2. Take `/device/screenshot/base64`.
3. Reason over the screenshot and task state.
4. Execute one bounded action (`tap`, `swipe`, `text`, `key`, or `start_app`).
5. Wait briefly for UI settling.
6. Take another screenshot.
7. Stop or ask for human approval before sensitive actions.

Use async jobs for long-running operations and poll `/jobs/<job_id>`.

## 9. Security and policy controls

For production deployments:

- Require `API_TOKEN` and `ORCH_API_TOKEN`.
- Prefer orchestrator leases for multi-agent access.
- Keep `/adb/shell` restricted to trusted agents.
- Record screenshots or session artifacts for auditability.
- Require human approval before posting, messaging, payment, account changes,
  identity-sensitive camera use, or external communications.
- Use customer-owned accounts and authorized test environments.

## 10. Troubleshooting

| Symptom | Check |
| --- | --- |
| `/health` is degraded | `adb devices`, `sudo systemctl status cuttlefish-launch.service` |
| Screenshot fails | Android boot state and `ADB_CONNECT` value |
| OBS cannot connect | OCI security list/firewall for TCP `1935`; nginx status |
| RTMP test fails | `./scripts/test-cuttlefish-rtmp-bridge.sh --vm <IP> --keep-artifacts` |
| Camera not visible in app | Cuttlefish camera injector backend/sink mapping for that image |
| Multiple agents collide | Use orchestrator leases and shorter task timeouts |

