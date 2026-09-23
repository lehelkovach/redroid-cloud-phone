"""An instance never carries one user's Android state to the next user.

Redroid keeps everything in `/data`: logged-in sessions, cookies, saved app
credentials, downloads, the clipboard, and the keyboard's learned-word
dictionary — which quietly accumulates names, addresses and card numbers. A
released instance returning to the idle pool carries all of it.

Before this module, `release_instance` cleared the lease and nothing else, and
a repo-wide search for wipe / factory reset / `pm clear` found only `swipe`
the gesture. With one user that is latent. With two it is a breach.

The fix is to **fail closed** rather than to remember to wipe: an instance
records who last used it, and handing it to a different owner is refused
unless it has been provably reset since. Wiping can be added later and
instances become reusable again automatically, because the guard is a
statement about evidence rather than about intent.
"""
import time

import pytest

from orchestrator import server


@pytest.fixture(autouse=True)
def _clean_state():
    server._leases.clear()
    server._instance_tenancy.clear()
    yield
    server._leases.clear()
    server._instance_tenancy.clear()


def test_a_fresh_instance_may_be_leased_by_anyone():
    assert server.can_lease_to("inst-1", "alice") is True


def test_the_same_owner_may_take_their_instance_back():
    server.record_tenancy("inst-1", "alice")
    assert server.can_lease_to("inst-1", "alice") is True


def test_a_used_instance_is_refused_to_a_different_owner():
    server.record_tenancy("inst-1", "alice")
    assert server.can_lease_to("inst-1", "bob") is False


def test_reason_names_the_previous_tenant_without_leaking_their_data():
    server.record_tenancy("inst-1", "alice")
    ok, reason = server.lease_decision("inst-1", "bob")
    assert ok is False
    assert "reset" in reason.lower()
    # The refusal explains the rule; it must not hand Bob Alice's identity.
    assert "alice" not in reason.lower()


def test_a_reset_after_the_last_use_makes_it_reusable():
    server.record_tenancy("inst-1", "alice")
    time.sleep(0.01)
    server.record_reset("inst-1")
    assert server.can_lease_to("inst-1", "bob") is True


def test_a_reset_recorded_before_the_last_use_does_not_count():
    server.record_reset("inst-1")
    time.sleep(0.01)
    server.record_tenancy("inst-1", "alice")
    assert server.can_lease_to("inst-1", "bob") is False


def test_releasing_a_lease_does_not_by_itself_clear_tenancy():
    """Releasing frees the lease. It does not make the data disappear."""
    server.record_tenancy("inst-1", "alice")
    server._clear_lease("inst-1")
    assert server.can_lease_to("inst-1", "bob") is False


def test_acquiring_records_tenancy_so_the_guard_has_something_to_check():
    server._set_lease("inst-1", "alice", 60)
    assert server._instance_tenancy.get("inst-1", {}).get("last_owner") == "alice"
