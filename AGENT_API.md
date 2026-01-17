# Cloud Phone Agent API

A REST API designed for consumption by LLM-based automation agents (like osl-agent-prototype).

## Overview

This API provides a simple, consistent interface for controlling Android devices programmatically. It's optimized for:

- **LLM Tool Use**: Clear, simple endpoints that map to tool commands
- **Automation**: Reliable input methods with both pixel and percentage coordinates
- **Visibility**: Screenshots and screen info for visual feedback
- **Logging**: Comprehensive logging for debugging and analysis

## Quick Start

```bash
# Start the API
python3 api/agent_api.py

# Or via systemd
sudo systemctl start agent-api

# Test health
curl http://localhost:8081/health
```

## API Design Principles

1. **Consistent Response Format**: All endpoints return:
   ```json
   {
     "success": true|false,
     "data": {...} | null,
     "error": "error message" | null,
     "timestamp": "2024-01-15T12:00:00Z"
   }
   ```

2. **Coordinate Flexibility**: Input coordinates can be:
   - **Pixels**: Absolute x, y values
   - **Percentages**: 0-100 values (use `"percentage": true`)

3. **Idempotent Operations**: Most operations can be safely retried

4. **Detailed Logging**: All requests logged to `/var/log/cloud-phone/agent-api.log`

---

## Endpoints

### Screen Operations

#### GET /screen/info
Get screen dimensions and orientation.

**Response:**
```json
{
  "success": true,
  "data": {
    "width": 1080,
    "height": 2400,
    "density": 420,
    "orientation": 0
  }
}
```

**Agent Tool Definition:**
```yaml
name: get_screen_info
description: Get the screen dimensions and orientation of the Android device
parameters: {}
returns:
  width: Screen width in pixels
  height: Screen height in pixels
  density: Screen DPI
  orientation: 0=portrait, 1=landscape
```

---

#### GET /screen/screenshot
Capture the current screen.

**Query Parameters:**
- `format`: "png" (binary), "base64" (JSON with base64 image)

**Response (format=base64):**
```json
{
  "success": true,
  "data": {
    "image": "iVBORw0KGgoAAAANSUhEUgAA...",
    "format": "png",
    "width": 1080,
    "height": 2400
  }
}
```

**Agent Tool Definition:**
```yaml
name: take_screenshot
description: Capture the current screen as an image
parameters:
  format:
    type: string
    enum: [png, base64]
    default: base64
returns:
  image: Base64 encoded PNG image
  width: Image width
  height: Image height
```

---

### Input Operations

#### POST /input/tap
Tap at a specific location.

**Request Body:**
```json
{
  "x": 540,
  "y": 1200,
  "percentage": false
}
```

Or with percentage coordinates:
```json
{
  "x": 50,
  "y": 50,
  "percentage": true
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "x": 540,
    "y": 1200,
    "action": "tap"
  }
}
```

**Agent Tool Definition:**
```yaml
name: tap
description: Tap at a location on the screen
parameters:
  x:
    type: number
    description: X coordinate (pixels or percentage)
  y:
    type: number
    description: Y coordinate (pixels or percentage)
  percentage:
    type: boolean
    default: false
    description: If true, x and y are percentages (0-100)
```

---

#### POST /input/swipe
Perform a swipe gesture.

**Request Body:**
```json
{
  "x1": 540,
  "y1": 1800,
  "x2": 540,
  "y2": 600,
  "duration": 300,
  "percentage": false
}
```

**Agent Tool Definition:**
```yaml
name: swipe
description: Perform a swipe gesture from one point to another
parameters:
  x1:
    type: number
    description: Start X coordinate
  y1:
    type: number
    description: Start Y coordinate
  x2:
    type: number
    description: End X coordinate
  y2:
    type: number
    description: End Y coordinate
  duration:
    type: integer
    default: 300
    description: Swipe duration in milliseconds
  percentage:
    type: boolean
    default: false
```

**Common Swipe Patterns:**
```
Swipe Up:    {"x1": 50, "y1": 75, "x2": 50, "y2": 25, "percentage": true}
Swipe Down:  {"x1": 50, "y1": 25, "x2": 50, "y2": 75, "percentage": true}
Swipe Left:  {"x1": 75, "y1": 50, "x2": 25, "y2": 50, "percentage": true}
Swipe Right: {"x1": 25, "y1": 50, "x2": 75, "y2": 50, "percentage": true}
```

