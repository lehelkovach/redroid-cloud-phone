# Changelog

All notable changes to this project are documented in this file.

## [0.3.0] - 2026-01-22

### Added
- **Service Orchestration**: New `service-orchestrator.sh` for managing services with dependency ordering
- **Services Command**: `./cloud-phone services` with start/stop/restart/status/health/deps actions
- **Service Tests**: Comprehensive test coverage in `tests/test_services.py`
  - Service file existence and validation
  - Dependency ordering verification
  - Health checks for all services
  - Restart and recovery tests
  - Inter-service communication tests
- **Log Type Labels**: Structured logging with source labels (RDR, LCT, API, NGX, FFM, etc.)
- **Unified Log**: Combined `unified.log` with all sources and type labels
- **Log Filtering**: Filter logs by type, level, and search patterns
- **API Dockerfile**: Container build for control-api service

### Changed
- **Systemd Services**: Fixed dependency order, added PartOf/BindsTo relationships
  - `redroid-container.service`: Added health check, proper cleanup
  - `nginx-rtmp.service`: Fixed duplicate WantedBy
  - `ffmpeg-bridge.service`: Added BindsTo nginx-rtmp
  - `control-api.service`: Added ADB pre-check, health endpoint check
  - `log-collector.service`: Proper forking service setup
- **Docker Compose**: Updated with health checks, profiles (streaming, logging, proxy, all)
- **Config Schema**: Added `unified.log` to logging.files

### Fixed
- Duplicate WantedBy in nginx-rtmp and ffmpeg-bridge services
- Target file not including log-collector.service

## [0.2.0] - 2026-01-21

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

## [0.1.0] - 2026-01-09 - Initial Release

### Added
- Redroid Docker container configuration
- Control API with 11+ endpoints (server.py)
- Agent API for LLM automation (agent_api.py)
- VNC access (port 5900)
- ADB access (port 5555)
- nginx-rtmp service for RTMP ingest
- ffmpeg-bridge service for video/audio conversion
- v4l2loopback virtual camera support
- ALSA loopback virtual microphone support
- OBS streaming support
- Full test coverage for API endpoints
- Streaming pipeline tests
- Connectivity tests
- Orchestrator service for multi-instance management
- Terraform configuration for OCI deployment
- Docker build scripts and Dockerfiles

### Technical Decisions
- Chose Redroid over Waydroid for Docker compatibility
- Selected Ubuntu 20.04 for kernel 5.x (v4l2loopback support)
- Used ARM64 OCI instances (Always Free tier)

---

## Version History

| Version | Date | Highlights |
|---------|------|------------|
| 0.3.0 | 2026-01-22 | Service orchestration, log labels, services tests |
| 0.2.0 | 2026-01-21 | Documentation consolidation, test suite |
| 0.1.0 | 2026-01-09 | Initial release with Redroid, API, streaming |

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
