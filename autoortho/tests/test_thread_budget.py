"""OpenMP thread budget per native build, with headroom for X-Plane in flight."""

import os
import sys

import pytest

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

import getortho  # noqa: E402


@pytest.fixture
def sixteen_cpus(monkeypatch):
    monkeypatch.setattr(getortho, "CURRENT_CPU_COUNT", 16)
    monkeypatch.setattr(getortho.CFG.autoortho, "native_pipeline_threads", 0)
    monkeypatch.setattr(
        getortho.CFG.autoortho, "in_flight_reserved_threads", "auto"
    )


def _in_flight(monkeypatch, flying):
    monkeypatch.setattr(getortho.datareftracker, "connected", flying)


def test_loading_screen_uses_every_core(sixteen_cpus, monkeypatch):
    _in_flight(monkeypatch, False)

    assert getortho._thread_budget_for(1) == 16
    assert getortho._thread_budget_for(4) == 4


def test_in_flight_keeps_a_quarter_free_by_default(sixteen_cpus, monkeypatch):
    _in_flight(monkeypatch, True)

    assert getortho._thread_budget_for(1) == 12
    assert getortho._thread_budget_for(3) == 4
    assert getortho._thread_budget_for(20) == 2


@pytest.mark.parametrize(
    "setting, expected",
    [(0, 16), (6, 10), ("6", 10), (99, 2), ("garbage", 12)],
)
def test_reservation_setting(sixteen_cpus, monkeypatch, setting, expected):
    _in_flight(monkeypatch, True)
    monkeypatch.setattr(
        getortho.CFG.autoortho, "in_flight_reserved_threads", setting
    )

    assert getortho._thread_budget_for(1) == expected


def test_explicit_thread_count_wins(sixteen_cpus, monkeypatch):
    _in_flight(monkeypatch, True)
    monkeypatch.setattr(getortho.CFG.autoortho, "native_pipeline_threads", 5)

    assert getortho._thread_budget_for(1) == 5
