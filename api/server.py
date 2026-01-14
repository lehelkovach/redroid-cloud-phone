#!/usr/bin/env python3
"""
Cloud Phone Control API Server

Thin HTTP API wrapping ADB for remote Android automation.
ADB-first design: all operations go through ADB, not VNC.

Works with both:
- Redroid: default `ADB_CONNECT=127.0.0.1:5555` (host-mapped ADB)
- Waydroid (legacy): falls back to `192.168.240.112:5555`

Endpoints:
  GET  /device/info        - Device dimensions and density
  GET  /device/screenshot  - PNG screenshot
  POST /device/tap         - Tap at coordinates
  POST /device/swipe       - Swipe gesture
  POST /device/press       - Long press
  POST /device/text        - Input text
  POST /device/key         - Press key
  GET  /health             - Health check
"""

import os
import subprocess
import tempfile
import logging
from typing import Dict, Tuple, Optional, List
from flask import Flask, request, jsonify, send_file, Response
from functools import wraps

app = Flask(__name__)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
API_HOST = os.environ.get('API_HOST', '127.0.0.1')
API_PORT = int(os.environ.get('API_PORT', 8080))
ADB_DEVICE = os.environ.get('ADB_DEVICE', '')  # Empty = default device
ADB_CONNECT = os.environ.get('ADB_CONNECT', '127.0.0.1:5555')  # host:port for adb connect (optional)

# Legacy Waydroid fallback (only used if ADB_CONNECT fails)
WAYDROID_FALLBACK = '192.168.240.112:5555'


def run_adb(args, capture_output=True, binary=False):
    """Execute ADB command and return output."""
    cmd = ['adb']
    if ADB_DEVICE:
        cmd.extend(['-s', ADB_DEVICE])
    cmd.extend(args)
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=capture_output,
            timeout=30
        )
        if result.returncode != 0:
            stderr = (result.stderr or b'').decode('utf-8', errors='ignore').strip()
            stdout = (result.stdout or b'').decode('utf-8', errors='ignore').strip()
            msg = stderr or stdout or f"adb exited with code {result.returncode}"
            raise RuntimeError(msg)
        if binary:
            return result.stdout
        return result.stdout.decode('utf-8', errors='ignore').strip()
    except subprocess.TimeoutExpired:
        logger.error(f"ADB command timed out: {' '.join(cmd)}")
        raise
    except Exception as e:
        logger.error(f"ADB command failed: {e}")
        raise


def run_adb_shell(command: str) -> str:
    """Run an ADB shell command preserving quoting/pipes."""
    return run_adb(['shell', 'sh', '-c', command])


def parse_adb_devices(output: str) -> Dict[str, str]:
    """
    Parse `adb devices` output into {serial: state}.
    Example line: "127.0.0.1:5555 device"
    """
    devices: Dict[str, str] = {}
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    for ln in lines[1:]:  # skip header
        parts = ln.split()
        if len(parts) >= 2:
            devices[parts[0]] = parts[1]
    return devices


def get_connected_state() -> Tuple[bool, Dict[str, str]]:
    """Return (connected, devices_map)."""
    devices_out = run_adb(['devices'])
    devices = parse_adb_devices(devices_out)
    if ADB_DEVICE:
        return (devices.get(ADB_DEVICE) == 'device', devices)
    return (any(state == 'device' for state in devices.values()), devices)


def adb_connect(target: str) -> None:
    """Connect to ADB target (host:port)."""
    run_adb(['connect', target])


def ensure_adb_connected():
    """Ensure ADB is connected to a device (Redroid or Waydroid)."""
    try:
        connected, devices = get_connected_state()
        if connected:
            return

        # Prefer explicit ADB_CONNECT; fall back to legacy Waydroid IP.
        targets: List[str] = []
        if ADB_CONNECT:
            targets.append(ADB_CONNECT)
        targets.append(WAYDROID_FALLBACK)

        last_err: Optional[Exception] = None
        for t in targets:
            try:
                adb_connect(t)
                if get_connected_state()[0]:
                    return
            except Exception as e:
                last_err = e

        if last_err:
            raise last_err
    except Exception as e:
        logger.warning(f"ADB connection check failed: {e}")


