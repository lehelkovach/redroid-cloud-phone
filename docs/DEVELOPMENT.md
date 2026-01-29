# Development Guide

This guide covers development workflow, testing, and contribution guidelines.

## Development Workflow

### Recommended Approach (Hybrid)

Use a combination of cloud/remote development and local testing:

| Task | Best Environment |
|------|------------------|
| Code editing, scripting | Cloud Agent / Local IDE |
| Remote testing | Cloud Agent (SSH) |
| Visual testing (VNC) | Local machine |
| ADB interactive testing | Local machine |

### Development Cycle

```bash
# 1. Make code changes locally or via agent

# 2. Deploy to instance
scp -r scripts/ ubuntu@<IP>:/opt/redroid-scripts/
ssh ubuntu@<IP> 'sudo systemctl restart control-api'

# 3. Run automated tests
VM_HOST=<IP> pytest tests/ -v

# 4. Visual verification (local)
ssh -L 5900:localhost:5900 ubuntu@<IP> -N
vncviewer localhost:5900

# 5. Fix issues and repeat
```

## Testing

### Test Structure

```
tests/
├── test_streaming_unit.py        # Unit tests (17 tests)
├── test_streaming_integration.py # Integration tests (18 tests)
├── test_streaming_e2e.py         # End-to-end tests (20 tests)
├── test_virtual_camera.py        # Virtual camera tests (20 tests)
├── test_agent_api.py             # API tests
├── test_connectivity.py          # Network connectivity tests
└── test_orchestrator_*.py        # Orchestrator tests
```

### Running Tests

```bash
# Setup
python3 -m venv .venv
source .venv/bin/activate
pip install pytest

# Set target VM
export VM_HOST=132.226.155.1

# Run all tests
pytest tests/ -v

# Run specific test files
pytest tests/test_streaming_unit.py -v
pytest tests/test_virtual_camera.py -v

# Run with output
pytest tests/ -v --tb=short
```

### Test Categories

| Category | File | Tests | Description |
|----------|------|-------|-------------|
| Unit | `test_streaming_unit.py` | 17 | Service configs, files exist |
| Integration | `test_streaming_integration.py` | 18 | Services running, connected |
| E2E | `test_streaming_e2e.py` | 20 | Full pipeline verification |
| Virtual Camera | `test_virtual_camera.py` | 20 | Camera/audio device tests |

### Writing Tests

Tests connect to the VM via SSH and run commands:

```python
def ssh_cmd(cmd: str, timeout: int = 30) -> tuple:
    """Run command via SSH and return (returncode, stdout, stderr)."""
    full_cmd = [
        "ssh", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=no",
        f"{SSH_USER}@{VM_HOST}", cmd
    ]
    result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout.strip(), result.stderr.strip()

class TestExample:
    def test_service_running(self):
        code, out, _ = ssh_cmd("sudo systemctl is-active nginx-rtmp")
        assert code == 0 and out == "active"
```

## Git Workflow

### Branch Strategy

- `main` - Stable, production-ready code
- `develop` - Integration branch for features
- `feature/*` - New features
- `fix/*` - Bug fixes

### Commit Messages

```
<type>: <description>

[optional body]

Types:
- feat: New feature
- fix: Bug fix
- docs: Documentation
- test: Tests
- refactor: Code refactoring
- chore: Maintenance
```

### Pull Request Process

1. Create feature branch from `develop`
2. Make changes and add tests
3. Run full test suite: `pytest tests/ -v`
4. Submit PR to `develop`
5. After review, merge to `develop`
6. Periodically merge `develop` to `main`

## Local Development Setup

### Prerequisites

```bash
# Python 3.9+
python3 --version

# SSH access to OCI instance
ssh -i ~/.ssh/your_key ubuntu@<IP> 'echo ok'

# Optional: ADB for Android debugging
sudo apt install adb

# Optional: VNC client
sudo apt install tigervnc-viewer

# Optional: scrcpy for screen mirroring
sudo apt install scrcpy
```

### Environment Variables

```bash
# For tests
export VM_HOST=132.226.155.1
export SSH_USER=ubuntu

# For OCI deployment
export COMPARTMENT_ID="ocid1.compartment..."
export SUBNET_ID="ocid1.subnet..."
export AVAILABILITY_DOMAIN="AD-1"
```

## Debugging

### Check Service Status

```bash
ssh ubuntu@<IP> 'sudo systemctl status redroid-container nginx-rtmp ffmpeg-bridge control-api'
```

### View Logs

```bash
# Container logs
ssh ubuntu@<IP> 'sudo docker logs redroid --tail 100'

# Service logs
ssh ubuntu@<IP> 'sudo journalctl -u control-api -n 50'
ssh ubuntu@<IP> 'sudo journalctl -u ffmpeg-bridge -n 50'
```

### ADB Debugging

```bash
# Connect via SSH tunnel
ssh -L 5555:localhost:5555 ubuntu@<IP> -N &
adb connect localhost:5555
adb shell

# Common commands
adb shell dumpsys media.camera
adb shell getprop ro.build.version.release
adb shell pm list packages
```

## Code Style

- Python: Follow PEP 8
- Shell scripts: Use shellcheck
- YAML: 2-space indentation
- Markdown: Use proper headers and code blocks

## Documentation

- Keep README.md concise with links to docs/
- Update docs/ when adding features
- Include Mermaid diagrams for architecture
- Document all API endpoints
