# OpenClaw Cloud Android Skill

This directory contains reusable skill artifacts for exposing a deployed
Cuttlefish cloud phone to OpenClaw-style agents.

Files:

- `cloud_android_phone.yaml` - declarative tool manifest.
- `cloud_android_phone.py` - standard-library Python adapter and CLI.

See `../../docs/OPENCLAW_SKILL.md` for the full deployment guide.

## Direct CLI smoke test

```bash
python3 skills/openclaw/cloud_android_phone.py \
  --base-url http://<PHONE_IP>:8080 \
  --token "$API_TOKEN" \
  health
```

Tap:

```bash
python3 skills/openclaw/cloud_android_phone.py \
  --base-url http://<PHONE_IP>:8080 \
  --token "$API_TOKEN" \
  tap --x 540 --y 1200
```

## Import from an agent runtime

```python
from skills.openclaw.cloud_android_phone import CloudAndroidPhone

phone = CloudAndroidPhone("http://1.2.3.4:8080", token="...")
screen = phone.screenshot()
phone.tap(540, 1200)
```

## Runtime notes

- Use the Control API base URL for one phone: `http://<PHONE_IP>:8080`.
- Use the Orchestrator API for leases/fleet routing: `http://<ORCH_IP>:8090`.
- Keep `android_shell` restricted to trusted agents.
- Require human approval before sensitive external actions.

