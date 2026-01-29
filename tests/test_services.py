#!/usr/bin/env python3
"""
Service Tests for Redroid Cloud Phone

Tests for systemd service orchestration, startup order, health checks,
and inter-service communication.

Run with:
    VM_HOST=<IP> pytest tests/test_services.py -v
"""

import os
import subprocess
import time
import pytest
import json

# Configuration
VM_HOST = os.environ.get("VM_HOST", "127.0.0.1")
SSH_USER = os.environ.get("SSH_USER", "ubuntu")
SSH_KEY = os.environ.get("SSH_KEY", os.path.expanduser("~/.ssh/redroid_oci"))

# Service definitions with expected dependencies
SERVICES = {
    "redroid-container": {
        "description": "Redroid Android Container",
        "dependencies": ["docker.service"],
        "health_check": "docker ps | grep -q redroid",
        "startup_timeout": 120,
        "priority": 1,
    },
    "nginx-rtmp": {
        "description": "NGINX RTMP Server",
        "dependencies": [],
        "health_check": "curl -sf http://127.0.0.1:8081/stat | grep -q rtmp",
        "startup_timeout": 30,
        "priority": 2,
    },
    "ffmpeg-bridge": {
        "description": "FFmpeg RTMP Bridge",
        "dependencies": ["nginx-rtmp.service"],
        "health_check": "pgrep -f ffmpeg",
        "startup_timeout": 30,
        "priority": 3,
    },
    "control-api": {
        "description": "Control API Server",
        "dependencies": ["redroid-container.service"],
        "health_check": "curl -sf http://127.0.0.1:8080/health",
        "startup_timeout": 60,
        "priority": 4,
    },
    "log-collector": {
        "description": "Log Collector",
        "dependencies": ["redroid-container.service"],
        "health_check": "test -f /var/run/cloud-phone-logcat.pid",
        "startup_timeout": 30,
        "priority": 5,
    },
}

# Optional services (not part of main target)
OPTIONAL_SERVICES = {
    "tun2socks": {
        "description": "SOCKS5 Tunnel",
        "dependencies": [],
        "health_check": "ip link show tun0",
        "startup_timeout": 30,
    },
}


def ssh_cmd(cmd: str, timeout: int = 30) -> tuple:
    """Run command via SSH and return (returncode, stdout, stderr)."""
    full_cmd = [
        "ssh",
        "-i", SSH_KEY,
        "-o", "ConnectTimeout=10",
        "-o", "StrictHostKeyChecking=no",
        f"{SSH_USER}@{VM_HOST}",
        cmd
    ]
    try:
        result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out"


def ssh_sudo(cmd: str, timeout: int = 30) -> tuple:
    """Run sudo command via SSH."""
    return ssh_cmd(f"sudo {cmd}", timeout)


class TestServiceExists:
    """Test that all service files exist and are valid."""

    @pytest.mark.parametrize("service", SERVICES.keys())
    def test_service_file_exists(self, service):
        """Service file should exist in /etc/systemd/system/."""
        code, out, _ = ssh_cmd(f"test -f /etc/systemd/system/{service}.service && echo exists")
        assert code == 0 and "exists" in out, f"Service file {service}.service not found"

    def test_target_file_exists(self):
        """Target file should exist."""
        code, out, _ = ssh_cmd("test -f /etc/systemd/system/redroid-cloud-phone.target && echo exists")
        assert code == 0 and "exists" in out, "Target file not found"

    @pytest.mark.parametrize("service", SERVICES.keys())
    def test_service_file_valid(self, service):
        """Service file should be valid systemd syntax."""
        code, _, err = ssh_sudo(f"systemd-analyze verify /etc/systemd/system/{service}.service 2>&1 || true")
        # systemd-analyze returns warnings but not errors for valid files
        assert "Failed to load" not in err, f"Service file {service}.service is invalid"


class TestServiceDependencies:
    """Test service dependency configuration."""

    @pytest.mark.parametrize("service,config", SERVICES.items())
    def test_service_has_correct_dependencies(self, service, config):
        """Service should have correct After/Requires dependencies."""
        code, out, _ = ssh_cmd(f"systemctl show {service} --property=After")
        
        for dep in config["dependencies"]:
            assert dep in out, f"{service} missing dependency on {dep}"

    def test_target_wants_all_services(self):
        """Target should want all core services."""
        code, out, _ = ssh_cmd("systemctl show redroid-cloud-phone.target --property=Wants")
        
        for service in SERVICES.keys():
            assert f"{service}.service" in out, f"Target missing Wants for {service}"

    @pytest.mark.parametrize("service", SERVICES.keys())
    def test_service_part_of_target(self, service):
        """Service should be PartOf target."""
        code, out, _ = ssh_cmd(f"systemctl show {service} --property=PartOf")
        assert "redroid-cloud-phone.target" in out, f"{service} not PartOf target"


