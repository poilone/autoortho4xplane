"""Mount workers mirror the parent's flight state and SimBrief route.

Workers have no X-Plane UDP listener; they read what the parent publishes in
the shared stats store. This must work with prefetching disabled because
dynamic zoom and night exclusion read the same state.
"""

import os
import sys
import threading

import pytest

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

import getortho  # noqa: E402


FLIGHT_STATE = {
    "connected": True,
    "data_valid": True,
    "has_ever_connected": True,
    "lat": 48.72,
    "lon": 2.38,
    "alt": 30.0,
    "hdg": 70.0,
    "spd": 0.0,
    "local_time_sec": 36000.0,
    "pressure_alt": 300.0,
    "sun_pitch": 25.0,
}

ROUTE = {"origin": {"icao_code": "LFPO"}, "navlog": {"fix": []}}


@pytest.fixture
def worker_tracker(monkeypatch):
    """A worker-side tracker: not running, never connected."""
    tracker = getortho.datareftracker
    monkeypatch.setattr(tracker, "running", False)
    monkeypatch.setattr(tracker, "has_ever_connected", False)
    monkeypatch.setattr(tracker, "connected", False)
    monkeypatch.setattr(tracker, "data_valid", False)
    monkeypatch.setattr(getortho, "_shared_flight_state_last_poll", 0.0)
    monkeypatch.setattr(getortho, "_shared_flight_state_allowed", False)
    monkeypatch.setattr(getortho, "_shared_simbrief_last_poll", 0.0)
    monkeypatch.setattr(getortho, "_shared_simbrief_generation", None)
    return tracker


@pytest.fixture
def published(monkeypatch):
    stats = {
        "flight_state": dict(FLIGHT_STATE, timestamp=getortho.time.time()),
        "simbrief_route_generation": 1234,
        "simbrief_route_snapshot": ROUTE,
    }
    calls = []

    def fake_get_stat(key):
        calls.append(key)
        return stats.get(key, 0)

    monkeypatch.setattr(getortho, "get_stat", fake_get_stat)
    return stats, calls


@pytest.fixture
def loaded_routes(monkeypatch):
    routes = []
    monkeypatch.setattr(
        getortho.simbrief_flight_manager,
        "load_flight_data",
        lambda data: routes.append(data) or True,
    )
    return routes


def test_gate_start_mirrors_flight_state_with_prefetch_disabled(
    worker_tracker, published, monkeypatch
):
    monkeypatch.setattr(getortho.CFG.autoortho, "prefetch_enabled", False)
    gate = getortho.FlightRuntimeGate(getortho._sync_shared_flight_state)
    try:
        gate.start()
    finally:
        gate.stop()

    assert worker_tracker.connected is True
    assert worker_tracker.lat == pytest.approx(48.72)
    assert worker_tracker.get_sun_pitch() == pytest.approx(25.0)
    assert getortho.is_flight_runtime_allowed() is False


def test_worker_loads_published_route_once(worker_tracker, published, loaded_routes):
    getortho._sync_shared_simbrief_route()
    getortho._shared_simbrief_last_poll = 0.0
    getortho._sync_shared_simbrief_route()

    assert loaded_routes == [ROUTE]


def test_route_polling_is_rate_limited(worker_tracker, published, loaded_routes):
    _stats, calls = published
    getortho._sync_shared_simbrief_route()
    polls = calls.count("simbrief_route_generation")
    getortho._sync_shared_simbrief_route()

    assert calls.count("simbrief_route_generation") == polls == 1


def test_parent_does_not_mirror_its_own_route(
    worker_tracker, published, loaded_routes, monkeypatch
):
    _stats, calls = published
    monkeypatch.setattr(worker_tracker, "running", True)

    getortho._sync_shared_simbrief_route()

    assert calls == []
    assert loaded_routes == []


def test_cleared_route_is_propagated(worker_tracker, published, monkeypatch):
    stats, _calls = published
    stats["simbrief_route_snapshot"] = None
    cleared = []
    monkeypatch.setattr(
        getortho.simbrief_flight_manager, "clear", lambda: cleared.append(True)
    )

    getortho._sync_shared_simbrief_route()

    assert cleared == [True]


def test_watcher_runs_poll_hook():
    polled = threading.Event()
    gate = getortho.FlightRuntimeGate(
        lambda: False, poll_interval=0.1, on_poll=polled.set
    )
    try:
        gate.start()
        assert polled.wait(2.0)
    finally:
        gate.stop()
