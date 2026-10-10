"""STATS log lines leave out the SimBrief route published for the workers."""

import importlib
import logging
import os
import sys
import threading
from types import SimpleNamespace

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

import aostats  # noqa: E402

ROUTE = {"origin": {"metar": "LFPO 101200Z"}, "navlog": {"fix": ["OKRIX"]}}


class FakeStore(dict):
    def set(self, key, value):
        self[key] = value

    def snapshot(self):
        return dict(self)

    def delete(self, key):
        self.pop(key, None)


def test_worker_stats_line_leaves_out_the_route():
    filtered = aostats.filter_stats_snapshot({
        "chunk_hit": 3,
        "simbrief_route_generation": 1234,
        "simbrief_route_snapshot": ROUTE,
    })

    assert filtered == {"chunk_hit": 3, "simbrief_route_generation": 1234}


def test_parent_stats_line_leaves_out_the_route():
    autoortho_mod = importlib.import_module("autoortho")
    store = FakeStore(
        chunk_hit=3,
        simbrief_route_generation=1234,
        simbrief_route_snapshot=ROUTE,
    )
    parent = SimpleNamespace(
        _shared_store=store,
        _reporter_stop=threading.Event(),
        _reporter_thread=None,
    )
    logged = threading.Event()

    class Capture(logging.Handler):
        def emit(self, record):
            if record.getMessage().startswith("STATS:"):
                lines.append(record.getMessage())
                logged.set()

    lines = []
    handler = Capture()
    autoortho_mod.log.addHandler(handler)
    previous_level = autoortho_mod.log.level
    autoortho_mod.log.setLevel(logging.INFO)
    try:
        autoortho_mod.AOMount.start_reporter(
            parent, interval_sec=0.0, aggregation_interval_sec=0.01
        )
        assert logged.wait(5)
    finally:
        autoortho_mod.AOMount.stop_reporter(parent)
        autoortho_mod.log.removeHandler(handler)
        autoortho_mod.log.setLevel(previous_level)

    assert "simbrief_route_generation" in lines[0]
    assert "OKRIX" not in lines[0]
    assert "simbrief_route_snapshot" not in lines[0]
