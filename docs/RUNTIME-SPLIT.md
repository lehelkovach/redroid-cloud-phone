# Runtime split — Redroid phones vs Cuttlefish ingest

**Decision:** two images, two jobs. Do not merge them.

Cuttlefish exists because **streaming a virtual camera through the Android Camera HAL failed on Redroid and Waydroid**. That work stays parked. See [`FUTURE_CONSIDERATIONS_CAMERA_STACK.md`](../FUTURE_CONSIDERATIONS_CAMERA_STACK.md).

| Runtime | Job | GApps / Play | Virtual camera / mic | Orchestrator purpose |
|---|---|---|---|---|
| **Redroid** (Docker on an OCI VM) | Default **automation pool** — Play / `mobile.*` / app login | **Yes** — operator zip baked or installed on first boot | **No.** Do not bind `v4l2loopback`. | `automation` (default) |
| **Cuttlefish** (KVM `/dev/kvm`) | On-demand **ingest** hosts: nginx-rtmp + FFmpeg + camera/mic sinks | **No.** Do not bake Play/GMS. | **Yes** | `camera` / `ingest` / `stream` |
| **Waydroid** | Parked | Would be GApps-capable | Same HAL failure as Redroid | — |

## Orchestrator pool

The orchestrator **defaults to Redroid**. A session or instance create with no `purpose` (or `purpose=automation`) reuses an idle Redroid VM or spawns one from `REDROID_GOLDEN_IMAGE_ID`.

A request with `purpose=camera` (aliases: `ingest`, `stream`, `rtmp`, `webrtc`) never reuses a Redroid phone. It takes an idle Cuttlefish VM or spawns one from `CUTTLEFISH_GOLDEN_IMAGE_ID` / `GOLDEN_IMAGE_ID` with the existing nginx-rtmp stack.

```bash
# default: Redroid + GApps automation VM from the pool
curl -X POST $ORCH/sessions -d '{"owner_user_id":"alice"}'

# camera stream: Cuttlefish ingest VM
curl -X POST $ORCH/sessions -d '{"owner_user_id":"alice","purpose":"camera"}'

curl $ORCH/pool
```

Local Docker (no OCI) is `ORCH_DEPLOY_MODE=redroid`. Production spawn is `ORCH_DEPLOY_MODE=oci`.

## Why not GApps on Cuttlefish

Cuttlefish goldens are sized for KVM + camera HAL + RTMP. Play/GMS is a different bake (zip, privileged APKs, uncertified-device pain). Putting both on one image made Mobile IO wait on ingest, and ingest wait on Play.

Empty `/opt/gapps/gapps.zip` on the old Redroid lab is the same class of bug this split avoids: **no silent empty zip, no spoof props as GApps**.

## Operator loop

**Automation pool (Play):**

```bash
./cloud-phone deploy-redroid --name redroid-source --ocpus 2 --memory 8
GAPPS_ZIP=/path/to/MindTheGapps-arm64.zip ./cloud-phone gapps-install --adb 127.0.0.1:5555
./cloud-phone gapps-check --adb 127.0.0.1:5555
COMPARTMENT_ID=<ocid> ./cloud-phone create-golden <IP> cloud-phone-redroid-gapps-v1 redroid
REDROID_GOLDEN_IMAGE_ID=<ocid> ./cloud-phone deploy-fleet --platform redroid --count 3
```

**Ingest (camera / mic):** unchanged Cuttlefish path.

```bash
./cloud-phone deploy --name cuttlefish-source --ocpus 4 --memory 24
./cloud-phone verify-ingest --vm <OCI_PUBLIC_IP>
```

Never commit a GApps zip. Details: [`GAPPS.md`](./GAPPS.md).

Proof of the split is the TDD ladder in [`TESTING.md`](./TESTING.md) (`./cloud-phone test`, R0–R3 offline). Verbose IO logs (`[CMD]` commandlets, `[APM]` Appium, `[VNC]` viewports) are documented in [`LOGGING.md`](./LOGGING.md).

## Non-goals

- Do not restore `docker/Dockerfile.camera` / v4l2loopback-into-Redroid, or Magisk-on-Cuttlefish as a Play shortcut.
- Device-profile spoof (`ro.com.google.gmsversion`) is **not** GApps.
- Waydroid stays a later option, not this change.
