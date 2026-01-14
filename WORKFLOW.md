# Complete Deployment Workflow

This guide walks you through the complete process of setting up, testing, and creating a golden image.

> **Note (2026):** This workflow is **Waydroid-focused** (uses `install.sh` and `waydroid-cloud-phone.target`).
>
> If you’re using the **current Redroid** approach, prefer:
> - `sudo ./install-redroid.sh`
> - `sudo systemctl start redroid-cloud-phone.target`
> - `./scripts/test-redroid-full.sh <INSTANCE_IP>`

## Prerequisites

- OCI CLI installed and configured ✓
- SSH key created ✓
- Compartment ID configured ✓

## Step-by-Step Workflow

### Step 1: Setup Networking (First Time Only)

If you don't have a VCN and subnet yet:

```bash
./scripts/setup-networking.sh
```

This creates:
- A VCN (Virtual Cloud Network)
- A public subnet
- Internet Gateway
- Security rules for SSH (22) and RTMP (1935)

**Output**: You'll get a `SUBNET_ID` - save this!

### Step 2: Configure Subnet ID

Update `scripts/launch-fleet.sh` with your subnet ID:

```bash
# Edit the file
nano scripts/launch-fleet.sh

# Or set it via export
export SUBNET_ID="ocid1.subnet.oc1.phx.xxx"
```

### Step 3: Create a Test Instance

```bash
./scripts/create-instance.sh waydroid-test-1
```

This will:
- Find Ubuntu 22.04 ARM image
- Create an instance (2 OCPU, 8GB RAM)
- Assign public IP
- Wait for it to be running

**Output**: Instance OCID and Public IP

### Step 4: Deploy Waydroid

Wait 30-60 seconds for SSH to be ready, then:

```bash
./scripts/deploy-to-instance.sh <PUBLIC_IP>
```

This will:
- Upload the project files
- Run `install.sh` on the instance
- Reboot the instance (for kernel modules)
- Initialize Waydroid (downloads ~1GB, takes 5-10 min)

**Note**: The Waydroid initialization will prompt for GAPPS (1) or VANILLA (2). The script defaults to GAPPS.

### Step 5: Start Services

After deployment completes:

```bash
ssh -i ~/.ssh/waydroid_oci ubuntu@<PUBLIC_IP> 'sudo systemctl start waydroid-cloud-phone.target'
```

### Step 6: Test the Instance

```bash
./scripts/test-instance.sh <PUBLIC_IP>
```

This checks:
- All services are running
- API is responding
- ADB devices connected
- Health check passes

### Step 7: Create Golden Image

Once everything works:

```bash
./scripts/create-golden-image.sh <PUBLIC_IP> waydroid-cloud-phone-v1
```

This will:
- Prepare the instance (stop services, clean up)
- Shutdown the instance
- Create a custom image in OCI
- Wait for image to be available (10-20 minutes)

**Output**: Image OCID

### Step 8: Update Configuration

Update `scripts/launch-fleet.sh` with the image OCID:

```bash
# Edit launch-fleet.sh
nano scripts/launch-fleet.sh

# Set IMAGE_ID to the OCID from step 7
IMAGE_ID="ocid1.image.oc1.phx.xxx"
```

### Step 9: Launch Multiple Instances

Now you can launch as many instances as you want:

```bash
./scripts/launch-fleet.sh 2  # Launch 2 instances
```

Each new instance just needs:
```bash
ssh -i ~/.ssh/waydroid_oci ubuntu@<NEW_IP> 'sudo systemctl start waydroid-cloud-phone.target'
```

## Quick Reference

| Task | Command |
|------|---------|
| Setup networking | `./scripts/setup-networking.sh` |
| Create instance | `./scripts/create-instance.sh <name>` |
| Deploy waydroid | `./scripts/deploy-to-instance.sh <IP>` |
| Test instance | `./scripts/test-instance.sh <IP>` |
| Create image | `./scripts/create-golden-image.sh <IP> <name>` |
| Launch fleet | `./scripts/launch-fleet.sh <count>` |

## Troubleshooting

### Instance creation fails (out of capacity)
- Try a different availability domain
- Try a smaller shape temporarily
- Try again later (capacity changes)

### Deployment fails
- Check SSH connection: `ssh -i ~/.ssh/waydroid_oci ubuntu@<IP>`
- Check security list allows SSH (port 22)
- Verify instance is running: `oci compute instance get --instance-id <OCID>`

### Services won't start
- Check kernel modules: `lsmod | grep v4l2loopback`
- Check logs: `journalctl -u waydroid-container -f`
- Reboot if modules aren't loaded

## Next Steps After Golden Image

Once you have a golden image:
1. Update `launch-fleet.sh` with `IMAGE_ID`
2. Launch instances: `./scripts/launch-fleet.sh <count>`
3. Start services on each: `sudo systemctl start waydroid-cloud-phone.target`
4. Access via SSH tunnels (VNC, API)

