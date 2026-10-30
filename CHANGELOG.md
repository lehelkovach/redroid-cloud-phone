# Changelog

Notable changes, newest first. Dates are when the change was made on a branch.

## Unreleased

### Added

- Per-owner, size-limited, encrypted volumes for phone `/data` and browser
  profiles, behind two flags that default to the old behaviour:
  `ORCH_PHONE_DATA=per_user` and `ORCH_BROWSER_PROFILE=volume`
  (`orchestrator/user_volumes.py`). Each owner gets a sparse LUKS2 + ext4 image
  per kind, sized to a quota (`ORCH_QUOTA_PHONE_GB=8`, `ORCH_QUOTA_BROWSER_GB=1`,
  per-owner overrides in `ORCH_VOLUME_QUOTA_FILE`), unlocked with a key derived
  for that owner and kind from `ORCH_VOLUME_MASTER_KEY_FILE`, mounted only while
  leased. New images are refused with `507 host_disk_low` below
  `ORCH_HOST_MIN_FREE_GB`. New endpoints `GET /volumes/<owner>` and
  `DELETE /volumes/<owner>/<kind>`; `/health` gains `volumes`. Usage warnings
  at 80 % and 95 %. See [docs/isolation.md](docs/isolation.md#per-owner-volumes-phone-data-and-browser-profiles)
  for enabling and rollback (2026-10-30).
- `scripts/redroid-up.sh --data-dir PATH` bind-mounts PATH as the phone's
  `/data` (`REDROID_DATA_SOURCE` in both Redroid compose files).

### Changed

- With `ORCH_PHONE_DATA=per_user`, `POST /instances/<id>/lease` answers
  `409 use_sessions`, and an instance that ran on per-user volumes is refused
  once the flag is turned off until it is reprovisioned.
