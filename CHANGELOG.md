# Changelog

Notable changes, newest first. Dates are when the change was made on a branch.

## Unreleased

### Added

- OCI Compute phone provider, default off (2026-10-31): with
  `ORCH_PHONE_PROVIDER=oci` every new automation phone is its own OCI
  `VM.Standard.A1.Flex` instance (`ORCH_OCI_OCPUS`/`ORCH_OCI_MEMORY_GB`,
  default 2/12) launched from a custom image of the GApps phone host
  (`ORCH_OCI_IMAGE_ID`) on the private subnet, authenticated as the
  orchestrator instance (instance principal; a dev box falls back to the
  `oci` config file), tagged `oslo-owner`/`oslo-role=phone`/`oslo-created`,
  and handed out as `api_url = http://<private ip>:8080` once `RUNNING` and
  `/health` answer. Two hard caps are checked before any OCI call,
  `ORCH_OCI_MAX_INSTANCES` (2) and `ORCH_OCI_MAX_LAUNCHES_PER_HOUR` (4,
  failures count), answering `429 oci_cap`; an OCI error is `502 oci_error`
  with the service code and is never retried; a phone that never got healthy
  is terminated and the refusal says so (`504 oci_unhealthy`).
  `DELETE /instances/<id>` terminates the instance with its boot volume; at
  start-up tagged orphans older than `ORCH_OCI_ORPHAN_TTL` are terminated.
  `/health` gains `phone_provider`. Unset, nothing changes. New module
  `orchestrator/providers/oci_compute.py`, suite `oci-provider` (35 tests,
  fake SDK), `orchestrator/requirements-oci.txt`; report
  `.AGENT/reports/2026-10-31-oci-compute-provider.md`.
- Browser slots (2026-10-31): one owner may hold several browsers at once,
  one per `slot`, each its own Chrome with its own profile and cookie jar,
  all still that owner's. `POST /sessions` with `purpose: "browser"` takes an
  optional `slot` (`^[a-z0-9_-]{1,32}$`, default `main`); `main` is the
  browser every caller had before and behaves exactly as it did. Each session
  carries `slot` and `isolation_key` (`<owner token>/<slot>`). The per-owner
  cap `ORCH_BROWSER_SLOTS_PER_OWNER` (default 1, so nothing changes until the
  founder raises it) answers `429 slots_exhausted` with the cap in the body.
  `DELETE /sessions/<owner>?purpose=browser` releases the slot named
  (`slot=`) or the lease (`lease_id=`), default `main`, and never another
  slot; another owner's lease id is `403`. `/tickets`, `/sessions/<owner>`
  and the kill switch take `slot` or `lease_id` the same way. Lease rows
  written before this change load as slot `main`. With
  `ORCH_BROWSER_PROFILE=volume` every slot of an owner lives on that owner's
  one profile volume, which closes when the last slot is released. OSLO's
  swarm (`OSLO_SWARM`) leases `OSLO_SWARM_PARALLEL` such slots. New suite
  `browser-slots`; report `.AGENT/reports/2026-10-31-browser-slots.md`.
- Control API rung M0, honest input and a richer UI dump (2026-10-31):
  `POST /device/input` tap/swipe accept `percentage: true` (0–100 of the
  phone's `wm size`, cached per phone); `text` accepts `clear: true` (empty
  the focused field, bounded by its current length) and types non-ASCII text
  through ADBKeyboard or the clipboard + paste where the phone has one, else
  answers `unsupported_unicode`; the reply's `path` says which way was used.
  `GET /device/ui` elements gain `class`, `package`, `content_desc`, `text`,
  `hint`, `checkable`, `checked`, `enabled`, `focusable`, `editable`,
  `long_clickable`, `scrollable`, `selected`, `text_state`, `text_len` and
  `value_redacted`; the reply gains `values_included` and `hints_exposed`.
  New offline suite `device-input` with a hand-written Contacts editor
  fixture (`tests/fixtures/ui/contacts_editor.xml`).
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

- `POST /device/input` and `POST /device/screen` check the `adb` result:
  failures answer `success: false` with an `error_code` (`adb_failed`,
  `adb_timeout`, `input_rejected`, `bad_request`, …) instead of `success: true`.
  Unknown input types and screen actions are refused rather than reported as done.
  Coordinates, durations and keycodes must be numbers (or `KEYCODE_` names), so
  they can no longer carry shell text.
- `input text` is single-quoted for the device shell, so `O'Brien` and text
  with `$`, `&`, `;` or backticks types as written (the old `\'` escaping broke
  the command). The same encoder backs `/ui/text`.
- `GET /device/ui` redacts what inputs hold (`text: null`, `value_redacted`,
  `text_len`) unless the caller passes `include_values=1`; passwords are always
  redacted. `tap_label`'s `matched` and `available` follow the same rule.
  `scripts/real-app-smoke.py` asks for values, since it reads typed text back,
  and its Contacts surname now contains an apostrophe.
- Typed text and UI dumps are no longer written to the ADB/CMD logs; those
  commands are logged by a label and a length.
- With `ORCH_PHONE_DATA=per_user`, `POST /instances/<id>/lease` answers
  `409 use_sessions`, and an instance that ran on per-user volumes is refused
  once the flag is turned off until it is reprovisioned.
