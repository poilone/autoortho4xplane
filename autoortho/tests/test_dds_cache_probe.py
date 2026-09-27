"""A tile probes the persistent DDS cache again only after a write for it."""

import os
import sys

import pytest

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

import getortho  # noqa: E402
from aopipeline.dynamic_dds_cache import DynamicDDSCache  # noqa: E402


class FakeCache:
    def __init__(self):
        self.generations = {}

    def write_generation(self, tile_id, max_zoom):
        return self.generations.get((tile_id, max_zoom), 0)


@pytest.fixture
def fake_cache(monkeypatch):
    cache = FakeCache()
    monkeypatch.setattr(getortho, "dynamic_dds_cache", cache)
    return cache


@pytest.fixture
def tile(tmp_path):
    return getortho.Tile(2176, 3232, "BI", 13, cache_dir=str(tmp_path))


def test_first_read_probes(fake_cache, tile):
    assert tile._dds_cache_probe_key() == (tile.max_zoom, 0)


def test_known_miss_is_not_probed_again(fake_cache, tile):
    tile._dds_cache_miss = tile._dds_cache_probe_key()

    assert tile._dds_cache_probe_key() is None


def test_write_for_the_tile_triggers_a_new_probe(fake_cache, tile):
    tile._dds_cache_miss = tile._dds_cache_probe_key()
    fake_cache.generations[(tile.id, tile.max_zoom)] = 1

    assert tile._dds_cache_probe_key() == (tile.max_zoom, 1)


def test_zoom_change_triggers_a_new_probe(fake_cache, tile):
    tile._dds_cache_miss = tile._dds_cache_probe_key()
    tile.max_zoom += 1

    assert tile._dds_cache_probe_key() == (tile.max_zoom, 0)


def test_no_probe_without_cache_or_once_prepopulated(fake_cache, tile, monkeypatch):
    tile._prepopulated = True
    assert tile._dds_cache_probe_key() is None

    tile._prepopulated = False
    monkeypatch.setattr(getortho, "dynamic_dds_cache", None)
    assert tile._dds_cache_probe_key() is None


def test_cache_counts_writes_per_entry(tmp_path):
    cache = DynamicDDSCache(cache_dir=str(tmp_path), max_size_mb=10)
    try:
        base = os.path.join(str(tmp_path), "entry")
        assert cache.write_generation("t", 16) == 0

        cache._record_store(cache._tile_key("t", 16), base + ".dds", base + ".ddm")
        cache._record_store(cache._tile_key("t", 16), base + ".dds", base + ".ddm")

        assert cache.write_generation("t", 16) == 2
        assert cache.write_generation("t", 17) == 0
    finally:
        cache.close()
