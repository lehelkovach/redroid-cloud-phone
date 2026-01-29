# Agent Coordination

Agent-to-agent coordination is handled by a separate service:
`Inter-Agent Communication Bus (IAC Bus)` at
`https://github.com/lehelkovach/iac-bus`.

See that repository for bus auth, endpoints, and deployment details.
The orchestrator here only manages and routes to cloud phone instances.

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
