"""Stop Streaming then Start Streaming in the same session.

Stop discards the HTTP/2 broker and the stats store; Start creates new ones.
State this process kept about the old ones must not survive the restart.
"""

import importlib
import os
import sys
import types
from types import SimpleNamespace

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

import aostats  # noqa: E402
import getortho  # noqa: E402

ROUTE = {
    "origin": {"icao_code": "LFPO"},
    "destination": {"icao_code": "LFBO"},
    "general": {"initial_altitude": 35000},
    "navlog": {"fix": [{"ident": "OKRIX"}]},
}


class FakeClient:
    def __init__(self, address):
        self.address = address
        self.stopped = False

    def stop(self):
        self.stopped = True


class FakeBroker:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


def test_restart_connects_to_the_new_broker(monkeypatch):
    autoortho_mod = importlib.import_module("autoortho")
    mount = autoortho_mod.AOMount.__new__(autoortho_mod.AOMount)
    broker = FakeBroker()
    mount._download_broker = broker
    mount._download_broker_env = {}

    connected = []

    def fake_connect(address, token, **kwargs):
        client = FakeClient(address)
        connected.append(client)
        return client

    monkeypatch.setattr(
        getortho, "HTTP2Broker", SimpleNamespace(connect=fake_connect)
    )
    monkeypatch.setattr(getortho, "_http2_client", None)
    monkeypatch.setattr(getortho, "_http2_client_failed", False)
    monkeypatch.setenv("AO_HTTP2_BROKER_ADDR", "tcp://127.0.0.1:1111")
    monkeypatch.setenv("AO_HTTP2_BROKER_TOKEN", "old")

    old_client = getortho._get_http2_client()
    assert old_client.address == "tcp://127.0.0.1:1111"

    mount.stop_download_broker()

    assert broker.stopped is True
    assert old_client.stopped is True
    assert getortho._http2_client is None
    assert getortho._get_http2_client() is None  # no broker while stopped

    # Start Streaming publishes the new broker's address.
    monkeypatch.setenv("AO_HTTP2_BROKER_ADDR", "tcp://127.0.0.1:2222")
    monkeypatch.setenv("AO_HTTP2_BROKER_TOKEN", "new")

    assert getortho._get_http2_client().address == "tcp://127.0.0.1:2222"
    assert len(connected) == 2


def test_failed_connection_is_retried_after_restart(monkeypatch):
    autoortho_mod = importlib.import_module("autoortho")
    mount = autoortho_mod.AOMount.__new__(autoortho_mod.AOMount)
    mount._download_broker = None
    mount._download_broker_env = {}
    monkeypatch.setattr(getortho, "_http2_client", None)
    monkeypatch.setattr(getortho, "_http2_client_failed", True)

    mount.stop_download_broker()

    assert getortho._http2_client_failed is False


class FakeStore(dict):
    def set(self, key, value):
        self[key] = value

    def __bool__(self):  # aostats tests the bound store with "if _store"
        return True


def _parent(monkeypatch, simbrief_data, use_flight_data, stats_manager=None):
    """A parent with the UI's SimBrief publishing, without Qt widgets."""
    autoortho_mod = importlib.import_module("autoortho")
    config_ui = autoortho_mod.config_ui
    store = FakeStore()
    monkeypatch.setattr(aostats, "_store", None)
    monkeypatch.setattr(aostats, "STATS", aostats.STATS)
    parent = SimpleNamespace(
        stats_manager=stats_manager,
        log_server=object(),
        cfg=SimpleNamespace(
            simbrief=SimpleNamespace(use_flight_data=use_flight_data)
        ),
        simbrief_flight_data=simbrief_data,
        start_download_broker=lambda: None,
        start_reporter=lambda: None,
        _publish_simbrief_to_workers=(
            config_ui.ConfigUI._publish_simbrief_to_workers
        ),
    )

    def start_stats_manager():
        aostats.bind_local_store(store)
        parent.stats_manager = object()

    parent.start_stats_manager = start_stats_manager
    parent._publish_current_simbrief_route = types.MethodType(
        config_ui.ConfigUI._publish_current_simbrief_route, parent
    )
    return autoortho_mod, parent, store


def test_restart_republishes_the_loaded_simbrief_route(monkeypatch):
    autoortho_mod, parent, store = _parent(monkeypatch, ROUTE, "True")

    autoortho_mod.AOMount._ensure_parent_services(parent)

    snapshot = store["simbrief_route_snapshot"]
    assert snapshot["origin"] == {"icao_code": "LFPO"}
    assert snapshot["navlog"]["fix"] == [{"ident": "OKRIX"}]
    assert store["simbrief_route_enabled"] is True
    assert store["simbrief_route_generation"]


def test_restart_without_route_publishes_nothing(monkeypatch):
    autoortho_mod, parent, store = _parent(monkeypatch, None, True)

    autoortho_mod.AOMount._ensure_parent_services(parent)

    assert "simbrief_route_snapshot" not in store


def test_running_stats_store_is_not_republished(monkeypatch):
    autoortho_mod, parent, store = _parent(
        monkeypatch, ROUTE, True, stats_manager=object()
    )
    aostats.bind_local_store(store)

    autoortho_mod.AOMount._ensure_parent_services(parent)

    assert store == {}
