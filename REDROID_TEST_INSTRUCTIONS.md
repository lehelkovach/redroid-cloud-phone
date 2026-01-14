# Redroid Test Instructions

## Current Status

**Instance:** Not accessible (connection timeout)  
**Next Step:** Wait for instance to come online, then run test script

---

## Quick Start

### 1. Check Instance Status

```bash
./scripts/check-instance.sh
```

Or manually:
```bash
ssh -i ~/.ssh/waydroid_oci ubuntu@161.153.55.58 "echo 'Connected'"
```

### 2. Run Redroid Test

Once instance is accessible:

```bash
./scripts/test-redroid-complete.sh
```

Or with custom IP:
```bash
./scripts/test-redroid-complete.sh YOUR_INSTANCE_IP
```

---

## What the Test Script Does

1. **Checks Docker** - Installs if needed
2. **Loads Virtual Devices** - v4l2loopback and snd-aloop modules
3. **Pulls Redroid Image** - Downloads latest Redroid Docker image
4. **Starts Container** - With device passthrough for `/dev/video42` and `/dev/snd`
5. **Enables ADB** - Sets up ADB over network (port 5555)
6. **Checks Device Visibility** - Verifies if Android can see virtual devices
7. **Tests Connection** - ADB and VNC access

---

## Expected Results

### Success Indicators:

✅ Container starts and stays running  
✅ Android boots successfully  
✅ ADB connection works (`adb devices` shows device)  
✅ VNC access works (can see Android UI on port 5900)  
✅ Virtual devices visible in container (`/dev/video42`, `/dev/snd`)  
✅ Android can see camera/audio devices  

### Failure Indicators:

❌ Container crashes or won't start  
❌ Binderfs errors in logs  
❌ Android doesn't boot  
❌ ADB connection fails  
❌ Virtual devices not visible in container  
❌ Android can't see camera/audio  

---

## Manual Testing Steps

If the script completes but you want to verify manually:

### 1. Check Container Status

```bash
ssh -i ~/.ssh/waydroid_oci ubuntu@161.153.55.58
sudo docker ps -a | grep redroid
sudo docker logs redroid
```

### 2. Test ADB Connection

From your local machine:
```bash
adb connect 161.153.55.58:5555
adb devices
adb shell getprop ro.build.version.release
```

### 3. Test VNC Connection

```bash
vncviewer 161.153.55.58:5900
# Password: redroid
```

### 4. Check Virtual Devices in Container

```bash
ssh -i ~/.ssh/waydroid_oci ubuntu@161.153.55.58
sudo docker exec redroid ls -la /dev/video*
sudo docker exec redroid ls -la /dev/snd/
```

### 5. Check Android Camera/Audio

```bash
adb shell dumpsys media.camera | grep -i camera
adb shell dumpsys audio | grep -i input
```

---

## Troubleshooting

### Container Won't Start

**Check logs:**
```bash
sudo docker logs redroid
```

**Common issues:**
- Binderfs errors → Kernel compatibility issue
- Device passthrough fails → Try without `--device` flags
- Port conflicts → Stop other services using ports 5555/5900

### Android Doesn't Boot

**Wait longer:**
- Android boot can take 30-60 seconds
- Check logs: `sudo docker logs -f redroid`

**Check resources:**
```bash
free -h
df -h
```

### Virtual Devices Not Visible

**Check host devices:**
```bash
ls -la /dev/video42
ls -la /dev/snd/
```

**Check container devices:**
```bash
sudo docker exec redroid ls -la /dev/video*
sudo docker exec redroid ls -la /dev/snd/
```

**If not visible:**
- Device passthrough may not work with Redroid
- May need to use different approach
- Consider Waydroid instead (documented virtual device support)

### ADB Connection Fails

**Check ADB is enabled:**
```bash
sudo docker exec redroid getprop service.adb.tcp.port
sudo docker exec redroid getprop init.svc.adbd
```

**Manually enable:**
```bash
sudo docker exec redroid setprop service.adb.tcp.port 5555
sudo docker exec redroid start adbd
```

---

## Next Steps After Testing

### If Redroid Works with Virtual Devices:

1. ✅ Migrate from Waydroid to Redroid
2. ✅ Update deployment scripts
3. ✅ Update documentation
4. ✅ Test full pipeline (RTMP → FFmpeg → virtual devices → Android)

### If Redroid Works BUT Virtual Devices Don't:

1. ⚠️ Continue Waydroid debugging (has documented virtual device support)
2. ⚠️ Try alternative device passthrough methods
3. ⚠️ Consider hybrid approach (Redroid for apps, Waydroid for media)

### If Redroid Doesn't Work:

1. ❌ Continue Waydroid debugging
2. ❌ Check binderfs compatibility
3. ❌ Consider kernel downgrade/upgrade
4. ❌ Consider commercial solutions (Genymotion)

---

## Files Created

- `scripts/test-redroid-complete.sh` - Complete Redroid test script
- `scripts/check-instance.sh` - Quick instance connectivity check
- `REDROID_TEST_INSTRUCTIONS.md` - This file

---

## When Instance is Accessible

Run:
```bash
./scripts/test-redroid-complete.sh
```

Then check the output for:
- ✅ Container running
- ✅ Android booted
- ✅ ADB working
- ✅ Virtual devices visible
- ✅ Android sees camera/audio








