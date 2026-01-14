#!/usr/bin/env python3
"""
Cloud Phone Control API

Enhanced API server for Redroid cloud phone control including:
- ADB command interface
- Proxy configuration
- GPS/Location spoofing
- Device settings
- App management
- Screen capture
"""

import os
import json
import subprocess
import shlex
import time
import base64
import tempfile
from functools import wraps
from flask import Flask, request, jsonify, Response

app = Flask(__name__)

# Configuration
ADB_CONNECT = os.environ.get("ADB_CONNECT", "127.0.0.1:5555")
API_TOKEN = os.environ.get("API_TOKEN", "")
CONFIG_FILE = os.environ.get("CONFIG_FILE", "/etc/cloud-phone/config.json")
PROXY_SCRIPT = "/opt/waydroid-scripts/proxy-control.sh"

# In-memory state
_state = {
    "proxy": {"enabled": False, "type": None, "host": None, "port": None},
    "location": {"enabled": False, "latitude": 0, "longitude": 0},
    "connected": False
}

# =============================================================================
# Helpers
# =============================================================================

def run_adb(*args, timeout=30):
    """Run ADB command and return (success, stdout, stderr)"""
    cmd = ["adb", "-s", ADB_CONNECT] + list(args)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "", "Command timed out"
    except Exception as e:
        return False, "", str(e)

def run_adb_shell(command, timeout=30):
    """Run ADB shell command"""
    return run_adb("shell", command, timeout=timeout)

def ensure_adb_connected():
    """Ensure ADB is connected to device"""
    success, out, _ = run_adb("devices")
    if ADB_CONNECT in out and "device" in out:
        _state["connected"] = True
        return True
    
    # Try to connect
    success, out, err = run_adb("connect", ADB_CONNECT)
    if success and "connected" in out.lower():
        _state["connected"] = True
        return True
    
    _state["connected"] = False
    return False

def require_auth(f):
    """Decorator for API authentication"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if API_TOKEN:
            token = request.headers.get("Authorization", "").replace("Bearer ", "")
            if token != API_TOKEN:
                return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

def load_config():
    """Load configuration from file"""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {}

def save_config(config):
    """Save configuration to file"""
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

# =============================================================================
# Health & Status Endpoints
# =============================================================================

@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint"""
    connected = ensure_adb_connected()
    return jsonify({
        "status": "healthy" if connected else "degraded",
        "adb_connected": connected,
        "adb_target": ADB_CONNECT,
        "state": _state
    })

@app.route("/status", methods=["GET"])
@require_auth
def status():
    """Detailed device status"""
    ensure_adb_connected()
    
    # Get device properties
    success, model, _ = run_adb_shell("getprop ro.product.model")
    success, android_version, _ = run_adb_shell("getprop ro.build.version.release")
    success, sdk, _ = run_adb_shell("getprop ro.build.version.sdk")
    success, battery, _ = run_adb_shell("dumpsys battery | grep level")
    success, screen, _ = run_adb_shell("dumpsys display | grep mScreenState")
    
    return jsonify({
        "connected": _state["connected"],
        "device": {
            "model": model or "unknown",
            "android_version": android_version or "unknown",
            "sdk_version": sdk or "unknown"
        },
        "battery": battery.split(":")[-1].strip() if battery else "unknown",
        "screen_state": "on" if "ON" in screen else "off" if screen else "unknown",
        "proxy": _state["proxy"],
        "location": _state["location"]
    })

# =============================================================================
# Proxy Control Endpoints
# =============================================================================

@app.route("/proxy", methods=["GET"])
@require_auth
def get_proxy():
    """Get current proxy configuration"""
    return jsonify(_state["proxy"])

