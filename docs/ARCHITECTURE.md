# Architecture

This document describes the system architecture of the Redroid Cloud Phone project.

## System Overview

```mermaid
graph TB
    subgraph "External"
        OBS[OBS Studio]
        User[User/Agent]
    end

    subgraph "OCI Instance"
        subgraph "Streaming Pipeline"
            NGINX[nginx-rtmp<br/>:1935]
            FFMPEG[ffmpeg-bridge]
            V4L2[/dev/video42<br/>v4l2loopback]
            ALSA[ALSA Loopback<br/>snd-aloop]
        end

        subgraph "Android Container"
            REDROID[Redroid Docker<br/>Android 11]
            ADB[ADB Server<br/>:5555]
            VNC[VNC Server<br/>:5900]
        end

        subgraph "Control Layer"
            API[Control API<br/>:8080]
            ORCH[Orchestrator<br/>:8000]
        end
    end

    OBS -->|RTMP Stream| NGINX
    NGINX -->|localhost RTMP| FFMPEG
    FFMPEG -->|Video Frames| V4L2
    FFMPEG -->|Audio PCM| ALSA
    V4L2 -->|Virtual Camera| REDROID
    ALSA -->|Virtual Mic| REDROID
    
    User -->|SSH/scrcpy| ADB
    User -->|VNC Client| VNC
    User -->|REST API| API
    API -->|ADB Commands| REDROID
    ORCH -->|Coordinates| API
```

## Component Details

### Streaming Pipeline

| Component | Service | Port | Purpose |
|-----------|---------|------|---------|
| nginx-rtmp | `nginx-rtmp.service` | 1935 | RTMP ingest from OBS |
| ffmpeg-bridge | `ffmpeg-bridge.service` | - | Transcode RTMP to virtual devices |
| v4l2loopback | kernel module | /dev/video42 | Virtual camera device |
| snd-aloop | kernel module | hw:Loopback | Virtual audio device |

### Android Container

| Component | Service | Port | Purpose |
|-----------|---------|------|---------|
| Redroid | `redroid-container.service` | - | Android 11 in Docker |
| ADB | exposed by Redroid | 5555 | Android Debug Bridge |
| VNC | built into Redroid | 5900 | Screen viewing |

### Control Layer

| Component | Service | Port | Purpose |
|-----------|---------|------|---------|
| Control API | `control-api.service` | 8080 | REST API for automation |
| Orchestrator | `orchestrator.service` | 8000 | Multi-instance coordination |

## Data Flow

### Video/Audio Streaming

```mermaid
sequenceDiagram
    participant OBS as OBS Studio
    participant NGINX as nginx-rtmp
    participant FF as ffmpeg-bridge
    participant V4L as /dev/video42
    participant RD as Redroid
    participant App as Camera App

    OBS->>NGINX: RTMP stream (rtmp://IP/live/cam)
    NGINX->>FF: localhost RTMP
    FF->>V4L: YUV420P frames @ 15fps
    FF->>ALSA: PCM audio @ 44.1kHz
    RD->>V4L: Read video frames
    RD->>ALSA: Read audio samples
    App->>RD: Camera API (requires HAL)
    Note over App,RD: Camera HAL missing in standard Redroid
```

### API Control Flow

```mermaid
sequenceDiagram
    participant Agent as LLM Agent
    participant API as Control API
    participant ADB as ADB Server
    participant RD as Redroid

    Agent->>API: POST /adb/shell {"command": "..."}
    API->>ADB: adb shell command
    ADB->>RD: Execute in Android
    RD-->>ADB: Output
    ADB-->>API: Result
    API-->>Agent: JSON response
```

## Directory Structure

```
/opt/
├── cloud-phone-api/          # Control API
│   ├── server.py
│   └── requirements.txt
├── redroid-scripts/          # Operational scripts
│   ├── ffmpeg-bridge.sh
│   ├── health-check.sh
│   ├── install-gapps.sh
│   └── ...
├── redroid-data/             # Redroid persistent data
└── gapps/                    # Google Apps packages

/etc/systemd/system/
├── redroid-container.service
├── control-api.service
├── nginx-rtmp.service
├── ffmpeg-bridge.service
└── redroid-cloud-phone.target
```

## Network Architecture

```mermaid
graph LR
    subgraph "Internet"
        OBS[OBS Studio]
        CLIENT[Client]
    end

    subgraph "OCI Instance"
        FW[iptables]
        
        subgraph "Ports"
            P22[22/SSH]
            P1935[1935/RTMP]
            P5555[5555/ADB]
            P5900[5900/VNC]
            P8080[8080/API]
        end
    end

    OBS -->|RTMP| P1935
    CLIENT -->|SSH Tunnel| P22
    P22 -.->|Tunneled| P5555
    P22 -.->|Tunneled| P5900
    P22 -.->|Tunneled| P8080
```

**Security Note:** ADB (5555), VNC (5900), and API (8080) should be accessed via SSH tunnel. Only ports 22 (SSH) and 1935 (RTMP) need to be open in OCI security lists.

## Proxy Architecture (Optional)

```mermaid
graph LR
    subgraph "Redroid Container"
        APP[Android Apps]
        TUN[tun2socks]
    end

    subgraph "External"
        PROXY[SOCKS5 Proxy]
        INTERNET[Internet]
    end

    APP -->|All traffic| TUN
    TUN -->|SOCKS5| PROXY
    PROXY -->|Forwarded| INTERNET
```

When proxy is enabled via `socks5-toggle.sh`, all Android network traffic routes through the specified SOCKS5 proxy.

## Known Limitations

### Camera HAL

Standard Redroid images do not include a Camera HAL (Hardware Abstraction Layer). This means:

- `/dev/video42` is accessible inside the container ✅
- Android's CameraService runs ✅
- **But apps cannot detect cameras** ❌

```
CameraService → Camera Provider → camera.v4l2.so → /dev/video42
                     ↑
                 MISSING
```

**Workaround:** Use VLC app to view RTMP stream directly (`rtmp://127.0.0.1/live/cam`).

See [docs/CAMERA_HAL_FIX.md](CAMERA_HAL_FIX.md) for potential solutions.

## Resource Requirements

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| OCPUs | 1 | 2 |
| Memory | 4GB | 8GB |
| Disk | 20GB | 50GB |
| Ubuntu | 20.04 | 20.04 |
| Kernel | 5.x | 5.15+ |

**Note:** Ubuntu 20.04 with kernel 5.x is required for v4l2loopback and snd-aloop module compatibility.
