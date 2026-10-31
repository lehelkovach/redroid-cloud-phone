# Browser slots: N isolated browsers per owner, one per slot

- **Date:** 2026-10-31
- **Asked by:** the founder's build order, via the OSLO coordinator ("swarm prod-blocker rung: one isolated browser slot per subtask")
- **Question:** Can the orchestrator lease more than one browser to one owner, each its own runtime and cookie jar, still owner-bound, without changing anything for a caller that leases the way it did yesterday?
- **Scope:** `orchestrator/server.py` (browser lease, release, tickets, kill, relay, state load), `orchestrator/browser_pool.py` (slot names, profile paths, container names), `scripts/run-tests.sh` (new suite), `tests/test_browser_slots.py` (new), `CHANGELOG.md`, `docs/configuration.md`, `docs/api/orchestrator.md`, `docs/isolation.md`. Branch `browser-slots` from `master` 980c35e. No live host; the phone pool and `user_volumes.py` untouched except for closing a shared profile volume only when its last slot goes.
- **Method:** wrote `tests/test_browser_slots.py` first (17 tests, Flask test client plus one live two-slot relay check through `tests/fixtures/isolation_harness.py`), then the code; ran `PYTHON=<py3.12 venv> ./scripts/run-tests.sh --coverage --fail-under 60` and `bash -n scripts/run-tests.sh`.
- **Verdict:** Built and gated, default off: `ORCH_BROWSER_SLOTS_PER_OWNER=1` keeps the single `main` browser per owner byte-for-byte (all 23 existing suites pass untouched, including the two-owner isolation gate). Raising the cap lets one owner hold N slots, each a separate Chrome, profile, DevTools port, lease and ticket set. Not run against a live orchestrator or a real Chrome/Docker runtime here.
- **Follow-up:** OSLO branch `browser-slots` (osl-oc-agent) sends `slot` per swarm subtask and treats `slots_exhausted` as a per-subtask block. No PR opened, per instructions. The founder sets the cap.

## Slot semantics

| | |
|---|---|
| Key | A browser session is `(owner, slot)`. `slot` matches `^[a-z0-9_-]{1,32}$`; anything else is `400 bad_slot`. Default `main`. |
| `main` | The browser every caller had before. In memory and in the state DB it is keyed by the owner id alone, as before; rows written before this change load as `slot: main` with an `isolation_key` filled in (migrate on read, nothing rewritten). Renew, TTL, tickets, kill, release: unchanged. |
| Another slot | Its own Chrome runtime (mock / local / docker), profile directory `ORCH_PROFILE_ROOT/<token>.slot-<slot>` (a sibling, never inside the main profile, so the main container never mounts it), DevTools port, lease id (`bl-…`), relay URL, tickets. Docker container `orch-chrome-<token>.<slot>`; a dot never appears in a token or a slot, so names cannot collide. |
| `isolation_key` | `<owner token>/<slot>` on every session: the name of the runtime the lease is alone in. OSLO's swarm refuses two subtasks on one key. |
| Cap | `ORCH_BROWSER_SLOTS_PER_OWNER` (default 1). Over it: `429 slots_exhausted` with `cap` and the held `slots` in the body; nothing is started. The pool-wide `ORCH_MAX_CHROME_INSTANCES` still applies after it (`409 at_capacity`). |
| Release | `DELETE /sessions/<owner>?purpose=browser` with `slot=` or `lease_id=` (body or query), default `main`. One slot's release stops that browser, revokes its tickets, kills its streams, keeps its profile, and touches no other slot. A lease id another owner holds is `403` and names nobody. |
| Lookups | `GET /sessions/<owner>?purpose=browser&slot=…`, `POST /tickets {slot | lease_id}`, `POST /sessions/<owner>/kill {slot | lease_id}`; the CDP relay and ticket checks resolve the lease by `(owner, lease_id)`, so a slot's ticket opens that slot only. |
| Expiry | Per slot (`_reap_browser_sessions`). |
| `ORCH_BROWSER_PROFILE=volume` | One profile volume per owner, as before; a slot other than `main` is `<mount>/slots/<slot>` on it. The volume is opened once and closed only when the owner's last slot goes (`_close_profile_volume_if_unused`). `DELETE /volumes` and `DELETE /profiles` stay refused while any slot is leased; `DELETE /profiles` removes the slot directories too. |

## Tests (`tests/test_browser_slots.py`, suite `browser-slots`, 17)

- Default unchanged: no `slot` → `main`, same key, same renew; explicit `main` is the same browser; bad slots `400`; an old persisted row loads as `main`, renews and mints a ticket.
- Two slots → two runtimes: three distinct lease ids, isolation keys, profile dirs (0700, siblings), DevTools ports and upstream URLs; three `start` calls on the runtime; listing shows the slots; renewing one slot leaves `main`'s counter alone.
- Cap: default 1 refuses a second slot `429 slots_exhausted` with `cap: 1`, starts nothing, leaves `main` renewable, does not affect another owner; cap 2 allows two and refuses a third until one is released; `ORCH_MAX_CHROME_INSTANCES` still bites.
- Release one keeps the others (by slot, by lease id, by query string); a slot not held is `404`; expiry and the kill switch are per slot; tickets by slot or lease id; `_lease_check` of a released slot fails while `main`'s still passes.
- Another owner cannot renew (`403`), release (`403`, holder not named), ticket (`403`/`404`) or kill a slot by naming its lease id or slot; their own same-named slot is a different browser.
- Profiles and containers: slot profile is a sibling dir, deleted with the owner and never another owner's; docker `--name` per slot, unchanged for `main`.
- Volumes (fake host): two slots of one owner share one volume (one `cryptsetup open`), releasing `main` keeps it mounted for the slot, the last release closes it; delete refused while a slot is leased; two owners open two volumes.
- Live, through the real relay over sockets: a cookie set in slot 1 is not in slot 2 nor in `main`; slot 1's ticket cannot open slot 2's relay; releasing slot 1 leaves slot 2 and `main` connected; the listing hides ports.

## CI (this worktree, Python 3.12 venv, 2026-10-31)

| | this branch |
|---|---|
| Suites | 24 passed, 0 failed (23 existing, untouched, plus `browser-slots` with 17 tests) |
| Coverage (`api/*`, `orchestrator/*`) | 80 % (floor 60) |
| `orchestrator/server.py` | 79 % |
| `orchestrator/browser_pool.py` | 80 % |

The 2026-10-30 user-volumes report measured 79 % total on its branch; `origin/master` was not re-measured here. `bash -n scripts/run-tests.sh`: clean. Read the counts off a run after today.

## What the founder must set

`ORCH_BROWSER_SLOTS_PER_OWNER=N` on the orchestrator host, with N at least OSLO's `OSLO_SWARM_PARALLEL`, and `ORCH_MAX_CHROME_INSTANCES` large enough for every owner's slots together. Nothing else changes; with the variable unset the orchestrator behaves as before.

## Not done, said plainly

- No live orchestrator, real Chrome (`local`) or Docker run: the per-slot container name and profile mount are tested as argv, not executed.
- A slot's profile is kept on release like `main`'s (same policy); nothing reaps old `swarm-<n>` profile directories. `DELETE /profiles/<owner>` removes them with the owner's main profile.
- In volume mode the slots of one owner share one encrypted volume and one quota; the main container's bind mount of `<mount>` includes `slots/` (the same owner's data). A volume per slot was not built.
- Concurrent acquires of the same `(owner, slot)` are serialised by the browser lock, but two different slots of one owner racing the cap are checked under the same lock, so the cap holds.