---

#### POST /input/long_press
Long press at a location.

**Request Body:**
```json
{
  "x": 50,
  "y": 50,
  "duration": 1000,
  "percentage": true
}
```

---

#### POST /input/text
Type text.

**Request Body:**
```json
{
  "text": "Hello World",
  "clear_first": false
}
```

**Agent Tool Definition:**
```yaml
name: type_text
description: Type text on the currently focused input field
parameters:
  text:
    type: string
    description: The text to type
  clear_first:
    type: boolean
    default: false
    description: Clear the field before typing
```

---

#### POST /input/key
Send a key event.

**Request Body:**
```json
{
  "key": "KEYCODE_ENTER"
}
```

**Common Key Codes:**
| Key | Code |
|-----|------|
| Home | KEYCODE_HOME or 3 |
| Back | KEYCODE_BACK or 4 |
| Enter | KEYCODE_ENTER or 66 |
| Delete | KEYCODE_DEL or 67 |
| Tab | KEYCODE_TAB or 61 |
| Escape | KEYCODE_ESCAPE or 111 |
| Menu | KEYCODE_MENU or 82 |

---

#### Shortcut Endpoints

| Endpoint | Description |
|----------|-------------|
| `POST /input/back` | Press back button |
| `POST /input/home` | Press home button |
| `POST /input/recent` | Open recent apps |

---

### App Operations

#### GET /apps
List installed apps.

**Query Parameters:**
- `type`: "user" (default), "system", "all"

**Response:**
```json
{
  "success": true,
  "data": {
    "packages": ["com.example.app1", "com.example.app2"],
    "count": 2
  }
}
```

---

#### POST /apps/{package}/launch
Launch an app.

**Example:**
```
POST /apps/com.android.settings/launch
```

**Response:**
```json
{
  "success": true,
  "data": {
    "package": "com.android.settings",
    "launched": true
  }
}
```

---

#### POST /apps/{package}/close
Force close an app.

---

#### GET /apps/current
Get the currently visible app.

**Response:**
```json
{
  "success": true,
  "data": {
    "package": "com.android.settings",
    "activity": "Settings"
  }
}
```

---

### Device Operations

#### GET /device/info
Get device information.

**Response:**
```json
{
  "success": true,
  "data": {
    "model": "SM-G991B",
    "brand": "samsung",
    "android_version": "12",
    "sdk_version": "31",
    "screen": {
      "width": 1080,
      "height": 2400,
      "density": 420
    }
  }
}
```

---

#### GET /device/status
Get device status.

**Response:**
```json
{
  "success": true,
  "data": {
    "battery": {"level": 73, "status": 3},
    "network_connected": true,
    "adb_connected": true
  }
}
```

---

### File Operations

#### GET /files/list
List files in a directory.

**Query Parameters:**
- `path`: Directory path (default: /sdcard)

---

#### GET /files/read
Read file contents.

**Query Parameters:**
- `path`: File path (required)
- `encoding`: "text" (default) or "base64"

---

#### POST /files/write
Write file to device.

**Request Body:**
```json
{
  "path": "/sdcard/test.txt",
  "content": "Hello World",
  "encoding": "text"
}
```

---

### Shell Operations

#### POST /shell
Execute shell command.

**Request Body:**
```json
{
  "command": "ls /sdcard",
  "timeout": 30
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "command": "ls /sdcard",
    "stdout": "Download\nPictures\nDocuments",
    "stderr": "",
    "exit_code": 0
  }
}
```

---

### Wait/Sync Operations

#### POST /wait/idle
Wait for device to become idle (animations complete).

**Request Body:**
```json
{
  "timeout": 10
}
```

---

#### POST /wait/activity
Wait for specific activity to appear.

**Request Body:**
```json
{
  "package": "com.example",
  "activity": "MainActivity",
  "timeout": 10
}
```

---

## LLM Agent Integration

### Tool Definitions for osl-agent-prototype

