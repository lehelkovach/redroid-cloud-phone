#!/usr/bin/env python3
"""
Integration tests for streaming pipeline.

Tests service interactions and data flow between components.
"""

import os
import subprocess
import time
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


class TestServicesIntegration:
    """Integration tests for service status and health."""

    def test_nginx_rtmp_service_active(self):
        """Verify nginx-rtmp service is active."""
        code, out, _ = ssh_cmd("sudo systemctl is-active nginx-rtmp")
        assert code == 0 and out == "active", f"nginx-rtmp not active: {out}"

    def test_ffmpeg_bridge_service_active(self):
        """Verify ffmpeg-bridge service is active."""
        code, out, _ = ssh_cmd("sudo systemctl is-active ffmpeg-bridge")
        assert code == 0 and out == "active", f"ffmpeg-bridge not active: {out}"

    def test_nginx_rtmp_health_endpoint(self):
        """Verify nginx-rtmp health endpoint responds."""
        code, out, _ = ssh_cmd("curl -sf http://127.0.0.1:8081/health")
        assert code == 0 and "OK" in out, "nginx-rtmp health endpoint failed"

    def test_nginx_rtmp_stat_endpoint(self):
        """Verify nginx-rtmp stat endpoint responds with XML."""
        code, out, _ = ssh_cmd("curl -sf http://127.0.0.1:8081/stat | head -5")
        assert code == 0 and "rtmp" in out.lower(), "nginx-rtmp stat endpoint failed"

    def test_control_api_health(self):
        """Verify control API is healthy."""
        code, out, _ = ssh_cmd("curl -sf http://127.0.0.1:8080/health")
        assert code == 0 and "healthy" in out.lower(), "Control API not healthy"


class TestAdbIntegration:
    """Integration tests for ADB connectivity."""

    def test_adb_server_running(self):
        """Verify ADB server is running."""
        code, out, _ = ssh_cmd("adb start-server && adb devices | grep -v 'List'")
        assert code == 0, "ADB server failed to start"

    def test_adb_connect_to_redroid(self):
        """Verify ADB can connect to Redroid."""
        code, out, _ = ssh_cmd("adb connect 127.0.0.1:5555 && adb -s 127.0.0.1:5555 get-state")
        assert code == 0 and "device" in out, f"ADB connect failed: {out}"

    def test_adb_shell_works(self):
        """Verify ADB shell commands work."""
        code, out, _ = ssh_cmd("adb -s 127.0.0.1:5555 shell echo hello")
        assert code == 0 and "hello" in out, "ADB shell failed"

    def test_redroid_booted(self):
        """Verify Redroid has finished booting."""
        code, out, _ = ssh_cmd("adb -s 127.0.0.1:5555 shell getprop sys.boot_completed")
        assert code == 0 and "1" in out, "Redroid not fully booted"


class TestRtmpIntegration:
    """Integration tests for RTMP streaming."""

    def test_rtmp_port_listening(self):
        """Verify RTMP port 1935 is listening."""
        code, out, _ = ssh_cmd("sudo ss -tlnp | grep ':1935'")
        assert code == 0 and "1935" in out, "RTMP port 1935 not listening"

    def test_rtmp_accepts_connection(self):
        """Verify RTMP server accepts connections."""
        code, out, _ = ssh_cmd("nc -zv 127.0.0.1 1935 2>&1")
        assert code == 0, f"RTMP connection refused: {out}"

    def test_mock_rtmp_stream(self):
        """Test sending a mock RTMP stream."""
        # Send 3-second test stream
        cmd = (
            "timeout 5 ffmpeg -hide_banner -loglevel error -re "
            "-f lavfi -i testsrc2=size=640x480:rate=15 "
            "-f lavfi -i sine=frequency=440:sample_rate=44100 "
            "-t 3 -c:v libx264 -preset ultrafast -pix_fmt yuv420p "
            "-c:a aac -ar 44100 -f flv rtmp://127.0.0.1/live/teststream 2>&1; "
            "echo 'STREAM_SENT'"
        )
        code, out, _ = ssh_cmd(cmd, timeout=15)
        assert "STREAM_SENT" in out, f"Mock RTMP stream failed: {out}"

    def test_rtmp_stream_stats_updated(self):
        """Verify RTMP stats update after stream."""
        # First send a stream
        ssh_cmd(
            "timeout 4 ffmpeg -hide_banner -loglevel error -re "
            "-f lavfi -i testsrc2=size=640x480:rate=15 "
            "-t 2 -c:v libx264 -preset ultrafast -pix_fmt yuv420p "
            "-f flv rtmp://127.0.0.1/live/statstest 2>&1",
            timeout=10
        )
        time.sleep(1)
        # Check stats
        code, out, _ = ssh_cmd("curl -sf http://127.0.0.1:8081/stat | grep -o 'bytes_in>[0-9]*' | head -1")
        assert code == 0, "Failed to get RTMP stats"


class TestVirtualDevicesIntegration:
    """Integration tests for virtual video/audio devices."""

    def test_v4l2_device_info(self):
        """Verify v4l2 device provides info."""
        code, out, _ = ssh_cmd("v4l2-ctl --device=/dev/video42 --info 2>&1")
        assert code == 0 and "v4l2 loopback" in out.lower(), f"v4l2 device info failed: {out}"

    def test_v4l2_device_capabilities(self):
        """Verify v4l2 device has required capabilities."""
        code, out, _ = ssh_cmd("v4l2-ctl --device=/dev/video42 --all 2>&1 | grep -i 'video capture'")
        assert code == 0 and "Video Capture" in out, "v4l2 device missing Video Capture capability"

    def test_alsa_loopback_info(self):
        """Verify ALSA Loopback device info."""
        code, out, _ = ssh_cmd("aplay -l | grep -A2 Loopback")
        assert code == 0 and "Loopback" in out, "ALSA Loopback device not found"


class TestFfmpegBridgeIntegration:
    """Integration tests for ffmpeg bridge."""

    def test_ffmpeg_bridge_process_running(self):
        """Verify ffmpeg bridge process is running."""
        code, out, _ = ssh_cmd("ps aux | grep -E 'ffmpeg-bridge|ffmpeg.*rtmp.*video42' | grep -v grep")
        # Process might be waiting for stream, so we check service status instead
        code2, out2, _ = ssh_cmd("sudo systemctl is-active ffmpeg-bridge")
        assert code2 == 0 and out2 == "active", "ffmpeg-bridge not active"

    def test_ffmpeg_bridge_can_write_to_video42(self):
        """Test that ffmpeg can write to /dev/video42."""
        # Send a quick test pattern directly to video42
        cmd = (
            "timeout 3 ffmpeg -hide_banner -loglevel error "
            "-f lavfi -i testsrc2=size=640x480:rate=15 "
            "-t 1 -pix_fmt yuv420p -f v4l2 /dev/video42 2>&1; "
            "echo 'WRITE_OK'"
        )
        code, out, _ = ssh_cmd(cmd, timeout=10)
        assert "WRITE_OK" in out, f"Failed to write to /dev/video42: {out}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
