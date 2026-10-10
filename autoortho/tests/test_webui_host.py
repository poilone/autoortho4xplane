"""The map server stays on this computer unless the config opens it."""

import os
import sys

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

import flighttrack  # noqa: E402


def _listening_address(monkeypatch):
    calls = []
    monkeypatch.setattr(
        flighttrack.socketio,
        "run",
        lambda app, **kwargs: calls.append(kwargs),
    )
    flighttrack.run()
    return calls[0]["host"]


def test_default_config_keeps_the_map_on_this_computer(monkeypatch):
    assert flighttrack.CFG.flightdata.webui_host == "127.0.0.1"
    assert _listening_address(monkeypatch) == "127.0.0.1"


def test_empty_setting_falls_back_to_this_computer(monkeypatch):
    monkeypatch.setattr(flighttrack.CFG.flightdata, "webui_host", "")
    assert _listening_address(monkeypatch) == "127.0.0.1"


def test_local_network_on_request(monkeypatch):
    monkeypatch.setattr(flighttrack.CFG.flightdata, "webui_host", "0.0.0.0")
    assert _listening_address(monkeypatch) == "0.0.0.0"
