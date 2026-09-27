"""
disk_budget_manager.py - Unified disk space management for AutoOrtho

Provides centralized disk accounting and eviction across cache types:
- DDS cache (.dds + .ddm + .ddm.rows) - compiled textures
- JPEGs (.jpg) - source tile images

Budget enforcement is soft: writes are never blocked. Instead, when a
category exceeds its allocation, background eviction reclaims space by
deleting the least-recently-accessed entries.

Every mount worker process owns a DiskBudgetManager for the same cache
directory. Scans and evictions are serialized through a cross-process lock,
and the startup scan is published so sibling workers can reuse it.
"""

import json
import logging
import os
import threading
import time
from typing import List, Optional, Tuple

try:
    from autoortho.utils.process_lock import ProcessLock
    from autoortho.worker_modes import SESSION_ENV
except ImportError:
    from utils.process_lock import ProcessLock
    from worker_modes import SESSION_ENV

log = logging.getLogger(__name__)

_LOCK_FILENAME = ".disk_budget.lock"
_SHARED_SCAN_FILENAME = ".disk_usage.json"
_SHARED_SCAN_VERSION = 1
# Siblings start together; a restarted worker must not trust an old scan.
_SHARED_SCAN_MAX_AGE_SEC = 600.0
# A sibling stuck while holding the lock must not stall this worker forever.
_SCAN_LOCK_WAIT_SEC = 300.0
_JPEG_EXTENSIONS = (".jpg", ".jpeg")
_DDS_DIRNAME = "dds_cache"


class DiskUsageReport:
    """Snapshot of disk usage across all cache categories."""
    __slots__ = ("dds_bytes", "jpeg_bytes",
                 "total_bytes", "budget_bytes", "scan_time_ms")

    def __init__(self):
        self.dds_bytes = 0
        self.jpeg_bytes = 0
        self.total_bytes = 0
        self.budget_bytes = 0
        self.scan_time_ms = 0.0

    def __repr__(self):
        return (f"DiskUsage(dds={self.dds_bytes/(1024**2):.0f}MB, "
                f"jpeg={self.jpeg_bytes/(1024**2):.0f}MB, "
                f"total={self.total_bytes/(1024**2):.0f}MB / "
                f"{self.budget_bytes/(1024**2):.0f}MB)")


