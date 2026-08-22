"""Regression test: artist_name is no longer DB-unique (see
publisher_musicbrainz_import.py / album_musicbrainz_review_import.py --
MB-import resolvers now create a second same-named Artist rather than
merge two people confirmed distinct by different MBIDs). The duplicate-
artist fuzzy scanner must not immediately turn around and suggest
re-merging exactly the pair the import resolver just kept apart.
"""

from types import SimpleNamespace

from src.artist.artist_fuzzy_match import ArtistFuzzyMatchWorker


def _artist(artist_id, name, mbid=None):
    return SimpleNamespace(artist_id=artist_id, artist_name=name, MBID=mbid)


def test_conflicting_mbids_are_never_suggested_as_a_match(qapp):
    artists = [
        _artist(1, "John Smith", mbid="mbid-a"),
        _artist(2, "John Smith", mbid="mbid-b"),
    ]
    worker = ArtistFuzzyMatchWorker(artists, threshold=0.85)

    matches = worker._find_matches()

    assert matches == []


def test_identical_name_with_one_missing_mbid_is_still_suggested(qapp):
    # Only one side has an MBID -- not a confirmed conflict, still a
    # plausible duplicate worth flagging for a human to review.
    artists = [
        _artist(1, "John Smith", mbid="mbid-a"),
        _artist(2, "John Smith", mbid=None),
    ]
    worker = ArtistFuzzyMatchWorker(artists, threshold=0.85)

    matches = worker._find_matches()

    assert len(matches) == 1


def test_identical_mbid_pair_is_still_suggested(qapp):
    # Same MBID on both sides isn't the conflict case this guard targets;
    # unaffected, should score normally.
    artists = [
        _artist(1, "John Smith", mbid="same-mbid"),
        _artist(2, "John Smith", mbid="same-mbid"),
    ]
    worker = ArtistFuzzyMatchWorker(artists, threshold=0.85)

    matches = worker._find_matches()

    assert len(matches) == 1