@app.route("/proxy", methods=["POST"])
@require_auth
def set_proxy():
    """
    Set proxy configuration
    
    Body:
    {
        "enabled": true,
        "type": "socks5",  // http, socks5, transparent
        "host": "proxy.example.com",
        "port": 1080,
        "username": "",  // optional
        "password": ""   // optional
    }
    """
    data = request.get_json() or {}
    
    enabled = data.get("enabled", False)
    proxy_type = data.get("type", "socks5")
    host = data.get("host", "")
    port = data.get("port", 0)
    username = data.get("username", "")
    password = data.get("password", "")
    
    if enabled and (not host or not port):
        return jsonify({"error": "host and port required when enabled"}), 400
    
    success = False
    message = ""
    
    if enabled:
        if proxy_type == "http":
            # Set Android global HTTP proxy
            success, _, err = run_adb_shell(
                f"settings put global http_proxy {host}:{port}"
            )
            if success:
                message = f"HTTP proxy set to {host}:{port}"
        
        elif proxy_type == "socks5":
            # Use iptables + redsocks/tun2socks for SOCKS5
            # This requires the proxy-control.sh script
            if os.path.exists(PROXY_SCRIPT):
                result = subprocess.run(
                    [PROXY_SCRIPT, "enable", proxy_type, host, str(port), username, password],
                    capture_output=True, text=True
                )
                success = result.returncode == 0
                message = result.stdout or result.stderr
            else:
                # Fallback: set via ADB
                success, _, err = run_adb_shell(
                    f"setprop persist.proxy.socks5.host {host} && "
                    f"setprop persist.proxy.socks5.port {port}"
                )
                message = f"SOCKS5 proxy set to {host}:{port} (app-level)"
        
        elif proxy_type == "transparent":
            # Transparent proxy via iptables
            if os.path.exists(PROXY_SCRIPT):
                result = subprocess.run(
                    [PROXY_SCRIPT, "enable", "transparent", host, str(port)],
                    capture_output=True, text=True
                )
                success = result.returncode == 0
                message = result.stdout or result.stderr
            else:
                return jsonify({"error": "Transparent proxy requires proxy-control.sh"}), 500
    else:
        # Disable proxy
        run_adb_shell("settings put global http_proxy :0")
        if os.path.exists(PROXY_SCRIPT):
            subprocess.run([PROXY_SCRIPT, "disable"], capture_output=True)
        success = True
        message = "Proxy disabled"
    
    if success:
        _state["proxy"] = {
            "enabled": enabled,
            "type": proxy_type if enabled else None,
            "host": host if enabled else None,
            "port": port if enabled else None
        }
    
    return jsonify({
        "success": success,
        "message": message,
        "proxy": _state["proxy"]
    })

@app.route("/proxy", methods=["DELETE"])
@require_auth
def disable_proxy():
    """Disable proxy"""
    return set_proxy()  # Will call with empty body = disabled

# =============================================================================
# GPS/Location Spoofing Endpoints
# =============================================================================

@app.route("/location", methods=["GET"])
@require_auth
def get_location():
    """Get current spoofed location"""
    return jsonify(_state["location"])