class DiskBudgetManager:
    """
    Unified disk space management for AutoOrtho caches.
    
    Tracks disk usage across DDS cache and JPEG files.
    Enforces per-category budgets through LRU eviction.
    
    Budget allocation (configurable, % of ``total_budget_mb``):
    - DDS cache: 80% (primary persistent storage)
    - JPEGs: 20% (source images retained until DDS is complete)
    
    Thread Safety:
        All public methods are thread-safe. Eviction runs in background
        threads to avoid blocking callers.
    """

    def __init__(self, cache_dir: str, total_budget_mb: int,
                 dds_budget_pct: int = 80,
                 jpeg_budget_pct: Optional[int] = None,
                 dds_cache=None):
        """
        Args:
            cache_dir: Base cache directory.
            total_budget_mb: Total disk budget in MB across all categories.
            dds_budget_pct: Percentage allocated to DDS cache (10-90).
            jpeg_budget_pct: Deprecated compatibility value. DDS allocation is
                authoritative and JPEGs receive the remaining percentage.
            dds_cache: Optional DynamicDDSCache instance for DDS eviction.
        """
        self._cache_dir = cache_dir
        self._total_budget = total_budget_mb * 1024 * 1024  # bytes

        # dds_budget_pct is a literal percentage of the total cache. Older
        # versions normalized it against a fixed 20% JPEG weight, so 40 meant
        # 66.7% DDS rather than the documented 40%.
        dds_budget_pct = max(10, min(90, dds_budget_pct))
        expected_jpeg_pct = 100 - dds_budget_pct
        if (
            jpeg_budget_pct is not None
            and int(jpeg_budget_pct) != expected_jpeg_pct
        ):
            log.warning(
                "Ignoring jpeg_budget_pct=%s; dds_budget_pct=%s is an "
                "absolute percentage and JPEGs receive the remaining %s%%",
                jpeg_budget_pct,
                dds_budget_pct,
                expected_jpeg_pct,
            )
        self._dds_budget = int(
            self._total_budget * dds_budget_pct / 100
        )
        self._jpeg_budget = self._total_budget - self._dds_budget

        # Current usage tracking (updated by scan and accounting calls)
        self._dds_usage = 0
        self._jpeg_usage = 0

        self._dds_cache = dds_cache  # Reference to DynamicDDSCache for eviction

        self._lock = threading.Lock()
        self._scan_complete = threading.Event()
        self._last_scan_time = 0.0
        self._eviction_in_progress = False

        self._process_lock_path = os.path.join(cache_dir, _LOCK_FILENAME)
        self._shared_scan_path = os.path.join(cache_dir, _SHARED_SCAN_FILENAME)

        log.info(f"DiskBudgetManager initialized: total={total_budget_mb}MB "
                 f"(DDS={self._dds_budget/(1024**2):.0f}MB, "
                 f"JPEGs={self._jpeg_budget/(1024**2):.0f}MB)")

    # ------------------------------------------------------------------
    # Accounting (called after writes)
    # ------------------------------------------------------------------

    def account_dds(self, size_bytes: int) -> None:
        """Account for a DDS cache write.
        
        Args:
            size_bytes: Size of the DDS file written (positive for add,
                        negative for removal).
        """
        with self._lock:
            self._dds_usage += size_bytes
            self._dds_usage = max(0, self._dds_usage)

        if self._dds_usage > self._dds_budget:
            self._schedule_eviction("dds")

    def account_jpeg(self, size_bytes: int) -> None:
        """Account for a JPEG cache write or deletion."""
        with self._lock:
            self._jpeg_usage += size_bytes
            self._jpeg_usage = max(0, self._jpeg_usage)
        if self._jpeg_usage > self._jpeg_budget:
            self._schedule_eviction("jpeg")

    # ------------------------------------------------------------------
    # Eviction
    # ------------------------------------------------------------------

    def check_and_evict(self) -> None:
        """
        Check all categories and evict if over budget.

        Called periodically (e.g., from TileCacher.clean loop) and
        after accounting calls when a budget is exceeded. Skipped when another
        process already holds the cache lock: it is scanning or evicting the
        same directory.
        """
        lock = ProcessLock(self._process_lock_path)
        if not lock.acquire(blocking=False):
            log.debug("Disk budget check skipped: cache lock held elsewhere")
            return
        try:
            self._evict_over_budget()
        finally:
            lock.release()

    def _evict_over_budget(self) -> None:
        """Evict over-budget categories. Caller holds the cache lock."""
        # DDS eviction
        if self._dds_usage > self._dds_budget and self._dds_cache is not None:
            excess = self._dds_usage - int(self._dds_budget * 0.9)
            if excess > 0:
                freed = self._dds_cache.evict_lru(excess)
                if freed == 0:
                    log.warning(
                        "DDS cache is over budget but no tracked entries "
                        "were available for eviction"
                    )

        if self._jpeg_usage > self._jpeg_budget:
            self._enforce_jpeg_budget()

    def _schedule_eviction(self, category: str) -> None:
        """Schedule a background eviction check for the given category."""
        with self._lock:
            if self._eviction_in_progress:
                return
            self._eviction_in_progress = True

        def _run():
            try:
                self.check_and_evict()
            finally:
                with self._lock:
                    self._eviction_in_progress = False

        t = threading.Thread(target=_run, daemon=True, name=f"disk_evict_{category}")
        t.start()

    # ------------------------------------------------------------------
    # Disk scanning
    # ------------------------------------------------------------------

    def scan_disk_usage(self) -> DiskUsageReport:
        """
        Scan the cache directory tree and compute actual disk usage.
        
        This is I/O intensive and should be called from a background thread.
        
        Returns:
            DiskUsageReport with per-category byte counts.
        """
        report = DiskUsageReport()
        report.budget_bytes = self._total_budget
        start = time.monotonic()

        try:
            # Include compiled DDS data, metadata, and partial-row sidecars.
            dds_dir = os.path.join(self._cache_dir, _DDS_DIRNAME)
            if os.path.isdir(dds_dir):
                report.dds_bytes = self._scan_dir_size(
                    dds_dir,
                    (".dds", ".ddm", ".ddm.rows"),
                )

            # Scan JPEG files
            report.jpeg_bytes = self._scan_jpegs_size()

        except Exception as e:
            log.warning(f"Disk usage scan error: {e}")

        report.total_bytes = report.dds_bytes + report.jpeg_bytes
        report.scan_time_ms = (time.monotonic() - start) * 1000

        # Update tracked usage
        with self._lock:
            self._dds_usage = report.dds_bytes
            self._jpeg_usage = report.jpeg_bytes
            self._last_scan_time = time.time()

        self._scan_complete.set()

        log.info(f"Disk scan complete in {report.scan_time_ms:.0f}ms: {report}")
        return report

    def initial_scan(self) -> None:
        """
        Run initial disk scan and cleanup. Intended for background thread at startup.

        Performs:
        1. Full disk usage scan, or adoption of the scan a sibling mount
           worker of the same session just published
        2. Budget enforcement (eviction if needed)

        The cache lock makes sibling workers wait for the first scan instead
        of walking the same tree concurrently.
        """
        lock = ProcessLock(self._process_lock_path)
        try:
            if not lock.acquire(timeout=_SCAN_LOCK_WAIT_SEC):
                log.warning(
                    "Disk cache lock still held after %.0fs; scanning "
                    "without it and skipping eviction",
                    _SCAN_LOCK_WAIT_SEC,
                )
                self.scan_disk_usage()
                return
            try:
                scanned = not self._adopt_shared_scan()
                if scanned:
                    self.scan_disk_usage()
                usage_before = self._tracked_usage()
                self._evict_over_budget()
                if scanned or self._tracked_usage() != usage_before:
                    self._publish_shared_scan()
            finally:
                lock.release()
        except Exception as e:
            log.warning(f"Initial disk scan error: {e}")
        finally:
            self._scan_complete.set()

    def _tracked_usage(self) -> Tuple[int, int]:
        with self._lock:
            return self._dds_usage, self._jpeg_usage

    @staticmethod
    def _session_id() -> str:
        return os.environ.get(SESSION_ENV, "")

    def _adopt_shared_scan(self) -> bool:
        """Load usage published by a sibling worker of this session, if fresh."""
        session = self._session_id()
        if not session:
            return False
        try:
            with open(self._shared_scan_path, "r", encoding="utf-8") as f:
                shared = json.load(f)
            if (
                shared.get("version") != _SHARED_SCAN_VERSION
                or shared.get("session") != session
                or os.path.normcase(shared.get("cache_dir", ""))
                != os.path.normcase(os.path.abspath(self._cache_dir))
            ):
                return False
            scanned_at = float(shared["scanned_at"])
            dds_bytes = int(shared["dds_bytes"])
            jpeg_bytes = int(shared["jpeg_bytes"])
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            return False
        age = time.time() - scanned_at
        if not 0 <= age <= _SHARED_SCAN_MAX_AGE_SEC:
            return False
        with self._lock:
            self._dds_usage = max(0, dds_bytes)
            self._jpeg_usage = max(0, jpeg_bytes)
            self._last_scan_time = scanned_at
        log.info(
            "Disk usage reused from sibling worker scan (%.0fs old): "
            "dds=%.0fMB jpeg=%.0fMB",
            age,
            dds_bytes / (1024 ** 2),
            jpeg_bytes / (1024 ** 2),
        )
        return True

    def _publish_shared_scan(self) -> None:
        """Publish tracked usage for sibling workers. Caller holds the lock."""
        session = self._session_id()
        if not session:
            return
        with self._lock:
            shared = {
                "version": _SHARED_SCAN_VERSION,
                "session": session,
                "cache_dir": os.path.abspath(self._cache_dir),
                "scanned_at": self._last_scan_time,
                "dds_bytes": self._dds_usage,
                "jpeg_bytes": self._jpeg_usage,
            }
        tmp = f"{self._shared_scan_path}.tmp.{os.getpid()}"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(shared, f)
            os.replace(tmp, self._shared_scan_path)
        except OSError as exc:
            log.debug("Could not publish shared disk scan: %s", exc)
            try:
                os.remove(tmp)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def usage_report(self) -> dict:
        """Return current usage statistics."""
        with self._lock:
            return {
                "dds_usage_mb": self._dds_usage / (1024 ** 2),
                "dds_budget_mb": self._dds_budget / (1024 ** 2),
                "jpeg_usage_mb": self._jpeg_usage / (1024 ** 2),
                "jpeg_budget_mb": self._jpeg_budget / (1024 ** 2),
                "total_usage_mb": (self._dds_usage + self._jpeg_usage) / (1024 ** 2),
                "total_budget_mb": self._total_budget / (1024 ** 2),
                "last_scan": self._last_scan_time,
            }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _iter_files(root: str, skip_dirnames=()):
        """Yield ``os.DirEntry`` objects for files under ``root``.

        ``DirEntry.stat()`` reuses the directory listing on Windows, whereas
        ``os.path.getsize`` issues one extra system call per file; the JPEG
        cache holds hundreds of thousands of files.
        """
        stack = [root]
        while stack:
            directory = stack.pop()
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                if entry.name not in skip_dirnames:
                                    stack.append(entry.path)
                            elif entry.is_file(follow_symlinks=False):
                                yield entry
                        except OSError:
                            continue
            except OSError:
                continue

    @classmethod
    def _scan_dir_size(cls, root: str, extensions) -> int:
        """Sum file sizes under ``root`` matching one or more extensions."""
        if isinstance(extensions, str):
            extensions = (extensions,)
        else:
            extensions = tuple(extensions)
        total = 0
        for entry in cls._iter_files(root):
            if entry.name.endswith(extensions):
                try:
                    total += entry.stat(follow_symlinks=False).st_size
                except OSError:
                    pass
        return total

    def _list_jpegs(self) -> List[Tuple[float, str, int]]:
        """Return ``(mtime, path, size)`` for JPEG files outside the DDS cache."""
        jpegs = []
        for entry in self._iter_files(self._cache_dir, (_DDS_DIRNAME,)):
            if not entry.name.lower().endswith(_JPEG_EXTENSIONS):
                continue
            try:
                stat = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            jpegs.append((stat.st_mtime, entry.path, stat.st_size))
        return jpegs

    def _scan_jpegs_size(self) -> int:
        """Total size of JPEG files in the cache."""
        return sum(size for _mtime, _path, size in self._list_jpegs())

    def _enforce_jpeg_budget(self) -> int:
        """Delete the oldest JPEGs until actual usage is 90% of the budget.

        Usage is re-measured instead of trusting the in-memory counter: each
        worker only accounts its own writes, and a sibling may already have
        evicted. Returns the number of bytes freed.
        """
        candidates = self._list_jpegs()
        actual = sum(size for _mtime, _path, size in candidates)
        freed = 0
        if actual > self._jpeg_budget:
            bytes_to_free = actual - int(self._jpeg_budget * 0.9)
            candidates.sort()
            for _mtime, path, size in candidates:
                if freed >= bytes_to_free:
                    break
                try:
                    os.remove(path)
                    freed += size
                except OSError:
                    continue
        with self._lock:
            self._jpeg_usage = max(0, actual - freed)
        if freed:
            log.info(
                "JPEG cache eviction freed %.0fMB",
                freed / (1024 ** 2),
            )
        return freed

    @staticmethod
    def _safe_remove(path: str) -> None:
        """Remove a file, ignoring errors."""
        try:
            os.remove(path)
        except OSError:
            pass
