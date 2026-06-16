# Agent Coordination

Agent-to-agent coordination is handled by a separate service:
`Inter-Agent Communication Bus (IAC Bus)` at
`https://github.com/lehelkovach/iac-bus`.

See that repository for bus auth, endpoints, and deployment details.
The orchestrator here only manages and routes to cloud phone instances.

For OpenClaw/mobile-agent skill deployment, tool definitions, and runtime
verification steps, see `docs/OPENCLAW_SKILL.md`.

## Clean environment setup

For cloud agents running in ephemeral environments:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r api/requirements.txt -r orchestrator/requirements.txt
```

Run services:

```bash
./cloud-phone api-run
./cloud-phone orchestrator-run
```

## Orchestrator Auth

Set a token and include it in requests:

```bash
export ORCH_API_TOKEN="replace-me"
```

Each client uses:

```bash
Authorization: Bearer <ORCH_API_TOKEN>
```

## Phone Routing

Interact with a specific phone by ID via orchestrator routing:

```bash
# Status
curl http://<ORCH_IP>:8090/phones/<ID>/status \
  -H "Authorization: Bearer $ORCH_API_TOKEN"

# Input
curl -X POST http://<ORCH_IP>:8090/phones/<ID>/input \
  -H "Authorization: Bearer $ORCH_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"type":"tap","x":540,"y":1200}'

# Screenshot (base64)
curl http://<ORCH_IP>:8090/phones/<ID>/screenshot \
  -H "Authorization: Bearer $ORCH_API_TOKEN"
```

## Per-instance launch config

When provisioning, pass a launch config so the new phone configures itself at boot
(proxy, device identity, fire-and-forget startup tasks, free-form labels/extra):

```bash
curl -X POST http://<ORCH_IP>:8090/instances \
  -H "Authorization: Bearer $ORCH_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"launch_config": {
        "proxy": {"enabled": true, "type": "socks5", "host": "10.0.0.9", "port": 1080},
        "startup_tasks": [{"type": "adb_shell", "payload": {"command": "settings put system screen_off_timeout 600000"}}],
        "labels": {"role": "dev"}
      }}'
```

In OCI mode the config is delivered via cloud-init (`deploy-from-golden.sh --user-data-file`)
and applied by the Control API at boot (`/etc/cloud-phone/launch.json`, or `POST /launch-config/apply`).
See `config/launch-config.example.json` and `orchestrator/launch_config.py`.

## Async fan-out to several phones

Drive many phones concurrently with one call, then poll aggregate status:

```bash
# Dispatch to all registered phones (or pass {"instance_ids": [...]})
curl -X POST http://<ORCH_IP>:8090/fleet/operations \
  -H "Authorization: Bearer $ORCH_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"operation": "login", "app_package": "com.example.app",
       "login": {"username": "u", "password": "p"}}'

# Poll per-instance status
curl http://<ORCH_IP>:8090/fleet/operations/<FLEET_ID> \
  -H "Authorization: Bearer $ORCH_API_TOKEN"
```