@app.route("/location", methods=["POST"])
@require_auth
def set_location():
    """
    Set GPS location (mock location)
    
    Body:
    {
        "enabled": true,
        "latitude": 37.7749,
        "longitude": -122.4194,
        "altitude": 0,
        "accuracy": 10
    }
    """
    data = request.get_json() or {}
    
    enabled = data.get("enabled", False)
    latitude = data.get("latitude", 0)
    longitude = data.get("longitude", 0)
    altitude = data.get("altitude", 0)
    accuracy = data.get("accuracy", 10)
    
    if enabled:
        # Enable mock locations in developer settings
        run_adb_shell("settings put secure mock_location 1")
        
        # Method 1: Use geo fix command via adb
        # Format: geo fix <longitude> <latitude> [altitude] [satellites] [velocity]
        success, out, err = run_adb_shell(
            f"am broadcast -a android.intent.action.MOCK_LOCATION "
            f"--ef latitude {latitude} --ef longitude {longitude} "
            f"--ef altitude {altitude} --ef accuracy {accuracy}"
        )
        
        # Method 2: Use appops to allow mock location for shell
        run_adb_shell("appops set com.android.shell android:mock_location allow")
        
        # Method 3: Direct geo command (telnet-style, may not work on all)
        geo_cmd = f"geo fix {longitude} {latitude} {altitude}"
        run_adb("emu", geo_cmd)
        
        # Alternative: Use location mock app if installed
        run_adb_shell(
            f"am start -a android.intent.action.VIEW "
            f"-d 'geo:{latitude},{longitude}'"
        )
        
        _state["location"] = {
            "enabled": True,
            "latitude": latitude,
            "longitude": longitude,
            "altitude": altitude,
            "accuracy": accuracy
        }
        
        return jsonify({
            "success": True,
            "message": f"Location set to {latitude}, {longitude}",
            "location": _state["location"]
        })
    else:
        run_adb_shell("settings put secure mock_location 0")
        _state["location"] = {"enabled": False, "latitude": 0, "longitude": 0}
        
        return jsonify({
            "success": True,
            "message": "Mock location disabled",
            "location": _state["location"]
        })

@app.route("/location", methods=["DELETE"])
@require_auth
def disable_location():
    """Disable mock location"""
    return set_location()  # Empty body = disabled

# =============================================================================
# ADB Command Interface
# =============================================================================

@app.route("/adb/shell", methods=["POST"])
@require_auth
def adb_shell():
    """
    Execute ADB shell command
    
    Body:
    {
        "command": "ls /sdcard",
        "timeout": 30
    }
    """
    data = request.get_json() or {}
    command = data.get("command", "")
    timeout = data.get("timeout", 30)
    
    if not command:
        return jsonify({"error": "command required"}), 400
    
    ensure_adb_connected()
    success, stdout, stderr = run_adb_shell(command, timeout=timeout)
    
    return jsonify({
        "success": success,
        "stdout": stdout,
        "stderr": stderr
    })

@app.route("/adb/push", methods=["POST"])
@require_auth
def adb_push():
    """
    Push file to device
    
    Body (multipart/form-data):
    - file: The file to push
    - path: Destination path on device (e.g., /sdcard/myfile.txt)
    """
    if "file" not in request.files:
        return jsonify({"error": "file required"}), 400
    
    file = request.files["file"]
    dest_path = request.form.get("path", f"/sdcard/{file.filename}")
    
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        file.save(tmp.name)
        success, out, err = run_adb("push", tmp.name, dest_path)
        os.unlink(tmp.name)
    
    return jsonify({
        "success": success,
        "path": dest_path,
        "message": out or err
    })

@app.route("/adb/pull", methods=["POST"])
@require_auth
def adb_pull():
    """
    Pull file from device
    
    Body:
    {
        "path": "/sdcard/myfile.txt"
    }
    
    Returns: File content as base64
    """
    data = request.get_json() or {}
    path = data.get("path", "")
    
    if not path:
        return jsonify({"error": "path required"}), 400
    
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        success, out, err = run_adb("pull", path, tmp.name)
        if success and os.path.exists(tmp.name):
            with open(tmp.name, "rb") as f:
                content = base64.b64encode(f.read()).decode()
            os.unlink(tmp.name)
            return jsonify({
                "success": True,
                "path": path,
                "content_base64": content
            })
        os.unlink(tmp.name)
    
    return jsonify({"success": False, "error": err}), 404

@app.route("/adb/install", methods=["POST"])
@require_auth
def adb_install():
    """
    Install APK on device
    
    Body (multipart/form-data):
    - file: APK file
    - options: Additional options (e.g., "-r" for reinstall)
    """
    if "file" not in request.files:
        return jsonify({"error": "APK file required"}), 400
    
    file = request.files["file"]
    options = request.form.get("options", "-r").split()
    
    with tempfile.NamedTemporaryFile(suffix=".apk", delete=False) as tmp:
        file.save(tmp.name)
        success, out, err = run_adb("install", *options, tmp.name, timeout=120)
        os.unlink(tmp.name)
    
    return jsonify({
        "success": success,
        "message": out or err
    })