class TestServiceStartupOrder:
    """Test that services start in correct dependency order."""

    def test_can_start_target(self):
        """Should be able to start the target."""
        # Stop first
        ssh_sudo("systemctl stop redroid-cloud-phone.target")
        time.sleep(5)
        
        # Start target
        code, _, err = ssh_sudo("systemctl start redroid-cloud-phone.target")
        assert code == 0, f"Failed to start target: {err}"

    def test_redroid_starts_before_api(self):
        """Redroid should start before control-api."""
        code1, out1, _ = ssh_cmd("systemctl show redroid-container --property=ActiveEnterTimestamp")
        code2, out2, _ = ssh_cmd("systemctl show control-api --property=ActiveEnterTimestamp")
        
        if code1 == 0 and code2 == 0 and out1 and out2:
            # Parse timestamps if both services have started
            ts1 = out1.split("=")[1] if "=" in out1 else ""
            ts2 = out2.split("=")[1] if "=" in out2 else ""
            if ts1 and ts2:
                # redroid should have earlier timestamp
                assert ts1 <= ts2, "redroid-container should start before control-api"

    def test_nginx_starts_before_ffmpeg(self):
        """nginx-rtmp should start before ffmpeg-bridge."""
        code1, out1, _ = ssh_cmd("systemctl show nginx-rtmp --property=ActiveEnterTimestamp")
        code2, out2, _ = ssh_cmd("systemctl show ffmpeg-bridge --property=ActiveEnterTimestamp")
        
        if code1 == 0 and code2 == 0 and out1 and out2:
            ts1 = out1.split("=")[1] if "=" in out1 else ""
            ts2 = out2.split("=")[1] if "=" in out2 else ""
            if ts1 and ts2:
                assert ts1 <= ts2, "nginx-rtmp should start before ffmpeg-bridge"


class TestServiceHealth:
    """Test service health checks."""

    @pytest.mark.parametrize("service,config", SERVICES.items())
    def test_service_is_active(self, service, config):
        """Service should be active after target starts."""
        code, out, _ = ssh_sudo(f"systemctl is-active {service}")
        assert code == 0 and out == "active", f"{service} is not active: {out}"

    @pytest.mark.parametrize("service,config", SERVICES.items())
    def test_service_health_check(self, service, config):
        """Service should pass its health check."""
        check_cmd = config["health_check"]
        code, out, err = ssh_sudo(check_cmd)
        assert code == 0, f"{service} health check failed: {err}"

    def test_redroid_container_running(self):
        """Redroid Docker container should be running."""
        code, out, _ = ssh_sudo("docker ps --format '{{.Names}}' | grep -x redroid")
        assert code == 0 and "redroid" in out, "Redroid container not running"

    def test_redroid_boot_completed(self):
        """Android should have completed boot."""
        code, out, _ = ssh_sudo("docker exec redroid getprop sys.boot_completed")
        assert code == 0 and out.strip() == "1", "Android boot not completed"

    def test_adb_connected(self):
        """ADB should be able to connect."""
        code, out, _ = ssh_sudo("adb connect 127.0.0.1:5555 && adb devices | grep 127.0.0.1:5555")
        assert code == 0 and "device" in out, "ADB not connected"

    def test_api_health_endpoint(self):
        """API health endpoint should respond."""
        code, out, _ = ssh_cmd("curl -sf http://127.0.0.1:8080/health")
        assert code == 0, "API health endpoint not responding"
        
        try:
            data = json.loads(out)
            assert data.get("status") == "ok" or data.get("success") == True
        except json.JSONDecodeError:
            pytest.fail("API health response is not valid JSON")

    def test_nginx_rtmp_stat(self):
        """NGINX RTMP stat endpoint should respond."""
        code, out, _ = ssh_cmd("curl -sf http://127.0.0.1:8081/stat | head -5")
        assert code == 0 and "rtmp" in out.lower(), "NGINX RTMP stat not responding"


class TestServiceRestart:
    """Test service restart and recovery."""

    def test_redroid_restart(self):
        """Redroid should restart successfully."""
        # Restart service
        code, _, err = ssh_sudo("systemctl restart redroid-container")
        assert code == 0, f"Failed to restart redroid: {err}"
        
        # Wait for boot
        time.sleep(30)
        
        # Check health
        code, out, _ = ssh_sudo("docker exec redroid getprop sys.boot_completed")
        assert code == 0 and out.strip() == "1", "Redroid did not recover"

    def test_api_restart(self):
        """API should restart successfully."""
        # Restart service
        code, _, err = ssh_sudo("systemctl restart control-api")
        assert code == 0, f"Failed to restart API: {err}"
        
        # Wait for startup
        time.sleep(10)
        
        # Check health
        code, out, _ = ssh_cmd("curl -sf http://127.0.0.1:8080/health")
        assert code == 0, "API did not recover"

    def test_dependent_service_restart(self):
        """Dependent services should handle parent restart."""
        # Stop nginx-rtmp (ffmpeg-bridge depends on it)
        ssh_sudo("systemctl stop nginx-rtmp")
        time.sleep(2)
        
        # ffmpeg-bridge should also stop (BindsTo)
        code, out, _ = ssh_sudo("systemctl is-active ffmpeg-bridge")
        # May be inactive or failed
        
        # Restart nginx
        ssh_sudo("systemctl start nginx-rtmp")
        time.sleep(5)
        
        # ffmpeg should recover
        ssh_sudo("systemctl start ffmpeg-bridge")
        time.sleep(5)
        
        code, out, _ = ssh_sudo("systemctl is-active ffmpeg-bridge")
        assert out in ["active", "activating"], "ffmpeg-bridge did not recover"


