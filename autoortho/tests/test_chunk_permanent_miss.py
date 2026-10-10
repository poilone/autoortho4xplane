"""A chunk the provider does not have is not downloaded again for a while."""

import logging
import os
import sys

import pytest

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

import getortho  # noqa: E402

JPEG = b"\xff\xd8\xff" + b"\x00" * 64


class FakeResponse:
    def __init__(self, status_code=200, content=b""):
        self.status_code = status_code
        self.content = content
        self.headers = {"content-type": "image/png"}


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    monkeypatch.setattr(getortho, "tile_completion_tracker", None, raising=False)
    monkeypatch.setattr(getortho, "_permanent_misses", getortho._PermanentMissMemo())
    monkeypatch.setattr(getortho, "_reported_permanent_statuses", set())


def _chunk(cache_dir, maptype="BI"):
    return getortho.Chunk(1, 2, maptype, 13, cache_dir=str(cache_dir), tile_id="t")


def _fail(chunk, response):
    request = chunk.begin_network_attempt()
    assert isinstance(request, getortho._NetworkRequest)
    outcome = chunk.finish_network_attempt(request, response)
    assert outcome.resolved is True
    return chunk


def test_non_jpeg_answer_is_a_permanent_failure(tmp_path):
    chunk = _fail(_chunk(tmp_path), FakeResponse(200, b"\x89PNG no imagery"))

    assert chunk.permanent_failure is True
    assert chunk.failure_reason == "invalid_jpeg"
    assert chunk.data == b""
    assert chunk.ready.is_set()


@pytest.mark.parametrize(
    "response, reason",
    [
        (FakeResponse(404), "404"),
        (FakeResponse(410), "410"),
        (FakeResponse(200, b"<html>no tile</html>"), "invalid_jpeg"),
    ],
)
def test_known_miss_resolves_without_a_request(tmp_path, response, reason):
    _fail(_chunk(tmp_path), response)

    retry = _chunk(tmp_path)
    outcome = retry.begin_network_attempt()

    assert isinstance(outcome, getortho._AttemptOutcome)
    assert outcome.resolved is True
    assert retry.permanent_failure is True
    assert retry.failure_reason == reason
    assert retry.data == b""
    assert retry.ready.is_set()
    assert retry.attempt == 0


def test_other_permanent_codes_are_not_remembered(tmp_path):
    _fail(_chunk(tmp_path), FakeResponse(400))

    request = _chunk(tmp_path).begin_network_attempt()

    assert isinstance(request, getortho._NetworkRequest)


def test_cached_jpeg_wins_over_a_known_miss(tmp_path):
    first = _fail(_chunk(tmp_path), FakeResponse(404))
    with open(first.cache_path, "wb") as handle:
        handle.write(JPEG)
    getortho._chunk_cache_index.invalidate(first.cache_path)

    retry = _chunk(tmp_path)
    outcome = retry.begin_network_attempt()

    assert isinstance(outcome, getortho._AttemptOutcome)
    assert retry.permanent_failure is False
    assert retry.data == JPEG


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _memo(clock):
    return getortho._PermanentMissMemo(
        ttl=900, retry=30, confirm=120, clock=clock
    )


def test_miss_is_retried_every_30_s_for_2_min_then_kept_15_min():
    clock = FakeClock()
    memo = _memo(clock)

    for _ in range(4):  # answers at 0, 30, 60 and 90 s
        memo.add("a.jpg", "404")
        clock.now += 29
        assert memo.get("a.jpg") == "404"
        clock.now += 1
        assert memo.get("a.jpg") is None  # asked again

    memo.add("a.jpg", "404")  # still missing after 2 min
    clock.now += 899
    assert memo.get("a.jpg") == "404"
    clock.now += 1
    assert memo.get("a.jpg") is None


def test_answer_in_flight_does_not_extend_a_miss():
    clock = FakeClock()
    memo = _memo(clock)

    memo.add("a.jpg", "404")
    clock.now += 10
    memo.add("a.jpg", "404")  # request sent before the first answer
    clock.now += 20
    assert memo.get("a.jpg") is None


def test_late_retry_of_a_lasting_hole_is_kept_15_min():
    clock = FakeClock()
    memo = _memo(clock)

    memo.add("a.jpg", "404")
    clock.now += 300  # nobody asked during the first 2 min
    assert memo.get("a.jpg") is None
    memo.add("a.jpg", "404")
    clock.now += 899
    assert memo.get("a.jpg") == "404"


def test_old_miss_is_forgotten():
    clock = FakeClock()
    memo = _memo(clock)

    memo.add("a.jpg", "404")
    clock.now += 30 + 900
    assert memo.get("a.jpg") is None
    memo.add("a.jpg", "404")  # a new hole: retried every 30 s again
    clock.now += 30
    assert memo.get("a.jpg") is None


def test_memo_is_bounded():
    bounded = getortho._PermanentMissMemo(ttl=60, retry=60, max_entries=2)
    for key in ("a.jpg", "b.jpg", "c.jpg"):
        bounded.add(key, "404")
    assert bounded.get("a.jpg") is None
    assert bounded.get("c.jpg") == "404"


def test_repeated_404_is_reported_once_per_maptype(tmp_path, caplog):
    caplog.set_level(logging.DEBUG, logger=getortho.log.name)
    for index in range(3):
        _fail(_chunk(tmp_path / str(index)), FakeResponse(404))

    messages = [
        record for record in caplog.records
        if "permanently failed with 404" in record.getMessage()
    ]
    assert [record.levelno for record in messages] == [
        logging.INFO, logging.DEBUG, logging.DEBUG
    ]
    assert not [
        record for record in caplog.records
        if record.levelno >= logging.WARNING
    ]
