# Quick Start for New Agent

## 1. Verify Current State
```bash
# Check instance
oci compute instance get --instance-id ocid1.instance.oc1.phx.anyhqljrgmifkaqclk7h23un7agzbd6zay7muuqkoxbhm4xgxnsqsdt5w2eq --query 'data."lifecycle-state"'

# Check Redroid
ssh -i ~/.ssh/waydroid_oci ubuntu@137.131.52.69 'sudo docker ps | grep redroid'

# Run tests
./scripts/test-redroid-full.sh 137.131.52.69
```

## 2. Key Information
- **Instance IP:** 137.131.52.69
- **SSH Key:** ~/.ssh/waydroid_oci
- **VNC Password:** redroid
- **Status:** ✅ Operational

## 3. Read Full Handoff
See `HANDOFF.md` for complete details.

## 4. Next Steps
1. Install ADB: `sudo apt-get install android-tools-adb`
2. Test VNC: `ssh -L 5900:localhost:5900 ubuntu@137.131.52.69 -N` then `vncviewer localhost:5900`
3. Test ADB: `adb connect 137.131.52.69:5555`
4. Address virtual devices (kernel 6.8 compatibility)
