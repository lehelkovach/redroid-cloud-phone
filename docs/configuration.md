# Configuration

Everything is configured with environment variables. Nothing reads `.env`
automatically; export the variables in your shell, or on a deployed host put
them where systemd reads them (see [Deployed hosts](#deployed-hosts)).

Line references point at the code that reads each variable.

## Control API (`api/server.py`)

| Variable | Default | What it does | Read at |
|---|---|---|---|
| `API_TOKEN` | empty | Bearer token required on every endpoint except `/health`, `/logs`, `/appium/status` and `/vnc/status`. **Empty means no authentication.** | `api/server.py:50` |
| `API_HOST` | `127.0.0.1` | Listen address. The systemd units set `0.0.0.0`. | `api/server.py:1689` |
| `API_PORT` | `8080` | Listen port | `api/server.py:1690` |
| `API_DEBUG` | `false` | `true` runs Flask in debug mode | `api/server.py:1691` |
| `ADB_CONNECT` | `127.0.0.1:5555` | ADB serial of the phone. A request can override it with the `X-Cloud-Phone-Adb` header. | `api/server.py:49` |
| `UI_BACKEND` | `adb` | Default backend for `/ui/*` commands: `adb` or `appium` | `api/server.py:51` |
| `APPIUM_URL` | `http://127.0.0.1:4723` | Appium server URL. Appium support is not wired up; see [api/control-api.md](api/control-api.md#appium). | `api/server.py:52` |
| `CONFIG_FILE` | `/etc/cloud-phone/config.json` | JSON file behind `GET/POST /config` | `api/server.py:53` |
| `JOB_TTL_SECONDS` | `3600` | How long finished jobs are kept | `api/server.py:55` |
| `JOB_MAX` | `500` | Maximum jobs kept in memory | `api/server.py:56` |
| `PROFILES_DIR` | `/opt/cloud-phone/config/device-profiles` | Directory of `.prop` device profiles for `/device/identity` | `api/server.py:1297` |
| `CLOUD_PHONE_RUNTIME` | `unknown` | Label reported as `runtime` in `/health` and `/vnc/status` (the Redroid install sets `redroid`) | `api/server.py:613`, `api/viewport.py:49` |
| `VNC_PORT` | `5900` | Port reported by `/vnc/status`. No VNC server is started. | `api/viewport.py:29` |
| `VNC_WIDTH` | `REDROID_WIDTH` or `1280` | Screen width used when the real size cannot be read | `api/viewport.py:23` |
| `VNC_HEIGHT` | `REDROID_HEIGHT` or `720` | Screen height, same fallback | `api/viewport.py:24` |
| `VNC_FPS` | `REDROID_FPS` or `30` | Frame rate reported by `/vnc/status` | `api/viewport.py:33` |
| `APP_INSTALL_DIRS` | `/opt/cloud-phone/apks` | Colon-separated directories `POST /apps/install` may install from by `path` | `api/app_install.py` (`install_dirs`) |
| `APP_INSTALL_MAX_BYTES` | `1073741824` (1 GiB) | Largest download `POST /apps/install` accepts per URL | `api/server.py` |
| `APP_INSTALL_MAX_FILES` | `32` | Most files in one install (split set) | `api/app_install.py` |
| `APP_INSTALL_TIMEOUT` | `300` | Seconds `adb install` may take | `api/server.py` |
| `APP_INSTALL_ALLOW_PRIVATE` | unset | `1`/`true`/`yes` lets install URLs resolve to private, loopback or link-local addresses (e.g. an internal mirror) | `api/server.py` |
| `PLAY_INSTALL_TIMEOUT` | `600` | Default seconds `POST /apps/play/install` waits for Play to finish | `api/server.py` |

The systemd units also set `CLOUD_PHONE_CONFIG`, but the code reads
`CONFIG_FILE`, so that setting has no effect.

## Orchestrator (`orchestrator/server.py`)

| Variable | Default | What it does | Read at |
|---|---|---|---|
| `ORCH_HOST` | `127.0.0.1` | Listen address. Wildcard or public addresses are refused unless `ORCH_ALLOW_PUBLIC_BIND=1` | `orchestrator/server.py` `main()` |
| `ORCH_PORT` | `8090` | Listen port | `orchestrator/server.py` `main()` |
| `ORCH_API_TOKEN` | empty | Bearer token required on every endpoint except `/health` and `/relay/*`. **Empty means the process refuses to start** (unless loopback and `ORCH_ALLOW_NO_TOKEN=1`). | `orchestrator/server.py:76` |
| `ORCH_CONTROL_API_TOKEN` | `ORCH_API_TOKEN` | Token the orchestrator sends to phones' Control APIs. Must equal their `API_TOKEN`. | `orchestrator/server.py:81` |
| `ORCH_DEPLOY_MODE` | `mock` | `mock`: no machines, every instance uses `ORCH_MOCK_API_URL`. `redroid`: local containers via `redroid-up.sh`. `oci`: VMs from golden images. | `orchestrator/server.py:73` |
| `ORCH_MOCK_API_URL` | `http://127.0.0.1:8080` | Control API used by mock instances, and the default for `ORCH_REDROID_API_URL` | `orchestrator/server.py:74` |
| `ORCH_MOCK_CAMERA_API_URL` | empty | Control API for mock camera instances (falls back to `ORCH_MOCK_API_URL`) | `orchestrator/server.py:75` |
| `ORCH_REDROID_API_URL` | `ORCH_MOCK_API_URL` | Control API shared by all containers in `redroid` mode | `orchestrator/server.py:420` |
| `ORCH_REDROID_ADB_PORT_BASE` | `5555` | First host ADB port in `redroid` mode; each new container takes the next one | `orchestrator/server.py:103` |
| `ORCH_REDROID_UP_SCRIPT` | `scripts/redroid-up.sh` | Script used to start and stop containers | `orchestrator/server.py:99` |
| `ORCH_REDROID_DRY_RUN` | unset | `1`/`true`/`yes` adds `--dry-run` to `redroid-up.sh` | `orchestrator/server.py:413` |
| `ORCH_DEPLOY_SCRIPT` | `scripts/deploy-from-golden.sh` | Script used to launch VMs in `oci` mode | `orchestrator/server.py:95` |
| `REDROID_GOLDEN_IMAGE_ID` | `ORCH_REDROID_GOLDEN_IMAGE_ID`, then `GOLDEN_IMAGE_ID` | Golden image for automation (Redroid) VMs | `orchestrator/server.py:85`, `:388` |
| `CUTTLEFISH_GOLDEN_IMAGE_ID` | `ORCH_CUTTLEFISH_GOLDEN_IMAGE_ID`, then `GOLDEN_IMAGE_ID` | Golden image for camera (Cuttlefish) VMs | `orchestrator/server.py:88` |
| `GOLDEN_IMAGE_ID` | empty | Fallback for both of the above | `orchestrator/server.py:84` |
| `ORCH_MAX_INSTANCES` | `5` | Total instance limit | `orchestrator/server.py:92` |
| `ORCH_MAX_REDROID_INSTANCES` | `3` | Automation pool limit | `orchestrator/server.py:93` |
| `ORCH_MAX_CUTTLEFISH_INSTANCES` | `2` | Camera pool limit | `orchestrator/server.py:94` |
| `ORCH_INSTANCE_NAME_PREFIX` | `orchestrated-phone` | Prefix for container and VM names | `orchestrator/server.py:83` |
| `ORCH_API_TIMEOUT` | `30` | Seconds to wait for a Control API or driver call | `orchestrator/server.py:82` |
| `ORCH_OCI_PROFILE` | `DEFAULT` | `oci` CLI profile used to terminate VMs | `orchestrator/server.py:104` |
| `ORCH_OCI_CONFIG` | `~/.oci/config` | `oci` CLI config file used to terminate VMs | `orchestrator/server.py:105` |
| `ORCH_OCI_AUTH` | `security_token` | `oci` CLI `--auth` value used to terminate VMs | `orchestrator/server.py:106` |
| `ORCH_PHONE_PROVIDER` | unset | `oci`: every new automation (Redroid) phone is its own OCI Compute instance, launched through `orchestrator/providers/oci_compute.py` with the `ORCH_OCI_*` variables below, whatever `ORCH_DEPLOY_MODE` says. Unset: phones come from `ORCH_DEPLOY_MODE` as before. Does not combine with `ORCH_PHONE_DATA=per_user`. See [deploy-oci.md](deploy-oci.md#phones-as-oci-instances-orch_phone_provideroci) | `_provision_instance()`, `provider_config_refusal()` |
| `ORCH_OCI_IMAGE_ID` | empty, **required** with the provider | Custom image a phone boots from (made from the GApps phone host) | `providers/oci_compute.py` `config_from_env()` |
| `ORCH_OCI_SUBNET_ID` | empty, **required** | Subnet for the phone's one VNIC; no public IP is assigned | same |
| `ORCH_OCI_COMPARTMENT_ID` | empty, **required** | Compartment the phones are launched in and listed from | same |
| `ORCH_OCI_AVAILABILITY_DOMAIN` | first AD of the compartment | Availability domain to launch in | same |
| `ORCH_OCI_OCPUS` / `ORCH_OCI_MEMORY_GB` | `2` / `12` | Shape config of each `VM.Standard.A1.Flex` phone | same |
| `ORCH_OCI_MAX_INSTANCES` | `2` | Phones the provider may have alive at once; over it: `429 oci_cap`, no OCI call. `/pool` reports the tighter of this and `ORCH_MAX_REDROID_INSTANCES` | `OciComputeProvider._check_caps()` |
| `ORCH_OCI_MAX_LAUNCHES_PER_HOUR` | `4` | Launch attempts per rolling hour, failures included; over it: `429 oci_cap`, no OCI call | same |
| `ORCH_OCI_RUNNING_TIMEOUT` / `ORCH_OCI_HEALTH_TIMEOUT` | `300` / `300` | Seconds to wait for `RUNNING`, then for the phone's `/health`. Giving up terminates the instance and answers `504 oci_timeout` / `504 oci_unhealthy` | `_wait_running()`, `_wait_health()` |
| `ORCH_OCI_POLL_SECONDS` | `5` | Poll interval for both waits | same |
| `ORCH_OCI_ORPHAN_TTL` | `7200` | At start-up, a tagged phone the state DB does not know and older than this is terminated; a younger one is kept and counted | `reconcile()` |
| `ORCH_OCI_NAME_PREFIX` | `oslo-phone` | Display-name prefix: `<prefix>-<owner tag>-<n>` | `launch()` |
| `ORCH_OCI_PHONE_PORT` | `8080` | Control API port on the phone's private IP | same |
| `ORCH_OCI_PROVIDER_AUTH` | `auto` | `auto`: instance principal, else the `oci` config file (`ORCH_OCI_CONFIG`, `ORCH_OCI_PROFILE`). `instance_principal` or `config` forces one | `_signer_and_config()` |
| `ORCH_ALLOW_NO_TOKEN` | unset | `1` lets a loopback-only orchestrator start without `ORCH_API_TOKEN` (development) | `startup_refusal()` |
| `ORCH_ALLOW_PUBLIC_BIND` | unset | `1` allows a wildcard or public `ORCH_HOST` | `startup_refusal()` |
| `ORCH_STATE_DB` | empty (memory) | SQLite file for leases, sessions, tenancy and ticket digests; survives restarts | `init_state()` |
| `ORCH_PUBLIC_URL` | request host | Base used to build relay URLs returned to callers | `_relay_base()` |
| `ORCH_PROFILE_ROOT` | `/var/lib/cloud-phone-orchestrator/profiles` | Per-owner browser profiles, 0700 | `orchestrator/browser_pool.py` |
| `ORCH_PROFILE_ENCRYPTION` | `none` | `none`, `luks` or `fscrypt`: verify the profile root is encrypted before using it | `check_profile_encryption()` |
| `ORCH_PHONE_DATA` | `instance` | `per_user`: each owner's encrypted, size-limited volume is the phone's `/data` (needs `ORCH_DEPLOY_MODE=redroid`). See [isolation.md](isolation.md#per-owner-volumes-phone-data-and-browser-profiles) | `volume_config_refusal()`, `_acquire_user_session()` |
| `ORCH_BROWSER_PROFILE` | `dir` | `volume`: each owner's encrypted, size-limited volume is the Chrome profile | `_acquire_browser_session()` |
| `ORCH_VOLUME_ROOT` | `/var/lib/cloud-phone-orchestrator/volumes` | Where per-owner images live (`<owner token>/<kind>.img`, mounted at `<kind>.mnt`), 0700 | `orchestrator/user_volumes.py` |
| `ORCH_VOLUME_MASTER_KEY_FILE` | empty | File (0600, at least 32 bytes) holding the master from which each owner's LUKS key is derived. Required when either flag is on. Never put the key itself in the environment. | `load_master_key()` |
| `ORCH_QUOTA_PHONE_GB` / `ORCH_QUOTA_BROWSER_GB` | `8` / `1` | Default size (GiB) of a new phone-data / browser-profile volume | `VolumeManager.quota_bytes()` |
| `ORCH_VOLUME_QUOTA_FILE` | empty | JSON of per-owner overrides in GiB, keyed by owner id or owner token: `{"avery-example": {"phone-data": 16}}` | `VolumeManager.quota_bytes()` |
| `ORCH_HOST_MIN_FREE_GB` | `10` | New volumes are refused (`507 host_disk_low`) if they would leave less free space than this, counting every image as fully allocated | `check_host_capacity()` |
| `ORCH_VOLUME_SAMPLE_SECONDS` | `300` | How often open volumes are measured (80 %/95 % warnings) and lapsed leases reaped | `_volume_sampler()` |
| `ORCH_CHROME_RUNTIME` | `mock` in mock mode, else `docker` | `mock`, `local` or `docker` | `_default_chrome_runtime()` |
| `ORCH_CHROME_BIN` | empty | Chromium binary for the `local` runtime | `LocalChromeRuntime` |
| `ORCH_CHROME_IMAGE` | empty | Container image for the `docker` runtime | `DockerChromeRuntime` |
| `ORCH_CHROME_NETWORK` | `cloud-phone-browsers` | Docker network for browser containers | `DockerChromeRuntime` |
| `ORCH_CHROME_CDP_PORT_BASE` | `19222` | First internal DevTools port (loopback only) | `_free_cdp_port()` |
| `ORCH_MAX_CHROME_INSTANCES` | `4` | Browser pool limit | `_acquire_browser_session()` |
| `ORCH_BROWSER_SLOTS_PER_OWNER` | `1` | Browsers one owner may hold at once, one per `slot` (`POST /sessions` `slot`). `1` is the single `main` browser of before; OSLO's swarm needs at least `OSLO_SWARM_PARALLEL`. Over the cap: `429 slots_exhausted` | `_acquire_browser_session()` |
| `ORCH_CHROME_RELAY_BIND` | `127.0.0.1` | Bind address of per-lease authenticating proxy relays | `LeaseProxyRelay` |
| `ORCH_TICKET_TTL_SECONDS` | `300` | Session ticket lifetime (max 3600, never past the lease) | `orchestrator/relay.py` |
| `ORCH_RELAY_RATE` / `ORCH_RELAY_BURST` | `50` / `200` | Per-lease relay rate limit (commands per second, bucket size) | `RateLimiter` |
| `ORCH_REQUIRE_TICKETS` | unset | `1` turns off the unticketed `/phones/<id>/*` routes | `_auth_middleware()` |
| `ORCH_PHONE_FPS` | `4` | Frame rate of `/relay/phone/stream` | `relay_phone_stream()` |
| `ORCH_WEB_DRIVER_URL` | empty | Enables the `web` procedure surface; steps are POSTed to `<url>/web/step` | `orchestrator/server.py:915` |
| `ORCH_CHROME_BRIDGE_URL` | empty | Enables the `chrome` surface; steps are POSTed to `<url>/chrome/step` | `orchestrator/server.py:919` |
| `ORCH_ENABLE_CONSOLE` | unset | `1`/`true`/`yes` enables the `console` surface, which runs shell commands **on the orchestrator host** | `orchestrator/server.py:923` |
| `ORCH_LOG_LEVEL` | `LOG_LEVEL`, then `INFO` | Orchestrator log level | `orchestrator/server.py:62` |

In `oci` mode the orchestrator runs `deploy-from-golden.sh`, so the OCI
variables in the next section must be set in its environment too.

## Logging (Control API, orchestrator, shell scripts)

| Variable | Default | What it does | Read at |
|---|---|---|---|
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`/`WARN`, `ERROR` | `api/cloudphone_logging.py:74`, `scripts/lib/log.sh:12` |
| `CLOUD_PHONE_VERBOSE` | unset | `1`/`true`/`yes`/`on` forces DEBUG and logs every ADB command at INFO | `api/cloudphone_logging.py:63` |
| `LOG_FORMAT` | `text` | `text` or `json` (one object per line) | `api/cloudphone_logging.py:187`, `scripts/lib/log.sh:11` |
| `LOG_FILE` | empty | Also write to this file. An unwritable path logs a warning. | `api/cloudphone_logging.py:214` |
| `LOG_RING_SIZE` | `4000` | Lines kept in memory for `GET /logs` | `api/cloudphone_logging.py:52` |
| `LOG_TYPE` | `SYS` | Label used by shell scripts that source `scripts/lib/log.sh` | `scripts/lib/log.sh:10` |

Details: [logging.md](logging.md).

## Redroid container

Read by `scripts/redroid-up.sh` and `docker/redroid-compose.yml`.

| Variable | Default | What it does | Read at |
|---|---|---|---|
| `REDROID_NAME` | `redroid` | Container and compose project name (`--name`) | `scripts/redroid-up.sh:22` |
| `ADB_PORT` | `5555` | Host port mapped to the container's ADB port (`--adb-port`) | `scripts/redroid-up.sh:23` |
| `REDROID_IMAGE` | `redroid/redroid:11.0.0-latest` | Container image (`--image`) | `scripts/redroid-up.sh:24` |
| `REDROID_COMPOSE_FILE` | `docker/redroid-compose.yml` | Compose file | `scripts/redroid-up.sh:16` |
| `REDROID_WIDTH`, `REDROID_HEIGHT` | `1280`, `720` | Screen size | `docker/redroid-compose.yml:22-23` |
| `REDROID_FPS` | `30` | Frame rate | `docker/redroid-compose.yml:24` |
| `REDROID_DPI` | `240` | Screen density | `docker/redroid-compose.yml:25` |
| `GPU_MODE` | `guest` | Passed as `androidboot.redroid_gpu_mode` | `docker/redroid-compose.yml:21` |

## GApps phone (`docker/redroid-gapps-compose.yml`, `systemd/redroid-gapps-container.service`)

Off by default (compose profile `gapps`; the unit is installed, not enabled).
The screen and GPU variables above apply too.

| Variable | Default | What it does | Read at |
|---|---|---|---|
| `REDROID_GAPPS_IMAGE` | none (required) | Image built by `build-gapps-image.sh` | `docker/redroid-gapps-compose.yml` |
| `REDROID_NAME` | `redroid-gapps` | Container name | `docker/redroid-gapps-compose.yml` |
| `ADB_BIND` | `127.0.0.1` | Host address the ADB port binds to. The phone holds a signed-in Google account; keep it on loopback | `docker/redroid-gapps-compose.yml` |
| `ADB_PORT` | `5555` | Host ADB port (the same as the plain phone's, so run one or the other) | `docker/redroid-gapps-compose.yml` |
| `REDROID_USE_MEMFD` | `true` | `androidboot.use_memfd`; lets Redroid run on kernels without ashmem (5.18+) | `docker/redroid-gapps-compose.yml` |

## GApps image build (`scripts/build-gapps-image.sh`)

Flags win over these. Details: [GAPPS-AND-APP-INSTALL.md](GAPPS-AND-APP-INSTALL.md).

| Variable | Default | What it does |
|---|---|---|
| `GAPPS_ZIP` | empty | Zip path (`--zip`) |
| `GAPPS_ZIP_URL` | empty | HTTPS URL (`--zip-url`); needs a SHA-256 |
| `GAPPS_ZIP_SHA256` | empty | Expected SHA-256 (`--sha256`) |
| `REDROID_GAPPS_ANDROID` | `13.0.0` | Android version (`--android`) |
| `REDROID_GAPPS_ARCH` | this machine's | `arm64` or `amd64` (`--arch`) |
| `REDROID_GAPPS_BASE` | `redroid/redroid:<android>-latest` | Base image (`--base`) |
| `REDROID_GAPPS_TAG` | `cloud-phone/redroid-gapps:<android>-<arch>` | Output tag (`--tag`) |

## GApps installer (`scripts/install-gapps-redroid.sh`)

| Variable | Default | What it does | Read at |
|---|---|---|---|
| `GAPPS_ZIP` | empty | Path to the GApps zip | `:21` |
| `GAPPS_ZIP_URL` | empty | URL to download the zip from if the file is missing | `:22` |
| `GAPPS_DIR` | `/opt/gapps` | Directory of the fallback `gapps.zip` | `:20` |
| `GAPPS_MIN_BYTES` | `1` | Minimum zip size | `:27` |
| `ADB_CONNECT` | `127.0.0.1:5555` | ADB serial (`--adb`) | `:19` |
| `ADB_BIN` | `adb` | adb binary | `:23` |

## OCI deploy scripts

Read by `deploy-redroid-oci.sh`, `deploy-cuttlefish-oci.sh`,
`deploy-from-golden.sh`, `deploy-golden-fleet.sh` and `create-golden-image.sh`.

| Variable | Default | What it does | Read at |
|---|---|---|---|
| `COMPARTMENT_ID` | none | Compartment OCID (required to create instances or images) | `scripts/deploy-redroid-oci.sh:27` |
| `SUBNET_ID` | none | Subnet OCID (required to create instances) | `scripts/deploy-redroid-oci.sh:28` |
| `AVAILABILITY_DOMAIN` | none | Availability domain (required to create instances) | `scripts/deploy-redroid-oci.sh:29` |
| `SSH_KEY_FILE` | `~/.ssh/android_arm_cloud_phone_oci.pub` | Public key for new instances; the private key is the same path without `.pub`. `create-golden-image.sh` instead treats it as the private key (default `~/.ssh/android_arm_cloud_phone_oci`). | `scripts/deploy-redroid-oci.sh:19`, `scripts/create-golden-image.sh:10` |
| `SECURITY_TOKEN_FILE` | `~/.oci/sessions/DEFAULT/token` | If this file exists, `oci` calls use `--auth security_token` | `scripts/deploy-redroid-oci.sh:30` |
| `GOLDEN_IMAGE_ID` | empty | Image for `deploy-golden` / `deploy-fleet` (`--image-id` wins) | `scripts/deploy-from-golden.sh:11` |
| `REDROID_GOLDEN_IMAGE_ID` | empty | Used for `--platform redroid` when no image is given | `scripts/deploy-from-golden.sh:83` |
| `CUTTLEFISH_GOLDEN_IMAGE_ID` | `GOLDEN_IMAGE_ID` | Used for `--platform cuttlefish` when no image is given | `scripts/deploy-from-golden.sh:86` |
| `REDROID_NAME`, `ADB_PORT`, `REDROID_IMAGE` | as above | Defaults for `deploy-redroid` | `scripts/deploy-redroid-oci.sh:21-23` |
| `GAPPS_ZIP`, `GAPPS_ZIP_URL` | empty | Copied or passed to the host by `deploy-redroid` | `scripts/deploy-redroid-oci.sh` |

The SSH user is `ubuntu` unless you pass `--ssh-user`; the `SSH_USER`
variable in `.env.example` is not read by the deploy scripts.
`OCI_PROFILE` in `.env.example` is not read by any script.

## Verification scripts

| Variable | Default | What it does | Read at |
|---|---|---|---|
| `API_PORT` | `8080` | Control API port checked by `verify-redroid-phone.sh` | `scripts/verify-redroid-phone.sh:13` |
| `REQUIRE_GAPPS` | `false` | Same as `--require-gapps` | `scripts/verify-redroid-phone.sh:14` |
| `VM_HOST`, `DEV_INSTANCE` | empty | Default host for the Cuttlefish validate, setup and bridge-test scripts | `scripts/cuttlefish-phase1-validate.sh:32` |
| `SSH_KEY` | `~/.ssh/android_arm_cloud_phone_oci` | Private key for `verify-ingest` | `scripts/verify-cuttlefish-ingest.sh:30` |

## Tests

| Variable | Default | What it does | Read at |
|---|---|---|---|
| `PYTHON` | `python3` | Interpreter used by `run-tests.sh` | `scripts/run-tests.sh:104` |
| `COVERAGE_FAIL_UNDER` | `80` | Coverage floor for `--coverage` (CI passes `--fail-under 60`) | `scripts/run-tests.sh:44` |
| `TEST_REPORT_DIR` | `.test-reports` | Where suite logs and coverage reports go | `scripts/run-tests.sh:49` |
| `CLOUD_PHONE_API_URL` | `http://127.0.0.1:8080` | Control API for live suites | `scripts/run-tests.sh:47`, `tests/test_live.py:10` |
| `CLOUD_PHONE_LIVE` | unset | `1` enables `tests/test_live.py` | `tests/test_live.py:9` |
| `REQUIRE_GAPPS` | unset | `1` makes the live suite require Play packages | `tests/test_live.py:22` |
| `CLOUD_PHONE_REAL_DEVICE` | unset | `1` enables the live class in `tests/test_real_app_smoke.py` (drives three real apps) | `tests/test_real_app_smoke.py` |
| `CLOUD_PHONE_API_TOKEN` | unset | Bearer token for `scripts/real-app-smoke.py` (falls back to `API_TOKEN`; `--token-file` wins) | `scripts/real-app-smoke.py` |
| `PUBLIC_IP`, `SSH_USER`, `SSH_KEY` | empty, `ubuntu`, empty | Target for `tests/test_connectivity.py` | `tests/test_connectivity.py:19-21` |

## Deployed hosts

| Host | File | Read by |
|---|---|---|
| Redroid | `/etc/default/redroid-cloud-phone` (written by the installer: `REDROID_NAME`, `ADB_PORT`, `REDROID_IMAGE`, `REDROID_COMPOSE_FILE`, `GAPPS_ZIP`, `CLOUD_PHONE_RUNTIME`) | `redroid-container.service`, `control-api-redroid.service` |
| Cuttlefish | `/etc/default/cuttlefish-cloud-phone` (`CF_INSTANCE_NAME`, `CF_WEBRTC_PORT`, `RTMP_URL`, `FRONT_SINK_URI`, `BACK_SINK_URI`, `MIC_SINK_URI`) | `cuttlefish-launch.service`, `cuttlefish-rtmp-bridge.service` |
| Cuttlefish | none; use `systemctl edit control-api.service` | `control-api.service` |

Add `API_TOKEN=...` to `/etc/default/redroid-cloud-phone` on Redroid hosts.
`control-api-redroid.service` sets `ADB_CONNECT=127.0.0.1:5555` itself, so if
you change `ADB_PORT`, also add `ADB_CONNECT=127.0.0.1:<port>` to the same file
(values from the `EnvironmentFile` override the unit's `Environment=` lines).
