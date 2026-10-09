"""Tests for src/place/place_hierarchy.py: descendants, cycle checks, and one-pass association counts."""

from types import SimpleNamespace

from src.place.place_hierarchy import association_counts, descendant_ids, would_create_cycle


def _place(place_id, parent_id=None, n_assoc=0):
    return SimpleNamespace(place_id=place_id, parent_id=parent_id, associations=[object()] * n_assoc)


def _tree():
    # 1 Country > 2 State > 3 City > 4 Venue; 5 is a separate top-level place
    places = [_place(1, None, 1), _place(2, 1, 2), _place(3, 2, 0), _place(4, 3, 4), _place(5, None, 3)]
    return places, {p.place_id: p for p in places}


def test_descendant_ids_returns_whole_subtree_without_the_place():
    _places, by_id = _tree()
    assert descendant_ids(by_id, 1) == {2, 3, 4}
    assert descendant_ids(by_id, 4) == set()


def test_would_create_cycle_detects_self_and_descendant_parents():
    _places, by_id = _tree()
    assert would_create_cycle(by_id, [2], 2) is True
    assert would_create_cycle(by_id, [2], 4) is True
    assert would_create_cycle(by_id, [2], 5) is False
    assert would_create_cycle(by_id, [2], None) is False
    assert would_create_cycle(by_id, [5, 2], 3) is True


def test_association_counts_sums_subtree():
    places, _by_id = _tree()
    counts = association_counts(places)
    assert counts[1] == (1, 7)
    assert counts[2] == (2, 6)
    assert counts[4] == (4, 4)
    assert counts[5] == (3, 3)


def test_association_counts_survives_parent_cycle_in_data():
    places = [_place(1, 2, 1), _place(2, 1, 1)]
    counts = association_counts(places)
    assert set(counts) == {1, 2}
    assert descendant_ids({p.place_id: p for p in places}, 1) == {2}
