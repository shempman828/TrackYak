"""Tests for search_canonical_album_for_recording.

Covers the two behaviors that make this function more than "sort releases
by date":
  - releases are grouped by release-group first, so many near-duplicate
    pressings of the same edition (e.g. several countries' singles) collapse
    into one representative instead of burying the one true album entry.
  - releases credited to someone other than the resolved primary artist
    (e.g. a various-artists compilation that merely contains the recording)
    are filtered out.
"""

from unittest.mock import patch

from src.musicbrainz import musicbrainz_recording as mc

_ARTIST_MBID = "artist-glenn-miller"
_OTHER_ARTIST_MBID = "artist-various"


def _artist_credit(mbid, name):
    return [{"artist": {"id": mbid, "name": name}, "name": name}]


def _release(id_, date, status="Official", country=None, group_id=None, group_type="Single", artist_mbid=_ARTIST_MBID, artist_name="Glenn Miller"):
    return {
        "id": id_,
        "title": "In the Mood",
        "artist-credit-phrase": artist_name,
        "artist-credit": _artist_credit(artist_mbid, artist_name),
        "status": status,
        "date": date,
        "country": country,
        "release-group": {"id": group_id or id_, "primary-type": group_type},
    }


def _recording(id_, score=100):
    return {"id": id_, "ext:score": str(score)}


def _release_with_tracks(id_, date, track_titles, recording_ids=None, **kwargs):
    r = _release(id_, date, **kwargs)
    if recording_ids is None:
        recording_ids = [f"rec-{id_}-{i}" for i in range(len(track_titles))]
    r["medium-list"] = [
        {
            "track-list": [
                {"recording": {"title": t, "id": rid}}
                for t, rid in zip(track_titles, recording_ids)
            ]
        }
    ]
    return r


def _paged_browse_releases(pages_by_artist):
    """Build a browse_releases(artist=..., offset=...) side_effect from
    {artist_mbid: [release, release, ...]}, paginated 2-per-page so
    multi-page scans are exercised without needing 100+ fixture releases."""

    def _side_effect(artist, includes, limit, offset):
        releases = pages_by_artist[artist]
        page = releases[offset : offset + 2]
        return {"release-list": page, "release-count": len(releases)}

    return _side_effect


class TestNoArtistResolvedFallback:
    def test_falls_back_to_recording_search_when_no_artist_identity_resolved(self):
        # No artist identity to browse a discography for (e.g. the track
        # has no known artist) -- falls back to a plain-text recording
        # search, browsing every recording within the search's own
        # relevance-score window.
        #
        # Confirmed against the live MusicBrainz API: recordings tied at
        # the same relevance score are NOT returned in chronological order,
        # and re-querying the same identity can even change that order
        # between calls. An earlier version of this function tried an
        # early-stop heuristic ("give up once drifted N years past the
        # best date found so far") to bound the number of browse_releases
        # calls in this fallback, and it empirically dropped the true
        # earliest release in live testing because the actual earliest
        # recording happened to sort late. Every tied recording must be
        # browsed here -- no order-dependent cutoff.
        recordings = [
            _recording("rec-a"),
            _recording("rec-b"),
            _recording("rec-c"),
            _recording("rec-d"),
        ]
        releases_by_recording = {
            "rec-a": [_release("rel-a", "1995-01-01", group_id="grp-a")],
            "rec-b": [_release("rel-b", "1948-01-01", group_id="grp-b")],
            "rec-c": [_release("rel-c", "2005-01-01", group_id="grp-c")],
            # The true earliest release sorts last among the tied
            # recordings -- exactly the case the old heuristic missed.
            "rec-d": [_release("rel-d", "1939-08-01", group_id="grp-d")],
        }
        browsed = []

        def _fake_browse_releases(recording, includes, limit):
            browsed.append(recording)
            return {"release-list": releases_by_recording[recording]}

        with patch.object(mc, "configure"), patch.object(
            mc, "_resolve_primary_artist_mbids", return_value=[]
        ), patch.object(
            mc.musicbrainzngs,
            "search_recordings",
            return_value={"recording-list": recordings},
        ), patch.object(
            mc.musicbrainzngs, "browse_releases", side_effect=_fake_browse_releases
        ):
            candidates = mc.search_canonical_album_for_recording(
                "In the Mood", "Some Unresolvable Artist"
            )

        assert browsed == ["rec-a", "rec-b", "rec-c", "rec-d"]
        assert candidates[0].id == "rel-d", "the true earliest release must still win"


