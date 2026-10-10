"""The startup scan of the DDS cache can run late without harming entries.

It now runs once the flight has started, while this and other mount workers
write entries and the tiles of the flight have already been read.
"""

import os
import sys
import time

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from autoortho.aopipeline.dynamic_dds_cache import DynamicDDSCache  # noqa: E402
from test_dynamic_dds_cache import MockTile, _manifest_rows  # noqa: E402

DAY_AGO = time.time() - 86400


def _cache(tmp_path):
    return DynamicDDSCache(cache_dir=str(tmp_path), max_size_mb=0, enabled=True)


def _file(path, age_sec=None, content=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(content)
    if age_sec is not None:
        mtime = time.time() - age_sec
        os.utime(path, (mtime, mtime))
    return path


def test_scan_deletes_only_old_orphans(tmp_path):
    cache = _cache(tmp_path)
    folder = os.path.join(cache._dds_root, "+40+000")
    old = [
        _file(os.path.join(folder, "old.dds"), age_sec=3600),
        _file(os.path.join(folder, "old.ddm.rows"), age_sec=3600),
        _file(os.path.join(folder, "old_bad.ddm"), age_sec=3600, content=b"{"),
    ]
    # Being written right now: .dds or rows before the .ddm.
    young = [
        _file(os.path.join(folder, "new.dds")),
        _file(os.path.join(folder, "new.ddm.rows")),
        _file(os.path.join(folder, "new_bad.ddm"), content=b"{"),
    ]
    try:
        cache.scan_existing()
    finally:
        cache.close()

    assert [path for path in old if os.path.exists(path)] == []
    assert [path for path in young if not os.path.exists(path)] == []


def test_scan_can_be_stopped_between_directories(tmp_path):
    cache = _cache(tmp_path)
    orphan = _file(
        os.path.join(cache._dds_root, "+40+000", "old.dds"), age_sec=3600
    )
    try:
        assert cache.scan_existing(lambda: False) == 0
        assert cache.wait_for_scan(0)
    finally:
        cache.close()

    assert os.path.exists(orphan)


def test_entry_read_before_the_scan_keeps_its_access_time(tmp_path):
    tile = MockTile()
    tile.cache_dir = str(tmp_path)
    dds_bytes = bytearray(tile.dds.total_size)
    dds_bytes[:4] = b"DDS "
    for mm in tile.dds.mipmap_list:
        dds_bytes[mm.startpos:mm.endpos] = bytes([mm.idx & 0xFF]) * mm.length
    dds_bytes = bytes(dds_bytes)

    previous_session = _cache(tmp_path)
    try:
        assert previous_session.store(
            tile.id, 16, dds_bytes, tile,
            mm0_manifest=_manifest_rows(tile, 16, dds_bytes),
        )
    finally:
        previous_session.close()
    dds_path, ddm_path = previous_session._paths_for(
        tile.row, tile.col, tile.maptype, tile.tilename_zoom, 16
    )
    for path in (dds_path, ddm_path):
        os.utime(path, (DAY_AGO, DAY_AGO))

    session = _cache(tmp_path)
    try:
        assert session.load(tile.id, 16, tile) == dds_bytes  # flight load
        session.scan_existing()
        access_time = session._entries[session._tile_key(tile.id, 16)][3]
    finally:
        session.close()

    assert access_time > time.time() - 60
