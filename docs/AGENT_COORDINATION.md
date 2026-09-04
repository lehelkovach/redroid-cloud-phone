# Agent Coordination

Agent-to-agent coordination is handled by a separate service:
`Inter-Agent Communication Bus (IAC Bus)` at
`https://github.com/lehelkovach/iac-bus`.

See that repository for bus auth, endpoints, and deployment details.
The orchestrator here manages Redroid GApps phones and Cuttlefish ingest VMs
(see `docs/RUNTIME-SPLIT.md`). Playwright-like acquire:

```bash
curl -X POST http://<ORCH_IP>:8090/sessions \
  -H "Authorization: Bearer $ORCH_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"owner_user_id":"alice","purpose":"play","ttl_seconds":3600,"provision":true}'
```

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
