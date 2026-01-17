# Cloud Agent Testing Guide

Use this checklist to validate a live Cloud Phone instance before integration.

## Prerequisites
- SSH access to the instance
- ADB installed on the controller
- VNC client on the controller

## Core health checks
1) Services and ports (on instance)
```
sudo /opt/waydroid-scripts/health-check.sh
```

2) ADB connectivity (from controller)
```
adb connect <INSTANCE_IP>:5555
adb shell getprop ro.build.version.release
adb shell wm size
```

3) VNC visual access (from controller)
```
ssh -i ~/.ssh/waydroid_oci -L 5900:localhost:5900 ubuntu@<INSTANCE_IP> -N
vncviewer localhost:5900
```

4) Agent API health (from controller)
```
ssh -i ~/.ssh/waydroid_oci -L 8081:localhost:8081 ubuntu@<INSTANCE_IP> -N
curl http://localhost:8081/health
curl http://localhost:8081/screen/info
```

## Optional deeper checks
- Run the Agent API test suite:
```
python3 tests/test_agent_api.py --api-url http://localhost:8081
```

- Run the full test runner (creates SSH tunnel):
```
./scripts/run-tests.sh --instance-ip <INSTANCE_IP>
```
