"""_wait_for_chunk_data: event-driven wait used before native mipmap builds."""

import os
import sys
import threading
import time

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

import getortho  # noqa: E402


class FakeChunk:
    def __init__(self, data=None, ready=False):
        self.data = data
        self.ready = threading.Event()
        if ready:
            self.ready.set()

    def resolve(self, data):
        self.data = data
        self.ready.set()


def _resolve_later(chunk, data, delay):
    timer = threading.Timer(delay, chunk.resolve, args=(data,))
    timer.start()
    return timer


def test_returns_as_soon_as_all_chunks_hold_data():
    chunks = [FakeChunk(), FakeChunk()]
    timers = [_resolve_later(c, b"jpeg", 0.05) for c in chunks]
    start = time.monotonic()

    got = getortho._wait_for_chunk_data(chunks, 2, start + 5.0)

    assert got == 2
    assert time.monotonic() - start < 2.0
    for t in timers:
        t.join()


def test_failed_download_ends_the_wait_immediately():
    """A chunk resolved without data makes the requirement unreachable."""
    failed = FakeChunk()
    slow = FakeChunk()
    timer = _resolve_later(failed, b"", 0.05)
    start = time.monotonic()

    got = getortho._wait_for_chunk_data([failed, slow], 2, start + 5.0)

    assert got == 0
    assert time.monotonic() - start < 2.0
    timer.join()


def test_unreachable_requirement_does_not_wait_at_all():
    """Chunks that already failed are not pending, so nothing can satisfy it."""
    start = time.monotonic()

    got = getortho._wait_for_chunk_data([FakeChunk()], 2, start + 5.0)

    assert got == 0
    assert time.monotonic() - start < 0.5


def test_deadline_is_respected():
    start = time.monotonic()

    got = getortho._wait_for_chunk_data([FakeChunk()], 1, start + 0.2)

    assert got == 0
    assert 0.15 <= time.monotonic() - start < 2.0


def test_nothing_required():
    assert getortho._wait_for_chunk_data([FakeChunk()], 0, time.monotonic() + 5) == 0
