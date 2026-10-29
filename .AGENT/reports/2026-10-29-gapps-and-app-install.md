# Google Play on the cloud phone: options, and what this sandbox could test

- **Date:** 2026-10-29
- **Asked by:** the owner, via an OSLO agent session ("redroid image with GApps and/or Cuttlefish, with some mechanism to install apps and Google Play services")
- **Question:** Which way of getting Play onto the phone fits this stack, and can it be proven here?
- **Scope:** this repo's docs, scripts and Control API; upstream redroid, redroid-script, MindTheGapps, microG, Cuttlefish, apkeep and Aurora docs; a cloud sandbox (x86_64, 4 vCPU, 15 GB, Docker 29). No live host was contacted.
- **Method:** read the code and upstream docs; ran `build-gapps-image.sh` against the real arm64 `redroid/redroid:13.0.0-latest` with a fake zip; tried to start the amd64 image; ran `check-redroid-host.sh`; offline test suites.
- **Verdict:** Redroid on the A1 hosts with MindTheGapps 13 arm64 baked in at build time. The image build works (arm64 on x86 without qemu). No Android was booted: this sandbox's kernel has no binder and no KVM.
- **Follow-up:** the PR that adds `docs/GAPPS-AND-APP-INSTALL.md`; first live run on a binder-capable ARM64 host.

## Findings

1. **Build mechanics work.** A derived image with one `COPY gapps/system/ /system/`
   layer built in about 20 s for `linux/arm64` on x86_64 without qemu/binfmt
   (no `RUN` step). Redroid 13's `/product` and `/system_ext` are symlinks
   into `/system`, so the zip's `system/product/...` and
   `system/system_ext/...` land on the right partitions from one copy.
2. **Redroid's real entrypoint is `["/init", "qemu=1", "androidboot.hardware=redroid"]`.**
   redroid-script's MindTheGapps path overrides it with a list that drops
   `qemu=1`. The build script reads the base's entrypoint and only appends
   `ro.setupwizard.mode=DISABLED`.
3. **This sandbox cannot run Redroid or Cuttlefish.** `CONFIG_ANDROID_BINDER_IPC`
   is not set, there is no module tree to load `binder_linux` from, and there
   is no `/dev/kvm` (no `vmx`/`svm`). The amd64 Redroid 13 container exits
   with code 129 within 2 s and prints nothing.
4. **Docker Hub rate-limited the sandbox** (HTTP 429 on anonymous pulls);
   `mirror.gcr.io/redroid/redroid:13.0.0-latest` worked. Hosts that pull often
   may hit the same limit.
5. **Cuttlefish on A1 VMs is unlikely to start.** Oracle says A1 VMs do not
   support nested virtualisation; the Cuttlefish deploy script creates
   `VM.Standard.A1.Flex` instances. This affects the camera roadmap, not just
   Play.
6. **No maintained arm64 GApps for Android 11.** MindTheGapps' arm64 release
   repos start at 13; redroid-script's Android 11 option is OpenGApps (last
   release 2022). Hence Android 13 for the GApps image while the plain phone
   stays on 11.
7. **The procedure adapter's `install` step targeted a route that did not
   exist** (`POST /apps/install`). It exists now.

## Not done

- No GApps zip was downloaded and no Google account was used.
- No phone booted, so `gapps-check`, GSF ID read-back, Play sign-in and
  `POST /apps/play/install` are untested against a real Play Store.
- Memory per GApps phone (about 4 GB) is an estimate, not a measurement.
