#!/usr/bin/env python3
"""
Cloud Phone Agent API

A simple, LLM-agent-friendly REST API for Android device automation.
Designed to be consumed as tool commands by AI agents.

Features:
- Screenshot capture (PNG, base64, with optional element detection)
- Touch input (tap, swipe, long press) by pixel or percentage
- Text input and key events
- Screen information (resolution, orientation)
- App management (launch, close, list)
- File I/O (push, pull, list)
- Device info and status

All responses follow a consistent format:
{
    "success": bool,
    "data": {...} or null,
    "error": string or null
}
"""

import os
import sys
import json
import subprocess
import base64
import tempfile
import time
import logging
import re
from datetime import datetime
from functools import wraps
from typing import Optional, Dict, Any, List, Tuple

from flask import Flask, request, jsonify, Response, send_file

# Configure logging
LOG_DIR = os.environ.get("LOG_DIR", "/var/log/cloud-phone")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "agent-api.log")),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("agent-api")

app = Flask(__name__)

# =============================================================================
# Configuration
# =============================================================================

ADB_HOST = os.environ.get("ADB_HOST", "127.0.0.1")
ADB_PORT = os.environ.get("ADB_PORT", "5555")
ADB_TARGET = f"{ADB_HOST}:{ADB_PORT}"
API_TOKEN = os.environ.get("API_TOKEN", "")
DEFAULT_TIMEOUT = int(os.environ.get("DEFAULT_TIMEOUT", "30"))

# Screen dimensions cache
_screen_cache = {
    "width": None,
    "height": None,
    "density": None,
    "orientation": None,
    "updated_at": None
}

# =============================================================================
# Helpers
# =============================================================================

