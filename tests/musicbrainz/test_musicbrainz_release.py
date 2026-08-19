"""Tests for bug #245: search_canonical_releases ranked an album's original
vinyl pressing behind later CD reissues (e.g. "King of the Tenors" by Ben
Webster) because MusicBrainz's search index omits `date` for some
older/sparsely-indexed releases even though a direct lookup of the same
release returns it correctly. Missing dates were defaulted to year 9999 for
ranking, burying the original pressing last.

Covers:
  - _backfill_release_details: fills in date/medium-list via a direct lookup
    only when the search result is missing them.
  - search_canonical_releases: ranks the backfilled original release first
    and surfaces its format (Vinyl) in the label.
"""

from unittest.mock import patch

from src.musicbrainz import musicbrainz_release as mc


def _release(
    id_,
    date=None,
    country=None,
    status="Official",
    score=100,
    medium_list=None,
    catalog=None,
):
    r = {
        "id": id_,
        "title": "King of the Tenors",
        "artist-credit-phrase": "Ben Webster",
        "status": status,
        "ext:score": str(score),
    }
    if date is not None:
        r["date"] = date
    if country is not None:
        r["country"] = country
    if medium_list is not None:
        r["medium-list"] = medium_list
    if catalog:
        r["label-info-list"] = [{"catalog-number": catalog}]
    return r


# The real-world case: MB's search index returns the 1953 original 12" with
# no date/country/medium-list, alongside three well-indexed 90s CD reissues.
_VINYL_ID = "b163eeb6-2eae-4fef-932b-d0eda8d728af"
_CD_ID = "fb42c5cd-1841-4b69-b4ff-8ed328beb082"


def _search_result():
    return {
        "release-list": [
            _release(_CD_ID, date="1993-10-26", country="US", catalog="519 806-2"),
            _release(_VINYL_ID, catalog="MG V-8020"),  # no date/country in search
        ]
    }


class TestBackfillReleaseDetails:
    def test_skips_when_already_complete(self):
        r = _release(_CD_ID, date="1993-10-26", medium_list=[{"format": "CD"}])
        with patch.object(mc.musicbrainzngs, "get_release_by_id") as get_by_id:
            mc._backfill_release_details(r)
        get_by_id.assert_not_called()

    def test_fills_missing_date_and_media(self):
        r = _release(_VINYL_ID)
        detail = {
            "release": {
                "date": "1953",
                "medium-list": [{"format": "Vinyl"}],
            }
        }
        with patch.object(mc.musicbrainzngs, "get_release_by_id", return_value=detail) as get_by_id:
            mc._backfill_release_details(r)
        get_by_id.assert_called_once_with(_VINYL_ID, includes=["media"])
        assert r["date"] == "1953"
        assert r["medium-list"] == [{"format": "Vinyl"}]

    def test_lookup_failure_degrades_gracefully(self):
        r = _release(_VINYL_ID)
        with patch.object(mc.musicbrainzngs, "get_release_by_id", side_effect=Exception("boom")):
            mc._backfill_release_details(r)
        assert r.get("date") is None


class TestSearchCanonicalReleases:
    def test_original_vinyl_outranks_cd_reissue_after_backfill(self):
        detail = {
            "release": {
                "date": "1953",
                "medium-list": [{"format": "Vinyl"}],
            }
        }
        with patch.object(mc, "configure"), patch.object(
            mc, "_resolve_artist_mbid", return_value=None
        ), patch.object(
            mc.musicbrainzngs, "search_releases", return_value=_search_result()
        ), patch.object(
            mc.musicbrainzngs, "get_release_by_id", return_value=detail
        ):
            candidates = mc.search_canonical_releases("King of the Tenors", "Ben Webster")

        ids = [c.id for c in candidates]
        assert ids[0] == _VINYL_ID, (
            "the 1953 original should rank first once its real date is "
            f"backfilled, got order: {ids}"
        )
        assert "Vinyl" in candidates[0].label

    def test_without_backfill_vinyl_would_sort_last(self):
        # Documents the pre-fix behavior: with no date info at all, a
        # missing-date release sorts after any release with a real date.
        with patch.object(mc, "configure"), patch.object(
            mc, "_resolve_artist_mbid", return_value=None
        ), patch.object(
            mc.musicbrainzngs, "search_releases", return_value=_search_result()
        ), patch.object(
            mc.musicbrainzngs, "get_release_by_id", side_effect=Exception("network down")
        ):
            candidates = mc.search_canonical_releases("King of the Tenors", "Ben Webster")

        ids = [c.id for c in candidates]
        assert ids[-1] == _VINYL_ID


class TestSearchCanonicalReleasesYearHint:
    """Covers #366: an album's already-known release year should steer a
    same-titled search away from an unrelated, decades-off release."""

    _WRONG_ERA_ID = "00000000-0000-0000-0000-00000000wrng"

    def _mixed_era_result(self):
        media = [{"format": "Vinyl"}]
        return {
            "release-list": [
                _release(_VINYL_ID, date="1944-05-01", country="US", medium_list=media),
                _release(
                    self._WRONG_ERA_ID,
                    date="2008-09-01",
                    country="US",
                    score=99,
                    medium_list=media,
                ),
            ]
        }

    def test_hint_drops_candidate_decades_off_from_known_year(self):
        with patch.object(mc, "configure"), patch.object(
            mc, "_resolve_artist_mbid", return_value=None
        ), patch.object(
            mc.musicbrainzngs, "search_releases", return_value=self._mixed_era_result()
        ), patch.object(mc.musicbrainzngs, "get_release_by_id") as get_by_id:
            candidates = mc.search_canonical_releases(
                "King of the Tenors", "Ben Webster", expected_year=1944
            )

        get_by_id.assert_not_called()  # both already have date + medium-list
        ids = [c.id for c in candidates]
        assert ids == [_VINYL_ID], (
            f"expected the 2008 release to be dropped as implausible for a 1944 "
            f"album, got: {ids}"
        )

    def test_no_hint_keeps_both_candidates(self):
        with patch.object(mc, "configure"), patch.object(
            mc, "_resolve_artist_mbid", return_value=None
        ), patch.object(
            mc.musicbrainzngs, "search_releases", return_value=self._mixed_era_result()
        ), patch.object(mc.musicbrainzngs, "get_release_by_id"):
            candidates = mc.search_canonical_releases("King of the Tenors", "Ben Webster")

        assert {c.id for c in candidates} == {_VINYL_ID, self._WRONG_ERA_ID}

    def test_hint_falls_back_to_unfiltered_pool_if_nothing_matches(self):
        # Both candidates are decades off from the hinted year -- a wrong or
        # stale hint must never turn "some results" into "no results".
        with patch.object(mc, "configure"), patch.object(
            mc, "_resolve_artist_mbid", return_value=None
        ), patch.object(
            mc.musicbrainzngs, "search_releases", return_value=self._mixed_era_result()
        ), patch.object(mc.musicbrainzngs, "get_release_by_id"):
            candidates = mc.search_canonical_releases(
                "King of the Tenors", "Ben Webster", expected_year=1700
            )

        assert {c.id for c in candidates} == {_VINYL_ID, self._WRONG_ERA_ID}
