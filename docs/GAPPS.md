# GApps / Play Services requirement

**Status:** required for useful Mobile IO · **Missing on tip `master`** · **Updated:** 2026-08-25

## Why

Cloud-phone automation that needs Play Store apps, Google login, or Play Integrity–adjacent flows **must** have Google Apps (GMS) inside the guest Android image. Spoof props alone (`ro.com.google.gmsversion` in `config/device-profiles/`) are **not** GApps.

Lab note (PR #10): empty `/opt/gapps/gapps.zip` → no Play Store / GMS on device.

## Current tip truth

| Fact | Detail |
|---|---|
| Runtime | **Cuttlefish-only** (Redroid/Waydroid paths removed in `55b593c`) |
| Image build | OCI **golden host** image — not a Redroid Dockerfile |
| GApps install scripts | **Deleted** with the slim (`install-gapps.sh`, `fix-play-services.sh`, docker `--gapps`) |
| Magisk | Never shipped as an installer |

Recovering the old Redroid scripts from `0028cb4` is a starting point for history, **not** a drop-in for Cuttlefish.

## What to build (when Stage-2 unparks)

1. **`scripts/install-gapps-cuttlefish.sh`** — after CVD boots, push a licensed/operator-supplied GApps or MindTheGapps/NikGApps zip via `adb` (or bake into the Android system image before `launch_cvd`). Do **not** commit proprietary zips.
2. Wire into `install-cuttlefish-cloud-phone.sh` / `cuttlefish-phase1-setup.sh` **after** guest boot.
3. Bake into golden only after Play opens once: `prepare-golden-image.sh` → `create-golden-image.sh`.
4. Validate: packages `com.google.android.gms` + Play Store present; optional uncertified registration flow.
5. Document ARM64 + Android version matrix; expect Play Integrity pain on emulators.

## Operator supply (secrets / artifacts — names only)

| Name | Role |
|---|---|
| `GAPPS_ZIP_URL` or host path | Operator-provided zip (never commit binary) |
| Optional Magisk / Integrity work | Separate; not implied by “needs GApps” |

## Non-goals right now

This does **not** block KnowShowGo / OSLO money path (Paddle C1, LinkedIn B1). Telephony + Mobile IO stay **Stage-2** until Stage-1 earns — but when Mobile IO returns, **GApps is a hard prerequisite**, not a nice-to-have.

## Related

- `docs/CUTTLEFISH_PHASE1.md`, `docs/CUTTLEFISH_OCI_GOLDEN_IMAGE.md`
- OSLO `docs/DEVELOPMENT-PLAN.md` § Later features (mobile IO)
- Historical Redroid gapps: git `0028cb4` (`scripts/install-gapps.sh`)
