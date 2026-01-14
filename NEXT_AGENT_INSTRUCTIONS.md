# Instructions for Next Agent / User

## 🛑 Blockers Identified
1. **Missing SSH Keys**: The `~/.ssh/waydroid_oci` key is required to access the existing instance (`137.131.52.69`) or create new ones. This key is gitignored. **You must obtain this key to proceed with cloud operations.**
2. **Kernel Compatibility**: The current instance (`137.131.52.69`) runs Ubuntu 22.04 with Kernel 6.8. Virtual devices (`v4l2loopback`) **do not work** on this kernel.

## ✅ Updates Made
1. **Scripts Refactored**:
   - `scripts/setup-redroid-virtual-devices.sh`: 
     - Now checks for Kernel 6.8+ and fails with a clear error advising to use Ubuntu 20.04.
     - Now automatically installs Docker if missing (ready for fresh instances).
   - `scripts/test-redroid-complete.sh`:
     - Updated default IP to `137.131.52.69`.

## 🚀 Next Steps (Once Keys Are Available)

### 1. Create Ubuntu 20.04 Instance
Use the existing script to create a compatible instance (Kernel 5.x):
```bash
./scripts/create-ubuntu-20-instance.sh my-redroid-node
```

### 2. Setup Redroid with Virtual Devices
Run the refactored setup script on the new instance:
```bash
./scripts/setup-redroid-virtual-devices.sh <NEW_INSTANCE_IP>
```
*This will now verify the kernel, install Docker, compile v4l2loopback, and start Redroid.*

### 3. Verify Functionality
Run the complete test suite:
```bash
./scripts/test-redroid-complete.sh <NEW_INSTANCE_IP>
```

## 📋 Goal Status
- **Remote Access (ADB/VNC)**: Functionality achieved on current instance, but blocked by missing keys for verification.
- **Virtual Devices**: Blocked by Kernel 6.8. Solution (Ubuntu 20.04) is prepared via script updates.
- **Automation**: Scripts are now more robust and self-contained.
