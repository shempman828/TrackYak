"""Unit tests for QueueManager's upcoming-queue mutation methods.

These cover the batch operations the queue dock delegates to
(remove_upcoming / move_upcoming_to_next / jump_to_upcoming) — added when
the dock's inline queue.pop/insert reimplementations were consolidated back
onto the manager.
"""

import pytest

from src.player.queue_utility import QueueManager

pytestmark = pytest.mark.usefixtures("qapp")


class _FakeTrack:
    def __init__(self, name):
        self.track_name = name

    def __repr__(self):
        return f"<{self.track_name}>"


def _manager(n):
    qm = QueueManager()
    qm.queue = [_FakeTrack(f"t{i}") for i in range(n)]
    return qm


def _names(qm):
    return [t.track_name for t in qm.queue]


def _count_signals(qm):
    hits = []
    qm.queue_changed.connect(lambda: hits.append(1))
    return hits


# ── remove_upcoming ──────────────────────────────────────────────────────────


def test_remove_upcoming_removes_by_upcoming_index_and_emits_once():
    qm = _manager(6)  # current t0, upcoming t1..t5
    hits = _count_signals(qm)

    removed = qm.remove_upcoming([0, 2])  # -> t1 and t3

    assert removed == 2
    assert _names(qm) == ["t0", "t2", "t4", "t5"]
    assert len(hits) == 1


def test_remove_upcoming_ignores_out_of_range_and_duplicate_rows():
    qm = _manager(3)  # current t0, upcoming t1, t2
    hits = _count_signals(qm)

    removed = qm.remove_upcoming([1, 1, 99, -1])  # only row 1 (t2) is valid

    assert removed == 1
    assert _names(qm) == ["t0", "t1"]
    assert len(hits) == 1


def test_remove_upcoming_no_valid_rows_does_not_emit():
    qm = _manager(3)
    hits = _count_signals(qm)

    assert qm.remove_upcoming([50, 60]) == 0
    assert _names(qm) == ["t0", "t1", "t2"]
    assert hits == []


def test_remove_upcoming_never_touches_current_track():
    qm = _manager(4)
    # There is no upcoming index that maps to queue[0].
    qm.remove_upcoming(range(-5, 10))
    assert qm.queue[0].track_name == "t0"


# ── move_upcoming_to_next ────────────────────────────────────────────────────


def test_move_upcoming_to_next_preserves_relative_order_and_emits_once():
    qm = _manager(6)  # current t0, upcoming t1..t5
    hits = _count_signals(qm)

    moved = qm.move_upcoming_to_next([1, 3])  # t2 and t4 -> right after current

    assert moved == 2
    assert _names(qm) == ["t0", "t2", "t4", "t1", "t3", "t5"]
    assert len(hits) == 1


def test_move_upcoming_to_next_noop_when_no_valid_rows():
    qm = _manager(3)
    hits = _count_signals(qm)

    assert qm.move_upcoming_to_next([42]) == 0
    assert _names(qm) == ["t0", "t1", "t2"]
    assert hits == []


def test_move_upcoming_to_next_already_adjacent_is_stable():
    qm = _manager(4)
    moved = qm.move_upcoming_to_next([0, 1])
    assert moved == 2
    assert _names(qm) == ["t0", "t1", "t2", "t3"]


# ── jump_to_upcoming ────────────────────────────────────────────────────────


def test_jump_to_upcoming_moves_track_to_front_and_returns_it():
    qm = _manager(5)  # current t0, upcoming t1..t4
    hits = _count_signals(qm)

    result = qm.jump_to_upcoming(2)  # t3

    assert result.track_name == "t3"
    assert _names(qm) == ["t3", "t0", "t1", "t2", "t4"]
    assert len(hits) == 1


def test_jump_to_upcoming_out_of_range_returns_none_without_emitting():
    qm = _manager(3)
    hits = _count_signals(qm)

    assert qm.jump_to_upcoming(10) is None
    assert _names(qm) == ["t0", "t1", "t2"]
    assert hits == []
