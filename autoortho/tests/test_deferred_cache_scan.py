"""The startup cache scans wait for the flight and give way to live reads."""

import os
import sys
import threading

import pytest

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

import getortho  # noqa: E402


class FakeGate:
    def __init__(self):
        self.allowed = threading.Event()
        self.timeouts = []

    def wait_until_allowed(self, stop_event, timeout=None):
        self.timeouts.append(timeout)
        while not self.allowed.is_set() and not stop_event.is_set():
            self.allowed.wait(0.01)
        return self.allowed.is_set() and not stop_event.is_set()


class FakeDDSCache:
    def __init__(self):
        self.calls = []
        self.scanned = threading.Event()

    def scan_existing(self, should_continue=None):
        self.calls.append(("scan", should_continue()))
        self.scanned.set()

    def migrate_uncompressed(self, should_continue=None):
        self.calls.append(("migrate", should_continue()))


class FakeBudget:
    def __init__(self):
        self.scans = 0

    def initial_scan(self):
        self.scans += 1


@pytest.fixture
def caches(monkeypatch):
    gate = FakeGate()
    dds_cache = FakeDDSCache()
    budget = FakeBudget()
    monkeypatch.setattr(getortho, "flight_runtime_gate", gate)
    monkeypatch.setattr(getortho, "dynamic_dds_cache", dds_cache)
    monkeypatch.setattr(getortho, "disk_budget_manager", budget)
    monkeypatch.setattr(getortho, "DISK_CACHE_SCAN_DELAY_SEC", 0.0)
    monkeypatch.setattr(
        getortho, "live_pressure_controller", getortho.LivePressureController()
    )
    return gate, dds_cache, budget


def _start(stop):
    thread = threading.Thread(
        target=getortho._initialize_disk_caches, args=(stop,), daemon=True
    )
    thread.start()
    return thread


def test_scans_wait_for_the_flight(caches):
    gate, dds_cache, budget = caches
    stop = threading.Event()
    thread = _start(stop)

    assert not dds_cache.scanned.wait(0.2)  # X-Plane still loading
    gate.allowed.set()
    thread.join(5)

    assert dds_cache.calls == [("scan", True), ("migrate", True)]
    assert budget.scans == 1
    assert gate.timeouts == [getortho.DISK_CACHE_SCAN_MAX_WAIT_SEC]


def test_scans_give_way_to_live_reads(caches):
    gate, dds_cache, budget = caches
    gate.allowed.set()
    getortho.live_pressure_controller.live_read_start()
    stop = threading.Event()
    thread = _start(stop)

    assert not dds_cache.scanned.wait(0.2)  # X-Plane waits on a texture
    getortho.live_pressure_controller.live_read_end()
    thread.join(5)

    assert dds_cache.calls[0] == ("scan", True)
    assert budget.scans == 1


def test_stop_before_the_flight_skips_the_scans(caches):
    gate, dds_cache, budget = caches
    stop = threading.Event()
    thread = _start(stop)

    stop.set()
    thread.join(5)

    assert not thread.is_alive()
    assert dds_cache.calls == []
    assert budget.scans == 0


def test_flight_gate_wait_can_time_out():
    gate = getortho.FlightRuntimeGate(lambda: False, poll_interval=0.1)
    try:
        assert gate.wait_until_allowed(threading.Event(), timeout=0.2) is False
    finally:
        gate.stop()
