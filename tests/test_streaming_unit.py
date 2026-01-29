#!/usr/bin/env python3
"""
Unit tests for streaming components.

Tests configuration, service status, and device availability.
"""

import os
import subprocess
import pytest

# Configuration from environment
VM_HOST = os.environ.get("VM_HOST", "132.226.155.1")
SSH_USER = os.environ.get("SSH_USER", "ubuntu")


def ssh_cmd(cmd: str, timeout: int = 30) -> tuple:
    """Run command via SSH and return (returncode, stdout, stderr)."""
    full_cmd = [
        "ssh", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=no",
        f"{SSH_USER}@{VM_HOST}", cmd
    ]
    try:
        result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out"


class TestNginxRtmpUnit:
    """Unit tests for nginx-rtmp service."""

    def test_nginx_rtmp_service_exists(self):
        """Verify nginx-rtmp systemd service file exists."""
        code, out, _ = ssh_cmd("test -f /etc/systemd/system/nginx-rtmp.service && echo exists")
        assert code == 0 and "exists" in out, "nginx-rtmp.service not found"

    def test_nginx_rtmp_config_exists(self):
        """Verify nginx-rtmp config file exists."""
        code, out, _ = ssh_cmd("test -f /etc/nginx/nginx.conf && echo exists")
        assert code == 0 and "exists" in out, "nginx.conf not found"

    def test_nginx_rtmp_config_has_rtmp_block(self):
        """Verify nginx config contains RTMP block."""
        code, out, _ = ssh_cmd("grep -c 'rtmp {' /etc/nginx/nginx.conf")
        assert code == 0 and int(out) >= 1, "RTMP block not found in nginx.conf"

    def test_nginx_rtmp_listens_on_1935(self):
        """Verify nginx config listens on port 1935."""
        code, out, _ = ssh_cmd("grep 'listen 1935' /etc/nginx/nginx.conf")
        assert code == 0, "Port 1935 not configured in nginx.conf"


class TestFfmpegBridgeUnit:
    """Unit tests for ffmpeg bridge service."""

    def test_ffmpeg_bridge_script_exists(self):
        """Verify ffmpeg-bridge.sh script exists."""
        code, out, _ = ssh_cmd("test -f /opt/redroid-scripts/ffmpeg-bridge.sh -o -f /opt/waydroid-scripts/ffmpeg-bridge.sh && echo exists")
        assert code == 0 and "exists" in out, "ffmpeg-bridge.sh not found"

    def test_ffmpeg_bridge_service_exists(self):
        """Verify ffmpeg-bridge systemd service file exists."""
        code, out, _ = ssh_cmd("test -f /etc/systemd/system/ffmpeg-bridge.service && echo exists")
        assert code == 0 and "exists" in out, "ffmpeg-bridge.service not found"

    def test_ffmpeg_installed(self):
        """Verify ffmpeg is installed."""
        code, out, _ = ssh_cmd("which ffmpeg")
        assert code == 0 and "ffmpeg" in out, "ffmpeg not installed"

    def test_ffprobe_installed(self):
        """Verify ffprobe is installed."""
        code, out, _ = ssh_cmd("which ffprobe")
        assert code == 0 and "ffprobe" in out, "ffprobe not installed"


class TestVirtualDevicesUnit:
    """Unit tests for virtual video/audio devices."""

    def test_v4l2loopback_module_loaded(self):
        """Verify v4l2loopback kernel module is loaded."""
        code, out, _ = ssh_cmd("lsmod | grep v4l2loopback")
        assert code == 0 and "v4l2loopback" in out, "v4l2loopback module not loaded"

    def test_video42_device_exists(self):
        """Verify /dev/video42 exists."""
        code, out, _ = ssh_cmd("test -e /dev/video42 && echo exists")
        assert code == 0 and "exists" in out, "/dev/video42 not found"

    def test_snd_aloop_module_loaded(self):
        """Verify snd-aloop kernel module is loaded."""
        code, out, _ = ssh_cmd("lsmod | grep snd_aloop")
        assert code == 0 and "snd_aloop" in out, "snd-aloop module not loaded"

    def test_alsa_loopback_device_exists(self):
        """Verify ALSA Loopback device exists."""
        code, out, _ = ssh_cmd("aplay -l | grep Loopback")
        assert code == 0 and "Loopback" in out, "ALSA Loopback device not found"


class TestRedroidUnit:
    """Unit tests for Redroid container."""

    def test_docker_installed(self):
        """Verify Docker is installed."""
        code, out, _ = ssh_cmd("which docker")
        assert code == 0 and "docker" in out, "Docker not installed"

    def test_redroid_container_exists(self):
        """Verify Redroid container exists."""
        code, out, _ = ssh_cmd("sudo docker ps -a --format '{{.Names}}' | grep -x redroid")
        assert code == 0 and "redroid" in out, "Redroid container not found"

    def test_redroid_container_running(self):
        """Verify Redroid container is running."""
        code, out, _ = ssh_cmd("sudo docker ps --format '{{.Names}}' | grep -x redroid")
        assert code == 0 and "redroid" in out, "Redroid container not running"

    def test_redroid_adb_port_exposed(self):
        """Verify Redroid exposes ADB port 5555."""
        code, out, _ = ssh_cmd("sudo docker port redroid 5555")
        assert code == 0 and "5555" in out, "ADB port 5555 not exposed"

    def test_video42_mounted_in_container(self):
        """Verify /dev/video42 is accessible in Redroid container."""
        code, out, _ = ssh_cmd("sudo docker exec redroid ls -la /dev/video42 2>/dev/null")
        assert code == 0 and "video42" in out, "/dev/video42 not mounted in container"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