def get_device_info():
    """Get device screen dimensions and density."""
    try:
        size_output = run_adb(['shell', 'wm', 'size'])
        density_output = run_adb(['shell', 'wm', 'density'])
        
        # Parse "Physical size: 1080x1920"
        width, height = 1080, 1920
        if 'x' in size_output:
            parts = size_output.split(':')[-1].strip().split('x')
            width, height = int(parts[0]), int(parts[1])
        
        # Parse "Physical density: 320"
        density = 320
        if ':' in density_output:
            density = int(density_output.split(':')[-1].strip())
        
        return {'width': width, 'height': height, 'density': density}
    except Exception as e:
        logger.error(f"Failed to get device info: {e}")
        return {'width': 1080, 'height': 1920, 'density': 320}


def validate_json(*required_fields):
    """Decorator to validate JSON request body."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not request.is_json:
                return jsonify({'error': 'Content-Type must be application/json'}), 400
            
            data = request.get_json()
            missing = [field for field in required_fields if field not in data]
            if missing:
                return jsonify({'error': f'Missing fields: {missing}'}), 400
            
            return f(data, *args, **kwargs)
        return wrapper
    return decorator


# ============================================
# API Endpoints
# ============================================

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint."""
    try:
        ensure_adb_connected()
        devices = run_adb(['devices'])
        connected = 'device' in devices
        return jsonify({
            'status': 'healthy' if connected else 'degraded',
            'adb_connected': connected
        })
    except Exception as e:
        return jsonify({'status': 'unhealthy', 'error': str(e)}), 500


@app.route('/device/info', methods=['GET'])
def device_info():
    """Get device dimensions and density."""
    ensure_adb_connected()
    info = get_device_info()
    
    # Add rotation if available
    try:
        rotation_output = run_adb_shell('dumpsys input | grep -m 1 SurfaceOrientation || true')
        # Example: "SurfaceOrientation: 0"
        if ':' in rotation_output:
            info['rotation'] = int(rotation_output.split(':')[-1].strip() or '0')
        else:
            info['rotation'] = 0
    except Exception:
        info['rotation'] = 0
    
    return jsonify(info)


