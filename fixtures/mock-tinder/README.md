# Mock Tinder fixture

Offline-friendly login UI for mobileio QA. **Fake credentials only:**

| Field | Value |
|-------|--------|
| Email | `bs@example.com` |
| Password | `fake-password-not-real` |

## Open on Redroid (no Firefox / no GApps)

WebView shell only accepts `http`/`https` — serve the file from the phone VM:

```bash
# on phone VM
cp fixtures/mock-tinder/index.html /tmp/mock-tinder.html
cd /tmp && python3 -m http.server 8765 &
adb -s 127.0.0.1:5555 shell \
  'am start -a android.intent.action.VIEW -d http://10.0.1.127:8765/mock-tinder.html -n org.chromium.webview_shell/.WebViewBrowserActivity'
```

Then from `osl-oc-agent`:

```bash
CLOUD_PHONE_API_URL=http://127.0.0.1:18080 \
CLOUD_PHONE_API_TOKEN=… \
  node scripts/mobile_mock_tinder_demo.mjs --live
```
