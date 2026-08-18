"""Tests for fetch_release_detail's release-level `artist-credit` parsing --
the release "Album Artist" byline, structurally distinct from
`artist-relation-list` (producer/engineer/performer-type credits, see
test_fetch_release_detail_labels.py's neighbors for that path).
"""

from unittest.mock import patch

from src.musicbrainz import musicbrainz_release as mc

_ARTIST_ID = "9b58a6ab-9d70-4d1a-994b-1af881000000"
_ARTIST_ID_2 = "0d557908-0d24-4a15-9982-9d1e1e000000"


def _release(artist_credit):
    return {
        "release": {
            "id": "faada5d1-971b-499c-b902-5bab9e03bc1b",
            "release-group": {"id": "gggggggg-gggg-gggg-gggg-gggggggggggg"},
            "artist-credit": artist_credit,
            "medium-list": [],
        }
    }


class TestFetchReleaseDetailArtistCredit:
    def test_parses_single_release_artist_as_album_artist_credit(self):
        release = _release(
            [{"name": "Uncle Tupelo", "artist": {"id": _ARTIST_ID, "name": "Uncle Tupelo"}}]
        )
        with patch.object(mc.musicbrainzngs, "get_release_by_id", return_value=release):
            detail = mc.fetch_release_detail("faada5d1-971b-499c-b902-5bab9e03bc1b")

        album_artist_credits = [c for c in detail.credits if c.role_name == "Album Artist"]
        assert len(album_artist_credits) == 1
        credit = album_artist_credits[0]
        assert credit.artist_mbid == _ARTIST_ID
        assert credit.artist_name == "Uncle Tupelo"
        assert credit.canonical_name == "Uncle Tupelo"

    def test_as_credited_name_can_differ_from_canonical_name(self):
        release = _release(
            [{"name": "H. Arlen", "artist": {"id": _ARTIST_ID, "name": "Harold Arlen"}}]
        )
        with patch.object(mc.musicbrainzngs, "get_release_by_id", return_value=release):
            detail = mc.fetch_release_detail("faada5d1-971b-499c-b902-5bab9e03bc1b")

        credit = next(c for c in detail.credits if c.role_name == "Album Artist")
        assert credit.artist_name == "H. Arlen"
        assert credit.canonical_name == "Harold Arlen"

    def test_joinphrase_strings_between_artists_are_skipped(self):
        release = _release(
            [
                {"name": "Artist One", "artist": {"id": _ARTIST_ID, "name": "Artist One"}, "joinphrase": " & "},
                " & ",
                {"name": "Artist Two", "artist": {"id": _ARTIST_ID_2, "name": "Artist Two"}},
            ]
        )
        with patch.object(mc.musicbrainzngs, "get_release_by_id", return_value=release):
            detail = mc.fetch_release_detail("faada5d1-971b-499c-b902-5bab9e03bc1b")

        album_artist_credits = [c for c in detail.credits if c.role_name == "Album Artist"]
        assert {c.artist_mbid for c in album_artist_credits} == {_ARTIST_ID, _ARTIST_ID_2}

    def test_missing_artist_id_is_skipped(self):
        release = _release([{"name": "Various Artists", "artist": {}}])
        with patch.object(mc.musicbrainzngs, "get_release_by_id", return_value=release):
            detail = mc.fetch_release_detail("faada5d1-971b-499c-b902-5bab9e03bc1b")

        assert not [c for c in detail.credits if c.role_name == "Album Artist"]

    def test_missing_artist_credit_key_does_not_error(self):
        release = _release([])
        del release["release"]["artist-credit"]
        with patch.object(mc.musicbrainzngs, "get_release_by_id", return_value=release):
            detail = mc.fetch_release_detail("faada5d1-971b-499c-b902-5bab9e03bc1b")

        assert detail.credits == []
