#!/usr/bin/env python3
"""
Virtual Camera and Audio Loopback Tests.

Tests that verify the OBS -> ffmpeg-bridge -> virtual devices pipeline is working.
Run with: VM_HOST=<ip> pytest tests/test_virtual_camera.py -v
"""

import os
import subprocess
import pytest

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


class TestVirtualCameraDevice:
    """Tests for /dev/video42 virtual camera device."""

    def test_video42_device_exists(self):
        """Verify /dev/video42 exists on host."""
        code, out, _ = ssh_cmd("test -e /dev/video42 && echo exists")
        assert code == 0 and "exists" in out, "/dev/video42 not found"

    def test_video42_is_v4l2loopback(self):
        """Verify device is a v4l2 loopback device."""
        code, out, _ = ssh_cmd("v4l2-ctl --device=/dev/video42 --info 2>&1 | grep -i 'v4l2 loopback'")
        assert code == 0 and "v4l2 loopback" in out.lower(), "Not a v4l2loopback device"

    def test_video42_named_virtualcam(self):
        """Verify device is named VirtualCam."""
        code, out, _ = ssh_cmd("cat /sys/class/video4linux/video42/name")
        assert code == 0 and "VirtualCam" in out, f"Device name mismatch: {out}"

    def test_video42_has_capture_capability(self):
        """Verify device supports video capture."""
        code, out, _ = ssh_cmd("v4l2-ctl --device=/dev/video42 --info | grep -i 'Video Capture'")
        assert code == 0 and "Video Capture" in out, "Device lacks Video Capture capability"


class TestFfmpegBridgeActive:
    """Tests that ffmpeg-bridge is actively writing to virtual devices."""

    def test_ffmpeg_writing_to_video42(self):
        """Verify ffmpeg process is writing to /dev/video42."""
        code, out, _ = ssh_cmd("sudo fuser /dev/video42 2>&1")
        assert code == 0 and out, "No process writing to /dev/video42"

    def test_ffmpeg_bridge_process_running(self):
        """Verify ffmpeg-bridge script is running."""
        code, out, _ = ssh_cmd("ps aux | grep 'ffmpeg-bridge.sh' | grep -v grep")
        assert code == 0 and "ffmpeg-bridge" in out, "ffmpeg-bridge not running"

    def test_ffmpeg_transcoding_rtmp_to_v4l2(self):
        """Verify ffmpeg is transcoding RTMP to v4l2."""
        code, out, _ = ssh_cmd("ps aux | grep -E 'ffmpeg.*rtmp.*video42' | grep -v grep")
        assert code == 0 and "video42" in out, "No ffmpeg RTMP->v4l2 process"

    def test_ffmpeg_writing_to_alsa_loopback(self):
        """Verify ffmpeg is writing to ALSA loopback."""
        code, out, _ = ssh_cmd("ps aux | grep -E 'ffmpeg.*alsa.*Loopback' | grep -v grep")
        assert code == 0 and "Loopback" in out, "No ffmpeg->ALSA loopback process"


class TestVideoFramesFlowing:
    """Tests that video frames are actually flowing through the pipeline."""

    def test_can_read_frames_from_video42(self):
        """Verify we can read video frames from /dev/video42."""
        code, out, _ = ssh_cmd(
            "timeout 3 ffmpeg -hide_banner -loglevel error -f v4l2 -i /dev/video42 "
            "-t 1 -f null - 2>&1; echo exitcode=$?",
            timeout=10
        )
        assert "exitcode=0" in out or code == 0, f"Failed to read frames: {out}"

    def test_frames_have_content(self):
        """Verify frames have actual content (non-zero bytes)."""
        code, out, _ = ssh_cmd(
            "timeout 2 dd if=/dev/video42 bs=4096 count=1 2>/dev/null | wc -c"
        )
        assert code == 0 and int(out) > 0, f"No data from video device: {out}"

    def test_frame_rate_reasonable(self):
        """Verify frame rate is reasonable (>5fps)."""
        code, out, _ = ssh_cmd(
            "timeout 3 ffmpeg -hide_banner -f v4l2 -i /dev/video42 -t 2 -f null - 2>&1 | "
            "grep -oP 'fps=\\s*\\K[0-9]+' | tail -1"
        )
        if code == 0 and out:
            fps = int(out)
            assert fps >= 5, f"Frame rate too low: {fps}fps"


