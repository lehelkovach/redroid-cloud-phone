# Mock Tinder fixture

Offline-friendly login UI for mobileio QA. **Fake credentials only:**

| Field | Value |
|-------|--------|
| Email | `bs@example.com` |
| Password | `fake-password-not-real` |

## Why a fixture?

Play Store install of real Tinder is often blocked on Redroid (GMS/geo/account).
This HTML stand-in exercises `mobile.launch` / tap / type / **swipe** without
real accounts or paid APIs.

## Install / open on the Oracle phone

### Option A — Firefox (no APK build)

```bash
# From the phone VM (or adb)
adb -s 127.0.0.1:5555 push fixtures/mock-tinder/index.html /sdcard/Download/mock-tinder.html
adb -s 127.0.0.1:5555 shell \
  'am start -a android.intent.action.VIEW -d "file:///sdcard/Download/mock-tinder.html" -p org.mozilla.firefox'
```

Or serve over HTTP on the VM and open the URL in Firefox.

### Option B — package id `com.mock.tinder`

Build a trivial WebView APK wrapping this HTML (or use Bubblewrap / Cordova)
and `adb install mock-tinder.apk`. Agent automation defaults to package
`com.mock.tinder` via `mobile.mock_tinder_login` / `runMockTinderLogin`.

## Agent offline path

No device needed:

```bash
cd ../osl-oc-agent
node scripts/mobile_mock_tinder_demo.mjs
```