# =============================================================================
# Device Control Endpoints
# =============================================================================

@app.route("/device/screen", methods=["POST"])
@require_auth
def control_screen():
    """
    Control screen state
    
    Body:
    {
        "action": "on" | "off" | "toggle" | "unlock"
    }
    """
    data = request.get_json() or {}
    action = data.get("action", "toggle")
    
    if action == "on":
        run_adb_shell("input keyevent KEYCODE_WAKEUP")
    elif action == "off":
        run_adb_shell("input keyevent KEYCODE_SLEEP")
    elif action == "toggle":
        run_adb_shell("input keyevent KEYCODE_POWER")
    elif action == "unlock":
        run_adb_shell("input keyevent KEYCODE_WAKEUP")
        time.sleep(0.5)
        run_adb_shell("input swipe 500 1500 500 500")
    
    return jsonify({"success": True, "action": action})

@app.route("/device/input", methods=["POST"])
@require_auth
def device_input():
    """
    Send input to device
    
    Body:
    {
        "type": "tap" | "swipe" | "text" | "key",
        "x": 500,        // for tap
        "y": 500,        // for tap
        "x1": 100,       // for swipe
        "y1": 100,       // for swipe
        "x2": 500,       // for swipe
        "y2": 500,       // for swipe
        "duration": 300, // for swipe (ms)
        "text": "hello", // for text
        "keycode": 4     // for key (KEYCODE_BACK=4, HOME=3, etc)
    }
    """
    data = request.get_json() or {}
    input_type = data.get("type", "tap")
    
    if input_type == "tap":
        x, y = data.get("x", 500), data.get("y", 500)
        run_adb_shell(f"input tap {x} {y}")
    
    elif input_type == "swipe":
        x1, y1 = data.get("x1", 100), data.get("y1", 100)
        x2, y2 = data.get("x2", 500), data.get("y2", 500)
        duration = data.get("duration", 300)
        run_adb_shell(f"input swipe {x1} {y1} {x2} {y2} {duration}")
    
    elif input_type == "text":
        text = data.get("text", "")
        # Escape special characters
        text = text.replace(" ", "%s").replace("'", "\\'")
        run_adb_shell(f"input text '{text}'")
    
    elif input_type == "key":
        keycode = data.get("keycode", 4)
        run_adb_shell(f"input keyevent {keycode}")
    
    return jsonify({"success": True, "type": input_type})

@app.route("/device/screenshot", methods=["GET"])
@require_auth
def screenshot():
    """Take screenshot and return as PNG"""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        run_adb_shell(f"screencap -p > {tmp.name}")
        run_adb("pull", "/sdcard/screenshot.png", tmp.name)
        
        # Alternative: direct screencap
        success, _, _ = run_adb("exec-out", "screencap", "-p", ">", tmp.name)
        
        if os.path.exists(tmp.name) and os.path.getsize(tmp.name) > 0:
            with open(tmp.name, "rb") as f:
                data = f.read()
            os.unlink(tmp.name)
            return Response(data, mimetype="image/png")
        
        os.unlink(tmp.name)
    
    return jsonify({"error": "Failed to capture screenshot"}), 500

@app.route("/device/screenshot/base64", methods=["GET"])
@require_auth
def screenshot_base64():
    """Take screenshot and return as base64 JSON"""
    result = subprocess.run(
        ["adb", "-s", ADB_CONNECT, "exec-out", "screencap", "-p"],
        capture_output=True, timeout=30
    )
    
    if result.returncode == 0 and result.stdout:
        return jsonify({
            "success": True,
            "image_base64": base64.b64encode(result.stdout).decode()
        })
    
    return jsonify({"success": False, "error": "Failed to capture screenshot"}), 500