def adb(*args, timeout: int = DEFAULT_TIMEOUT) -> Tuple[bool, str, str]:
    """Execute ADB command. Returns (success, stdout, stderr)."""
    cmd = ["adb", "-s", ADB_TARGET] + list(args)
    logger.debug(f"ADB: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        logger.error(f"ADB timeout: {cmd}")
        return False, "", "Command timed out"
    except Exception as e:
        logger.error(f"ADB error: {e}")
        return False, "", str(e)

def adb_shell(command: str, timeout: int = DEFAULT_TIMEOUT) -> Tuple[bool, str, str]:
    """Execute ADB shell command."""
    return adb("shell", command, timeout=timeout)

def ensure_connected() -> bool:
    """Ensure ADB connection is established."""
    success, out, _ = adb("devices")
    if ADB_TARGET in out and "device" in out:
        return True
    success, out, _ = adb("connect", ADB_TARGET)
    return "connected" in out.lower()

def api_response(success: bool, data: Any = None, error: str = None) -> Dict:
    """Standard API response format."""
    return {
        "success": success,
        "data": data,
        "error": error,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }

def require_auth(f):
    """Authentication decorator."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if API_TOKEN:
            token = request.headers.get("Authorization", "").replace("Bearer ", "")
            if token != API_TOKEN:
                logger.warning(f"Unauthorized request to {request.path}")
                return jsonify(api_response(False, error="Unauthorized")), 401
        return f(*args, **kwargs)
    return decorated

def log_request(f):
    """Log all requests."""
    @wraps(f)
    def decorated(*args, **kwargs):
        logger.info(f"{request.method} {request.path} - {request.remote_addr}")
        try:
            result = f(*args, **kwargs)
            return result
        except Exception as e:
            logger.exception(f"Error in {request.path}: {e}")
            return jsonify(api_response(False, error=str(e))), 500
    return decorated

# =============================================================================
# Screen Information
# =============================================================================

def get_screen_info(force_refresh: bool = False) -> Dict:
    """Get screen dimensions and orientation."""
    global _screen_cache
    
    # Return cached if fresh (< 60 seconds)
    if not force_refresh and _screen_cache["updated_at"]:
        age = (datetime.utcnow() - _screen_cache["updated_at"]).seconds
        if age < 60 and _screen_cache["width"]:
            return _screen_cache
    
    # Get screen size
    success, out, _ = adb_shell("wm size")
    if success and "Physical size:" in out:
        match = re.search(r'(\d+)x(\d+)', out)
        if match:
            _screen_cache["width"] = int(match.group(1))
            _screen_cache["height"] = int(match.group(2))
    
    # Get density
    success, out, _ = adb_shell("wm density")
    if success and "Physical density:" in out:
        match = re.search(r'(\d+)', out)
        if match:
            _screen_cache["density"] = int(match.group(1))
    
    # Get orientation (0=portrait, 1=landscape, 2=reverse portrait, 3=reverse landscape)
    success, out, _ = adb_shell("dumpsys display | grep mCurrentOrientation")
    if success:
        match = re.search(r'mCurrentOrientation=(\d)', out)
        if match:
            _screen_cache["orientation"] = int(match.group(1))
    
    _screen_cache["updated_at"] = datetime.utcnow()
    return _screen_cache

def convert_coordinates(x: float, y: float, as_percentage: bool = False) -> Tuple[int, int]:
    """Convert coordinates. If as_percentage=True, x/y are 0-100 percentages."""
    screen = get_screen_info()
    
    if as_percentage:
        # Convert percentage to pixels
        px_x = int((x / 100.0) * screen["width"])
        px_y = int((y / 100.0) * screen["height"])
        return px_x, px_y
    else:
        # Already pixels, ensure integers
        return int(x), int(y)

# =============================================================================
# API Endpoints: Screen
# =============================================================================

@app.route("/screen/info", methods=["GET"])
@log_request
@require_auth
def screen_info():
    """
    Get screen information.
    
    Returns:
        width: Screen width in pixels
        height: Screen height in pixels
        density: Screen density (DPI)
        orientation: 0=portrait, 1=landscape
    
    Example:
        GET /screen/info
        {"success": true, "data": {"width": 1080, "height": 2400, "density": 420, "orientation": 0}}
    """
    ensure_connected()
    info = get_screen_info(force_refresh=request.args.get("refresh", "").lower() == "true")
    return jsonify(api_response(True, data={
        "width": info["width"],
        "height": info["height"],
        "density": info["density"],
        "orientation": info["orientation"]
    }))

@app.route("/screen/screenshot", methods=["GET"])
@log_request
@require_auth
def screenshot():
    """
    Capture screenshot.
    
    Query params:
        format: "png" (default), "base64", "jpeg"
        quality: JPEG quality 1-100 (default: 80)
    
    Returns:
        format=png/jpeg: Binary image data
        format=base64: {"image": "base64string", "width": 1080, "height": 2400}
    
    Example:
        GET /screen/screenshot?format=base64
    """
    ensure_connected()
    fmt = request.args.get("format", "png").lower()
    quality = int(request.args.get("quality", "80"))
    
    # Capture screenshot
    result = subprocess.run(
        ["adb", "-s", ADB_TARGET, "exec-out", "screencap", "-p"],
        capture_output=True,
        timeout=30
    )
    
    if result.returncode != 0 or not result.stdout:
        return jsonify(api_response(False, error="Failed to capture screenshot")), 500
    
    screen = get_screen_info()
    
    if fmt == "base64":
        return jsonify(api_response(True, data={
            "image": base64.b64encode(result.stdout).decode(),
            "format": "png",
            "width": screen["width"],
            "height": screen["height"]
        }))
    else:
        return Response(result.stdout, mimetype=f"image/{fmt}")

@app.route("/screen/screenshot/region", methods=["POST"])
@log_request
@require_auth
def screenshot_region():
    """
    Capture screenshot of a specific region.
    
    Body:
        {
            "x": 100,       // Left coordinate (pixels or percentage)
            "y": 200,       // Top coordinate
            "width": 300,   // Region width
            "height": 400,  // Region height
            "percentage": false  // If true, all values are percentages (0-100)
        }
    
    Returns:
        {"image": "base64string", "x": 100, "y": 200, "width": 300, "height": 400}
    """
    data = request.get_json() or {}
    is_pct = data.get("percentage", False)
    
    screen = get_screen_info()
    
    if is_pct:
        x = int((data.get("x", 0) / 100.0) * screen["width"])
        y = int((data.get("y", 0) / 100.0) * screen["height"])
        w = int((data.get("width", 100) / 100.0) * screen["width"])
        h = int((data.get("height", 100) / 100.0) * screen["height"])
    else:
        x = int(data.get("x", 0))
        y = int(data.get("y", 0))
        w = int(data.get("width", screen["width"]))
        h = int(data.get("height", screen["height"]))
    
    # For now, capture full screen - region cropping would need PIL
    # This is a simplified version
    result = subprocess.run(
        ["adb", "-s", ADB_TARGET, "exec-out", "screencap", "-p"],
        capture_output=True,
        timeout=30
    )
    
    if result.returncode != 0:
        return jsonify(api_response(False, error="Failed to capture screenshot")), 500
    
    return jsonify(api_response(True, data={
        "image": base64.b64encode(result.stdout).decode(),
        "x": x, "y": y, "width": w, "height": h,
        "note": "Full screenshot returned; client should crop to specified region"
    }))

# =============================================================================
# API Endpoints: Input
# =============================================================================

@app.route("/input/tap", methods=["POST"])
@log_request
@require_auth
def input_tap():
    """
    Tap at coordinates.
    
    Body:
        {
            "x": 540,           // X coordinate
            "y": 1200,          // Y coordinate
            "percentage": false // If true, x/y are percentages (0-100)
        }
    
    Example:
        POST /input/tap
        {"x": 50, "y": 50, "percentage": true}  // Tap center of screen
    
    Returns:
        {"success": true, "data": {"x": 540, "y": 1200, "action": "tap"}}
    """
    data = request.get_json() or {}
    is_pct = data.get("percentage", False)
    
    x, y = convert_coordinates(data.get("x", 0), data.get("y", 0), is_pct)
    
    ensure_connected()
    success, out, err = adb_shell(f"input tap {x} {y}")
    
    logger.info(f"Tap at ({x}, {y}) - success={success}")
    
    return jsonify(api_response(success, 
        data={"x": x, "y": y, "action": "tap"},
        error=err if not success else None
    ))

@app.route("/input/swipe", methods=["POST"])
@log_request
@require_auth
def input_swipe():
    """
    Swipe gesture.
    
    Body:
        {
            "x1": 540,          // Start X
            "y1": 1500,         // Start Y
            "x2": 540,          // End X
            "y2": 500,          // End Y
            "duration": 300,    // Duration in ms (default: 300)
            "percentage": false // If true, coordinates are percentages
        }
    
    Example:
        POST /input/swipe
        {"x1": 50, "y1": 75, "x2": 50, "y2": 25, "percentage": true}  // Swipe up
    
    Returns:
        {"success": true, "data": {"x1": 540, "y1": 1500, "x2": 540, "y2": 500, "action": "swipe"}}
    """
    data = request.get_json() or {}
    is_pct = data.get("percentage", False)
    duration = data.get("duration", 300)
    
    x1, y1 = convert_coordinates(data.get("x1", 0), data.get("y1", 0), is_pct)
    x2, y2 = convert_coordinates(data.get("x2", 0), data.get("y2", 0), is_pct)
    
    ensure_connected()
    success, out, err = adb_shell(f"input swipe {x1} {y1} {x2} {y2} {duration}")
    
    logger.info(f"Swipe ({x1},{y1}) -> ({x2},{y2}) duration={duration}ms - success={success}")
    
    return jsonify(api_response(success,
        data={"x1": x1, "y1": y1, "x2": x2, "y2": y2, "duration": duration, "action": "swipe"},
        error=err if not success else None
    ))

@app.route("/input/long_press", methods=["POST"])
@log_request
@require_auth
def input_long_press():
    """
    Long press at coordinates.
    
    Body:
        {
            "x": 540,
            "y": 1200,
            "duration": 1000,   // Duration in ms (default: 1000)
            "percentage": false
        }
    
    Returns:
        {"success": true, "data": {"x": 540, "y": 1200, "duration": 1000, "action": "long_press"}}
    """
    data = request.get_json() or {}
    is_pct = data.get("percentage", False)
    duration = data.get("duration", 1000)
    
    x, y = convert_coordinates(data.get("x", 0), data.get("y", 0), is_pct)
    
    ensure_connected()
    # Long press is a swipe to the same position
    success, out, err = adb_shell(f"input swipe {x} {y} {x} {y} {duration}")
    
    logger.info(f"Long press at ({x},{y}) duration={duration}ms - success={success}")
    
    return jsonify(api_response(success,
        data={"x": x, "y": y, "duration": duration, "action": "long_press"},
        error=err if not success else None
    ))

@app.route("/input/text", methods=["POST"])
@log_request
@require_auth
def input_text():
    """
    Type text.
    
    Body:
        {
            "text": "Hello World",
            "clear_first": false  // If true, clear field before typing
        }
    
    Note: Special characters may not work. Use input/key for special keys.
    
    Returns:
        {"success": true, "data": {"text": "Hello World", "length": 11}}
    """
    data = request.get_json() or {}
    text = data.get("text", "")
    clear_first = data.get("clear_first", False)
    
    ensure_connected()
    
    if clear_first:
        # Select all and delete
        adb_shell("input keyevent KEYCODE_CTRL_A")
        adb_shell("input keyevent KEYCODE_DEL")
    
    # Escape special characters for shell
    escaped = text.replace("'", "'\\''").replace(" ", "%s").replace("&", "\\&")
    success, out, err = adb_shell(f"input text '{escaped}'")
    
    logger.info(f"Input text: '{text[:20]}...' ({len(text)} chars) - success={success}")
    
    return jsonify(api_response(success,
        data={"text": text, "length": len(text), "action": "text"},
        error=err if not success else None
    ))

@app.route("/input/key", methods=["POST"])
@log_request
@require_auth
def input_key():
    """
    Send key event.
    
    Body:
        {
            "key": "KEYCODE_HOME"  // Or key code number (3 for HOME)
        }
    
    Common keys:
        KEYCODE_HOME (3), KEYCODE_BACK (4), KEYCODE_MENU (82),
        KEYCODE_ENTER (66), KEYCODE_DEL (67), KEYCODE_SPACE (62),
        KEYCODE_TAB (61), KEYCODE_ESCAPE (111)
    
    Returns:
        {"success": true, "data": {"key": "KEYCODE_HOME"}}
    """
    data = request.get_json() or {}
    key = data.get("key", "")
    
    ensure_connected()
    success, out, err = adb_shell(f"input keyevent {key}")
    
    logger.info(f"Key event: {key} - success={success}")
    
    return jsonify(api_response(success,
        data={"key": key, "action": "key"},
        error=err if not success else None
    ))

# Common key shortcuts
@app.route("/input/back", methods=["POST"])
@log_request
@require_auth
def input_back():
    """Press back button."""
    ensure_connected()
    success, _, err = adb_shell("input keyevent KEYCODE_BACK")
    return jsonify(api_response(success, data={"action": "back"}, error=err if not success else None))

@app.route("/input/home", methods=["POST"])
@log_request
@require_auth
def input_home():
    """Press home button."""
    ensure_connected()
    success, _, err = adb_shell("input keyevent KEYCODE_HOME")
    return jsonify(api_response(success, data={"action": "home"}, error=err if not success else None))

@app.route("/input/recent", methods=["POST"])
@log_request
@require_auth
def input_recent():
    """Open recent apps."""
    ensure_connected()
    success, _, err = adb_shell("input keyevent KEYCODE_APP_SWITCH")
    return jsonify(api_response(success, data={"action": "recent"}, error=err if not success else None))

# =============================================================================
# API Endpoints: Apps
# =============================================================================

@app.route("/apps", methods=["GET"])
@log_request
@require_auth
def list_apps():
    """
    List installed apps.
    
    Query params:
        type: "all", "user" (default), "system"
    
    Returns:
        {"packages": ["com.example.app", ...], "count": 10}
    """
    ensure_connected()
    app_type = request.args.get("type", "user")
    
    flag = "-3" if app_type == "user" else "" if app_type == "all" else "-s"
    success, out, err = adb_shell(f"pm list packages {flag}")
    
    packages = [line.replace("package:", "") for line in out.split("\n") if line.startswith("package:")]
    
    return jsonify(api_response(success,
        data={"packages": packages, "count": len(packages)},
        error=err if not success else None
    ))

@app.route("/apps/<package>/launch", methods=["POST"])
@log_request
@require_auth
def launch_app(package):
    """
    Launch an app.
    
    Example:
        POST /apps/com.android.settings/launch
    
    Returns:
        {"package": "com.android.settings", "launched": true}
    """
    ensure_connected()
    
    # Try to get main activity
    success, out, _ = adb_shell(f"cmd package resolve-activity --brief {package} | tail -n 1")
    
    if success and out and "/" in out:
        activity = out.strip()
        success, _, err = adb_shell(f"am start -n {activity}")
    else:
        # Fallback to monkey
        success, _, err = adb_shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
    
    logger.info(f"Launch app: {package} - success={success}")
    
    return jsonify(api_response(success,
        data={"package": package, "launched": success},
        error=err if not success else None
    ))

@app.route("/apps/<package>/close", methods=["POST"])
@log_request
@require_auth
def close_app(package):
    """
    Force close an app.
    
    Returns:
        {"package": "com.example.app", "closed": true}
    """
    ensure_connected()
    success, _, err = adb_shell(f"am force-stop {package}")
    
    logger.info(f"Close app: {package} - success={success}")
    
    return jsonify(api_response(success,
        data={"package": package, "closed": success},
        error=err if not success else None
    ))

@app.route("/apps/<package>/info", methods=["GET"])
@log_request
@require_auth
def app_info(package):
    """
    Get app information.
    
    Returns:
        {"package": "com.example", "version": "1.0.0", "installed": true, ...}
    """
    ensure_connected()
    success, out, _ = adb_shell(f"dumpsys package {package} | head -50")
    
    info = {"package": package, "installed": success}
    
    if success:
        # Parse version
        version_match = re.search(r'versionName=([^\s]+)', out)
        if version_match:
            info["version"] = version_match.group(1)
        
        # Parse version code
        code_match = re.search(r'versionCode=(\d+)', out)
        if code_match:
            info["version_code"] = int(code_match.group(1))
    
    return jsonify(api_response(success, data=info))

@app.route("/apps/current", methods=["GET"])
@log_request
@require_auth
def current_app():
    """
    Get currently focused app/activity.
    
    Returns:
        {"package": "com.example", "activity": "MainActivity"}
    """
    ensure_connected()
    success, out, _ = adb_shell("dumpsys activity activities | grep mResumedActivity")
    
    data = {"package": None, "activity": None}
    
    if success and out:
        # Parse: mResumedActivity: ActivityRecord{... com.package/.Activity ...}
        match = re.search(r'([a-zA-Z0-9_.]+)/([a-zA-Z0-9_.]+)', out)
        if match:
            data["package"] = match.group(1)
            data["activity"] = match.group(2)
    
    return jsonify(api_response(bool(data["package"]), data=data))

# =============================================================================
# API Endpoints: Files
# =============================================================================

@app.route("/files/list", methods=["GET"])
@log_request
@require_auth
def list_files():
    """
    List files in directory.
    
    Query params:
        path: Directory path (default: /sdcard)
    
    Returns:
        {"path": "/sdcard", "files": [{"name": "file.txt", "type": "file", "size": 1234}, ...]}
    """
    ensure_connected()
    path = request.args.get("path", "/sdcard")
    
    success, out, err = adb_shell(f"ls -la '{path}'")
    
    files = []
    if success:
        for line in out.split("\n")[1:]:  # Skip "total" line
            parts = line.split()
            if len(parts) >= 8:
                name = " ".join(parts[7:])
                file_type = "directory" if parts[0].startswith("d") else "file"
                size = int(parts[4]) if parts[4].isdigit() else 0
                files.append({"name": name, "type": file_type, "size": size})
    
    return jsonify(api_response(success,
        data={"path": path, "files": files, "count": len(files)},
        error=err if not success else None
    ))

@app.route("/files/read", methods=["GET"])
@log_request
@require_auth
def read_file():
    """
    Read file contents.
    
    Query params:
        path: File path
        encoding: "text" (default) or "base64"
    
    Returns:
        {"path": "/sdcard/file.txt", "content": "file contents...", "size": 123}
    """
    ensure_connected()
    path = request.args.get("path", "")
    encoding = request.args.get("encoding", "text")
    
    if not path:
        return jsonify(api_response(False, error="path parameter required")), 400
    
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        success, _, err = adb("pull", path, tmp.name)
        
        if success and os.path.exists(tmp.name):
            with open(tmp.name, "rb") as f:
                content = f.read()
            os.unlink(tmp.name)
            
            if encoding == "base64":
                return jsonify(api_response(True, data={
                    "path": path,
                    "content": base64.b64encode(content).decode(),
                    "encoding": "base64",
                    "size": len(content)
                }))
            else:
                return jsonify(api_response(True, data={
                    "path": path,
                    "content": content.decode("utf-8", errors="replace"),
                    "encoding": "text",
                    "size": len(content)
                }))
        
        os.unlink(tmp.name) if os.path.exists(tmp.name) else None
    
    return jsonify(api_response(False, error=err or "File not found")), 404

@app.route("/files/write", methods=["POST"])
@log_request
@require_auth
def write_file():
    """
    Write file to device.
    
    Body:
        {
            "path": "/sdcard/file.txt",
            "content": "file contents",
            "encoding": "text"  // or "base64"
        }
    
    Returns:
        {"path": "/sdcard/file.txt", "written": true, "size": 123}
    """
    ensure_connected()
    data = request.get_json() or {}
    
    path = data.get("path", "")
    content = data.get("content", "")
    encoding = data.get("encoding", "text")
    
    if not path:
        return jsonify(api_response(False, error="path required")), 400
    
    with tempfile.NamedTemporaryFile(delete=False, mode="wb") as tmp:
        if encoding == "base64":
            tmp.write(base64.b64decode(content))
        else:
            tmp.write(content.encode())
        tmp_path = tmp.name
    
    success, _, err = adb("push", tmp_path, path)
    size = os.path.getsize(tmp_path)
    os.unlink(tmp_path)
    
    return jsonify(api_response(success,
        data={"path": path, "written": success, "size": size},
        error=err if not success else None
    ))

# =============================================================================
# API Endpoints: Device
# =============================================================================

@app.route("/device/info", methods=["GET"])
@log_request
@require_auth
def device_info():
    """
    Get device information.
    
    Returns comprehensive device info including model, Android version, etc.
    """
    ensure_connected()
    
    props = [
        ("model", "ro.product.model"),
        ("brand", "ro.product.brand"),
        ("manufacturer", "ro.product.manufacturer"),
        ("android_version", "ro.build.version.release"),
        ("sdk_version", "ro.build.version.sdk"),
        ("build_id", "ro.build.id"),
        ("fingerprint", "ro.build.fingerprint")
    ]
    
    info = {}
    for name, prop in props:
        _, value, _ = adb_shell(f"getprop {prop}")
        info[name] = value
    
    # Add screen info
    screen = get_screen_info()
    info["screen"] = {
        "width": screen["width"],
        "height": screen["height"],
        "density": screen["density"]
    }
    
    return jsonify(api_response(True, data=info))

@app.route("/device/status", methods=["GET"])
@log_request
@require_auth
def device_status():
    """
    Get device status (battery, network, etc).
    """
    ensure_connected()
    
    status = {}
    
    # Battery
    _, out, _ = adb_shell("dumpsys battery")
    if out:
        level_match = re.search(r'level: (\d+)', out)
        status_match = re.search(r'status: (\d+)', out)
        status["battery"] = {
            "level": int(level_match.group(1)) if level_match else None,
            "status": int(status_match.group(1)) if status_match else None
        }
    
    # Network
    _, out, _ = adb_shell("dumpsys connectivity | grep 'NetworkAgentInfo'")
    status["network_connected"] = "CONNECTED" in out if out else False
    
    # ADB connected
    status["adb_connected"] = ensure_connected()
    
    return jsonify(api_response(True, data=status))

# =============================================================================
# API Endpoints: Shell
# =============================================================================

@app.route("/shell", methods=["POST"])
@log_request
@require_auth
def shell_command():
    """
    Execute arbitrary shell command.
    
    Body:
        {
            "command": "ls /sdcard",
            "timeout": 30
        }
    
    Returns:
        {"command": "ls /sdcard", "stdout": "...", "stderr": "", "exit_code": 0}
    """
    ensure_connected()
    data = request.get_json() or {}
    
    command = data.get("command", "")
    timeout = data.get("timeout", DEFAULT_TIMEOUT)
    
    if not command:
        return jsonify(api_response(False, error="command required")), 400
    
    success, stdout, stderr = adb_shell(command, timeout=timeout)
    
    logger.info(f"Shell: {command[:50]}... - success={success}")
    
    return jsonify(api_response(success, data={
        "command": command,
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": 0 if success else 1
    }))

# =============================================================================
# API Endpoints: Wait/Sync
# =============================================================================

@app.route("/wait/idle", methods=["POST"])
@log_request
@require_auth
def wait_idle():
    """
    Wait for device to become idle (animations complete).
    
    Body:
        {
            "timeout": 10  // Max wait time in seconds
        }
    
    Returns:
        {"idle": true, "waited_ms": 500}
    """
    data = request.get_json() or {}
    timeout = data.get("timeout", 10)
    
    ensure_connected()
    
    start = time.time()
    # Use dumpsys to check window animation state
    adb_shell(f"cmd activity idle {timeout * 1000}")
    waited = int((time.time() - start) * 1000)
    
    return jsonify(api_response(True, data={"idle": True, "waited_ms": waited}))

@app.route("/wait/activity", methods=["POST"])
@log_request
@require_auth
def wait_activity():
    """
    Wait for specific activity to appear.
    
    Body:
        {
            "package": "com.example",
            "activity": "MainActivity",  // Optional
            "timeout": 10
        }
    """
    data = request.get_json() or {}
    package = data.get("package", "")
    activity = data.get("activity", "")
    timeout = data.get("timeout", 10)
    
    if not package:
        return jsonify(api_response(False, error="package required")), 400
    
    ensure_connected()
    
    start = time.time()
    while time.time() - start < timeout:
        _, out, _ = adb_shell("dumpsys activity activities | grep mResumedActivity")
        if package in out:
            if not activity or activity in out:
                waited = int((time.time() - start) * 1000)
                return jsonify(api_response(True, data={
                    "found": True,
                    "package": package,
                    "waited_ms": waited
                }))
        time.sleep(0.5)
    
    return jsonify(api_response(False, data={
        "found": False,
        "package": package,
        "timeout": True
    }))

# =============================================================================
# Health Check
# =============================================================================

@app.route("/health", methods=["GET"])
@log_request
def health():
    """Health check endpoint."""
    connected = ensure_connected()
    return jsonify(api_response(connected, data={
        "adb_connected": connected,
        "adb_target": ADB_TARGET,
        "api_version": "1.0.0"
    }))

@app.route("/", methods=["GET"])
def index():
    """API documentation."""
    endpoints = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint != 'static':
            doc = app.view_functions[rule.endpoint].__doc__
            endpoints.append({
                "path": rule.rule,
                "methods": list(rule.methods - {"OPTIONS", "HEAD"}),
                "description": doc.strip().split("\n")[0] if doc else ""
            })
    
    return jsonify({
        "name": "Cloud Phone Agent API",
        "version": "1.0.0",
        "description": "LLM-agent-friendly Android automation API",
        "endpoints": sorted(endpoints, key=lambda x: x["path"])
    })

# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    host = os.environ.get("API_HOST", "0.0.0.0")
    port = int(os.environ.get("API_PORT", "8081"))
    debug = os.environ.get("DEBUG", "false").lower() == "true"
    
    logger.info(f"Starting Cloud Phone Agent API on {host}:{port}")
    logger.info(f"ADB target: {ADB_TARGET}")
    logger.info(f"Log directory: {LOG_DIR}")
    
    app.run(host=host, port=port, debug=debug, threaded=True)
