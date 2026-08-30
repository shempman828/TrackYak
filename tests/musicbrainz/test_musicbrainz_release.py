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


# ---- test_musicbrainz_release__self_base.py ----------------------------------
def _release_base(
    id_, date=None, country=None, status="Official", score=100, medium_list=None, catalog=None
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


_VINYL_ID = "b163eeb6-2eae-4fef-932b-d0eda8d728af"

_CD_ID = "fb42c5cd-1841-4b69-b4ff-8ed328beb082"


def _search_result():
    return {
        "release-list": [
            _release_base(_CD_ID, date="1993-10-26", country="US", catalog="519 806-2"),
            _release_base(_VINYL_ID, catalog="MG V-8020"),  # no date/country in search
        ]
    }


class TestBackfillReleaseDetails:
    def test_skips_when_already_complete(self):
        r = _release_base(_CD_ID, date="1993-10-26", medium_list=[{"format": "CD"}])
        with patch.object(mc.musicbrainzngs, "get_release_by_id") as get_by_id:
            mc._backfill_release_details(r)
        get_by_id.assert_not_called()

    def test_fills_missing_date_and_media(self):
        r = _release_base(_VINYL_ID)
        detail = {"release": {"date": "1953", "medium-list": [{"format": "Vinyl"}]}}
        with patch.object(mc.musicbrainzngs, "get_release_by_id", return_value=detail) as get_by_id:
            mc._backfill_release_details(r)
        get_by_id.assert_called_once_with(_VINYL_ID, includes=["media"])
        assert r["date"] == "1953"
        assert r["medium-list"] == [{"format": "Vinyl"}]

    def test_lookup_failure_degrades_gracefully(self):
        r = _release_base(_VINYL_ID)
        with patch.object(mc.musicbrainzngs, "get_release_by_id", side_effect=Exception("boom")):
            mc._backfill_release_details(r)
        assert r.get("date") is None


class TestSearchCanonicalReleases:
    def test_original_vinyl_outranks_cd_reissue_after_backfill(self):
        detail = {"release": {"date": "1953", "medium-list": [{"format": "Vinyl"}]}}
        with (
            patch.object(mc, "configure"),
            patch.object(mc, "_resolve_artist_mbid", return_value=None),
            patch.object(mc.musicbrainzngs, "search_releases", return_value=_search_result()),
            patch.object(mc.musicbrainzngs, "get_release_by_id", return_value=detail),
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
        with (
            patch.object(mc, "configure"),
            patch.object(mc, "_resolve_artist_mbid", return_value=None),
            patch.object(mc.musicbrainzngs, "search_releases", return_value=_search_result()),
            patch.object(
                mc.musicbrainzngs, "get_release_by_id", side_effect=Exception("network down")
            ),
        ):
            candidates = mc.search_canonical_releases("King of the Tenors", "Ben Webster")

        ids = [c.id for c in candidates]
        assert ids[-1] == _VINYL_ID


class TestSearchCanonicalReleasesYearHint:
    """Covers #366 (steer a same-titled search away from an unrelated,
    decades-off release) and the later hint bug: a wrong or stale
    expected_year must never make the correct release invisible, only rank
    it behind on-hint candidates -- so this is a ranking preference, not a
    filter."""

    _WRONG_ERA_ID = "00000000-0000-0000-0000-00000000wrng"

    def _mixed_era_result(self):
        media = [{"format": "Vinyl"}]
        return {
            "release-list": [
                _release_base(_VINYL_ID, date="1944-05-01", country="US", medium_list=media),
                _release_base(
                    self._WRONG_ERA_ID, date="2008-09-01", country="US", score=99, medium_list=media
                ),
            ]
        }

    def test_hint_ranks_matching_year_ahead_of_decades_off_candidate(self):
        with (
            patch.object(mc, "configure"),
            patch.object(mc, "_resolve_artist_mbid", return_value=None),
            patch.object(
                mc.musicbrainzngs, "search_releases", return_value=self._mixed_era_result()
            ),
            patch.object(mc.musicbrainzngs, "get_release_by_id") as get_by_id,
        ):
            candidates = mc.search_canonical_releases(
                "King of the Tenors", "Ben Webster", expected_year=1944
            )

        get_by_id.assert_not_called()  # both already have date + medium-list
        ids = [c.id for c in candidates]
        assert ids == [_VINYL_ID, self._WRONG_ERA_ID], (
            f"expected the 1944 release ranked first and the 2008 release "
            f"ranked behind it, but both still present, got: {ids}"
        )

    def test_no_hint_keeps_both_candidates(self):
        with (
            patch.object(mc, "configure"),
            patch.object(mc, "_resolve_artist_mbid", return_value=None),
            patch.object(
                mc.musicbrainzngs, "search_releases", return_value=self._mixed_era_result()
            ),
            patch.object(mc.musicbrainzngs, "get_release_by_id"),
        ):
            candidates = mc.search_canonical_releases("King of the Tenors", "Ben Webster")

        assert {c.id for c in candidates} == {_VINYL_ID, self._WRONG_ERA_ID}

    def test_wrong_hint_never_drops_the_correct_release_from_the_pool(self):
        # A stale/wrong hint (e.g. an unsaved remaster year) must never turn
        # "some results" into "fewer results" -- the true release stays in
        # the pool even when every candidate is decades off from the hint.
        with (
            patch.object(mc, "configure"),
            patch.object(mc, "_resolve_artist_mbid", return_value=None),
            patch.object(
                mc.musicbrainzngs, "search_releases", return_value=self._mixed_era_result()
            ),
            patch.object(mc.musicbrainzngs, "get_release_by_id"),
        ):
            candidates = mc.search_canonical_releases(
                "King of the Tenors", "Ben Webster", expected_year=1700
            )

        assert {c.id for c in candidates} == {_VINYL_ID, self._WRONG_ERA_ID}


class TestSearchCanonicalReleasesTypeAndTrackCount:
    """The picker label surfaces the release-group type (Album/Single/EP/...
    plus secondary types like Live/Compilation) and the total track count so
    a same-titled single and full album are distinguishable before matching."""

    _ID = "aaaaaaaa-0000-0000-0000-000000000000"

    def _run(self, release):
        with (
            patch.object(mc, "configure"),
            patch.object(mc, "_resolve_artist_mbid", return_value=None),
            patch.object(
                mc.musicbrainzngs, "search_releases", return_value={"release-list": [release]}
            ),
            patch.object(mc.musicbrainzngs, "get_release_by_id", return_value={"release": {}}),
        ):
            return mc.search_canonical_releases("Whatever", None)[0].label

    def _release(self, *, release_group=None, medium_list=None, medium_track_count=None):
        r = _release_base(self._ID, date="1980-01-01", country="US")
        r["medium-list"] = medium_list if medium_list is not None else [{"format": "CD"}]
        if release_group is not None:
            r["release-group"] = release_group
        if medium_track_count is not None:
            r["medium-track-count"] = medium_track_count
        return r

    def test_primary_type_single_vs_album(self):
        single = self._run(self._release(release_group={"primary-type": "Single"}))
        album = self._run(self._release(release_group={"primary-type": "Album"}))
        assert "Single" in single
        assert "Album" in album and "Single" not in album

    def test_secondary_type_is_appended_in_parens(self):
        label = self._run(
            self._release(release_group={"primary-type": "Album", "secondary-type-list": ["Live"]})
        )
        assert "Album (Live)" in label

    def test_track_count_sums_across_media_and_pluralizes(self):
        multi = self._run(
            self._release(
                medium_list=[
                    {"format": "CD", "track-count": 15},
                    {"format": "CD", "track-count": 19},
                ]
            )
        )
        assert "34 tracks" in multi

        one = self._run(self._release(medium_list=[{"format": "CD", "track-count": 1}]))
        assert "1 track" in one and "1 tracks" not in one

    def test_track_count_falls_back_to_flat_medium_track_count(self):
        label = self._run(self._release(medium_list=[{"format": "CD"}], medium_track_count=7))
        assert "7 tracks" in label

    def test_missing_release_group_and_media_degrades_without_error(self):
        r = _release_base(self._ID, date="1980-01-01", country="US")
        # no release-group, no medium-list, no medium-track-count
        label = self._run_raw(r)
        assert label.startswith("King of the Tenors by Ben Webster")
        assert "track" not in label
        # existing bits still there, nothing crashed
        assert "1980-01-01" in label and "US" in label

    def _run_raw(self, release):
        with (
            patch.object(mc, "configure"),
            patch.object(mc, "_resolve_artist_mbid", return_value=None),
            patch.object(
                mc.musicbrainzngs, "search_releases", return_value={"release-list": [release]}
            ),
            patch.object(mc.musicbrainzngs, "get_release_by_id", return_value={"release": {}}),
        ):
            return mc.search_canonical_releases("Whatever", None)[0].label

    def test_existing_label_parts_preserved_with_type_before_status(self):
        label = self._run(
            self._release(
                release_group={"primary-type": "Single"},
                medium_list=[{"format": "CD", "track-count": 2}],
            )
        )
        # order inside the bracket: type, tracks, status, date, country, format
        assert "[Single — 2 tracks — Official — 1980-01-01 — US — CD]" in label


# ---- test_fetch_release_detail_artist_credit.py ------------------------------
# Tests for fetch_release_detail's release-level `artist-credit` parsing --
# the release "Album Artist" byline, structurally distinct from
# `artist-relation-list` (producer/engineer/performer-type credits, see
# test_fetch_release_detail_labels.py's neighbors for that path).
_ARTIST_ID = "9b58a6ab-9d70-4d1a-994b-1af881000000"

_ARTIST_ID_2 = "0d557908-0d24-4a15-9982-9d1e1e000000"


def _release_ac(artist_credit):
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
        release = _release_ac(
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
        release = _release_ac(
            [{"name": "H. Arlen", "artist": {"id": _ARTIST_ID, "name": "Harold Arlen"}}]
        )
        with patch.object(mc.musicbrainzngs, "get_release_by_id", return_value=release):
            detail = mc.fetch_release_detail("faada5d1-971b-499c-b902-5bab9e03bc1b")

        credit = next(c for c in detail.credits if c.role_name == "Album Artist")
        assert credit.artist_name == "H. Arlen"
        assert credit.canonical_name == "Harold Arlen"

    def test_joinphrase_strings_between_artists_are_skipped(self):
        release = _release_ac(
            [
                {
                    "name": "Artist One",
                    "artist": {"id": _ARTIST_ID, "name": "Artist One"},
                    "joinphrase": " & ",
                },
                " & ",
                {"name": "Artist Two", "artist": {"id": _ARTIST_ID_2, "name": "Artist Two"}},
            ]
        )
        with patch.object(mc.musicbrainzngs, "get_release_by_id", return_value=release):
            detail = mc.fetch_release_detail("faada5d1-971b-499c-b902-5bab9e03bc1b")

        album_artist_credits = [c for c in detail.credits if c.role_name == "Album Artist"]
        assert {c.artist_mbid for c in album_artist_credits} == {_ARTIST_ID, _ARTIST_ID_2}

    def test_missing_artist_id_is_skipped(self):
        release = _release_ac([{"name": "Various Artists", "artist": {}}])
        with patch.object(mc.musicbrainzngs, "get_release_by_id", return_value=release):
            detail = mc.fetch_release_detail("faada5d1-971b-499c-b902-5bab9e03bc1b")

        assert not [c for c in detail.credits if c.role_name == "Album Artist"]

    def test_missing_artist_credit_key_does_not_error(self):
        release = _release_ac([])
        del release["release"]["artist-credit"]
        with patch.object(mc.musicbrainzngs, "get_release_by_id", return_value=release):
            detail = mc.fetch_release_detail("faada5d1-971b-499c-b902-5bab9e03bc1b")

        assert detail.credits == []


# ---- test_fetch_release_detail_labels.py -------------------------------------
# Tests for fetch_release_detail's label (record company/publisher)
# parsing -- the release-embedded label-info-list entry is only a stub
# (name, catalog number), so each unique label gets its own get_label_by_id
# follow-up (see _fetch_label_by_id/_parse_label) for life-span, annotation,
# headquarters area, and founder relations.
_LABEL_ID = "5a5c8d97-9d47-49d8-9958-4b06e6e0f81a"

_NYC_ID = "5099dc37-eab7-3c96-a20d-6f0b3c5f9601"

_USA_ID = "489ce91b-6658-3307-9877-795b68554c98"


def _release_lbl():
    return {
        "release": {
            "id": "faada5d1-971b-499c-b902-5bab9e03bc1b",
            "release-group": {"id": "gggggggg-gggg-gggg-gggg-gggggggggggg"},
            "label-info-list": [
                {"catalog-number": "ATL-1", "label": {"id": _LABEL_ID, "name": "Atlantic Records"}}
            ],
            "medium-list": [],
        }
    }


def _get_label_by_id(label_mbid, includes=None):
    assert label_mbid == _LABEL_ID
    return {
        "label": {
            "id": _LABEL_ID,
            "name": "Atlantic Records",
            "disambiguation": "US label",
            "life-span": {"begin": "1947-10", "end": None},
            "area": {"id": _NYC_ID, "name": "New York City"},
            "annotation": {"text": "Founded by Ahmet Ertegun and Herb Abramson."},
            "artist-relation-list": [
                {
                    "type": "founder",
                    "artist": {
                        "id": "aaaaaaaa-1111-1111-1111-111111111111",
                        "name": "Ahmet Ertegun",
                    },
                },
                {
                    "type": "founder",
                    "artist": {
                        "id": "bbbbbbbb-2222-2222-2222-222222222222",
                        "name": "Herb Abramson",
                    },
                },
                # Not a founder relation -- must be filtered out.
                {
                    "type": "legal representation",
                    "artist": {"id": "cccccccc-3333-3333-3333-333333333333", "name": "Some Lawyer"},
                },
            ],
        }
    }


def _get_area_by_id_lbl(area_mbid, includes=None):
    if area_mbid == _NYC_ID:
        return {
            "area": {
                "name": "New York City",
                "type": "City",
                "area-relation-list": [
                    {
                        "type": "part of",
                        "direction": "backward",
                        "area": {"id": _USA_ID, "name": "United States", "type": "Country"},
                    }
                ],
            }
        }
    if area_mbid == _USA_ID:
        return {"area": {"name": "United States", "type": "Country", "area-relation-list": []}}
    raise AssertionError(f"unexpected area_mbid lookup: {area_mbid}")


class TestFetchReleaseDetailLabels:
    def test_parses_label_scalars_annotation_and_founders(self):
        with (
            patch.object(mc.musicbrainzngs, "get_release_by_id", return_value=_release_lbl()),
            patch.object(mc.musicbrainzngs, "get_label_by_id", side_effect=_get_label_by_id),
            patch.object(mc.musicbrainzngs, "get_area_by_id", side_effect=_get_area_by_id_lbl),
        ):
            detail = mc.fetch_release_detail("faada5d1-971b-499c-b902-5bab9e03bc1b")

        assert len(detail.labels) == 1
        label = detail.labels[0]
        assert label.mbid == _LABEL_ID
        assert label.name == "Atlantic Records"
        assert label.catalog_number == "ATL-1"
        assert label.disambiguation == "US label"
        assert label.annotation == "Founded by Ahmet Ertegun and Herb Abramson."
        assert label.begin_year == 1947
        assert label.begin_month == 10
        assert label.end_year is None

        founder_names = {f.name for f in label.founders}
        assert founder_names == {"Ahmet Ertegun", "Herb Abramson"}

    def test_resolves_headquarters_area_chain(self):
        with (
            patch.object(mc.musicbrainzngs, "get_release_by_id", return_value=_release_lbl()),
            patch.object(mc.musicbrainzngs, "get_label_by_id", side_effect=_get_label_by_id),
            patch.object(mc.musicbrainzngs, "get_area_by_id", side_effect=_get_area_by_id_lbl),
        ):
            detail = mc.fetch_release_detail("faada5d1-971b-499c-b902-5bab9e03bc1b")

        chain_names = [node["name"] for node in detail.labels[0].area_chain]
        assert chain_names == ["New York City", "United States"]

    def test_missing_label_id_is_skipped_without_error(self):
        release = _release_lbl()
        release["release"]["label-info-list"] = [{"catalog-number": "NO-ID", "label": {}}]
        with (
            patch.object(mc.musicbrainzngs, "get_release_by_id", return_value=release),
            patch.object(mc.musicbrainzngs, "get_label_by_id") as get_label,
        ):
            detail = mc.fetch_release_detail("faada5d1-971b-499c-b902-5bab9e03bc1b")

        get_label.assert_not_called()
        assert detail.labels == []

    def test_label_lookup_failure_is_best_effort(self):
        with (
            patch.object(mc.musicbrainzngs, "get_release_by_id", return_value=_release_lbl()),
            patch.object(mc.musicbrainzngs, "get_label_by_id", side_effect=RuntimeError("boom")),
        ):
            detail = mc.fetch_release_detail("faada5d1-971b-499c-b902-5bab9e03bc1b")

        assert detail.labels == []

    def test_known_label_mbid_skips_network_fetch(self):
        """A label MBID already on file locally (see
        album_musicbrainz_known_entities.known_publisher_mbids) shouldn't
        pay for a get_label_by_id/get_area_by_id round trip -- the local
        Publisher already has founders/headquarters from whenever it was
        first imported, and resolve_or_create_publisher matches on MBID
        regardless of what's in the stub."""
        with (
            patch.object(mc.musicbrainzngs, "get_release_by_id", return_value=_release_lbl()),
            patch.object(mc.musicbrainzngs, "get_label_by_id") as get_label,
            patch.object(mc.musicbrainzngs, "get_area_by_id") as get_area,
        ):
            detail = mc.fetch_release_detail(
                "faada5d1-971b-499c-b902-5bab9e03bc1b", known_label_mbids=frozenset({_LABEL_ID})
            )

        get_label.assert_not_called()
        get_area.assert_not_called()
        assert len(detail.labels) == 1
        label = detail.labels[0]
        assert label.mbid == _LABEL_ID
        assert label.name == "Atlantic Records"
        assert label.catalog_number == "ATL-1"
        assert label.founders == []
        assert label.area_chain == []


# ---- test_fetch_release_detail_place_chain.py --------------------------------
# Tests for bug #248: a recording location's place got added during
# MusicBrainz album track matching, but no parent area was ever attached --
# e.g. importing faada5d1-971b-499c-b902-5bab9e03bc1b added "Church of the
# Holy Trinity" with no parent.
#
# Root cause: MusicBrainz's "recorded at" relation embeds a reduced place
# stub on the recording (id/name/type/coordinates only) that never carries
# the place's containing area, even though a direct place lookup does. The
# code trusted the embedded stub's (always-empty) "area" field instead of
# doing that direct lookup, so `pending_areas` was always empty for
# recording locations and `resolve_area_chain` was never invoked for them --
# `place_chains` ended up with a chain of exactly one node (the place
# itself, no parent) every time.
_CHURCH_ID = "063005ea-5f9c-45c3-aff2-f4b11f966aae"

_TORONTO_ID = "74b24e62-d2fe-42d2-9d96-31f2da756c77"

_ONTARIO_ID = "22222222-2222-2222-2222-222222222222"

_RELEASE = {
    "release": {
        "id": "faada5d1-971b-499c-b902-5bab9e03bc1b",
        "release-group": {"id": "gggggggg-gggg-gggg-gggg-gggggggggggg"},
        "medium-list": [
            {
                "position": "1",
                "track-list": [
                    {
                        "number": "1",
                        "position": "1",
                        "recording": {
                            "id": "rrrrrrrr-rrrr-rrrr-rrrr-rrrrrrrrrrrr",
                            "title": "Mystery",
                            # MusicBrainz's real XML response for this
                            # relation -- confirmed against the live API --
                            # has no <area> under <place> at all.
                            "place-relation-list": [
                                {
                                    "type": "recorded at",
                                    "place": {
                                        "id": _CHURCH_ID,
                                        "name": "Church of the Holy Trinity",
                                        "type": "Religious building",
                                    },
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }
}


def _get_place_by_id(place_mbid, includes=None):
    if place_mbid == _CHURCH_ID:
        return {
            "place": {
                "id": _CHURCH_ID,
                "name": "Church of the Holy Trinity",
                "area": {"id": _TORONTO_ID, "name": "Toronto"},
            }
        }
    raise AssertionError(f"unexpected place_mbid lookup: {place_mbid}")


def _get_area_by_id_pc(area_mbid, includes=None):
    if area_mbid == _TORONTO_ID:
        return {
            "area": {
                "name": "Toronto",
                "type": "City",
                "area-relation-list": [
                    {
                        "type": "part of",
                        "direction": "backward",
                        "area": {"id": _ONTARIO_ID, "name": "Ontario", "type": "Subdivision"},
                    }
                ],
            }
        }
    if area_mbid == _ONTARIO_ID:
        return {"area": {"name": "Ontario", "type": "Subdivision", "area-relation-list": []}}
    raise AssertionError(f"unexpected area_mbid lookup: {area_mbid}")


class TestFetchReleaseDetailPlaceChain:
    def test_recording_location_place_chain_includes_parent_areas(self):
        with (
            patch.object(mc.musicbrainzngs, "get_release_by_id", return_value=_RELEASE),
            patch.object(mc.musicbrainzngs, "get_place_by_id", side_effect=_get_place_by_id),
            patch.object(mc.musicbrainzngs, "get_area_by_id", side_effect=_get_area_by_id_pc),
        ):
            detail = mc.fetch_release_detail("faada5d1-971b-499c-b902-5bab9e03bc1b")

        chain = detail.place_chains.get(_CHURCH_ID)
        assert chain is not None, "place chain missing entirely for the recorded-at place"
        names = [node["name"] for node in chain]
        assert names == ["Church of the Holy Trinity", "Toronto", "Ontario"], (
            "expected the church's chain to include its parent areas, but got "
            f"a chain with no parent walked: {names}"
        )

    def test_known_place_mbid_skips_area_chain_walk(self):
        """A recording-location place already on file locally (see
        album_musicbrainz_known_entities.known_place_mbids) shouldn't pay
        for get_place_by_id/get_area_by_id -- resolve_place_chain matches
        on MBID and trusts the place's existing local ancestry, so only a
        one-node stub is needed."""
        with (
            patch.object(mc.musicbrainzngs, "get_release_by_id", return_value=_RELEASE),
            patch.object(mc.musicbrainzngs, "get_place_by_id") as get_place,
            patch.object(mc.musicbrainzngs, "get_area_by_id") as get_area,
        ):
            detail = mc.fetch_release_detail(
                "faada5d1-971b-499c-b902-5bab9e03bc1b", known_place_mbids=frozenset({_CHURCH_ID})
            )

        get_place.assert_not_called()
        get_area.assert_not_called()
        chain = detail.place_chains.get(_CHURCH_ID)
        assert chain is not None
        assert [node["mbid"] for node in chain] == [_CHURCH_ID]


# ---- test_fetch_release_detail_work_credits.py -------------------------------
# Tests for fetch_release_detail's writing-credit (composer/lyricist/
# writer/...) parsing -- a recording's embedded work-relation-list entry
# carries only a work stub (id/title/type), so each unique work gets its own
# get_work_by_id follow-up (see _fetch_work_by_id/_parse_work_credits) for its
# artist-relation-list, same reasoning as labels needing their own
# get_label_by_id follow-up.
_WORK_ID = "41c94a08-a551-3c86-bb17-d9a52e3a618b"

_RECORDING_ID = "b1a9c0e9-d987-4042-ae91-78d6a3267d69"

_COMPOSER_ID = "022589ac-7177-460d-a178-9976cf70e29f"


def _release_wc(work_relations=None, recording_id=_RECORDING_ID):
    return {
        "release": {
            "id": "faada5d1-971b-499c-b902-5bab9e03bc1b",
            "release-group": {"id": "gggggggg-gggg-gggg-gggg-gggggggggggg"},
            "medium-list": [
                {
                    "position": "1",
                    "track-list": [
                        {
                            "position": "1",
                            "number": "1",
                            "recording": {
                                "id": recording_id,
                                "title": "Bohemian Rhapsody",
                                "work-relation-list": (
                                    work_relations
                                    if work_relations is not None
                                    else [
                                        {
                                            "type": "performance",
                                            "work": {"id": _WORK_ID, "title": "Bohemian Rhapsody"},
                                        }
                                    ]
                                ),
                            },
                        }
                    ],
                }
            ],
        }
    }


def _get_work_by_id(work_mbid, includes=None):
    assert work_mbid == _WORK_ID
    return {
        "work": {
            "id": _WORK_ID,
            "title": "Bohemian Rhapsody",
            "artist-relation-list": [
                {"type": "composer", "artist": {"id": _COMPOSER_ID, "name": "Freddie Mercury"}},
                {"type": "lyricist", "artist": {"id": _COMPOSER_ID, "name": "Freddie Mercury"}},
                # Not a writing credit -- must be filtered out.
                {
                    "type": "previous attribution",
                    "artist": {
                        "id": "cccccccc-3333-3333-3333-333333333333",
                        "name": "Someone Else",
                    },
                },
            ],
        }
    }


class TestFetchReleaseDetailWorkCredits:
    def test_parses_composer_and_lyricist_onto_the_track(self):
        with (
            patch.object(mc.musicbrainzngs, "get_release_by_id", return_value=_release_wc()),
            patch.object(mc.musicbrainzngs, "get_work_by_id", side_effect=_get_work_by_id),
        ):
            detail = mc.fetch_release_detail("faada5d1-971b-499c-b902-5bab9e03bc1b")

        assert len(detail.tracks) == 1
        roles = {(c.role_name, c.artist_name) for c in detail.tracks[0].credits}
        assert ("Composer", "Freddie Mercury") in roles
        assert ("Lyricist", "Freddie Mercury") in roles
        assert not any(role == "Previous Attribution" for role, _ in roles)

    def test_no_work_relation_means_no_writing_credits(self):
        with (
            patch.object(
                mc.musicbrainzngs, "get_release_by_id", return_value=_release_wc(work_relations=[])
            ),
            patch.object(mc.musicbrainzngs, "get_work_by_id") as get_work,
        ):
            detail = mc.fetch_release_detail("faada5d1-971b-499c-b902-5bab9e03bc1b")

        get_work.assert_not_called()
        assert detail.tracks[0].credits == []

    def test_non_performance_work_relation_is_ignored(self):
        release = _release_wc(
            work_relations=[
                {"type": "arrangement", "work": {"id": _WORK_ID, "title": "Bohemian Rhapsody"}}
            ]
        )
        with (
            patch.object(mc.musicbrainzngs, "get_release_by_id", return_value=release),
            patch.object(mc.musicbrainzngs, "get_work_by_id") as get_work,
        ):
            detail = mc.fetch_release_detail("faada5d1-971b-499c-b902-5bab9e03bc1b")

        get_work.assert_not_called()
        assert detail.tracks[0].credits == []

    def test_same_work_on_two_tracks_only_fetched_once(self):
        release = _release_wc()
        release["release"]["medium-list"].append(
            {
                "position": "1",
                "track-list": [
                    {
                        "position": "2",
                        "number": "2",
                        "recording": {
                            "id": "d2222222-2222-2222-2222-222222222222",
                            "title": "Bohemian Rhapsody (Reprise)",
                            "work-relation-list": [
                                {"type": "performance", "work": {"id": _WORK_ID}}
                            ],
                        },
                    }
                ],
            }
        )
        with (
            patch.object(mc.musicbrainzngs, "get_release_by_id", return_value=release),
            patch.object(
                mc.musicbrainzngs, "get_work_by_id", side_effect=_get_work_by_id
            ) as get_work,
        ):
            detail = mc.fetch_release_detail("faada5d1-971b-499c-b902-5bab9e03bc1b")

        get_work.assert_called_once()
        assert len(detail.tracks) == 2
        for track in detail.tracks:
            roles = {c.role_name for c in track.credits}
            assert {"Composer", "Lyricist"} <= roles

    def test_work_lookup_failure_is_best_effort(self):
        with (
            patch.object(mc.musicbrainzngs, "get_release_by_id", return_value=_release_wc()),
            patch.object(mc.musicbrainzngs, "get_work_by_id", side_effect=RuntimeError("boom")),
        ):
            detail = mc.fetch_release_detail("faada5d1-971b-499c-b902-5bab9e03bc1b")

        assert detail.tracks[0].credits == []


# ---- test_relation_role_names.py ---------------------------------------------
# Tests for bug #301: some credit roles weren't parsing from MusicBrainz on
# album import. Root cause: _relation_role_names (formerly _relation_role_name)
# assumed attribute-list[0] was always the instrument/vocal name, but MB's own
# link-phrase template for these relations is "{additional} {guest} {solo}
# {instrument}" -- a qualifier word can sit before the real value. Confirmed
# against the live API: Eagles' "Hotel California" credits Bill Armstrong's
# trumpet as attribute-list ["additional", "trumpet"], which the old code
# reported as role "Additional", silently dropping "trumpet".
#
# Also covers two relation types that were missing from the credit-type
# whitelist entirely (confirmed live, dropped with no trace): "performing
# orchestra" (Judy Garland's "Over the Rainbow") and "sound" (Queen's
# "Bohemian Rhapsody").
#
# And covers the split_and_merge_aliases.md change: a performer relation with
# multiple independent attribute values (e.g. one person playing both viola
# and violin) now yields one name per value instead of joining them into a
# single "Viola & Violin" string -- see TestMultiValuePerformerRelationSplits.
def _rel(type_, attributes=None):
    return {"type": type_, "attribute-list": attributes or []}


class TestPerformerRoleName:
    def test_plain_instrument_no_qualifier(self):
        assert mc._relation_role_names(_rel("instrument", ["trumpet"])) == ["Trumpet"]

    def test_qualifier_before_instrument_still_reports_instrument(self):
        # The real-world case: qualifier ("additional") sits at index 0,
        # ahead of the actual instrument value.
        rel = _rel("instrument", ["additional", "trumpet"])
        assert mc._relation_role_names(rel) == ["Additional Trumpet"]

    def test_guest_qualifier(self):
        rel = _rel("instrument", ["guest", "guitar"])
        assert mc._relation_role_names(rel) == ["Guest Guitar"]

    def test_qualifier_with_no_instrument_value_falls_back_to_qualifier(self):
        rel = _rel("instrument", ["additional"])
        assert mc._relation_role_names(rel) == ["Additional"]

    def test_vocal_relation_unaffected(self):
        assert mc._relation_role_names(_rel("vocal", ["lead vocals"])) == ["Lead Vocals"]

    def test_no_attributes_falls_back_to_type_name(self):
        assert mc._relation_role_names(_rel("performing orchestra")) == ["Performing Orchestra"]


class TestMultiValuePerformerRelationSplits:
    """A single performer relation can carry more than one independent
    attribute value (MB's own shape for "this person played two
    instruments on this recording"). These used to be joined into one
    "X & Y" role name; they're now reported as separate names so the
    importer creates separate credits instead of one combined role that
    then has to be split by hand."""

    def test_multiple_instrument_values_yield_separate_names(self):
        rel = _rel("instrument", ["piano", "organ"])
        assert mc._relation_role_names(rel) == ["Piano", "Organ"]

    def test_qualifier_applies_to_every_split_value(self):
        rel = _rel("instrument", ["additional", "viola", "violin"])
        assert mc._relation_role_names(rel) == ["Additional Viola", "Additional Violin"]


class TestProductionRoleNameUnaffected:
    """Production relations combine type+modifier into one name, unlike
    performer relations -- that's a real modifier relationship (the
    attribute describes the type), not multiple independent values, so
    these still return exactly one name."""

    def test_modifier_still_prefixes_type(self):
        assert mc._relation_role_names(_rel("engineer", ["assistant"])) == ["Assistant Engineer"]

    def test_sound_with_additional_modifier(self):
        assert mc._relation_role_names(_rel("sound", ["additional"])) == [
            "Additional Sound Engineer"
        ]

    def test_no_modifier_falls_back_to_type_name(self):
        assert mc._relation_role_names(_rel("producer")) == ["Producer"]

    def test_sound_with_no_modifier_reports_sound_engineer(self):
        # Bug #326: "sound" is the one production relation type whose own
        # name ("sound") isn't the full credited role -- MB's link phrase is
        # the compound "sound engineer", unlike "mastering"/"mix"/etc. which
        # are single words. Naive rel_type.title() silently dropped
        # "Engineer" from every plain sound-engineer credit.
        assert mc._relation_role_names(_rel("sound")) == ["Sound Engineer"]

    def test_mix_with_no_modifier_reports_mixer(self):
        # Same root cause as #326: MB's "mix" relation type credits as
        # "Mixer", not "Mix" -- confirmed live on Nirvana's Nevermind, where
        # Andy Wallace's mix relation has an empty attribute-list. Naive
        # rel_type.title() produced "Mix", not a real credit noun.
        assert mc._relation_role_names(_rel("mix")) == ["Mixer"]

    def test_mix_with_modifier_prefixes_mixer_not_mix(self):
        assert mc._relation_role_names(_rel("mix", ["assistant"])) == ["Assistant Mixer"]

    def test_recording_with_no_modifier_reports_recording_engineer(self):
        # "recording" was entirely missing from the credit-type whitelist
        # (silently dropped, not mislabeled) until this was added alongside
        # the sound/mix display-name fixes. Confirmed live on Nirvana's
        # Nevermind ("Something in the Way" credits James Johnson, Jeff
        # Sheehan, and Butch Vig via a plain "recording" relation).
        assert mc._relation_role_names(_rel("recording")) == ["Recording Engineer"]

    def test_recording_with_modifier_prefixes_recording_engineer(self):
        assert mc._relation_role_names(_rel("recording", ["assistant"])) == [
            "Assistant Recording Engineer"
        ]


class TestCreditRelationTypesIncludesPreviouslyDropped:
    def test_performing_orchestra_is_a_credit_type(self):
        assert "performing orchestra" in mc._CREDIT_RELATION_TYPES

    def test_sound_is_a_credit_type(self):
        assert "sound" in mc._CREDIT_RELATION_TYPES

    def test_recording_is_a_credit_type(self):
        assert "recording" in mc._CREDIT_RELATION_TYPES


class TestParseArtistCreditsIntegration:
    def test_qualifier_ordering_does_not_drop_instrument_credit(self):
        recording = {
            "artist-relation-list": [
                {
                    "type": "instrument",
                    "attribute-list": ["additional", "trumpet"],
                    "artist": {"id": "artist-1", "name": "Bill Armstrong"},
                }
            ]
        }
        credits = mc._parse_artist_credits(recording)
        assert len(credits) == 1
        assert credits[0].role_name == "Additional Trumpet"
        assert credits[0].artist_name == "Bill Armstrong"

    def test_previously_dropped_relation_types_now_parsed(self):
        recording = {
            "artist-relation-list": [
                {
                    "type": "performing orchestra",
                    "attribute-list": [],
                    "artist": {"id": "artist-2", "name": "MGM Studio Orchestra"},
                },
                {
                    "type": "sound",
                    "attribute-list": ["additional"],
                    "artist": {"id": "artist-3", "name": "Gary Lyons"},
                },
            ]
        }
        credits = mc._parse_artist_credits(recording)
        roles = {c.artist_name: c.role_name for c in credits}
        assert roles == {
            "MGM Studio Orchestra": "Performing Orchestra",
            "Gary Lyons": "Additional Sound Engineer",
        }

    def test_multi_instrument_relation_yields_two_separate_credits(self):
        recording = {
            "artist-relation-list": [
                {
                    "type": "instrument",
                    "attribute-list": ["viola", "violin"],
                    "artist": {"id": "artist-4", "name": "Multi Instrumentalist"},
                }
            ]
        }
        credits = mc._parse_artist_credits(recording)
        assert len(credits) == 2
        role_names = {c.role_name for c in credits}
        assert role_names == {"Viola", "Violin"}
        assert all(c.artist_name == "Multi Instrumentalist" for c in credits)
        assert all(c.artist_mbid == "artist-4" for c in credits)


# ---- media_format ----------------------------------------------------------
# The release's physical/digital carrier, taken verbatim from MusicBrainz's
# per-medium `format`. Distinct formats across media are sorted and
# "/"-joined; a release whose media carry no format is left None (not
# guessed). Surfaced on MBReleaseDetail.media_format and reused by
# search_canonical_releases' picker label.
class TestMediaFormatStr:
    def test_single_format_across_two_media(self):
        media = [{"format": "CD"}, {"format": "CD"}]
        assert mc._media_format_str(media) == "CD"

    def test_mixed_formats_sorted_and_slash_joined(self):
        media = [{"format": "DVD-Video"}, {"format": "CD"}]
        assert mc._media_format_str(media) == "CD/DVD-Video"

    def test_no_format_key_returns_none(self):
        assert mc._media_format_str([{"title": "bonus disc"}, {}]) is None

    def test_empty_or_none_list_returns_none(self):
        assert mc._media_format_str([]) is None
        assert mc._media_format_str(None) is None


_MF_RELEASE_ID = "faada5d1-971b-499c-b902-5bab9e03bc1b"


def _mf_release(medium_list):
    return {
        "release": {
            "id": _MF_RELEASE_ID,
            "release-group": {"id": "gggggggg-gggg-gggg-gggg-gggggggggggg"},
            "medium-list": medium_list,
        }
    }


class TestFetchReleaseDetailMediaFormat:
    def test_single_cd_release_sets_media_format(self):
        release = _mf_release([{"position": "1", "format": "CD", "track-list": []}])
        with patch.object(mc.musicbrainzngs, "get_release_by_id", return_value=release):
            detail = mc.fetch_release_detail(_MF_RELEASE_ID)
        assert detail.media_format == "CD"

    def test_mixed_media_release_joins_distinct_formats(self):
        release = _mf_release(
            [
                {"position": "1", "format": "CD", "track-list": []},
                {"position": "2", "format": "DVD-Video", "track-list": []},
            ]
        )
        with patch.object(mc.musicbrainzngs, "get_release_by_id", return_value=release):
            detail = mc.fetch_release_detail(_MF_RELEASE_ID)
        assert detail.media_format == "CD/DVD-Video"

    def test_release_with_no_format_leaves_media_format_none(self):
        release = _mf_release([{"position": "1", "track-list": []}])
        with patch.object(mc.musicbrainzngs, "get_release_by_id", return_value=release):
            detail = mc.fetch_release_detail(_MF_RELEASE_ID)
        assert detail.media_format is None
