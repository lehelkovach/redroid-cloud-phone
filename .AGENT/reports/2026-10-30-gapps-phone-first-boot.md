# GApps phone: first boot on OCI A1, app installs and real-app I/O

- **Date:** 2026-10-30
- **Asked by:** the founder, through the lead ("get one of the android images to be able to install apps"; then "run I/O tests on real apps, not just installed")
- **Question:** Does the PR #20 GApps image boot on an OCI A1 host, install apps by APK, and can the Control API (and OSLO's `mobile.*` tools on top of it) drive real apps?
- **Scope:** one new OCI instance `cloud-phone-gapps-a1` (`VM.Standard.A1.Flex`, 2 OCPU / 12 GB, 80 GB boot volume, PHX AD-2); branch `claude/jolly-clarke-g09xm5`; OSLO `src/openclaw_tools/cloud_phone.mjs` and `src/agent/tool_registry.mjs` at OSLO `deb71768`
- **Method:** launched the host with a cloud-init script that only starts an outbound command loop (commands read from one private Object Storage bucket, output written to another, both through pre-authenticated requests that expire 2026-11-03); ran every step below through that loop and read the outputs back
- **Verdict:** Yes. Android 13 with Play services boots in about a minute, APKs install by URL + SHA-256, and three real apps pass a 31-step I/O smoke repeatedly, also after host reboots; the same flow passes 24/24 through OSLO's own tools. Five bugs found on the way, all fixed on the branch. Play sign-in is the owner's step and was not done.
- **Follow-up:** PR #20 (this branch); owner steps listed at the end

## Why a new host

