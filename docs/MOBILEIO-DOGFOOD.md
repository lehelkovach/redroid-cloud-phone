# mobileio + Redroid — Oracle VM dogfood checklist

For tomorrow morning: validate **one phone per user**, **mobileio** (incl. swipe),
**mock Tinder** with fake credentials, and **IPRoyal** proxy plumbing.

Agent-side twin: `osl-oc-agent/docs/MOBILEIO-DOGFOOD.md`.

## Live instance pointers

See `docs/AGENT-MOBILE-ACCESS.md` and `docs/OCI-LIVE-TOPOLOGY.md`.

| Role | Host | Tunnel |
|------|------|--------|
| Phone + Control API | `129.146.55.133` (`10.0.1.127`) | `-L 18080:127.0.0.1:8080` |
| Orchestrator | `129.146.105.26` | `-L 18090:127.0.0.1:8090` |

Prefer these over older `cloud-phone-dev` (`129.146.70.170`, different SSH key).

## Offline (this Cloud VM / laptop)

```bash
source .venv/bin/activate   # or python3 -m venv .venv && pip install -r api/requirements.txt -r orchestrator/requirements.txt
python -m unittest tests.test_user_sessions tests.test_proxy_url -v
PROXY_STUB=1 ./scripts/proxy-control.sh enable http geo.iproyal.com 12321 user pass
```

Control API / orchestrator start without `adb` (device endpoints degrade — expected).

## Morning dogfood on Oracle VM

### 0. Binder + Redroid up

```bash
ssh -i ~/.ssh/oci_console ubuntu@<PHONE_IP>
sudo modprobe binder_linux devices="binder,hwbinder,vndbinder"
sudo modprobe ashmem_linux
sudo mkdir -p /dev/binderfs && sudo mount -t binder binder /dev/binderfs || true
sudo docker restart redroid
adb connect 127.0.0.1:5555
adb devices   # expect "device" — if "offline", remount binderfs + restart redroid
```

Overnight probe (`129.146.55.133`): Control API `/health` reported
`adb_connected:true` but `adb devices` showed `offline` — fix binder before
gesture smoke.

### 1. Tunnel APIs

```bash
# laptop / Cursor agent
ssh -i ~/.ssh/oci_console -N \
  -L 18080:127.0.0.1:8080 \
  -L 18090:127.0.0.1:8090 \
  ubuntu@<PHONE_IP>

export CLOUD_PHONE_API_URL=http://127.0.0.1:18080
export CLOUD_PHONE_ORCH_URL=http://127.0.0.1:18090
curl -sS $CLOUD_PHONE_API_URL/health
curl -sS $CLOUD_PHONE_ORCH_URL/health
```

### 2. One-phone-per-user session

```bash
# Register the phone once (if not auto-registered)
curl -sS -X POST $CLOUD_PHONE_ORCH_URL/instances \
  -H 'content-type: application/json' \
  -d "{\"api_url\":\"http://127.0.0.1:8080\",\"name\":\"dogfood-phone\"}"

curl -sS -X POST $CLOUD_PHONE_ORCH_URL/sessions \
  -H 'content-type: application/json' \
  -d '{"owner_user_id":"dogfood-user-1","ttl_seconds":3600}'

# Same user → renew; second user while sole phone leased → 409
curl -sS -X POST $CLOUD_PHONE_ORCH_URL/sessions \
  -H 'content-type: application/json' \
  -d '{"owner_user_id":"dogfood-user-2","ttl_seconds":3600}'
```

### 3. mobileio primitives (from osl-oc-agent)

```bash
cd ../osl-oc-agent
CLOUD_PHONE_API_URL=http://127.0.0.1:18080 node scripts/mobile_phone_smoke.mjs
```

### 4. IPRoyal proxy (founder allocates)

```bash
# On phone VM or via tunneled API — do NOT commit real credentials
export IPROYAL_PROXY='http://USER:PASS@geo.iproyal.com:12321'

curl -sS -X POST $CLOUD_PHONE_API_URL/proxy \
  -H 'content-type: application/json' \
  -d "{\"enabled\":true,\"url\":\"$IPROYAL_PROXY\"}"

# Or let the API read IPROYAL_PROXY from the environment:
curl -sS -X POST $CLOUD_PHONE_API_URL/proxy \
  -H 'content-type: application/json' \
  -d '{"enabled":true}'
```

Agent: `mobile.configure_proxy {}` picks `IPROYAL_PROXY` / `CLOUD_PHONE_PROXY`.

**Note:** Android `http_proxy` does not carry user/pass. For authenticated IPRoyal
HTTP, run a local forwarder on the VM (or SOCKS path via `proxy-control.sh`) —
the URL parse + `/proxy` hooks are ready; wire the forwarder when credentials land.

### 5. Mock Tinder

```bash
# On phone VM
adb push fixtures/mock-tinder/index.html /sdcard/Download/mock-tinder.html
adb shell 'am start -a android.intent.action.VIEW -d "file:///sdcard/Download/mock-tinder.html" -p org.mozilla.firefox'

# Drive with fake creds from agent repo
cd ../osl-oc-agent
node scripts/mobile_mock_tinder_demo.mjs --live   # needs API URL; or offline without --live
```

Fake login: `bs@example.com` / `fake-password-not-real`.

### 6. Real Tinder (optional)

If Play allows `com.tinder` install: launch → screenshot → tap/type/swipe →
**stop at SMS/CAPTCHA** (`user.ask`). Never automate with real user secrets in demos.

### 7. Travel / lodging ladder (Play → app)

Prefer the agent-side offline mock when adbd is flaky:

```bash
cd ../osl-oc-agent
node scripts/mobile_travel_ladder_demo.mjs
```

Live (phone up + tunnel):

1. `node scripts/mobile_phone_smoke.mjs` — require **Play Store launch** (`com.android.vending`).
2. In Play, search **Airbnb / VRBO / HomeToGo / Booking**.
3. Install/Open → Google Sign-In as `lehelsaiagent@gmail.com`.
4. **STOP** at SMS / CAPTCHA / payment. Never complete a paid booking unattended.
5. Airbnb **web** is out of scope for WebIO; mobile install exploration only.

Do not commit Google passwords or proxy URLs with credentials.

### Lab probe (2026-08-09)

| Check | Result |
|-------|--------|
| SSH phone `129.146.55.133` | OK |
| Orch `129.146.105.26:8090` | OK |
| Control API `/health` after fixing `ADB_CONNECT=127.0.0.1:5555` | healthy |
| Play Store `POST /apps/com.android.vending/start` | success |
| Screenshot / stable `adb devices` | **flaky** (device-not-found after connect) |
| Full gesture dogfood | **blocked on phone lab** until adbd stays `device` |

Gotcha: `/etc/systemd/system/control-api.service.d/serial.conf` must use
`ADB_CONNECT=127.0.0.1:5555` (host publish). `172.17.0.2:5554` is the container
port and routinely leaves the API degraded.

## Env cheat sheet

| Variable | Where | Purpose |
|----------|--------|---------|
| `CLOUD_PHONE_API_URL` | agent | Control API |
| `CLOUD_PHONE_ORCH_URL` | agent | Sessions |
| `IPROYAL_PROXY` / `CLOUD_PHONE_PROXY` | agent + API VM | Egress URL |
| `PROXY_STUB=1` | API host | Offline proxy-control.sh |
| `ORCH_DEPLOY_MODE=mock` | local orch | No OCI provision |
| `ORCH_MAX_INSTANCES` | orch | Cap fleet size (1 = single shared phone) |