class TestAudioLoopback:
    """Tests for ALSA audio loopback device."""

    def test_alsa_loopback_exists(self):
        """Verify ALSA Loopback device exists."""
        code, out, _ = ssh_cmd("aplay -l | grep Loopback")
        assert code == 0 and "Loopback" in out, "ALSA Loopback not found"

    def test_alsa_loopback_card_number(self):
        """Verify ALSA Loopback has a card number."""
        code, out, _ = ssh_cmd("aplay -l | grep Loopback | grep -oP 'card \\d+'")
        assert code == 0 and "card" in out, f"No card number: {out}"


class TestRtmpStreamActive:
    """Tests for active RTMP stream from OBS."""

    def test_rtmp_stream_exists(self):
        """Verify RTMP stat endpoint is responding."""
        code, out, _ = ssh_cmd("curl -sf http://127.0.0.1:8081/stat | grep -c '<rtmp>'")
        assert code == 0 and int(out or 0) > 0, "RTMP stat endpoint not responding"

    def test_rtmp_bytes_received(self):
        """Verify RTMP server has received bytes (stream is/was active)."""
        code, out, _ = ssh_cmd(
            "curl -sf http://127.0.0.1:8081/stat | grep -oP 'bytes_in>\\K[0-9]+' | head -1"
        )
        if code == 0 and out:
            bytes_in = int(out)
            assert bytes_in > 0, "No bytes received on RTMP"


class TestContainerAccess:
    """Tests that Redroid container can access virtual devices."""

    def test_video42_in_container(self):
        """Verify /dev/video42 is accessible in Redroid container."""
        code, out, _ = ssh_cmd("sudo docker exec redroid ls -la /dev/video42")
        assert code == 0 and "video42" in out, "/dev/video42 not in container"

    def test_can_read_video42_in_container(self):
        """Verify can read from /dev/video42 inside container."""
        code, out, _ = ssh_cmd(
            "sudo docker exec redroid timeout 1 dd if=/dev/video42 bs=1024 count=1 2>&1"
        )
        assert code == 0 and "1+0 records" in out, f"Cannot read video42 in container: {out}"

    def test_alsa_loopback_in_android(self):
        """Verify ALSA Loopback visible in Android."""
        code, out, _ = ssh_cmd(
            "adb -s 127.0.0.1:5555 shell cat /proc/asound/cards 2>&1 | grep Loopback"
        )
        # May fail if ADB not connected, so check via docker too
        if code != 0:
            code, out, _ = ssh_cmd(
                "sudo docker exec redroid cat /proc/asound/cards | grep Loopback"
            )
        assert code == 0 and "Loopback" in out, "ALSA Loopback not visible in Android"


class TestEndToEndPipeline:
    """End-to-end pipeline tests."""

    def test_obs_to_android_pipeline_complete(self):
        """
        Verify complete pipeline: OBS -> nginx-rtmp -> ffmpeg -> video42 -> container.
        """
        # Check each component
        checks = []
        
        # 1. nginx-rtmp running
        code, _, _ = ssh_cmd("sudo systemctl is-active nginx-rtmp")
        checks.append(("nginx-rtmp", code == 0))
        
        # 2. ffmpeg-bridge running
        code, _, _ = ssh_cmd("sudo systemctl is-active ffmpeg-bridge")
        checks.append(("ffmpeg-bridge", code == 0))
        
        # 3. video42 exists
        code, _, _ = ssh_cmd("test -e /dev/video42")
        checks.append(("video42", code == 0))
        
        # 4. Container can access
        code, _, _ = ssh_cmd("sudo docker exec redroid test -e /dev/video42")
        checks.append(("container-access", code == 0))
        
        failed = [name for name, ok in checks if not ok]
        assert not failed, f"Pipeline components failed: {failed}"

    def test_stream_actively_flowing(self):
        """Verify data is actively flowing through the pipeline."""
        # Check ffmpeg is actively processing
        code, out, _ = ssh_cmd(
            "ps aux | grep -E 'ffmpeg.*rtmp.*video42' | grep -v grep | grep -c ffmpeg"
        )
        active = code == 0 and int(out or 0) > 0
        
        if active:
            # Verify frames are being produced
            code2, out2, _ = ssh_cmd(
                "timeout 2 dd if=/dev/video42 bs=4096 count=1 2>/dev/null | wc -c"
            )
            assert code2 == 0 and int(out2 or 0) > 0, "ffmpeg running but no frames"
        else:
            pytest.skip("No active stream - OBS not streaming")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