class TestGroupingAndFiltering:
    def test_collapses_same_release_group_to_one_representative(self):
        # Three single pressings of the same release-group, different
        # countries/dates -- should collapse to the earliest one.
        releases = [
            _release("us-single", "1939-08-05", country="US", group_id="grp-single"),
            _release("uk-single", "1939-08-01", country="GB", group_id="grp-single"),
            _release("au-single", "1939-09-01", country="AU", group_id="grp-single"),
        ]
        with patch.object(mc, "configure"), patch.object(
            mc, "_resolve_primary_artist_mbids", return_value=[_ARTIST_MBID]
        ), patch.object(
            mc.musicbrainzngs,
            "browse_releases",
            return_value={"release-list": releases},
        ):
            candidates = mc.search_canonical_album_for_recording(
                "In the Mood", "Glenn Miller", recording_mbid="rec-1"
            )

        assert len(candidates) == 1
        assert candidates[0].id == "uk-single", "earliest pressing in the group should win"

    def test_surfaces_both_single_and_later_album_as_separate_candidates(self):
        releases = [
            _release("us-single", "1939-08-01", group_id="grp-single", group_type="Single"),
            _release("reissue-album", "1991-01-01", group_id="grp-album", group_type="Album"),
        ]
        with patch.object(mc, "configure"), patch.object(
            mc, "_resolve_primary_artist_mbids", return_value=[_ARTIST_MBID]
        ), patch.object(
            mc.musicbrainzngs,
            "browse_releases",
            return_value={"release-list": releases},
        ):
            candidates = mc.search_canonical_album_for_recording(
                "In the Mood", "Glenn Miller", recording_mbid="rec-1"
            )

        ids = [c.id for c in candidates]
        assert ids == ["us-single", "reissue-album"], (
            "both types should be returned, sorted earliest-first, so the "
            f"user can judge which is canonical -- got {ids}"
        )
        assert candidates[0].enrichment["release_type"] == "Single"
        assert candidates[1].enrichment["release_type"] == "Album"

    def test_excludes_releases_credited_to_a_different_artist(self):
        releases = [
            _release("primary-single", "1939-08-01", group_id="grp-a"),
            _release(
                "va-compilation",
                "1930-01-01",  # earlier date, but wrong artist credit
                group_id="grp-b",
                group_type="Compilation",
                artist_mbid=_OTHER_ARTIST_MBID,
                artist_name="Various Artists",
            ),
        ]
        with patch.object(mc, "configure"), patch.object(
            mc, "_resolve_primary_artist_mbids", return_value=[_ARTIST_MBID]
        ), patch.object(
            mc.musicbrainzngs,
            "browse_releases",
            return_value={"release-list": releases},
        ):
            candidates = mc.search_canonical_album_for_recording(
                "In the Mood", "Glenn Miller", recording_mbid="rec-1"
            )

        ids = [c.id for c in candidates]
        assert ids == ["primary-single"]

    def test_scans_full_catalog_of_each_resolved_artist_identity(self):
        # Regression for a real-world case found while verifying this
        # feature: "Glenn Miller" (solo) and "Glenn Miller and His
        # Orchestra" (the actual credit on the original 1939 single) are
        # distinct MusicBrainz artist entities. Resolving only the solo
        # entity silently drops every original-era release credited to the
        # orchestra -- exactly the release a "canonical first" search is
        # for. Both identities' full catalogs must be scanned, each
        # paginated (release-count larger than one page), and releases
        # without a matching-titled track must be filtered out even though
        # they're by the right artist.
        solo_mbid, orchestra_mbid = _ARTIST_MBID, "artist-orchestra"
        solo_releases = [
            _release_with_tracks(
                "modern-reissue",
                "1991-01-01",
                ["In the Mood"],
                group_id="grp-modern",
                artist_mbid=solo_mbid,
                artist_name="Glenn Miller",
            ),
            _release_with_tracks(
                "unrelated-solo-album",
                "1985-01-01",
                ["Moonlight Serenade"],  # no "In the Mood" track -- must be excluded
                group_id="grp-unrelated",
                artist_mbid=solo_mbid,
                artist_name="Glenn Miller",
            ),
        ]
        orchestra_releases = [
            _release_with_tracks(
                "original-single",
                "1939-08-01",
                ["In the Mood", "I Want to Be Happy"],
                group_id="grp-original",
                group_type="Single",
                artist_mbid=orchestra_mbid,
                artist_name="Glenn Miller and His Orchestra",
            ),
        ]

        with patch.object(mc, "configure"), patch.object(
            mc,
            "_resolve_primary_artist_mbids",
            return_value=[solo_mbid, orchestra_mbid],
        ), patch.object(
            mc.musicbrainzngs,
            "browse_releases",
            side_effect=_paged_browse_releases(
                {solo_mbid: solo_releases, orchestra_mbid: orchestra_releases}
            ),
        ) as browse_releases:
            candidates = mc.search_canonical_album_for_recording(
                "In the Mood", "Glenn Miller"
            )

        ids = [c.id for c in candidates]
        assert "original-single" in ids, (
            "the orchestra-credited original release must not be dropped "
            f"just because it's a different MB artist entity -- got {ids}"
        )
        assert "unrelated-solo-album" not in ids, (
            "a release by the right artist but with no matching-titled "
            "track must still be excluded"
        )
        assert ids == ["original-single", "modern-reissue"]
        # Both identities' catalogs actually got scanned, each via `artist=`
        # (not the old recording-scoped browse).
        called_artists = {c.kwargs["artist"] for c in browse_releases.call_args_list}
        assert called_artists == {solo_mbid, orchestra_mbid}

    def test_recording_mbid_skips_artist_catalog_scan(self):
        # The primary artist is still resolved (needed to filter the
        # known recording's own releases down to the right credit), but
        # with a known recording_mbid there's no discography to browse --
        # browse_releases must be called exactly once, scoped to that one
        # recording, not the artist's full catalog.
        with patch.object(mc, "configure"), patch.object(
            mc, "_resolve_primary_artist_mbids", return_value=[_ARTIST_MBID]
        ), patch.object(
            mc.musicbrainzngs,
            "browse_releases",
            return_value={"release-list": [_release("only-one", "1939-08-01")]},
        ) as browse_releases:
            candidates = mc.search_canonical_album_for_recording(
                "In the Mood", "Glenn Miller", recording_mbid="known-recording-id"
            )

        browse_releases.assert_called_once_with(
            recording="known-recording-id",
            includes=["release-groups", "artist-credits"],
            limit=100,
        )
        assert len(candidates) == 1

    def test_no_releases_returns_empty_list(self):
        with patch.object(mc, "configure"), patch.object(
            mc, "_resolve_primary_artist_mbids", return_value=[_ARTIST_MBID]
        ), patch.object(
            mc.musicbrainzngs, "browse_releases", return_value={"release-list": []}
        ):
            candidates = mc.search_canonical_album_for_recording(
                "In the Mood", "Glenn Miller", recording_mbid="rec-1"
            )

        assert candidates == []


