# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Added
- Comprehensive test suite (75 tests across unit, integration, E2E)
- Virtual camera/audio device testing
- Documentation consolidation into docs/ folder
- Mermaid architecture diagrams
- Camera HAL documentation and workarounds

### Changed
- Reorganized documentation structure
- Updated README with proper TOC and links
- Consolidated redundant documentation files

### Fixed
- Screenshot API endpoint ADB connection issue
- Test stream key collision in E2E tests

## [2026-01-29] Documentation Consolidation

### Changed
- Moved all documentation to `docs/` folder
- Created comprehensive README.md with TOC
- Added Mermaid diagrams to ARCHITECTURE.md
- Consolidated 41 markdown files into organized structure

### Removed
- Redundant status/progress files
- Outdated handoff documents
- Duplicate research documents

## [2026-01-14] Streaming Pipeline

### Added
- nginx-rtmp service for RTMP ingest
- ffmpeg-bridge service for video/audio conversion
- v4l2loopback virtual camera support
- ALSA loopback virtual microphone support
- OBS streaming support

### Changed
- Updated systemd services for auto-recovery
- Improved health check script

## [2026-01-11] Test Coverage

### Added
- Full test coverage for API endpoints
- Streaming pipeline tests
- Connectivity tests
- Orchestrator tests

## [2026-01-09] Initial Redroid Setup

### Added
- Redroid Docker container configuration
- Control API with 11+ endpoints
- VNC access (port 5900)
- ADB access (port 5555)
- Basic deployment scripts

### Technical Decisions
- Chose Redroid over Waydroid for Docker compatibility
- Selected Ubuntu 20.04 for kernel 5.x (v4l2loopback support)
- Used ARM64 OCI instances (Always Free tier)

---

## Version History

| Date | Version | Highlights |
|------|---------|------------|
| 2026-01-29 | - | Documentation consolidation, test suite |
| 2026-01-14 | - | Streaming pipeline, ffmpeg bridge |
| 2026-01-11 | - | Test coverage, API testing |
| 2026-01-09 | - | Initial Redroid setup |

## Known Issues

1. **Camera HAL Missing** - Android apps cannot detect virtual camera
   - Status: Documented workaround (VLC)
   - See: [CAMERA_HAL_FIX.md](CAMERA_HAL_FIX.md)

2. **Ubuntu 22.04 Kernel Incompatibility**
   - Status: Use Ubuntu 20.04 instead
   - Kernel 6.8+ breaks v4l2loopback module

## Future Plans

- [ ] Build custom Redroid image with Camera HAL
- [ ] Add WebRTC viewing option
- [ ] Multi-instance orchestration improvements
- [ ] Automated golden image creation
