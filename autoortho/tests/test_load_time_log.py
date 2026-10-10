"""The log states how long X-Plane took to load the flight."""

import importlib
import logging
import os
import sys
import time
from types import SimpleNamespace

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

import aostats  # noqa: E402

PREFIX = aostats.FIRST_SCENERY_OPEN_STAT


class FakeStore(dict):
    def set(self, key, value):
        self[key] = value


def _load_time_messages(caplog):
    return [
        record.getMessage() for record in caplog.records
        if record.getMessage().startswith("Flight started")
    ]


def _logger(caplog):
    autoortho_mod = importlib.import_module("autoortho")
    caplog.set_level(logging.INFO, logger=autoortho_mod.log.name)
    return autoortho_mod._LoadTimeLogger()


def test_load_time_is_logged_once_from_the_first_scenery_open(caplog):
    logger = _logger(caplog)
    now = time.time()
    store = FakeStore({
        f"{PREFIX}:101": now - 60,
        f"{PREFIX}:102": now - 125,  # the earliest worker counts
        "chunk_hit": 3,
    })

    logger.observe(False, store)
    logger.observe(True, store)
    logger.observe(False, store)  # X-Plane stalled past the UDP timeout
    logger.observe(True, store)

    messages = _load_time_messages(caplog)
    assert len(messages) == 1
    assert "2 min 05 s" in messages[0]


def test_flight_already_running_at_start_is_not_a_load(caplog):
    logger = _logger(caplog)
    store = FakeStore({f"{PREFIX}:101": time.time() - 30})

    logger.observe(True, store)
    logger.observe(True, store)

    assert _load_time_messages(caplog) == []


def test_connection_before_any_scenery_waits_for_the_next_start(caplog):
    logger = _logger(caplog)
    store = FakeStore()

    logger.observe(False, store)
    logger.observe(True, store)  # X-Plane menu, nothing loaded yet
    assert _load_time_messages(caplog) == []

    store[f"{PREFIX}:101"] = time.time() - 200
    logger.observe(False, store)  # busy loading the flight
    logger.observe(True, store)

    messages = _load_time_messages(caplog)
    assert len(messages) == 1
    assert "3 min 20 s" in messages[0]


def test_worker_publishes_its_first_scenery_open_once(monkeypatch):
    autoortho_fuse = importlib.import_module("autoortho_fuse")
    published = []
    monkeypatch.setattr(
        autoortho_fuse.getortho,
        "set_stat",
        lambda key, value: published.append((key, value)),
    )
    mount = SimpleNamespace(_first_scenery_open_noted=False)

    for _ in range(3):
        autoortho_fuse.AutoOrtho._note_first_scenery_open(mount)

    assert len(published) == 1
    key, value = published[0]
    assert key == f"{PREFIX}:{os.getpid()}"
    assert abs(value - time.time()) < 5


def test_stats_lines_leave_out_the_timestamps():
    filtered = aostats.filter_stats_snapshot({
        f"{PREFIX}:101": 1.0, "chunk_hit": 3,
    })

    assert filtered == {"chunk_hit": 3}
