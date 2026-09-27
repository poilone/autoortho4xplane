"""The web map reads the aircraft position from the parent's dataref tracker."""

import os
import sys

import pytest

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

import flighttrack  # noqa: E402


class FakeTracker:
    def __init__(self, data):
        self._data = data

    def get_flight_data(self):
        return self._data


@pytest.fixture(autouse=True)
def no_tracker(monkeypatch):
    monkeypatch.setattr(flighttrack, "_dataref_tracker", None)


def test_position_unknown_before_any_tracker():
    assert flighttrack._aircraft_position() == (flighttrack.ft.lat, flighttrack.ft.lon)


def test_position_comes_from_the_injected_tracker():
    flighttrack.set_dataref_tracker(FakeTracker({"lat": 48.72, "lon": 2.38}))

    assert flighttrack._aircraft_position() == (48.72, 2.38)


def test_position_unknown_while_tracker_has_no_flight():
    flighttrack.set_dataref_tracker(FakeTracker(None))

    assert flighttrack._aircraft_position() == (flighttrack.ft.lat, flighttrack.ft.lon)


def test_get_latlon_route_uses_tracker():
    flighttrack.set_dataref_tracker(FakeTracker({"lat": 45.5, "lon": -73.6}))

    response = flighttrack.app.test_client().get("/get_latlon")

    assert response.get_json() == {"lat": 45.5, "lon": -73.6}