```python
ANDROID_TOOLS = [
    {
        "name": "android_screenshot",
        "description": "Capture the current screen of the Android device",
        "parameters": {},
        "endpoint": "GET /screen/screenshot?format=base64"
    },
    {
        "name": "android_tap",
        "description": "Tap at a location. Use percentage=true for relative coordinates (0-100)",
        "parameters": {
            "x": "number - X coordinate",
            "y": "number - Y coordinate", 
            "percentage": "boolean - If true, x/y are percentages"
        },
        "endpoint": "POST /input/tap"
    },
    {
        "name": "android_swipe",
        "description": "Swipe from one point to another",
        "parameters": {
            "x1": "number - Start X",
            "y1": "number - Start Y",
            "x2": "number - End X",
            "y2": "number - End Y",
            "percentage": "boolean"
        },
        "endpoint": "POST /input/swipe"
    },
    {
        "name": "android_type",
        "description": "Type text in the currently focused field",
        "parameters": {
            "text": "string - Text to type"
        },
        "endpoint": "POST /input/text"
    },
    {
        "name": "android_press_key",
        "description": "Press a key (back, home, enter, etc)",
        "parameters": {
            "key": "string - Key name like KEYCODE_BACK or KEYCODE_HOME"
        },
        "endpoint": "POST /input/key"
    },
    {
        "name": "android_launch_app",
        "description": "Launch an app by package name",
        "parameters": {
            "package": "string - Package name like com.android.settings"
        },
        "endpoint": "POST /apps/{package}/launch"
    },
    {
        "name": "android_current_app",
        "description": "Get the currently visible app",
        "parameters": {},
        "endpoint": "GET /apps/current"
    },
    {
        "name": "android_shell",
        "description": "Execute a shell command on the device",
        "parameters": {
            "command": "string - Shell command to execute"
        },
        "endpoint": "POST /shell"
    }
]
```

### Example Agent Workflow

```
Agent Task: "Open Settings and enable WiFi"

1. Agent calls: android_launch_app(package="com.android.settings")
2. Agent calls: android_screenshot() -> sees Settings menu
3. Agent analyzes screenshot, finds "Network & internet" at ~50% x, 30% y
4. Agent calls: android_tap(x=50, y=30, percentage=true)
5. Agent calls: android_screenshot() -> sees Network settings
6. Agent finds WiFi toggle, calls: android_tap(x=85, y=25, percentage=true)
7. Agent calls: android_screenshot() -> confirms WiFi is enabled
8. Agent reports: "WiFi has been enabled successfully"
```

---

## Error Handling

### Error Response Format

```json
{
  "success": false,
  "data": null,
  "error": "ADB connection failed: device offline",
  "timestamp": "2024-01-15T12:00:00Z"
}
```

### Common Errors

| Error | Cause | Solution |
|-------|-------|----------|
| "ADB not connected" | Device offline | Restart Redroid container |
| "Command timed out" | Device unresponsive | Increase timeout or restart |
| "Invalid coordinates" | x/y out of range | Check screen dimensions |
| "Package not found" | App not installed | Verify package name |

---

## Logging

All API requests are logged to `/var/log/cloud-phone/agent-api.log`:

```
2024-01-15 12:00:00 - INFO - POST /input/tap - 127.0.0.1
2024-01-15 12:00:00 - INFO - Tap at (540, 1200) - success=True
2024-01-15 12:00:01 - INFO - GET /screen/screenshot - 127.0.0.1
```

### Log Levels

- **DEBUG**: Detailed ADB commands and responses
- **INFO**: All API requests and results
- **WARNING**: Recoverable errors
- **ERROR**: Failed operations

---

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ADB_HOST` | 127.0.0.1 | ADB host |
| `ADB_PORT` | 5555 | ADB port |
| `API_HOST` | 0.0.0.0 | API listen address |
| `API_PORT` | 8081 | API port |
| `API_TOKEN` | (none) | Authentication token |
| `LOG_DIR` | /var/log/cloud-phone | Log directory |
| `DEFAULT_TIMEOUT` | 30 | Default timeout in seconds |

Note: `API_PORT` defaults to 8081 to avoid clashing with the legacy Control API on 8080.

### Authentication

Set `API_TOKEN` environment variable to require authentication:

```bash
API_TOKEN=my-secret-token python3 agent_api.py
```

Then include in requests:
```
Authorization: Bearer my-secret-token
```

---

## Testing

Run the test suite:

```bash
# Local testing
python3 tests/test_agent_api.py --api-url http://localhost:8081

# With logging
python3 tests/test_agent_api.py \
  --api-url http://localhost:8081 \
  --log-file /var/log/test-results.log \
  --output-json /var/log/test-results.json \
  --verbose
```
