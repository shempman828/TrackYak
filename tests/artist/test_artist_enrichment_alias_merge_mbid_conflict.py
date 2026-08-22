"""Regression test: artist_name is no longer DB-unique (see
publisher_musicbrainz_import.py / album_musicbrainz_review_import.py), so
the MB alias-merge suggestion in ArtistEnrichmentReviewDialog must not
propose merging an alias onto a same-named local Artist that already
carries a *different* MBID -- MusicBrainz itself confirms those are
distinct people.
"""

from types import SimpleNamespace

from src.artist.artist_enrichment_review_dialog import ArtistEnrichmentReviewDialog
from src.musicbrainz.musicbrainz_artist import MBAlias


def _artist(artist_id, name, mbid=None):
    return SimpleNamespace(artist_id=artist_id, artist_name=name, MBID=mbid)


def _make_dialog(artist, all_artists):
    # Bypass QDialog/__init__'s full widget construction -- only
    # _find_alias_merge_candidates's own attribute reads (self.controller,
    # self.artist) are under test here.
    dialog = ArtistEnrichmentReviewDialog.__new__(ArtistEnrichmentReviewDialog)
    dialog.artist = artist
    dialog.controller = SimpleNamespace(
        get=SimpleNamespace(get_all_entities=lambda *a, **k: all_artists)
    )
    return dialog


def test_alias_match_with_conflicting_mbid_is_not_suggested():
    artist = _artist(1, "John Smith", mbid="mbid-a")
    other = _artist(2, "John Smith", mbid="mbid-b")
    dialog = _make_dialog(artist, [artist, other])

    candidates = dialog._find_alias_merge_candidates([MBAlias(name="John Smith", type="Legal name")])

    assert candidates == {}


def test_alias_match_with_no_existing_mbid_is_still_suggested():
    artist = _artist(1, "John Smith", mbid="mbid-a")
    other = _artist(2, "John Smith", mbid=None)
    dialog = _make_dialog(artist, [artist, other])

    candidates = dialog._find_alias_merge_candidates([MBAlias(name="John Smith", type="Legal name")])

    assert "John Smith" in candidates
    assert candidates["John Smith"][0] is other
