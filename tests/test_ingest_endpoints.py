"""A camera session tells the caller where to publish.

Cuttlefish exists to receive OBS over RTMP, but the session handed back to a
caller carried only `api_url` and `adb_connect`. To actually publish, every
client had to build `rtmp://<host>/live/cam` for itself out of the api_url —
which means the ingest layout lives in each caller instead of in the
orchestrator that owns it.

That is the shape of the stale-host-address bug: one fact, copied into N
places, and the copies do not move when the fact does. The orchestrator knows
the host, the purpose and the nginx-rtmp layout, so it should be the one to
say it.

Automation sessions get no ingest block at all — Redroid deliberately has no
virtual camera, and inventing a publish URL for one would be a lie the caller
only discovers by streaming into a void.
"""
import pytest

from orchestrator import server


def _inst(api_url="http://10.0.1.5:8080", purpose="camera", runtime="cuttlefish"):
    return {
        "id": "i-1",
        "name": "cf-1",
        "api_url": api_url,
        "purpose": purpose,
        "runtime": runtime,
        "adb_connect": "10.0.1.5:6520",
    }


def test_a_camera_session_carries_the_publish_url():
    sess = server._session_from_instance("lehel", _inst(), 3600, purpose="camera")
    ing = sess.get("ingest")
    assert ing, "camera sessions must say where to publish"
    assert ing["rtmp_url"] == "rtmp://10.0.1.5/live/cam"
    assert ing["rtmp_app"] == "live"
    assert ing["stream_key"] == "cam"


def test_the_host_comes_from_the_instance_not_a_constant():
    sess = server._session_from_instance(
        "lehel", _inst(api_url="http://203.0.113.9:8080"), 3600, purpose="camera"
    )
    assert sess["ingest"]["rtmp_url"] == "rtmp://203.0.113.9/live/cam"


def test_an_automation_session_gets_no_ingest_block():
    """Redroid has no virtual camera. Do not hand back a URL that goes nowhere."""
    sess = server._session_from_instance(
        "lehel",
        _inst(purpose="automation", runtime="redroid"),
        3600,
        purpose="automation",
    )
    assert sess.get("ingest") is None


def test_an_unparseable_api_url_yields_no_ingest_rather_than_a_guess():
    sess = server._session_from_instance("lehel", _inst(api_url=""), 3600, purpose="camera")
    assert sess.get("ingest") is None


def test_ingest_for_is_usable_on_its_own():
    assert server.ingest_for(_inst())["rtmp_url"] == "rtmp://10.0.1.5/live/cam"
    assert server.ingest_for(_inst(purpose="automation", runtime="redroid")) is None