@app.route('/device/screenshot', methods=['GET'])
def screenshot():
    """Take a screenshot and return PNG."""
    ensure_adb_connected()
    
    try:
        # Use exec-out for streaming screenshot
        png_data = run_adb(['exec-out', 'screencap', '-p'], binary=True)
        
        if not png_data or len(png_data) < 100:
            return jsonify({'error': 'Failed to capture screenshot'}), 500
        
        return Response(
            png_data,
            mimetype='image/png',
            headers={'Content-Disposition': 'inline; filename=screenshot.png'}
        )
    except Exception as e:
        logger.error(f"Screenshot failed: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/device/tap', methods=['POST'])
@validate_json('x', 'y')
def tap(data):
    """
    Tap at coordinates.
    
    Body:
      x: X coordinate
      y: Y coordinate
      mode: "px" (pixel, default) or "norm" (normalized 0-1)
    """
    ensure_adb_connected()
    
    x, y = float(data['x']), float(data['y'])
    mode = data.get('mode', 'px')
    
    # Convert normalized to pixels
    if mode == 'norm':
        info = get_device_info()
        x = int(x * info['width'])
        y = int(y * info['height'])
    else:
        x, y = int(x), int(y)
    
    try:
        run_adb(['shell', 'input', 'tap', str(x), str(y)])
        return jsonify({'success': True, 'x': x, 'y': y})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/device/swipe', methods=['POST'])
@validate_json('x1', 'y1', 'x2', 'y2')
def swipe(data):
    """
    Swipe gesture.
    
    Body:
      x1, y1: Start coordinates
      x2, y2: End coordinates
      duration_ms: Duration in milliseconds (default: 300)
      mode: "px" (pixel, default) or "norm" (normalized 0-1)
    """
    ensure_adb_connected()
    
    x1, y1 = float(data['x1']), float(data['y1'])
    x2, y2 = float(data['x2']), float(data['y2'])
    duration = int(data.get('duration_ms', 300))
    mode = data.get('mode', 'px')
    
    if mode == 'norm':
        info = get_device_info()
        x1, y1 = int(x1 * info['width']), int(y1 * info['height'])
        x2, y2 = int(x2 * info['width']), int(y2 * info['height'])
    else:
        x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
    
    try:
        run_adb(['shell', 'input', 'swipe', str(x1), str(y1), str(x2), str(y2), str(duration)])
        return jsonify({'success': True, 'from': [x1, y1], 'to': [x2, y2], 'duration_ms': duration})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/device/press', methods=['POST'])
@validate_json('x', 'y')
def long_press(data):
    """
    Long press at coordinates.
    
    Body:
      x, y: Coordinates
      duration_ms: Duration in milliseconds (default: 1000)
      mode: "px" or "norm"
    """
    ensure_adb_connected()
    
    x, y = float(data['x']), float(data['y'])
    duration = int(data.get('duration_ms', 1000))
    mode = data.get('mode', 'px')
    
    if mode == 'norm':
        info = get_device_info()
        x, y = int(x * info['width']), int(y * info['height'])
    else:
        x, y = int(x), int(y)
    
    # Long press is a swipe with same start/end
    try:
        run_adb(['shell', 'input', 'swipe', str(x), str(y), str(x), str(y), str(duration)])
        return jsonify({'success': True, 'x': x, 'y': y, 'duration_ms': duration})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/device/text', methods=['POST'])
@validate_json('text')
def input_text(data):
    """
    Input text.
    
    Body:
      text: Text to input
    """
    ensure_adb_connected()
    
    text = data['text']
    # Escape special characters for shell
    text = text.replace(' ', '%s').replace("'", "\\'").replace('"', '\\"')
    
    try:
        run_adb(['shell', 'input', 'text', text])
        return jsonify({'success': True, 'text': data['text']})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/device/key', methods=['POST'])
@validate_json('keycode')
def press_key(data):
    """
    Press a key.
    
    Body:
      keycode: Android keycode (e.g., "KEYCODE_HOME", "KEYCODE_BACK", "KEYCODE_ENTER")
               or numeric code
    """
    ensure_adb_connected()
    
    keycode = data['keycode']
    
    # Allow both "KEYCODE_HOME" and just "HOME"
    if isinstance(keycode, str) and not keycode.startswith('KEYCODE_') and not keycode.isdigit():
        keycode = f'KEYCODE_{keycode.upper()}'
    
    try:
        run_adb(['shell', 'input', 'keyevent', str(keycode)])
        return jsonify({'success': True, 'keycode': keycode})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/device/shell', methods=['POST'])
@validate_json('command')
def shell_command(data):
    """
    Run arbitrary ADB shell command.
    
    Body:
      command: Shell command to run
    """
    ensure_adb_connected()
    
    command = data['command']
    
    try:
        output = run_adb_shell(command)
        return jsonify({'success': True, 'output': output})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/device/app/start', methods=['POST'])
@validate_json('package')
def start_app(data):
    """
    Start an app.
    
    Body:
      package: Package name (e.g., "com.android.camera")
      activity: Optional activity name
    """
    ensure_adb_connected()
    
    package = data['package']
    activity = data.get('activity', '')
    
    try:
        if activity:
            component = f'{package}/{activity}'
        else:
            # Get launcher activity
            output = run_adb(['shell', 'cmd', 'package', 'resolve-activity', '-c', 'android.intent.category.LAUNCHER', package])
            # Try to parse or use monkey
            run_adb(['shell', 'monkey', '-p', package, '-c', 'android.intent.category.LAUNCHER', '1'])
        
        return jsonify({'success': True, 'package': package})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/device/app/stop', methods=['POST'])
@validate_json('package')
def stop_app(data):
    """Stop an app."""
    ensure_adb_connected()
    
    package = data['package']
    
    try:
        run_adb(['shell', 'am', 'force-stop', package])
        return jsonify({'success': True, 'package': package})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================
# Main
# ============================================

if __name__ == '__main__':
    logger.info(f"Starting Control API on {API_HOST}:{API_PORT}")
    
    # Initial ADB connection attempt
    try:
        ensure_adb_connected()
        logger.info("ADB connection established")
    except Exception as e:
        logger.warning(f"Initial ADB connection failed: {e}")
    
    app.run(
        host=API_HOST,
        port=API_PORT,
        debug=False,
        threaded=True
    )
