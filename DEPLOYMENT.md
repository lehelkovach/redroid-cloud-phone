# Cloud Phone Deployment Guide

This guide covers all deployment options for Cloud Phone.

## Table of Contents

- [Deployment Options Overview](#deployment-options-overview)
- [Option 1: CLI Script Deployment](#option-1-cli-script-deployment)
- [Option 2: Golden Image Deployment](#option-2-golden-image-deployment)
- [Option 3: Terraform (Infrastructure as Code)](#option-3-terraform-infrastructure-as-code)
- [Option 4: Cloud-init (Manual Instance)](#option-4-cloud-init-manual-instance)
- [Option 5: Docker Compose (Existing Server)](#option-5-docker-compose-existing-server)
- [Option 6: Custom Docker Image](#option-6-custom-docker-image)
- [Fleet Deployment (Multiple Instances)](#fleet-deployment-multiple-instances)

---

## Deployment Options Overview

| Option | Speed | Customization | Best For |
|--------|-------|---------------|----------|
| CLI Script | ~15 min | High | Quick single deployment |
| Golden Image | ~2 min | Medium | Production, scaling |
| Terraform | ~15 min | High | IaC, reproducible |
| Cloud-init | ~15 min | Medium | OCI Console users |
| Docker Compose | ~5 min | High | Existing servers |
| Custom Image | ~30 min build | Very High | Custom requirements |

---

## Option 1: CLI Script Deployment

**Best for:** Quick deployments with full customization

### Prerequisites

```bash
# OCI CLI configured
oci --version

# Environment variables
export COMPARTMENT_ID="ocid1.compartment..."
export SUBNET_ID="ocid1.subnet..."
export AVAILABILITY_DOMAIN="AD-1"
export SSH_KEY_FILE="~/.ssh/id_rsa.pub"
```

### Basic Deployment

```bash
./scripts/deploy-cloud-phone.sh --name my-phone
```

### Full-Featured Deployment

```bash
./scripts/deploy-cloud-phone.sh \
  --name production-phone \
  --ocpus 4 \
  --memory 16 \
  --os-version 20.04 \
  --proxy socks5://proxy.example.com:1080 \
  --gps 37.7749,-122.4194 \
  --gapps \
  --api-token my-secret-token \
  --image redroid/redroid:11.0.0-latest
```

### From Configuration File

```bash
# Create config
cat > my-phone.json <<EOF
{
  "instance": {"name": "my-phone", "ocpus": 2, "memory_gb": 8},
  "redroid": {"image": "redroid/redroid:latest", "gapps": {"enabled": true}},
  "network": {"proxy": {"enabled": true, "type": "socks5", "host": "proxy", "port": 1080}},
  "location": {"enabled": true, "latitude": 37.7749, "longitude": -122.4194}
}
EOF

# Deploy
./scripts/deploy-cloud-phone.sh --config my-phone.json
```

---

## Option 2: Golden Image Deployment

**Best for:** Production deployments, rapid scaling

A golden image is a pre-configured OCI custom image. Deployment from golden image takes ~2 minutes vs ~15 minutes for fresh install.

### Step 1: Create Golden Image

First, deploy and configure a phone instance:

```bash
./scripts/deploy-cloud-phone.sh --name golden-source
```

Then create the golden image:

```bash
./scripts/create-golden-image.sh 129.146.x.x cloud-phone-golden-v1
```

### Step 2: Deploy from Golden Image

```bash
# Set the golden image OCID
export GOLDEN_IMAGE_ID="ocid1.image.oc1.phx.aaaa..."

# Deploy single instance
./scripts/deploy-from-golden.sh --name production-phone-1

# Deploy with customization
./scripts/deploy-from-golden.sh \
  --name production-phone-2 \
  --proxy socks5://proxy:1080 \
  --gps 40.7128,-74.0060

# Deploy multiple instances
for i in 1 2 3; do
  ./scripts/deploy-from-golden.sh --name phone-$i &
done
wait
```

---

## Option 3: Terraform (Infrastructure as Code)

**Best for:** Reproducible deployments, GitOps, infrastructure management

### Setup

```bash
cd terraform/

# Copy example variables
cp terraform.tfvars.example terraform.tfvars

# Edit with your values
nano terraform.tfvars
```

### terraform.tfvars Example

```hcl
# OCI Authentication
tenancy_ocid     = "ocid1.tenancy.oc1..aaaa..."
user_ocid        = "ocid1.user.oc1..aaaa..."
fingerprint      = "aa:bb:cc:dd:ee:ff:..."
private_key_path = "~/.oci/oci_api_key.pem"
region           = "us-phoenix-1"

# Instance
compartment_ocid   = "ocid1.compartment.oc1..aaaa..."
subnet_ocid        = "ocid1.subnet.oc1.phx.aaaa..."
instance_name      = "cloud-phone"
instance_count     = 1
instance_ocpus     = 2
instance_memory_gb = 8
ubuntu_version     = "20.04"

# SSH
ssh_public_key = "ssh-rsa AAAA..."

# Features
proxy_enabled = true
proxy_type    = "socks5"
proxy_host    = "proxy.example.com"
proxy_port    = 1080

gps_enabled   = true
gps_latitude  = 37.7749
gps_longitude = -122.4194
```

### Deploy

```bash
# Initialize
terraform init

# Preview
terraform plan

# Deploy
terraform apply

# Deploy multiple instances
terraform apply -var="instance_count=5"

# Destroy
terraform destroy
```

### Outputs

```bash
# Get connection info
terraform output connection_commands
```

---

## Option 4: Cloud-init (Manual Instance)

**Best for:** OCI Console users, simple deployments

### Via OCI Console

1. Go to **Compute → Instances → Create Instance**
2. Select **Ubuntu 20.04** (for virtual device support)
3. Select **VM.Standard.A1.Flex** shape
4. Configure **2 OCPUs, 8GB RAM**
5. In **Advanced Options → Management**, paste `cloud-init.yaml` contents
6. Add SSH key and create

### Via OCI CLI with User Data

```bash
oci compute instance launch \
  --compartment-id "$COMPARTMENT_ID" \
  --availability-domain "$AD" \
  --shape "VM.Standard.A1.Flex" \
  --shape-config '{"ocpus":2,"memoryInGBs":8}' \
  --image-id "$IMAGE_ID" \
  --subnet-id "$SUBNET_ID" \
  --ssh-authorized-keys-file ~/.ssh/id_rsa.pub \
  --user-data-file cloud-init.yaml \
  --display-name "cloud-phone" \
  --assign-public-ip true
```

### Custom Metadata (Optional)

Pass configuration via instance metadata:

```bash
--metadata '{"cloud_phone_mode":"redroid","proxy_url":"socks5://proxy:1080","gps_coords":"37.7749,-122.4194"}'
```

---

## Option 5: Docker Compose (Existing Server)

**Best for:** Deploying on existing ARM64 servers

### Prerequisites

- ARM64 server (physical or VM)
- Docker and Docker Compose installed
- Ubuntu 20.04 recommended for virtual devices

### Deploy

```bash
cd docker/

# Basic deployment
docker-compose up -d

# With custom image
REDROID_IMAGE=my-registry.com/cloud-phone:v1 docker-compose up -d

# With proxy
docker-compose --profile proxy up -d

# With streaming
docker-compose --profile streaming up -d

# Multiple instances (requires port adjustment)
docker-compose up -d --scale redroid=3
```

### Configuration

Edit environment variables in docker-compose.yml or use .env file:

```bash
cat > .env <<EOF
REDROID_IMAGE=redroid/redroid:latest
REDROID_WIDTH=1920
REDROID_HEIGHT=1080
ADB_PORT=5555
VNC_PORT=5900
API_PORT=8080
API_TOKEN=my-secret
PROXY_URL=socks5://proxy:1080
EOF

docker-compose up -d
```

---

## Option 6: Custom Docker Image

**Best for:** Pre-configured deployments, custom apps

### Build Custom Image

```bash
cd docker/

# Basic build
./build.sh

# With specific Android version
./build.sh --android 11

# With GApps (place GApps files in gapps/ directory first)
./build.sh --gapps

# Build and push to registry
./build.sh --push myregistry.com/cloud-phone --tag v1.0
```

### Pre-install Apps

Place APK files in `docker/apps/` before building:

```bash
mkdir -p docker/apps
cp my-app.apk docker/apps/
./build.sh
```

### Use Custom Image

```bash
# Via script
./scripts/deploy-cloud-phone.sh --image myregistry.com/cloud-phone:v1.0

# Via Terraform
terraform apply -var="golden_image_ocid=" -var="redroid_image=myregistry.com/cloud-phone:v1.0"

# Via Docker Compose
REDROID_IMAGE=myregistry.com/cloud-phone:v1.0 docker-compose up -d
```

---

## Fleet Deployment (Multiple Instances)

### CLI Script Fleet

```bash
# Deploy 10 instances with different proxies
for i in {1..10}; do
  ./scripts/deploy-cloud-phone.sh \
    --name "phone-$i" \
    --proxy "socks5://proxy$i.example.com:1080" \
    &
done
wait
```

### Terraform Fleet

```hcl
# In terraform.tfvars
instance_count = 10
```

```bash
terraform apply
```

### Golden Image Fleet

```bash
export GOLDEN_IMAGE_ID="ocid1.image..."

# Parallel deployment
for i in {1..10}; do
  ./scripts/deploy-from-golden.sh --name "phone-$i" &
done
wait
```

### Monitoring Fleet

```bash
# Check all instances
for ip in $(cat instance-ips.txt); do
  echo "=== $ip ==="
  ssh -i key ubuntu@$ip 'sudo /opt/waydroid-scripts/health-check.sh'
done
```

---

## Post-Deployment Configuration

### Set Proxy

```bash
# Via API
curl -X POST http://localhost:8080/proxy \
  -H "Content-Type: application/json" \
  -d '{"enabled":true,"type":"socks5","host":"proxy","port":1080}'

# Via script
ssh ubuntu@IP 'sudo /opt/waydroid-scripts/proxy-control.sh enable socks5 proxy 1080'
```

### Set GPS

```bash
# Via API
curl -X POST http://localhost:8080/location \
  -d '{"enabled":true,"latitude":37.7749,"longitude":-122.4194}'
```

### Install Apps

```bash
# Via API
curl -X POST http://localhost:8080/adb/install -F "file=@app.apk"

# Via ADB
adb connect IP:5555
adb install app.apk
```

---

## Troubleshooting

### Check Deployment Status

```bash
# Cloud-init deployment
ssh ubuntu@IP 'tail -f /var/log/cloud-phone-setup.log'

# Check if complete
ssh ubuntu@IP 'ls /var/log/cloud-phone-setup-complete'

# Health check
ssh ubuntu@IP 'sudo /opt/waydroid-scripts/health-check.sh'
```

### Container Not Starting

```bash
ssh ubuntu@IP 'sudo docker logs redroid'
ssh ubuntu@IP 'sudo systemctl status docker'
```

### VNC Not Working

```bash
ssh ubuntu@IP 'sudo ss -tlnp | grep 5900'
ssh ubuntu@IP 'sudo docker exec redroid getprop | grep vnc'
```

---

## Cost Optimization

### Always Free Tier Limits

| Resource | Limit |
|----------|-------|
| OCPUs | 4 total (across all instances) |
| Memory | 24GB total |
| Boot Volume | 200GB total |
| Outbound Data | 10TB/month |

### Recommended Configurations

| Use Case | OCPUs | Memory | Instances |
|----------|-------|--------|-----------|
| Development | 1 | 6GB | 1 |
| Production | 2 | 8GB | 1 |
| Fleet (4 phones) | 1 | 6GB | 4 |

---

## Security Best Practices

1. **Use SSH tunnels** for VNC and API access
2. **Set API tokens** for production deployments
3. **Use private subnets** where possible
4. **Rotate credentials** regularly
5. **Monitor access logs**
6. **Use golden images** to ensure consistent configuration
