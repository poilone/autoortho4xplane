"""SparseMipmapBuffer keeps written rows without re-copying its neighbours."""

import os
import sys

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

import pydds  # noqa: E402

ROW = 64


def _buffer(rows=4):
    return pydds.SparseMipmapBuffer(
        rows * ROW, blocksize=8, unit_size=ROW, total_units=rows
    )


def test_rows_written_one_by_one_are_not_copied():
    sparse = _buffer()
    rows = [bytes([index]) * ROW for index in range(4)]
    for index in (2, 0, 3, 1):
        sparse.write_at(index * ROW, rows[index])

    segments = sparse.iter_segments()
    assert [start for start, _ in segments] == [0, ROW, 2 * ROW, 3 * ROW]
    # Same objects: no write re-copied the rows already in the buffer.
    assert all(data is row for (_, data), row in zip(segments, rows))
    assert sparse.read_at(0, 4 * ROW) == b"".join(rows)
    assert sparse.is_complete()
    assert sparse.allocated_bytes() == 4 * ROW


def test_rewriting_a_row_replaces_only_that_row():
    sparse = _buffer()
    for index in range(4):
        sparse.write_at(index * ROW, b"A" * ROW)
    sparse.write_at(ROW, b"B" * ROW)

    assert sparse.read_at(0, 4 * ROW) == (
        b"A" * ROW + b"B" * ROW + b"A" * 2 * ROW
    )
    assert sparse.allocated_bytes() == 4 * ROW


def test_write_across_adjacent_segments_keeps_their_outer_parts():
    sparse = _buffer(2)
    sparse.write_at(0, b"A" * ROW)
    sparse.write_at(ROW, b"B" * ROW)
    half = ROW // 2
    sparse.write_at(half, b"C" * ROW)

    assert sparse.read_at(0, 2 * ROW) == (
        b"A" * half + b"C" * ROW + b"B" * half
    )
    assert sparse.coverage() == {0, 1}
    assert sparse.allocated_bytes() == 2 * ROW


def test_unit_covered_by_two_adjacent_segments_counts_as_covered():
    sparse = _buffer(1)
    half = ROW // 2
    sparse.write_at(half, b"R" * half)
    assert sparse.coverage() == set()

    sparse.write_at(0, b"L" * half)

    assert sparse.coverage() == {0}
    assert sparse.is_complete()