class TestDistinctRecordings:
    def test_labels_distinct_recordings_when_multiple_present(self):
        # Regression for the real Louis Jordan case: "Ain't Nobody Here but
        # Us Chickens" has a genuine 1946 original and a distinct mid-1950s
        # re-recording, both under the same title and artist credit. A
        # title-only match can't tell these apart on its own, so when the
        # final results span more than one recording MBID, each candidate
        # must be tagged so the user isn't left thinking two different
        # performances are just two editions of the same one.
        original = _release_with_tracks(
            "original-1946",
            "1946-01-01",
            ["Ain't Nobody Here but Us Chickens"],
            recording_ids=["recording-original"],
            group_id="grp-original",
        )
        rerecording = _release_with_tracks(
            "rerecording-1956",
            "1956-01-01",
            ["Ain't Nobody Here but Us Chickens"],
            recording_ids=["recording-rerecorded"],
            group_id="grp-rerecorded",
        )
        with patch.object(mc, "configure"), patch.object(
            mc, "_resolve_primary_artist_mbids", return_value=[_ARTIST_MBID]
        ), patch.object(
            mc.musicbrainzngs,
            "browse_releases",
            return_value={
                "release-list": [original, rerecording],
                "release-count": 2,
            },
        ):
            candidates = mc.search_canonical_album_for_recording(
                "Ain't Nobody Here but Us Chickens", "Louis Jordan"
            )

        assert len(candidates) == 2
        assert "Recording 1 of 2" in candidates[0].label
        assert "Recording 2 of 2" in candidates[1].label
        assert candidates[0].enrichment["recording_mbid"] == "recording-original"
        assert candidates[1].enrichment["recording_mbid"] == "recording-rerecorded"

    def test_no_label_when_all_candidates_share_one_recording(self):
        # The same performance reissued as a single and, decades later, on
        # a compilation is NOT a distinct recording -- no "Recording N of
        # M" tag should appear just because it spans two release-groups.
        single = _release_with_tracks(
            "single-1946",
            "1946-01-01",
            ["Same Song"],
            recording_ids=["shared-recording"],
            group_id="grp-single",
            group_type="Single",
        )
        compilation = _release_with_tracks(
            "compilation-1990",
            "1990-01-01",
            ["Same Song"],
            recording_ids=["shared-recording"],
            group_id="grp-compilation",
            group_type="Compilation",
        )
        with patch.object(mc, "configure"), patch.object(
            mc, "_resolve_primary_artist_mbids", return_value=[_ARTIST_MBID]
        ), patch.object(
            mc.musicbrainzngs,
            "browse_releases",
            return_value={
                "release-list": [single, compilation],
                "release-count": 2,
            },
        ):
            candidates = mc.search_canonical_album_for_recording(
                "Same Song", "Glenn Miller"
            )

        assert len(candidates) == 2
        assert all("Recording" not in c.label for c in candidates)


class TestQuoteNormalization:
    def test_matches_track_title_regardless_of_apostrophe_style(self):
        # MusicBrainz stores this exact song with a curly apostrophe on most
        # entries and a straight one on others -- a bare exact-string match
        # would silently miss whichever style the query doesn't use.
        release = _release_with_tracks(
            "original",
            "1946-01-01",
            ["Ain’t Nobody Here but Us Chickens"],
            group_id="grp-a",
        )
        with patch.object(mc, "configure"), patch.object(
            mc, "_resolve_primary_artist_mbids", return_value=[_ARTIST_MBID]
        ), patch.object(
            mc.musicbrainzngs,
            "browse_releases",
            return_value={"release-list": [release], "release-count": 1},
        ):
            candidates = mc.search_canonical_album_for_recording(
                "Ain't Nobody Here but Us Chickens", "Louis Jordan"
            )

        assert len(candidates) == 1
        assert candidates[0].id == "original"