# =============================================================================
# App Management Endpoints
# =============================================================================

@app.route("/apps", methods=["GET"])
@require_auth
def list_apps():
    """List installed apps"""
    success, out, _ = run_adb_shell("pm list packages -3")  # -3 for third-party only
    packages = [line.replace("package:", "") for line in out.split("\n") if line]
    
    return jsonify({
        "success": success,
        "packages": packages,
        "count": len(packages)
    })

@app.route("/apps/<package>/start", methods=["POST"])
@require_auth
def start_app(package):
    """Start an app by package name"""
    # Get main activity
    success, out, _ = run_adb_shell(
        f"cmd package resolve-activity --brief {package} | tail -n 1"
    )
    
    if success and out:
        activity = out.strip()
        run_adb_shell(f"am start -n {activity}")
        return jsonify({"success": True, "activity": activity})
    
    # Fallback: use monkey
    run_adb_shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
    return jsonify({"success": True, "package": package})

@app.route("/apps/<package>/stop", methods=["POST"])
@require_auth
def stop_app(package):
    """Force stop an app"""
    success, out, err = run_adb_shell(f"am force-stop {package}")
    return jsonify({"success": success, "message": out or err})

@app.route("/apps/<package>/uninstall", methods=["DELETE"])
@require_auth
def uninstall_app(package):
    """Uninstall an app"""
    success, out, err = run_adb("uninstall", package)
    return jsonify({"success": success, "message": out or err})

@app.route("/apps/<package>/clear", methods=["POST"])
@require_auth
def clear_app_data(package):
    """Clear app data"""
    success, out, err = run_adb_shell(f"pm clear {package}")
    return jsonify({"success": success, "message": out or err})

# =============================================================================
# Settings Endpoints
# =============================================================================

@app.route("/settings/<namespace>/<key>", methods=["GET"])
@require_auth
def get_setting(namespace, key):
    """Get Android setting (namespace: system, secure, global)"""
    if namespace not in ["system", "secure", "global"]:
        return jsonify({"error": "Invalid namespace"}), 400
    
    success, value, _ = run_adb_shell(f"settings get {namespace} {key}")
    return jsonify({"namespace": namespace, "key": key, "value": value})

@app.route("/settings/<namespace>/<key>", methods=["PUT"])
@require_auth
def set_setting(namespace, key):
    """Set Android setting"""
    if namespace not in ["system", "secure", "global"]:
        return jsonify({"error": "Invalid namespace"}), 400
    
    data = request.get_json() or {}
    value = data.get("value", "")
    
    success, out, err = run_adb_shell(f"settings put {namespace} {key} {value}")
    return jsonify({"success": success, "message": out or err})

# =============================================================================
# Configuration Endpoints
# =============================================================================

@app.route("/config", methods=["GET"])
@require_auth
def get_config():
    """Get current configuration"""
    config = load_config()
    config["runtime_state"] = _state
    return jsonify(config)

@app.route("/config", methods=["POST"])
@require_auth
def update_config():
    """Update configuration"""
    data = request.get_json() or {}
    config = load_config()
    config.update(data)
    save_config(config)
    
    # Apply relevant settings
    if "proxy" in data:
        # Trigger proxy update
        pass
    
    if "location" in data:
        # Trigger location update
        pass
    
    return jsonify({"success": True, "config": config})

# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    host = os.environ.get("API_HOST", "127.0.0.1")
    port = int(os.environ.get("API_PORT", "8080"))
    debug = os.environ.get("API_DEBUG", "false").lower() == "true"
    
    print(f"Starting Cloud Phone Control API on {host}:{port}")
    print(f"ADB target: {ADB_CONNECT}")
    
    app.run(host=host, port=port, debug=debug, threaded=True)
