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


class PartialEntryCache(FakeCache):
    """An entry that exists but cannot serve the tile."""

    def __init__(self, populated):
        super().__init__()
        self.populated = populated
        self.metadata_reads = 0
        self.loads = 0

    def load_metadata(self, tile_id, max_zoom, tile):
        self.metadata_reads += 1
        return {"populated_mipmaps": list(self.populated)}

    def load(self, tile_id, max_zoom, tile):
        self.loads += 1
        return None  # strict miss: incomplete mipmap zero


def _read_mipmap_one(tile):
    # Mipmap 1 already built: get_bytes returns right after the cache probe.
    mm = tile.dds.mipmap_list[1]
    mm.retrieved = True
    assert tile.get_bytes(mm.startpos, 1024)


@pytest.mark.parametrize(
    "populated, loads_per_probe",
    [
        (range(1, 5), 0),  # lower mipmaps only
        (range(0, 5), 1),  # mipmap zero present but incomplete
    ],
)
def test_unusable_entry_is_read_once_until_written(
    monkeypatch, tile, populated, loads_per_probe
):
    cache = PartialEntryCache(populated)
    monkeypatch.setattr(getortho, "dynamic_dds_cache", cache)

    _read_mipmap_one(tile)
    _read_mipmap_one(tile)
    _read_mipmap_one(tile)

    assert cache.metadata_reads == 1
    assert cache.loads == loads_per_probe

    cache.generations[(tile.id, tile.max_zoom)] = 1
    _read_mipmap_one(tile)

    assert cache.metadata_reads == 2
    assert cache.loads == 2 * loads_per_probe


def test_restored_partial_rows_are_not_installed_again(monkeypatch, tmp_path):
    from dds_manifest import RowBuildManifest, manifests_to_dict

    tile = getortho.Tile(
        0, 0, "BI", 16, cache_dir=str(tmp_path), max_zoom=17
    )
    row_size = tile.dds.mipmap_list[0].length // 32
    row = b"P" * row_size

    class Cache:
        @staticmethod
        def load_partial_rows(tile_id, max_zoom, current_tile):
            return {5: row}

    monkeypatch.setattr(getortho, "dynamic_dds_cache", Cache())
    monkeypatch.setattr(
        getortho.CFG.autoortho, "persist_partial_dds_cache", True
    )
    manifest = RowBuildManifest.create(
        tile_id=tile.id,
        target_zoom=tile.max_zoom,
        mipmap=0,
        row_index=5,
        build_generation=3,
        sources=bytes([1] * tile.chunks_per_row),
        compressed_data=row,
    )
    metadata = {
        "mm0_manifest": manifests_to_dict(
            {5: manifest}, target_zoom=tile.max_zoom
        ),
        "partial_mipmaps": {
            "0": {"unit": "chunk_row", "total": 32, "covered": [5],
                  "degraded": [], "revision": 3},
        },
    }
    tile._restore_provenance_from_metadata(metadata)
    commits = []
    real_commit = tile._commit_mm0_rows
    monkeypatch.setattr(
        tile,
        "_commit_mm0_rows",
        lambda results, installer: commits.append(results)
        or real_commit(results, installer),
    )

    assert tile._load_partial_dds_rows(metadata)
    assert not tile._load_partial_dds_rows(metadata)

    assert len(commits) == 1
    assert tile._persisted_exact_rows == {5}


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
