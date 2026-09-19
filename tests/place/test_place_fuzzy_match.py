"""Tests for the place duplicate-detection scan (docs/specs/place_duplicate_detection.md).

Covers acceptance criteria 4, 5, and 6: a same-named pair with no
conflicting hierarchy context is flagged, a same-named pair with different
country ancestors is not, and a same-named pair with conflicting MBIDs is
never flagged regardless of similarity.
"""

from types import SimpleNamespace

from src.place.place_fuzzy_match import (
    CHAIN_THRESHOLD,
    NAME_THRESHOLD,
    FuzzyMatchDialog,
    PlaceFuzzyMatchWorker,
    _blocking_keys,
    place_chain_similarity,
    place_name_similarity,
)


def _place(place_id, name, place_type=None, parent=None, mbid=None, assoc_count=0):
    return SimpleNamespace(
        place_id=place_id,
        place_name=name,
        place_type=place_type,
        parent=parent,
        MBID=mbid,
        recursive_association_count=assoc_count,
    )


# ---- acceptance criterion 4: no hierarchy context on either side ----------


def test_identical_name_with_no_parent_is_flagged(qapp):
    places = [_place(1, "France"), _place(2, "France")]
    worker = PlaceFuzzyMatchWorker(places, NAME_THRESHOLD, CHAIN_THRESHOLD)

    matches = worker._find_matches()

    assert len(matches) == 1
    assert matches[0][2] == 100


# ---- acceptance criterion 5: same leaf name, different country ancestor ---


def test_same_name_different_country_ancestor_is_not_flagged(qapp):
    france = _place(10, "France", place_type="Country")
    usa = _place(11, "United States", place_type="Country")
    paris_fr = _place(1, "Paris", place_type="City", parent=france)
    paris_us = _place(2, "Paris", place_type="City", parent=usa)
    worker = PlaceFuzzyMatchWorker([paris_fr, paris_us], NAME_THRESHOLD, CHAIN_THRESHOLD)

    matches = worker._find_matches()

    assert matches == []


def test_same_name_same_country_ancestor_is_flagged(qapp):
    # Positive control for the chain check above -- two rows that really do
    # look like the same real place (identical leaf name, identical
    # ancestor chain) must still be caught.
    usa_a = _place(10, "United States", place_type="Country")
    usa_b = _place(11, "United States", place_type="Country")
    springfield_a = _place(1, "Springfield", place_type="City", parent=usa_a)
    springfield_b = _place(2, "Springfield", place_type="City", parent=usa_b)
    worker = PlaceFuzzyMatchWorker([springfield_a, springfield_b], NAME_THRESHOLD, CHAIN_THRESHOLD)

    matches = worker._find_matches()

    assert len(matches) == 1


# ---- acceptance criterion 6: conflicting MBIDs are never suggested --------


def test_conflicting_mbids_are_never_suggested_as_a_match(qapp):
    places = [_place(1, "Springfield", mbid="mbid-a"), _place(2, "Springfield", mbid="mbid-b")]
    worker = PlaceFuzzyMatchWorker(places, NAME_THRESHOLD, CHAIN_THRESHOLD)

    matches = worker._find_matches()

    assert matches == []


def test_identical_name_with_one_missing_mbid_is_still_suggested(qapp):
    places = [_place(1, "Springfield", mbid="mbid-a"), _place(2, "Springfield", mbid=None)]
    worker = PlaceFuzzyMatchWorker(places, NAME_THRESHOLD, CHAIN_THRESHOLD)

    matches = worker._find_matches()

    assert len(matches) == 1


# ---- scoring helpers --------------------------------------------------------


def test_place_name_similarity_is_symmetric():
    assert place_name_similarity("Springfield", "Springfeild") == place_name_similarity(
        "Springfeild", "Springfield"
    )


def test_place_chain_similarity_passes_when_either_side_has_no_chain():
    lone = _place(1, "Springfield")
    has_parent = _place(2, "Springfield", parent=_place(3, "Illinois", place_type="State"))
    assert place_chain_similarity(lone, has_parent) == 1.0


def test_blocking_keys_share_prefix_for_near_identical_names():
    assert _blocking_keys("Springfield") & _blocking_keys("Springfeild")


# ---- dialog rendering --------------------------------------------------------


def test_merge_dialog_radio_text_keeps_ampersand(qapp):
    a = _place(1, "Salem & Providence", assoc_count=3)
    b = _place(2, "Salem and Providence", assoc_count=1)
    dialog = FuzzyMatchDialog([(a, b, 90)], controller=None)
    try:
        radio_a = dialog.match_widgets[0][1]
        # QRadioButton.text() returns the doubled form; Qt renders it as a
        # single literal '&'.
        assert radio_a.text() == "Salem && Providence — 3 associations"
    finally:
        dialog.deleteLater()
