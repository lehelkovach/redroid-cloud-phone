# OSL Agent Integration Guide

This guide explains how to expose this Cloud Phone as a tool command for
`github.com/lehelkovach/osl-agent-prototype` once the device is verified.

## Recommended integration surface
Use the **Agent API** (`api/agent_api.py`) as the primary control surface.
It is designed for LLM tool use and provides consistent JSON responses.

- Default host: `0.0.0.0`
- Default port: `8081` (set by `agent-api.service`)
- Base URL (on host): `http://127.0.0.1:8081`

## Verification checklist (required before integration)
Run these checks to confirm the device is usable.

### 1) ADB connectivity
```
adb connect <INSTANCE_IP>:5555
adb shell getprop ro.build.version.release
adb shell wm size
```

### 2) VNC visual access
```
ssh -i ~/.ssh/waydroid_oci -L 5900:localhost:5900 ubuntu@<INSTANCE_IP> -N
vncviewer localhost:5900
```

### 3) Agent API health
```
ssh -i ~/.ssh/waydroid_oci -L 8081:localhost:8081 ubuntu@<INSTANCE_IP> -N
curl http://localhost:8081/health
curl http://localhost:8081/screen/info
```

### 4) Optional Appium validation
Appium is optional; use it only if you require WebDriver-style automation.
The simplest path is to run Appium on a separate controller machine and
connect via ADB to the Redroid device.

If you need Appium later, confirm:
- Appium server starts cleanly
- UiAutomator2 driver installs
- A basic session can take a screenshot

## Tool command wrapper (for osl-agent-prototype)
Use the provided CLI wrapper to make tool calls with JSON input:

- Script: `scripts/cloud-phone-tool.py`
- Env: `CLOUD_PHONE_API_URL=http://127.0.0.1:8081`

### Example: screenshot
```
echo '{"path":"/screen/screenshot","method":"GET"}' | \
  ./scripts/cloud-phone-tool.py
```

### Example: tap
```
echo '{"path":"/input/tap","method":"POST","data":{"x":540,"y":960}}' | \
  ./scripts/cloud-phone-tool.py
```

### Example: type text
```
echo '{"path":"/input/text","method":"POST","data":{"text":"hello"}}' | \
  ./scripts/cloud-phone-tool.py
```

## Integration notes
- Prefer Agent API for tool calls; it is more LLM-friendly than the legacy
  Control API.
- For UI debugging, VNC is the simplest visual option.
- If Appium is not required, skip it to keep the stack minimal.
