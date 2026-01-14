# Project Name Clarification

## Current Implementation: **REDROID** ✅

**Important:** This project uses **Redroid**, not Waydroid.

### Why the Confusion?

- **Directory name:** `waydroid-cloud-phone` (legacy name from initial exploration)
- **Actual implementation:** **Redroid** (Docker-based Android container)
- **Status:** Redroid is operational and working ✅

### History

1. **Started with:** Waydroid (LXC-based Android)
   - Encountered kernel 6.8 binder compatibility issues
   - Persistent zygote crashes
   - Not suitable for Oracle Cloud ARM

2. **Switched to:** Redroid (Docker-based Android)
   - Docker-based, simpler deployment
   - Better ARM64 support
   - No kernel binder dependencies
   - **Currently operational** ✅

### Current Status

- ✅ **Redroid container running**
- ✅ **ADB access working** (port 5555)
- ✅ **VNC access working** (port 5900)
- ⚠️ Virtual devices pending kernel 6.8 compatibility

### For GitHub Repo

**Recommended name:** `redroid-cloud-phone`

**Alternative:** Keep `waydroid-cloud-phone` but clarify in README that it uses Redroid.

### Documentation

All current documentation refers to **Redroid** as the implementation. The directory name `waydroid-cloud-phone` is historical and can be kept for continuity, but the actual system uses **Redroid**.

---

## Summary

- **Implementation:** Redroid ✅
- **Directory name:** `waydroid-cloud-phone` (legacy, OK to keep)
- **GitHub repo name:** `redroid-cloud-phone` (recommended)
- **Status:** Redroid operational ✅


