# Runtime split — Redroid phones vs Cuttlefish ingest

**Decision (2026-09-04):** two images, two jobs. Do not merge them again.

The Cuttlefish-only slim (`55b593c`) happened because **streaming a virtual camera through the Android Camera HAL failed on both Redroid and Waydroid** (ABI/VNDK mismatch, mixed vendor libs, apps seeing “external USB” or nothing). That work stays parked. See [`FUTURE_CONSIDERATIONS_CAMERA_STACK.md`](../FUTURE_CONSIDERATIONS_CAMERA_STACK.md).

| Runtime | Job | GApps / Play | Virtual camera / mic |
|---|---|---|---|
| **Redroid** (Docker, privileged) | Disposable Android **phones** for `mobile.*` / orchestrator sessions (Playwright-like containers) | **Yes** — operator zip or a gapps-tagged image | **No.** Do not bind `v4l2loopback`, do not rebuild camera HAL. |
| **Cuttlefish** (KVM/`/dev/kvm`) | Ingest image: **nginx-rtmp** + FFmpeg bridge + **v4l2loopback-class** sinks into guest cameras/mic | **No.** Do not bake Play/GMS here. | **Yes** — this is why the stack forked. |
| **Waydroid** (LXC) | Parked | Would be GApps-capable | Same HAL failure mode as Redroid. Not worth a third ops surface until Redroid phones are boring. |

## Why not GApps on Cuttlefish

Cuttlefish golden images are sized for KVM + camera HAL + RTMP. Play/GMS is a different bake (zip, privileged APKs, uncertified-device pain). Putting both on one image made Mobile IO wait on ingest, and ingest wait on Play. Split them.

Empty `/opt/gapps/gapps.zip` on the old Redroid lab is the same class of bug this split avoids: **no silent empty zip, no spoof props as GApps**.

## Operator loop

**Phones (Play / automation):**

```bash
./cloud-phone redroid-up --name phone-1
# operator-supplied zip (never commit the blob):
GAPPS_ZIP=/path/to/MindTheGapps-arm64.zip ./cloud-phone gapps-install --name phone-1
./cloud-phone gapps-check --adb 127.0.0.1:5555
```

Orchestrator: `ORCH_DEPLOY_MODE=redroid` then `POST /sessions` (one phone per owner; `provision:true` starts another container up to `ORCH_MAX_INSTANCES`).

**Ingest (camera / mic):** unchanged Cuttlefish path — `./cloud-phone deploy` → `verify-ingest`. No GApps step.

## Non-goals

- Do not restore `docker/Dockerfile.camera`, `scripts/fix-v4l2loopback.sh` into Redroid, or Magisk-on-Cuttlefish as a Play shortcut.
- Device-profile spoof (`ro.com.google.gmsversion`) is **not** GApps.
- Waydroid stays a later option, not this PR.
