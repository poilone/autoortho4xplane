"""Cross-process behaviour of DiskBudgetManager and ProcessLock.

Every mount worker owns a DiskBudgetManager on the same cache directory, so
these tests use several managers on one directory to stand in for siblings.
"""

import json
import os
import subprocess
import sys
import time

import pytest

AUTOORTHO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, AUTOORTHO_DIR)

from aopipeline.disk_budget_manager import (  # noqa: E402
    DiskBudgetManager,
    _LOCK_FILENAME,
    _SHARED_SCAN_FILENAME,
    _SHARED_SCAN_MAX_AGE_SEC,
)
from utils.process_lock import ProcessLock  # noqa: E402
from worker_modes import SESSION_ENV  # noqa: E402

KB = 1024


def _write(path, size, mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\0" * size)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def _jpeg_names(cache_dir):
    return sorted(name for name in os.listdir(cache_dir) if name.endswith(".jpg"))


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setenv(SESSION_ENV, "session-a")
    return "session-a"


def _fail_scan():
    raise AssertionError("sibling worker must reuse the shared scan")


class TestScan:
    def test_counts_jpegs_outside_dds_cache(self, tmp_path):
        cache = str(tmp_path)
        _write(os.path.join(cache, "1_2_16_BI.jpg"), 3 * KB)
        _write(os.path.join(cache, "nested", "3_4_16_BI.JPG"), 5 * KB)
        _write(os.path.join(cache, "dds_cache", "stray.jpg"), 7 * KB)
        _write(os.path.join(cache, "dds_cache", "a", "b_z16.dds"), 11 * KB)
        _write(os.path.join(cache, "dds_cache", "a", "b_z16.ddm"), 1 * KB)

        report = DiskBudgetManager(cache, total_budget_mb=100).scan_disk_usage()

        assert report.jpeg_bytes == 8 * KB
        assert report.dds_bytes == 12 * KB


class TestSharedStartupScan:
    def test_sibling_reuses_published_scan(self, tmp_path, session, monkeypatch):
        cache = str(tmp_path)
        _write(os.path.join(cache, "1_2_16_BI.jpg"), 4 * KB)

        first = DiskBudgetManager(cache, total_budget_mb=100)
        first.initial_scan()
        assert os.path.isfile(os.path.join(cache, _SHARED_SCAN_FILENAME))

        sibling = DiskBudgetManager(cache, total_budget_mb=100)
        monkeypatch.setattr(sibling, "scan_disk_usage", _fail_scan)
        sibling.initial_scan()

        assert sibling.usage_report["jpeg_usage_mb"] == pytest.approx(4 / 1024)
        assert sibling._scan_complete.is_set()

    def test_scan_from_another_session_is_ignored(self, tmp_path, session, monkeypatch):
        cache = str(tmp_path)
        _write(os.path.join(cache, "1_2_16_BI.jpg"), 4 * KB)
        DiskBudgetManager(cache, total_budget_mb=100).initial_scan()

        monkeypatch.setenv(SESSION_ENV, "session-b")
        _write(os.path.join(cache, "3_4_16_BI.jpg"), 4 * KB)
        later = DiskBudgetManager(cache, total_budget_mb=100)
        later.initial_scan()

        assert later.usage_report["jpeg_usage_mb"] == pytest.approx(8 / 1024)

    def test_stale_scan_is_ignored(self, tmp_path, session):
        cache = str(tmp_path)
        _write(os.path.join(cache, "1_2_16_BI.jpg"), 4 * KB)
        stale = {
            "version": 1,
            "session": session,
            "cache_dir": os.path.abspath(cache),
            "scanned_at": time.time() - _SHARED_SCAN_MAX_AGE_SEC - 60,
            "dds_bytes": 0,
            "jpeg_bytes": 999 * KB,
        }
        with open(os.path.join(cache, _SHARED_SCAN_FILENAME), "w") as f:
            json.dump(stale, f)

        mgr = DiskBudgetManager(cache, total_budget_mb=100)
        mgr.initial_scan()

        assert mgr.usage_report["jpeg_usage_mb"] == pytest.approx(4 / 1024)

    def test_stuck_lock_holder_does_not_block_startup(self, tmp_path, session, monkeypatch):
        import aopipeline.disk_budget_manager as dbm

        monkeypatch.setattr(dbm, "_SCAN_LOCK_WAIT_SEC", 0.3)
        cache = str(tmp_path)
        _write(os.path.join(cache, "1_2_16_BI.jpg"), 4 * KB)
        holder = ProcessLock(os.path.join(cache, _LOCK_FILENAME))
        assert holder.acquire(blocking=False)
        try:
            mgr = DiskBudgetManager(cache, total_budget_mb=100)
            mgr.initial_scan()
        finally:
            holder.release()

        assert mgr.usage_report["jpeg_usage_mb"] == pytest.approx(4 / 1024)
        assert mgr._scan_complete.is_set()
        assert not os.path.exists(os.path.join(cache, _SHARED_SCAN_FILENAME))

    def test_without_session_nothing_is_shared(self, tmp_path, monkeypatch):
        monkeypatch.delenv(SESSION_ENV, raising=False)
        cache = str(tmp_path)
        _write(os.path.join(cache, "1_2_16_BI.jpg"), 4 * KB)

        DiskBudgetManager(cache, total_budget_mb=100).initial_scan()

        assert not os.path.exists(os.path.join(cache, _SHARED_SCAN_FILENAME))


class TestJpegEviction:
    # 1 MiB total with 10% DDS leaves a 0.9 MiB JPEG budget.
    BUDGET_MB = 1
    DDS_PCT = 10

    def _fill(self, cache, count=6, size=200 * KB):
        base = time.time() - 1000
        for i in range(count):
            _write(os.path.join(cache, f"{i}_0_16_BI.jpg"), size, mtime=base + i)

    def test_evicts_oldest_down_to_ninety_percent(self, tmp_path):
        cache = str(tmp_path)
        self._fill(cache)
        mgr = DiskBudgetManager(cache, self.BUDGET_MB, dds_budget_pct=self.DDS_PCT)
        mgr._jpeg_usage = 6 * 200 * KB

        mgr.check_and_evict()

        assert _jpeg_names(cache) == [f"{i}_0_16_BI.jpg" for i in range(2, 6)]
        assert mgr._jpeg_usage == 4 * 200 * KB

    def test_stale_counter_does_not_over_evict(self, tmp_path):
        """A sibling already evicted: re-measure instead of trusting the counter."""
        cache = str(tmp_path)
        self._fill(cache, count=3)
        mgr = DiskBudgetManager(cache, self.BUDGET_MB, dds_budget_pct=self.DDS_PCT)
        mgr._jpeg_usage = 6 * 200 * KB

        mgr.check_and_evict()

        assert len(_jpeg_names(cache)) == 3
        assert mgr._jpeg_usage == 3 * 200 * KB

    def test_skipped_while_another_process_holds_the_lock(self, tmp_path):
        cache = str(tmp_path)
        self._fill(cache)
        mgr = DiskBudgetManager(cache, self.BUDGET_MB, dds_budget_pct=self.DDS_PCT)
        mgr._jpeg_usage = 6 * 200 * KB

        holder = ProcessLock(os.path.join(cache, _LOCK_FILENAME))
        assert holder.acquire(blocking=False)
        try:
            mgr.check_and_evict()
        finally:
            holder.release()

        assert len(_jpeg_names(cache)) == 6


class TestProcessLock:
    def test_second_handle_is_excluded_until_release(self, tmp_path):
        path = str(tmp_path / "x.lock")
        first, second = ProcessLock(path), ProcessLock(path)

        assert first.acquire(blocking=False)
        assert not second.acquire(blocking=False)
        assert not second.acquire(timeout=0.3)

        first.release()
        assert second.acquire(blocking=False)
        second.release()

    def test_not_reentrant(self, tmp_path):
        lock = ProcessLock(str(tmp_path / "x.lock"))
        with lock:
            with pytest.raises(RuntimeError):
                lock.acquire(blocking=False)
        assert not lock.held

    def test_released_when_holder_process_dies(self, tmp_path):
        """A crashed AutoOrtho must never leave a stale lock behind."""
        path = str(tmp_path / "x.lock")
        holder = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys, time\n"
                f"sys.path.insert(0, {AUTOORTHO_DIR!r})\n"
                "from utils.process_lock import ProcessLock\n"
                f"lock = ProcessLock({path!r})\n"
                "assert lock.acquire(blocking=False)\n"
                "print('held', flush=True)\n"
                "time.sleep(60)\n",
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert holder.stdout.readline().strip() == "held"
            assert not ProcessLock(path).acquire(blocking=False)
        finally:
            holder.kill()
            holder.wait(timeout=10)
            holder.stdout.close()

        survivor = ProcessLock(path)
        assert survivor.acquire(timeout=5)
        survivor.release()