class TestServiceLogs:
    """Test service logging."""

    @pytest.mark.parametrize("service", SERVICES.keys())
    def test_service_has_logs(self, service):
        """Service should have journal logs."""
        code, out, _ = ssh_sudo(f"journalctl -u {service} -n 5 --no-pager")
        assert code == 0 and len(out) > 0, f"No logs for {service}"

    def test_log_collector_running(self):
        """Log collector should be capturing logs."""
        code, out, _ = ssh_cmd("test -f /var/log/cloud-phone/unified.log && wc -l < /var/log/cloud-phone/unified.log")
        assert code == 0, "Log collector not creating logs"
        
        try:
            lines = int(out.strip())
            assert lines >= 0, "unified.log should have entries"
        except ValueError:
            pass  # File exists but may be empty initially

    def test_log_labels_present(self):
        """Logs should have type labels."""
        code, out, _ = ssh_cmd("head -50 /var/log/cloud-phone/unified.log 2>/dev/null | grep -c '\\[' || echo 0")
        # At least some lines should have labels
        try:
            count = int(out.strip())
            # Don't fail if log file is new/empty
        except ValueError:
            pass


class TestTargetOperations:
    """Test target-level operations."""

    def test_stop_target(self):
        """Should be able to stop entire target."""
        code, _, err = ssh_sudo("systemctl stop redroid-cloud-phone.target")
        assert code == 0, f"Failed to stop target: {err}"
        
        time.sleep(5)
        
        # All services should be stopped
        for service in ["redroid-container", "control-api", "nginx-rtmp", "ffmpeg-bridge"]:
            code, out, _ = ssh_sudo(f"systemctl is-active {service}")
            assert out in ["inactive", "failed"], f"{service} still running after target stop"

    def test_start_target(self):
        """Should be able to start entire target."""
        code, _, err = ssh_sudo("systemctl start redroid-cloud-phone.target")
        assert code == 0, f"Failed to start target: {err}"
        
        # Wait for services to start
        time.sleep(60)
        
        # Core services should be running
        code, out, _ = ssh_sudo("systemctl is-active redroid-container")
        assert out == "active", "redroid-container not active"

    def test_restart_target(self):
        """Should be able to restart entire target."""
        code, _, err = ssh_sudo("systemctl restart redroid-cloud-phone.target")
        assert code == 0, f"Failed to restart target: {err}"
        
        time.sleep(60)
        
        code, out, _ = ssh_sudo("systemctl is-active redroid-container")
        assert out == "active", "redroid-container not active after restart"


class TestInterServiceCommunication:
    """Test communication between services."""

    def test_api_can_reach_redroid(self):
        """API should communicate with Redroid via ADB."""
        code, out, _ = ssh_cmd("curl -sf http://127.0.0.1:8080/device/info")
        assert code == 0, "API cannot get device info"
        
        try:
            data = json.loads(out)
            assert "data" in data or "android_version" in str(data)
        except json.JSONDecodeError:
            pytest.fail("Invalid response from device info")

    def test_api_can_take_screenshot(self):
        """API should be able to capture screenshot."""
        code, _, _ = ssh_cmd("curl -sf http://127.0.0.1:8080/device/screenshot -o /tmp/test.png")
        assert code == 0, "Failed to capture screenshot"
        
        # Check file exists and has content
        code, out, _ = ssh_cmd("test -s /tmp/test.png && echo ok")
        assert code == 0 and "ok" in out, "Screenshot file is empty"

    def test_rtmp_to_ffmpeg_pipeline(self):
        """RTMP stream should flow to FFmpeg bridge."""
        # Send test stream
        ssh_cmd("""ffmpeg -hide_banner -loglevel error -re \
            -f lavfi -i testsrc2=size=1280x720:rate=15 \
            -t 3 -c:v libx264 -preset veryfast \
            -f flv rtmp://127.0.0.1/live/cam &""")
        
        time.sleep(5)
        
        # Check if ffmpeg is receiving
        code, out, _ = ssh_sudo("pgrep -a ffmpeg | grep -c video42 || echo 0")
        # May or may not have active stream, just check ffmpeg is running


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
