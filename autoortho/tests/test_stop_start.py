"""Stop Streaming then Start Streaming in the same session.

Stop discards the HTTP/2 broker and the stats store; Start creates new ones.
State this process kept about the old ones must not survive the restart.
"""

import importlib
import os
import sys
from types import SimpleNamespace

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

import getortho  # noqa: E402


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