The fleet had changed since the audit in OSLO's `.AGENT/OCI-FLEET.md`:
`cloud-phone-dev` and `redroid-camera-build` were stopped, and the one running
phone host (`cloud-phone-agent-6c58`) had no Run Command plugin running and no
SSH key this session holds, and is the live lab phone. Rather than change IAM
or disturb the lab phone, one new instance was launched. It sits in the same
VCN and subnet as OSLO prod, so OSLO can reach it over the private network
(see [How OSLO connects](#how-oslo-connects)).

## Host

| Item | Value |
|---|---|
| OS | Ubuntu 24.04.5 LTS (`Canonical-Ubuntu-24.04-aarch64-2026.09.18-0`) |
| Kernel | `6.17.0-1020-oracle` |
| Binder | `linux-modules-extra-$(uname -r)` + `modprobe binder_linux devices="binder,hwbinder,vndbinder"`, persisted in `/etc/modules-load.d/redroid-binder.conf` and `/etc/modprobe.d/redroid-binder.conf` |
| `host-check --json` | `{"arch":"aarch64","binder":"binderfs","ashmem":"missing","memfd_fallback":true,"docker":"running","mem_gb":11,"cpus":2,"kvm":"missing","redroid_ok":true,"cuttlefish_ok":false}` |
| Docker | 29.1.3 (Ubuntu `docker.io`, `docker-compose-v2`, no buildx) |
| Egress guard | `phone-egress-guard.service`: `DOCKER-USER` drops container traffic to `169.254.0.0/16` and `10.0.0.0/8` |
| Host firewall | Ubuntu's OCI rules plus one `INPUT` accept for TCP 8080 from OSLO prod's private address (also in `/etc/iptables/rules.v4`) |
| Network security group | `cloud-phone-gapps-nsg`: TCP 8080 ingress from OSLO prod's private address only |
| SSH | no key was installed; the host is administered only through the command loop until the owner adds a key |

## GApps zip

- `MindTheGapps-13.0.0-arm64-20231025_200931.zip`, 385,146,857 bytes, the
  latest non-prerelease asset of `github.com/MindTheGapps/13.0.0-arm64`,
  downloaded on the host over HTTPS.
- SHA-256 `3ff2edaa75406eb4214402fca6906d4b413ab50afdb1af033d9e396992338d0b`
  (GitHub gives no digest for assets this old).
- The zip is JAR-signed. Its signer certificate equals the release's
  `release.x509.pem` (SHA-256 fingerprint `91:37:0E:…:67:76`, subject the AOSP
  "Android" test key), `CERT.SF` verifies against it, and 39 of 39 entries
  match the manifest digests. That proves the zip is the one the release
  published, not who built it.
- The zip stays on the host under `/opt/gapps-dl`; nothing was committed or
  uploaded.

## Image

| Item | Value |
|---|---|
| Base | `redroid/redroid:13.0.0-latest`, arm64, `sha256:41e5f0c1ff27a4a474c474e5595168cedf6c40fc5dd102c5617f48c80f511e9e` |
| Built | `cloud-phone/redroid-gapps:13.0.0-arm64`, image ID `sha256:185591016f3b08f7ee42c8beee29421feaeb5f802effd81e05fc6565c9122915` |
| Entrypoint | `["/init","qemu=1","androidboot.hardware=redroid","ro.setupwizard.mode=DISABLED"]` |
| Build time | about 2 minutes on the host (legacy builder) |

The first build failed: `ERROR: BuildKit is enabled but the buildx component is missing or broken.`
The script's fallback set `DOCKER_BUILDKIT=1`; it now uses the legacy builder
and says how to install buildx (commit `6f8be99`).

## Boot

- `docker compose -f docker/redroid-gapps-compose.yml -p redroid-gapps --profile gapps up -d`:
  `sys.boot_completed=1` after 60 s, container healthy, 0 restarts.
- `ro.build.fingerprint`: `redroid/redroid_arm64/redroid_arm64:13/TQ3A.230805.001.S1/eng.frank.20240527.132658:userdebug/test-keys`.
- ABIs `arm64-v8a,armeabi-v7a,armeabi`.
- Google packages present: `com.android.vending`, `com.google.android.gms`,
  `com.google.android.gsf`, `com.google.android.setupwizard` and 11 others.
- ADB listens on `127.0.0.1:5555` only.
- Home screen screenshot: Play Store icon in the dock, Google search bar.

## Control API

- Files from `api/` in `/opt/cloud-phone-api` with a venv; units
  `redroid-gapps-container.service`, `control-api-redroid.service` and
  `redroid-cloud-phone.target` enabled.
- `API_TOKEN` (64 hex characters, generated on the host) is in
  `/etc/default/redroid-cloud-phone`, mode 600. Without it every endpoint
  answers 401.
- `API_HOST=0.0.0.0`, port 8080, reachable only from loopback and OSLO prod
  (host firewall plus the network security group).

`GET /health` (token sent): `status=healthy`, `usable=true`,
`adb_connected=true`, `gapps={gms,gsf,play_store,ready: true}`,
`auth.required=true`.

## Bugs found and fixed

| # | Symptom | Cause | Fix |
|---|---|---|---|
| 1 | Image build refused on Ubuntu's Docker | Fallback built with `DOCKER_BUILDKIT=1` without buildx | Legacy builder (`6f8be99`) |
| 2 | (latent) starting the Control API would stop the GApps phone | `control-api-redroid.service` `Wants=redroid-container.service`, which `Conflicts=` with the GApps unit | Order after both phone units, want neither (`6f8be99`) |
| 3 | `GET /apps/play/status` said `google_account: true, ready: true` on a fresh phone | `has_google_account` matched `type=com.google` anywhere; `dumpsys account` always lists `AuthenticatorDescription {type=com.google}` | Count only `Account {… type=com.google}` lines, regression test (`d980f46`) |
| 4 | `GET /device/ui` answered 500 `no UI hierarchy in dump output`; every label tap and the Play install flow depend on it | `uiautomator dump /dev/tty` prints only "UI hierchary dumped to: /dev/tty" on Android 13 without a pty | Dump to `/data/local/tmp/cloudphone_ui.xml` and `cat` it in the same shell (`50f7fc5`) |
| 5 | After a reboot the phone and Control API were gone | The units are `WantedBy=redroid-cloud-phone.target`, and that target `Wants=redroid-container.service` (the plain phone), whose `Conflicts=` stops the GApps phone | Target orders after both phone units and no longer wants the plain one (`f3f9528`); verified with a second reboot |

## APK install

- `POST /apps/install {"url":"https://f-droid.org/repo/org.fdroid.fdroid_2000052.apk","sha256":"19d51fad…7b52","expected_package":"org.fdroid.fdroid"}`:
  `success=true`, `verified=true`, `new_packages=["org.fdroid.fdroid"]`, 7 s.
  The hash came from F-Droid's `index-v2.json`.
- The same URL with an all-zero SHA-256: `checksum_mismatch`, "nothing was installed".
- `GET /apps`: `["org.fdroid.fdroid"]`; `GET /apps/org.fdroid.fdroid`: version 2.0.2, code 2000052.

## Real-app I/O (`scripts/real-app-smoke.py`)

Apps (F-Droid, pinned in the script): Fossify Calculator 1.4.0
(`org.fossify.math_10.apk`), Fossify Notes 1.7.0 (`org.fossify.notes_13.apk`),
Fossify Contacts 1.6.0 (`org.fossify.contacts_13.apk`). None needs an account.

| App | What is checked |
|---|---|
| Calculator | launch, read UI, `tap_label` C 7 × 6 =, read `result` = "42", screenshot, HOME |
| Notes | launch, tap the note field, type a unique token, read it back, BACK, HOME, force-stop, relaunch, token still there, screenshot |
| Contacts | launch, answer the app's own contacts prompt, tap "Create new contact", type First name and Surname, screenshot, Save, HOME, force-stop, relaunch, the contact is listed, screenshot, delete it |

Results: run 1 27/30, runs 2 to 6 mixed while fixing the script, then runs
7, 8 and 9 31/31 each, and 31/31 again after the second reboot. What the
failing runs taught (now handled in the script, and worth knowing for any
agent driving a phone):

- The AOSP keyboard asks for contacts access the first time it opens inside
  the Contacts app. The script allows a permission prompt only when it names
  the app under test and refuses the rest.
- The keyboard covers lower fields; a tap meant for one lands on a key. The
  script sends BACK only while `dumpsys input_method` says the keyboard is up
  (BACK otherwise leaves the screen).
- Once contacts exist, typing a first name opens a "matching contacts"
  dropdown over Surname and Save, and the next tap picks an old contact. The
  script uses a first name no contact starts with. The dropdown is a popup
  window that `/device/ui` does not show.

## The same flow through OSLO's tools

OSLO's `buildToolRegistry` (with a mock KSG client and an approver that says
yes) was run with Node 22 on the phone host, `CLOUD_PHONE_API_URL=http://127.0.0.1:8080`
and the token in `CLOUD_PHONE_API_TOKEN`. 24/24 steps passed: `mobile.health`,
`mobile.play_status` (gapps ready, GSF ID present, not signed in),
`mobile.install_app` (F-Droid by URL + SHA-256; the only approval asked),
`mobile.apps`, `mobile.close`/`mobile.launch`, `mobile.dump`,
`mobile.tap_label` (C 7 × 6 =, result "42"), `mobile.tap`, `mobile.key`,
`mobile.type`, `mobile.back`, `mobile.home`, `mobile.focus`,
`mobile.screenshot`; the note survived close and relaunch. The OSLO copy was
deleted from the host afterwards. Prod OSLO's configuration was not touched.

Seen on the way: `mobile.install_app` reinstalling an app already at that
version returns `package: null` and `new_packages: []`, which reads like
nothing happened.

## How OSLO connects

- `CLOUD_PHONE_API_URL=http://<private IP of cloud-phone-gapps-a1>:8080`
  (same VCN and subnet as OSLO prod; the value is in the session report, not
  here).
- `CLOUD_PHONE_API_TOKEN` = the `API_TOKEN` in the host's
  `/etc/default/redroid-cloud-phone`.
- No orchestrator is needed for this route: OSLO's direct client calls the
  Control API. `CLOUD_PHONE_ORCH_URL` can stay unset.

## Isolation checks (from inside Android, `nc -w 4`)

| Target | Result |
|---|---|
| `1.1.1.1:443` (control) | open |
| host `:8080` on both Docker bridges | blocked |
| instance metadata `169.254.169.254:80` | blocked |
| OSLO prod's private address `:8091` | blocked |

## Owner-only steps (not done)

1. Register the phone's GSF Android ID at <https://www.google.com/android/uncertified>
   with the owner's own Google account (the ID is in `GET /apps/play/status`).
2. Sign in to Google Play on the phone through screen take-over.
3. Install anything from Play that needs an account; any purchase.
4. Decide whether to add an SSH key, or another admin route, before the
   command loop's pre-authenticated requests expire on 2026-11-03.

## Not done

- Play sign-in and `POST /apps/play/install` (owner-only).
- OSLO prod reaching the phone over the private network: not tested from
  prod, which this session does not touch. The network security group and
  host firewall allow exactly that address.
