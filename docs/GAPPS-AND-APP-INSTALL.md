# Google Play and app install: options and decision

- **Date:** 2026-10-29
- **Question:** Can the cloud phone run Google Play (Redroid with GApps, or
  Cuttlefish), and how do apps get onto it, with and without Play?
- **Decision:** Redroid on the existing ARM64 hosts, with MindTheGapps baked
  into a derived image at build time. Apps are installed by APK (path or
  URL + SHA-256) through the Control API, or from Play by package name once
  the owner has signed in on the phone. Cuttlefish stays the camera runtime,
  without Play.
- **Status:** booted on an OCI A1 host on 2026-10-30: Android 13 with Play
  services, APK install by URL + SHA-256, and three real apps driven through
  the Control API. Play sign-in is the owner's step and has not been done (see
  [What was verified](#what-was-verified-and-what-was-not)).

This page is the research behind that decision and the operator guide for
it. The older runtime install path (`gapps-install`, pushing a zip into a
running phone) is still documented in [gapps.md](gapps.md).

## Recommendation in five lines

1. Run Redroid on the OCI `VM.Standard.A1.Flex` (Ampere Altra, ARM64) hosts
   the deploy scripts already create. ARM apps run natively, so no
   libndk/libhoudini translation is needed.
2. Bake MindTheGapps for Android 13 arm64 into `redroid/redroid:13.0.0-latest`
   with `./cloud-phone gapps-image` (one `COPY` layer, setup wizard off).
3. Run it with `docker/redroid-gapps-compose.yml` or the
   `redroid-gapps-container` systemd unit. Both are off by default.
4. The owner registers the phone's GSF Android ID as an uncertified device
   and signs in to Play on the phone through screen take-over. Nobody else
   does either step, and no Google credential is ever stored.
5. Install apps with `POST /apps/install` (APK, split set or bundle, by host
   path or HTTPS URL with SHA-256) or `POST /apps/play/install` (package
   name; works only after step 4).

## The host this runs on

From [deploy-oci.md](deploy-oci.md) and `scripts/deploy-redroid-oci.sh`:

| | Value | Why it matters |
|---|---|---|
| Shape | `VM.Standard.A1.Flex`, 2 OCPU / 8 GB by default | One GApps phone fits; plan on about 4 GB each (estimate, not measured) |
| CPU | Ampere Altra, `aarch64` | Use **arm64** images and **arm64** GApps zips. Altra cores also run 32-bit ARM code, so the non-`_64only` tag works if the kernel has `CONFIG_COMPAT` (Ubuntu's does). On AmpereOne (A2) shapes use a `_64only` tag: those cores have no 32-bit mode |
| OS | Ubuntu 24.04, else 22.04 | Ubuntu's Oracle kernel. Binder comes from `linux-modules-extra-$(uname -r)` + `modprobe binder_linux devices="binder,hwbinder,vndbinder"` ([redroid Ubuntu guide](https://github.com/remote-android/redroid-doc/blob/master/deploy/ubuntu.md)) |
| ashmem | Removed from the kernel in 5.18 | Not needed: the GApps compose file sets `androidboot.use_memfd=true` ([redroid README](https://github.com/remote-android/redroid-doc)) |
| `/dev/kvm` | Not available on A1 VMs | Rules out Cuttlefish on these hosts (below) |

The OSLO lab notes record that Redroid ran on one of these hosts with binder
working, and that its `/opt/gapps/gapps.zip` was 0 bytes, which is why Play
was never there. This repo does not set binder up;
`./cloud-phone host-check` now says whether a host has it (read-only).

On the 2026-10-30 host (Ubuntu 24.04.5, kernel `6.17.0-1020-oracle`),
`apt install linux-modules-extra-$(uname -r)` then
`modprobe binder_linux devices="binder,hwbinder,vndbinder"` was enough:
binderfs registers and Redroid mounts it itself. Persist it with
`/etc/modules-load.d/` and `/etc/modprobe.d/` entries.

## Options compared

### 1. Redroid + GApps (chosen)

Redroid publishes Android 8.1 to 16 images for both `arm64` and `amd64`, with
`_64only` variants from Android 12
([redroid-doc](https://github.com/remote-android/redroid-doc)). It names Open
GApps, MindTheGapps and microG as ways to add Google services. There are two
ways to get GApps in:

| Way | How | Verdict |
|---|---|---|
| Build Redroid from AOSP source with `vendor/gapps` | MindTheGapps' makefiles inherited in the device tree; redroid's [builder docs](https://github.com/remote-android/redroid-doc/tree/master/android-builder-docker) show it, and recommend `ro.setupwizard.mode=DISABLED` | Works, but a full AOSP build (hundreds of GB, hours) per Android version. Not worth it here |
| Layer a flashable zip onto the published image | Copy the zip's `system/` tree onto `/system` in a derived image. This is what [ayasa520/redroid-script](https://github.com/ayasa520/redroid-script) does (`-mtg` MindTheGapps, `-lg` LiteGapps, `-g` OpenGApps for Android 11 only, `-n` libndk, `-m` Magisk) | **Chosen.** One `COPY` layer, minutes to build, any arch |
| Push the zip into a running phone (this repo's `gapps-install`) | `adb root`, `remount`, push, reboot | Kept for old hosts. `/system` changes do not survive `docker rm` or a redeploy, so every new phone needs the step again |

Which GApps package:

- **MindTheGapps** (recommended). LineageOS's own recommendation; arm64
  release repos exist for Android 13 through 17
  ([github.com/MindTheGapps](https://github.com/MindTheGapps)); redroid-script
  pins arm64 builds of 12.1 to 15 by checksum. Its zip has a plain `system/`
  tree, which is what the build script copies.
- **LiteGapps.** Also maintained, arm64 builds for 13 to 15, but the payload is
  inside `files/files.tar.xz` and has to be unpacked to a `system/` tree first.
- **OpenGApps.** Last release 2022, Android 11 at most. The build script
  refuses its zips (`Core/*.tar.lz`); use `gapps-install` if you must.

Android version: the plain phone stays on Android 11. The GApps image
defaults to **Android 13** because arm64 MindTheGapps zips exist for 13 and
not (as a maintained release) for 11, and 13 runs on memfd without ashmem.
14 and 15 work the same way (`--android 14.0.0` with a 14 zip).

ARM apps on x86: only relevant to x86_64 hosts. Redroid's x86 images include
`libndk_translation`; redroid-script can add libndk or libhoudini. Not needed
on Ampere, where ARM apps run natively, which is one more reason to stay on
A1.

### 2. Redroid + microG

microG replaces Play services with an open implementation. It needs
**signature spoofing** (so microG can claim Google's signature), which stock
AOSP and Redroid images do not have. The ways to get it are a ROM with the
patch, an Xposed/LSPosed module (FakeGApps) on top of Magisk, or patching
the framework ([microG wiki](https://github.com/microg/GmsCore/wiki/Signature-Spoofing)).
On Redroid that means Magisk plus Zygisk plus LSPosed plus FakeGApps; the
patcher tools work properly with recent apps only on Android below 9, per
the same page. Apps that check Play Integrity or need real Play services
features (Play billing, some sign-in flows) still fail.

Not chosen: more moving parts than GApps, and less app compatibility. It is
the option to revisit if Google account sign-in on the phone becomes
unacceptable.

### 3. Cuttlefish

Cuttlefish is Google's reference virtual device. It needs KVM: "Cuttlefish
is a virtual device and is dependent on virtualization being available on
the host machine" ([get started](https://source.android.com/docs/devices/cuttlefish/get-started)).
The ARM64 target is `aosp_cf_arm64_only_phone`. AOSP images have no GMS. To
get Play you would build AOSP with MindTheGapps' `vendor/gapps` in the
Cuttlefish device tree, a full AOSP build like the Redroid source route.

The blocker for this project: Oracle states that Ampere A1 VMs do not support
nested virtualisation; its nested-KVM guide uses x86 shapes
(`VM.Standard.E5.Flex`, `VM.Standard3.Flex`)
([Oracle blog, 2025](https://blogs.oracle.com/linux/post/kvm-nested-virtualization-in-oci)).
So Cuttlefish on the A1.Flex VMs that `deploy` creates is unlikely to start
at all; the first step in [ROADMAP.md](../ROADMAP.md) ("confirm `/dev/kvm`")
will probably fail. Options are an x86 shape with nested virtualisation and
an x86_64 Cuttlefish build, or an A1 bare-metal shape. Either costs more than
a Redroid container; this repo's own baseline is 4 OCPU / 24 GB per
Cuttlefish host against 2 OCPU / 8 GB for Redroid.

Not chosen for Play. Cuttlefish stays the camera/mic runtime as
[architecture.md](architecture.md) decided, and that plan should check KVM
before anything else.

## Play certification and sign-in (the owner's steps)

A Redroid phone is not Play Protect certified. Google lets the owner of an
uncertified device register it so Google sign-in and Play work:

1. Start the GApps phone and let it boot.
2. Read its GSF Android ID: `GET /apps/play/status` returns it as
   `gsf_android_id` (it reads `gservices.db` as root). By hand:
   `adb shell sqlite3 /data/data/com.google.android.gsf/databases/gservices.db "select value from main where name='android_id';"`
   (the method redroid-script documents). Play services may need a minute
   after first boot to create the ID.
3. The owner opens <https://www.google.com/android/uncertified>, signs in
   with **their own** Google account, and registers that ID. The page was live
   (it redirects to Google sign-in) on 2026-10-29. Google removed its old
   100-device limit in 2018 ([XDA via Wccftech](https://wccftech.com/100-device-registration-limit-no-longer-applicable-to-googles-uncertified-device-page)).
   Community reports say registration can take from minutes to hours to
   apply, and that clearing Play services and Play Store data afterwards
   helps.
4. The owner signs in to Play **on the phone**, through screen take-over in
   OSLO or the Watch viewer. The agent does not type Google credentials.
5. `GET /apps/play/status` now shows `"ready": true`.

The GSF ID is tied to the phone's `/data`. Wiping the data volume
(`redroid-down`, `compose down -v`) creates a new ID that has to be
registered again, and removes the sign-in. The GApps systemd unit stops
without `-v` for that reason.

Registration lets the account use Play on the device. It does not make the
device pass Play Integrity's device-integrity verdict: apps that require it
(some banking and anti-fraud-heavy apps) will still refuse to run. This
project does not try to defeat that check (see the README's responsible-use
section).

## Installing apps

### Without Play

| Method | Where | Notes |
|---|---|---|
| `adb install` / `adb install-multiple` | `POST /apps/install` | Single APK, a split set (`paths` or `urls`), or an `.apks` / `.xapk` / `.apkm` bundle, which is unpacked and sent through `install-multiple`. OBB data in an XAPK is reported, not pushed |
| By host path | `{"path": ...}` | Only under `APP_INSTALL_DIRS` (default `/opt/cloud-phone/apks`), so the endpoint cannot be used to read other files on the host |
| By URL | `{"url": ..., "sha256": ...}` | HTTPS only, SHA-256 **required**, size-capped, private and link-local addresses refused (cloud metadata). A mismatch deletes the download and installs nothing |
| Upload | `POST /adb/install` (multipart, existing) | For an APK that lives with the caller |
| Aurora Store | an app on the phone | FOSS Play client; "does not have any approval, sponsorship or authorization from Google" ([README](https://gitlab.com/AuroraOSS/AuroraStore/-/raw/master/README.md)). Anonymous mode uses shared tokens and breaks when Google changes its API. Not integrated |
| apkeep | a CLI on the host | [EFF's apkeep](https://github.com/EFForg/apkeep) downloads from Google Play with an email and an AAS token, or from APKPure ("research purposes"), F-Droid (signature-checked) or Huawei. Its README warns Google may terminate the account. **Not integrated**: it needs a long-lived Google token outside the phone, which this project does not hold |

Where APKs come from is the real integrity question. A checksum proves the
file is the one you meant, not that the one you meant is safe. Prefer, in
order: the vendor's own download page or F-Droid (signature-verified), then
Play on the phone itself, then mirrors such as APKMirror, which says it checks
signatures against earlier versions but is still a third party. Record the
SHA-256 when you first vet a file and pin it.

### From Play, by package name

`POST /apps/play/install {"package": "com.example.app"}` opens
`market://details?id=<package>` in the Play Store, taps **Install**, and
waits for `pm path` to find the package. It:

- answers `409 gapps_missing` without Play on the phone, and
  `409 play_sign_in_required` (with an `owner_action` text) until a Google
  account is signed in;
- never buys: a price or **Buy** button stops it with `paid_app`;
- stops on a sign-in prompt, "not available for your device", or no
  Install button within 90 s, and reports which;
- runs as a job by default (`202`, poll `GET /jobs/<id>`), or in the request
  with `"wait": true`;
- reports whether an account exists, never its name.

The labels it reads are the English Play Store UI. A different locale or a
Play redesign shows up as `install_button_not_found`, not as a guess.

Endpoint details: [api/control-api.md](api/control-api.md#apps).

## Licensing and terms, plainly

- **GApps are Google's proprietary software.** Google licenses them to device
  makers under its own agreements; nobody else has a licence to redistribute
  them. MindTheGapps, LiteGapps and OpenGApps distribute them anyway, and
  Google tolerates it for people putting Play on devices they own. That is
  the only use this project supports.
- **Do not redistribute a GApps image.** `build-gapps-image.sh` has no push
  option on purpose. Keep the image on the hosts that run it or in a private
  registry; never commit a zip, the build context, or a `docker save`
  tarball. `.gitignore` already excludes `gapps.zip` and `*.apk`.
- **Google Play Terms of Service** license Play content "for your personal,
  non-commercial use only", let Google limit the number of devices, and
  forbid modifying or transferring content obtained through Play or
  circumventing its security features
  ([Play Terms](https://play.google.com/about/play-terms/index.html)). Signing
  in to your own account on your own phone and installing free apps is
  ordinary use. Running many accounts, reselling access to Play-installed
  apps, or pulling APKs out of Play with unofficial clients at scale is not,
  and can get the account closed (apkeep's README says so too).
- **Each app's own terms** still apply. Some forbid emulators, automation or
  multiple accounts.
- **The uncertified-device registration** is the owner's action, with the
  owner's account. No agent should do it, and no Google credential belongs
  in this repo, its logs or its tests.

## How to run it

On a machine with Docker (an x86_64 laptop can build the arm64 image; the
layer is a plain copy, so no qemu is needed):

```bash
# 1. Get MindTheGapps for Android 13 arm64 yourself and note its SHA-256.
./cloud-phone gapps-image --zip ~/Downloads/MindTheGapps-13.0.0-arm64-<date>.zip \
  --sha256 <sha256> --android 13.0.0 --arch arm64 --dry-run     # check the zip and the plan
./cloud-phone gapps-image --zip ~/Downloads/MindTheGapps-13.0.0-arm64-<date>.zip \
  --sha256 <sha256> --android 13.0.0 --arch arm64 --save /tmp/redroid-gapps.tar
```

On the phone host (copy the tarball over first, or build there: an arm64
host builds natively in about two minutes):

```bash
./cloud-phone host-check                        # binder, Docker, RAM
docker load -i /tmp/redroid-gapps.tar
echo 'REDROID_GAPPS_IMAGE=cloud-phone/redroid-gapps:13.0.0-arm64' | sudo tee -a /etc/default/redroid-cloud-phone
sudo systemctl disable --now redroid-container.service
sudo systemctl enable redroid-gapps-container.service control-api-redroid.service redroid-cloud-phone.target
sudo systemctl start redroid-cloud-phone.target   # also what brings it back after a reboot
adb connect 127.0.0.1:5555 && ./cloud-phone gapps-check --adb 127.0.0.1:5555
curl -s http://127.0.0.1:8080/apps/play/status -H "Authorization: Bearer $API_TOKEN"
./cloud-phone real-app-smoke --api-url http://127.0.0.1:8080   # token from $CLOUD_PHONE_API_TOKEN
```

Ubuntu's `docker.io` has no buildx; the build script then uses Docker's
legacy builder (the layer is a plain `COPY`). Install `docker-buildx` to use
BuildKit instead.

Then the owner's steps above. The GApps compose file binds ADB to
`127.0.0.1` by default (`ADB_BIND`), because the phone now holds a
signed-in Google account; the plain phone's compose file binds all
interfaces.

## What was verified and what was not

### On a real OCI A1 host, 2026-10-30

Host: a new `VM.Standard.A1.Flex`, 2 OCPU / 12 GB, Ubuntu 24.04.5, kernel
`6.17.0-1020-oracle`, Docker 29.1.3 (Ubuntu `docker.io`). Full record:
[.AGENT/reports/2026-10-30-gapps-phone-first-boot.md](../.AGENT/reports/2026-10-30-gapps-phone-first-boot.md).

| Check | Result |
|---|---|
| `host-check` | `binder=binderfs` (after `modprobe binder_linux`), `ashmem=missing` (memfd used), `redroid_ok=true`, no `/dev/kvm` |
| GApps zip | `MindTheGapps-13.0.0-arm64-20231025_200931.zip` from the MindTheGapps GitHub release, SHA-256 `3ff2edaa75406eb4214402fca6906d4b413ab50afdb1af033d9e396992338d0b`; its JAR signature checks against the release's `release.x509.pem` (39/39 entries) |
| `gapps-image` | Built `cloud-phone/redroid-gapps:13.0.0-arm64` on the host after one fix (legacy builder when buildx is missing) |
| Boot | `sys.boot_completed=1` 60 s after the first start, 52 s after a host reboot; ABIs `arm64-v8a,armeabi-v7a,armeabi` |
| `GET /apps/play/status` | `gapps.ready=true`, a GSF Android ID present (stable across two reboots; `null` for a few seconds right after boot), `google_account=false`, `ready=false` with the owner action, after one fix (below) |
| `POST /apps/install` by URL | F-Droid 2.0.2 from `f-droid.org` with its index SHA-256: installed in 7 s, listed in `GET /apps`. A wrong SHA-256 answered `checksum_mismatch` and installed nothing |
| Real apps (`real-app-smoke`) | Calculator (7 × 6 shows 42), Notes (typed text survives force-stop and relaunch), Contacts (a filled form is saved and listed, then deleted): 31/31 steps on three runs in a row and again after a reboot |
| Same flow through OSLO's `mobile.*` tools | 24/24 (health, play_status, install_app, apps, launch, dump, tap_label, tap, key, type, back, home, close, focus, screenshot), OSLO's tool registry run on the phone host against `127.0.0.1:8080` |
| Reboot | Phone, Control API and installed apps come back by themselves, after one fix to the target unit |
| Isolation | From inside Android: the host's `:8080`, instance metadata and the VCN are blocked; the internet is reachable |
| Play sign-in, Play install | **Not done**: owner-only |

Bugs the first boot found, all fixed on this branch: the build fallback asked
for BuildKit without buildx; `/apps/play/status` reported every GApps phone as
signed in (it matched the authenticator listing); `/device/ui` returned no
elements on Android 13 (`uiautomator dump /dev/tty`); and both
`control-api-redroid.service` and `redroid-cloud-phone.target` pulled in the
plain phone, whose `Conflicts=` stops the GApps phone.

### Offline, 2026-10-29

Done on 2026-10-29 in a cloud sandbox (x86_64, 4 vCPU, 15 GB, Docker 29):

| Check | Result |
|---|---|
| `build-gapps-image.sh` against the real `redroid/redroid:13.0.0-latest` arm64 image (pulled through `mirror.gcr.io`; Docker Hub rate-limited the sandbox) with a **fake** MindTheGapps-shaped zip | Built in about 20 s on x86_64 without qemu. The image is `arm64`, the files land in `/system/product/priv-app` and `/system/system_ext/priv-app` (Redroid's `/product` and `/system_ext` are symlinks into `/system`), and the entrypoint is the base's `["/init", "qemu=1", "androidboot.hardware=redroid"]` plus `ro.setupwizard.mode=DISABLED` |
| Boot any Redroid image here | **Not possible.** The kernel has no binder (`CONFIG_ANDROID_BINDER_IPC is not set`, no module directory) and no `/dev/kvm`. The amd64 `13.0.0-latest` container exits within 2 s with code 129. `host-check` reports both |
| Real GApps zip, Play sign-in, Play install | **Not done.** No zip was downloaded and no Google account was used |
| Control API endpoints | Offline tests with a fake `adb` and fake downloads (`tests/test_app_install.py`, 56 tests) |
| Script argument handling, zip checks, compose/unit shape | `tests/test_gapps_image.py`, 29 tests |

Still to check, after the owner's steps: Play opens after registration and
sign-in, and `POST /apps/play/install` installs a small free app.
