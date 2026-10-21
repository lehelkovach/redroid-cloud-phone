# Deploy on Oracle Cloud (OCI)

The deploy scripts create (or reuse) an Ampere ARM64 instance, copy this repo
to it over SSH, and install a phone stack with systemd units. This page covers
the Redroid automation host, golden images, fleets and running the
orchestrator against OCI. The Cuttlefish ingest host is in
[cuttlefish-ingest.md](cuttlefish-ingest.md).

## Before you start

On the machine you deploy from:

- The [`oci` CLI](https://docs.oracle.com/en-us/iaas/Content/API/SDKDocs/cliinstall.htm),
  configured for your tenancy. If `~/.oci/sessions/DEFAULT/token` exists
  (override with `SECURITY_TOKEN_FILE`), the scripts add
  `--auth security_token` to their `oci` calls.
- `ssh`, `scp`, `tar` and `python3`.
- An SSH key pair. The scripts default to
  `~/.ssh/android_arm_cloud_phone_oci.pub` and use the same path without
  `.pub` as the private key.

In OCI:

- A compartment, and a subnet that assigns public IPs. The scripts launch with
  `--assign-public-ip true`.
- Capacity for the `VM.Standard.A1.Flex` shape in your availability domain.
- Security list (or NSG) rules for the ports you need, opened **only to
  addresses you trust**:

| Port | Used by | Notes |
|---|---|---|
| TCP 22 | SSH | Needed by every script |
| TCP 8080 | Control API | Listens on `0.0.0.0` under systemd. Unauthenticated unless you set `API_TOKEN` |
| TCP 5555 | Redroid ADB | Published on all interfaces, no authentication |
| TCP 1935 | RTMP ingest (Cuttlefish) | Accepts a publish from any source |
| TCP 8443 | Cuttlefish WebRTC | |
| TCP+UDP 15550-15599 | Cuttlefish | |
| TCP 8090 | Orchestrator | Only if you run it on an OCI host |

Export the OCI settings (or put them in `.env` and `set -a; . ./.env; set +a`):

```bash
export COMPARTMENT_ID="ocid1.compartment.oc1..."
export SUBNET_ID="ocid1.subnet.oc1..."
export AVAILABILITY_DOMAIN="XXXX:REGION-AD-1"
export SSH_KEY_FILE="$HOME/.ssh/android_arm_cloud_phone_oci.pub"
```

If you do not pass `--image-id`, the scripts pick the newest Canonical Ubuntu
24.04 image for `VM.Standard.A1.Flex`, then fall back to 22.04.

## Redroid automation host

### What the host needs that the scripts do not install

Redroid needs binder support in the guest instance's kernel. The installer
does not check or configure it. After the instance is up, SSH in and run:

```bash
grep binder /proc/filesystems
```

If it prints nothing, enable binder for that kernel (see
[getting-started.md](getting-started.md#binder)) and then restart the stack with
`sudo systemctl restart redroid-cloud-phone.target`. Do this on the source
instance before you capture a golden image, so every copy has it.

### Deploy

```bash
GAPPS_ZIP=/path/to/gapps-arm64.zip \
  ./cloud-phone deploy-redroid --name redroid-source --ocpus 2 --memory 8
```

Useful flags (`./cloud-phone deploy-redroid --help`):

| Flag | Default | Meaning |
|---|---|---|
| `--to-instance IP` | | Reuse an existing instance instead of creating one |
| `--image-id OCID` | latest Ubuntu ARM64 | Launch image |
| `--ocpus N`, `--memory N` | 2, 8 | Shape size |
| `--adb-port PORT` | 5555 | Host ADB port |
| `--redroid-image IMAGE` | `redroid/redroid:11.0.0-latest` | Container image |
| `--skip-pull` | | Do not `docker pull` on the host |
| `--dry-run` | | Print the plan and exit |

`GAPPS_ZIP` is optional. If set, the zip is copied to `/opt/gapps/gapps.zip`
on the host. `GAPPS_ZIP_URL` makes the host download it instead. See
[gapps.md](gapps.md).

On the host, `scripts/install-redroid-cloud-phone.sh` (run as root):

1. Installs `docker.io`, the compose plugin, `android-tools-adb`, Python and a
   few tools with `apt-get`.
2. Copies the scripts to `/opt/cloud-phone-scripts/`, the compose file to
   `/opt/cloud-phone/docker/`, the Control API to `/opt/cloud-phone-api/`
   (with its own venv) and device profiles to
   `/opt/cloud-phone/config/device-profiles/`.
3. Installs and enables `redroid-container.service`,
   `control-api-redroid.service` and `redroid-cloud-phone.target`, and writes
   `/etc/default/redroid-cloud-phone`.
4. Pulls the image, starts the target, and installs GApps if a non-empty zip
   is present.

The script writes `/tmp/instance-<name>.json` on your machine with the public
IP and instance OCID.

### Set a Control API token

The installer does not set `API_TOKEN`, so the Control API on port 8080 starts
with no authentication. Set one right away:

```bash
ssh -i ~/.ssh/android_arm_cloud_phone_oci ubuntu@<HOST_IP>
echo "API_TOKEN=<long-random-string>" | sudo tee -a /etc/default/redroid-cloud-phone
sudo systemctl restart control-api-redroid.service
```

`control-api-redroid.service` reads `/etc/default/redroid-cloud-phone`, so the
token survives reboots and is baked into golden images made from this host.

### Check it

```bash
curl -s http://<HOST_IP>:8080/health -H "Authorization: Bearer <token>"
```

`./cloud-phone verify-phone --vm <HOST_IP> --require-gapps` checks `/health`
and the GApps packages, but it sends no token. Once `API_TOKEN` is set,
`/health` reports `status: "unauthorized"` to it and the check fails. Use the
`curl` above instead (listed in [ROADMAP.md](../ROADMAP.md)).

## Golden images

A golden image is an OCI custom image of a configured host. New phones boot
from it with everything already installed.

```bash
COMPARTMENT_ID=<ocid> SSH_KEY_FILE=~/.ssh/android_arm_cloud_phone_oci \
  ./cloud-phone create-golden <HOST_IP> cloud-phone-redroid-gapps-v1 redroid
```

`create-golden-image.sh` takes `<instance-ip> [image-name] [redroid|cuttlefish]`.
It runs `prepare-golden-image.sh` on the host over SSH (stops the services,
clears logs, apt cache, cloud-init state, `/tmp` and shell history), stops the
instance, creates the image, starts the instance again and writes
`/tmp/golden-image-info.json`.

Note that `create-golden` uses `SSH_KEY_FILE` as the **private** key (default
`~/.ssh/android_arm_cloud_phone_oci`), while the deploy scripts expect the
`.pub` file in the same variable. Set it as shown above for this command.

**Prepare does not wipe the phone.** The Redroid `/data` volume is kept, so
any Google account signed in on the source phone, app data and the
`API_TOKEN` in `/etc/default/redroid-cloud-phone` end up in every instance
launched from the image. Sign out and clear app data on the source phone first
if that is not what you want.

### Launch from a golden image

One instance:

```bash
./cloud-phone deploy-golden --platform redroid --image-id <image_ocid> \
  --name phone-1 --wait-check
```

A fleet:

```bash
REDROID_GOLDEN_IMAGE_ID=<image_ocid> \
  ./cloud-phone deploy-fleet --platform redroid --count 3 --parallel 2 --wait-check
```

Both default to `--platform redroid`. Always pass `--platform` explicitly:
with `GOLDEN_IMAGE_ID` set to a Cuttlefish image and no `--platform`, the fleet
script launches it as a Redroid host. Defaults are 2 OCPU / 8 GB for Redroid
and 4 OCPU / 24 GB for Cuttlefish. `deploy-golden --list-images` lists images
whose name starts with `cloud-phone-`. `deploy-fleet` writes
`/tmp/golden-fleet-<prefix>-<timestamp>.txt` with one `name|ip` line per host.

## Run the orchestrator against OCI

The orchestrator has no systemd unit. Run it on a machine that has the `oci`
CLI and the variables above:

```bash
export REDROID_GOLDEN_IMAGE_ID=<redroid image ocid>
export CUTTLEFISH_GOLDEN_IMAGE_ID=<cuttlefish image ocid>   # only for purpose=camera
export ORCH_API_TOKEN=<token for your agents>
export ORCH_CONTROL_API_TOKEN=<the API_TOKEN baked into the golden images>
ORCH_DEPLOY_MODE=oci ./cloud-phone orchestrator-run
```

When a session needs a new phone, it runs
`scripts/deploy-from-golden.sh --platform <runtime> --image-id <ocid> --name <prefix>-<runtime>-<timestamp> --wait-check`,
reads `/tmp/instance-<name>.json` and uses `http://<public_ip>:8080` as that
phone's Control API. `DELETE /instances/<id>` terminates the VM with
`oci compute instance terminate` using `ORCH_OCI_PROFILE`, `ORCH_OCI_CONFIG`
and `ORCH_OCI_AUTH` (default `security_token`).

Pool limits: `ORCH_MAX_INSTANCES` (5), `ORCH_MAX_REDROID_INSTANCES` (3),
`ORCH_MAX_CUTTLEFISH_INSTANCES` (2). All state is in memory; if the
orchestrator restarts, it forgets the VMs it created, and they keep running
(and billing) until you terminate them yourself.

## Cuttlefish ingest host

```bash
./cloud-phone deploy --name cuttlefish-source --ocpus 4 --memory 24
./cloud-phone verify-ingest --vm <HOST_IP>
```

Requirements (`/dev/kvm`, host tools, guest image) and the ingest pipeline are
in [cuttlefish-ingest.md](cuttlefish-ingest.md).
