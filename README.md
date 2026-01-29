# Redroid Cloud Phone

Run Android in the cloud with virtual camera/microphone support, controllable via REST API or ADB.

## Overview

This project deploys [Redroid](https://github.com/remote-android/redroid-doc) (Android in Docker) on Oracle Cloud Infrastructure (OCI) with:

- **Virtual Camera/Microphone** - Stream from OBS to Android via RTMP
- **REST API** - Programmatic control for automation/LLM agents
- **Multiple Viewing Options** - VNC, scrcpy, WebRTC
- **Proxy Support** - Route Android traffic through SOCKS5/HTTP proxy
- **GPS Spoofing** - Set fake location coordinates
- **Google Play** - Optional GApps installation

## Quick Start

### Prerequisites

- OCI account with Always Free tier (ARM instances)
- OCI CLI configured (`oci setup config`)
- SSH key pair

### Deploy

```bash
# Clone repository
git clone https://github.com/lehelkovach/redroid-cloud-phone.git
cd redroid-cloud-phone

# Set environment variables
export COMPARTMENT_ID="ocid1.compartment..."
export SUBNET_ID="ocid1.subnet..."
export AVAILABILITY_DOMAIN="AD-1"

# Deploy (Ubuntu 20.04 with Redroid)
./scripts/deploy-ubuntu20-redroid.sh my-cloud-phone

# Or with full options
./scripts/deploy-cloud-phone.sh \
  --name my-phone \
  --ocpus 2 \
  --memory 8 \
  --gapps \
  --proxy socks5://proxy:1080
```

### Connect

```bash
# SSH tunnel for secure access
ssh -L 5555:localhost:5555 -L 5900:localhost:5900 -L 8080:localhost:8080 ubuntu@<IP>

# View via scrcpy (install: sudo apt install scrcpy)
scrcpy -s localhost:5555

# Or via VNC
vncviewer localhost:5900  # password: redroid

# API health check
curl http://localhost:8080/health
```

### Stream from OBS

1. Configure OBS output:
   - Server: `rtmp://<INSTANCE_IP>/live`
   - Stream Key: `cam`
2. Start streaming
3. In Android, use VLC to view: `rtmp://127.0.0.1/live/cam`

## Documentation

| Document | Description |
|----------|-------------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System architecture, diagrams, data flow |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | All deployment options (CLI, Terraform, Golden Image) |
| [docs/FEATURES.md](docs/FEATURES.md) | Feature documentation (proxy, GPS, GApps, API) |
| [docs/QUICK_START.md](docs/QUICK_START.md) | Getting started guide |
| [docs/API_REFERENCE.md](docs/API_REFERENCE.md) | REST API documentation |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Common issues and solutions |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Development workflow, testing |
| [docs/CAMERA_HAL_FIX.md](docs/CAMERA_HAL_FIX.md) | Camera HAL limitation and workarounds |
| [docs/RESEARCH.md](docs/RESEARCH.md) | Research notes on alternatives |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | Version history and changes |

## Project Structure

```
redroid-cloud-phone/
├── api/                    # Control API server
│   ├── server.py          # Main Flask API
│   └── agent_api.py       # Agent coordination API
├── config/                 # Configuration files
│   ├── cloud-phone-config.example.json
│   ├── device-profiles/   # Anti-detection profiles
│   └── nginx-rtmp.conf    # RTMP server config
├── docker/                 # Docker build files
├── docs/                   # Documentation
├── orchestrator/           # Multi-instance orchestrator
├── scripts/                # Deployment and utility scripts
│   ├── deploy-cloud-phone.sh
│   ├── deploy-ubuntu20-redroid.sh
│   ├── ffmpeg-bridge.sh
│   ├── health-check.sh
│   └── ...
├── systemd/                # Systemd service files
├── terraform/              # Infrastructure as Code
├── tests/                  # Test suite
└── cloud-init.yaml         # Cloud-init configuration
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/status` | GET | Device status |
| `/device/screenshot` | GET | Capture screenshot |
| `/device/input` | POST | Send tap/swipe/text |
| `/adb/shell` | POST | Execute shell command |
| `/adb/install` | POST | Install APK |
| `/proxy` | GET/POST/DELETE | Proxy configuration |
| `/location` | GET/POST/DELETE | GPS spoofing |
| `/apps` | GET | List installed apps |
| `/apps/<pkg>/start` | POST | Launch app |

See [docs/API_REFERENCE.md](docs/API_REFERENCE.md) for complete API documentation.

## Testing

```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install pytest

# Run all tests
VM_HOST=<INSTANCE_IP> pytest tests/ -v

# Run specific test suites
pytest tests/test_streaming_unit.py -v      # Unit tests
pytest tests/test_streaming_integration.py -v  # Integration tests
pytest tests/test_streaming_e2e.py -v       # End-to-end tests
pytest tests/test_virtual_camera.py -v      # Virtual camera tests
```

## Known Limitations

1. **Camera HAL Missing** - Android apps cannot detect the virtual camera. Use VLC to view RTMP stream directly. See [docs/CAMERA_HAL_FIX.md](docs/CAMERA_HAL_FIX.md).

2. **Ubuntu 20.04 Required** - Kernel 5.x needed for v4l2loopback/snd-aloop modules.

3. **ARM Only** - OCI Always Free tier provides ARM (Ampere) instances. x86 requires paid instances.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Run tests: `pytest tests/ -v`
4. Submit a pull request

## License

MIT License - See LICENSE file for details.

## Acknowledgments

- [Redroid](https://github.com/remote-android/redroid-doc) - Android in Docker
- [v4l2loopback](https://github.com/umlaeute/v4l2loopback) - Virtual video device
- [nginx-rtmp-module](https://github.com/arut/nginx-rtmp-module) - RTMP server
