"""Regression tests for the MusicBrainz release-detail fetch slowdown.

`fetch_release_detail` used to make one `get_release_by_id` call with ten
includes, then a sequential, rate-limited follow-up call per unique work,
per label and per area chain -- and any one of those raising (a slow link
tripping the 30s socket timeout into musicbrainzngs's blind 8x retry ladder)
aborted the whole fetch after minutes of grinding.

Now the release is fetched in two smaller calls (a mandatory core call for
scalars/tracklist/labels, an optional call for the per-recording relation
lists), every follow-up is best-effort with a single end-of-pass retry, and
whatever still fails is recorded in `MBReleaseDetail.partial_failures`
instead of blowing up the import. These tests pin that behaviour.
"""

from unittest.mock import patch

import src.musicbrainz.musicbrainz_release as mc

_REL_ID = "faada5d1-971b-499c-b902-5bab9e03bc1b"
_RG_ID = "gggggggg-gggg-gggg-gggg-gggggggggggg"
_REC1 = "11111111-1111-1111-1111-111111111111"
_REC2 = "22222222-2222-2222-2222-222222222222"
_WORK = "wwwwwwww-wwww-wwww-wwww-wwwwwwwwwwww"
_COMPOSER = "cccccccc-cccc-cccc-cccc-cccccccccccc"
_LABEL = "llllllll-llll-llll-llll-llllllllllll"


def _core_release():
    """What the core include set (no recording-level-rels) comes back with:
    scalars, the tracklist skeleton, the label stub."""
    return {
        "release": {
            "id": _REL_ID,
            "title": "Test Album",
            "status": "Official",
            "date": "1975-11-21",
            "country": "GB",
            "barcode": "5099902895",
            "text-representation": {"language": "eng"},
            "release-group": {"id": _RG_ID, "primary-type": "Album"},
            "label-info-list": [
                {"catalog-number": "CAT-1", "label": {"id": _LABEL, "name": "Testophone"}}
            ],
            "medium-list": [
                {
                    "position": "1",
                    "format": "CD",
                    "track-count": 2,
                    "track-list": [
                        {"number": "1", "position": "1", "recording": {"id": _REC1, "title": "One"}},
                        {"number": "2", "position": "2", "recording": {"id": _REC2, "title": "Two"}},
                    ],
                }
            ],
        }
    }


def _rel_release():
    """What the recording-relation include set comes back with: the same
    tracklist, but every recording now carrying its work-relation-list."""
    work_rel = [{"type": "performance", "work": {"id": _WORK, "title": "The Work"}}]
    return {
        "release": {
            "id": _REL_ID,
            "medium-list": [
                {
                    "position": "1",
                    "track-list": [
                        {
                            "number": "1",
                            "position": "1",
                            "recording": {
                                "id": _REC1,
                                "title": "One",
                                "work-relation-list": work_rel,
                            },
                        },
                        {
                            "number": "2",
                            "position": "2",
                            "recording": {
                                "id": _REC2,
                                "title": "Two",
                                "work-relation-list": work_rel,
                            },
                        },
                    ],
                }
            ],
        }
    }


def _work_resp(work_mbid, includes=None):
    assert work_mbid == _WORK
    return {
        "work": {
            "id": _WORK,
            "title": "The Work",
            "artist-relation-list": [
                {"type": "composer", "artist": {"id": _COMPOSER, "name": "A Composer"}}
            ],
        }
    }


def _label_resp(label_mbid, includes=None):
    assert label_mbid == _LABEL
    return {"label": {"id": _LABEL, "name": "Testophone", "life-span": {"begin": "1970"}}}


class _ReleaseDispatcher:
    """Stands in for musicbrainzngs.get_release_by_id: routes the core include
    set to `_core_release()` and the recording-relation set to `_rel_release()`
    (or raises, per `rel_errors`, a list popped left-to-right -- an entry that
    is an exception is raised, None means return the payload)."""

    def __init__(self, rel_errors=None):
        self.rel_errors = list(rel_errors or [])
        self.core_calls = 0
        self.rel_calls = 0

    def __call__(self, release_mbid, includes=None):
        inc = set(includes or [])
        if "recording-level-rels" in inc:
            self.rel_calls += 1
            if self.rel_errors:
                err = self.rel_errors.pop(0)
                if err is not None:
                    raise err
            return _rel_release()
        self.core_calls += 1
        return _core_release()


def test_release_is_fetched_in_two_smaller_calls():
    dispatch = _ReleaseDispatcher()
    with (
        patch.object(mc.musicbrainzngs, "get_release_by_id", side_effect=dispatch),
        patch.object(mc.musicbrainzngs, "get_work_by_id", side_effect=_work_resp),
        patch.object(mc.musicbrainzngs, "get_label_by_id", side_effect=_label_resp),
    ):
        detail = mc.fetch_release_detail(_REL_ID, retry_pause=0)

    # Exactly one core call + one relation call on the happy path.
    assert (dispatch.core_calls, dispatch.rel_calls) == (1, 1)
    # Core call populated scalars + tracklist + label; relation call merged
    # the writing credit onto both tracks.
    assert detail.status == "Official"
    assert detail.media_format == "CD"
    assert [t.title for t in detail.tracks] == ["One", "Two"]
    assert detail.labels and detail.labels[0].mbid == _LABEL
    for track in detail.tracks:
        assert ("Composer", "A Composer") in {(c.role_name, c.artist_name) for c in track.credits}
    assert detail.partial_failures == []


def test_relation_call_failing_twice_degrades_to_a_scalar_import():
    dispatch = _ReleaseDispatcher(rel_errors=[RuntimeError("slow"), RuntimeError("slow again")])
    with (
        patch.object(mc.musicbrainzngs, "get_release_by_id", side_effect=dispatch),
        patch.object(mc.musicbrainzngs, "get_work_by_id", side_effect=_work_resp) as get_work,
        patch.object(mc.musicbrainzngs, "get_label_by_id", side_effect=_label_resp),
    ):
        detail = mc.fetch_release_detail(_REL_ID, retry_pause=0)

    # Tried the relation call, retried it once, then gave up -- without
    # aborting: scalars, tracklist and the label all still came through.
    assert dispatch.rel_calls == 2
    assert detail.status == "Official"
    assert [t.title for t in detail.tracks] == ["One", "Two"]
    assert detail.labels and detail.labels[0].mbid == _LABEL
    # No per-track relations means no works to resolve at all.
    get_work.assert_not_called()
    assert all(t.credits == [] for t in detail.tracks)
    assert any("track relationship" in desc for desc in detail.partial_failures)


def test_relation_call_recovers_on_the_inline_retry():
    dispatch = _ReleaseDispatcher(rel_errors=[RuntimeError("transient")])
    with (
        patch.object(mc.musicbrainzngs, "get_release_by_id", side_effect=dispatch),
        patch.object(mc.musicbrainzngs, "get_work_by_id", side_effect=_work_resp),
        patch.object(mc.musicbrainzngs, "get_label_by_id", side_effect=_label_resp),
    ):
        detail = mc.fetch_release_detail(_REL_ID, retry_pause=0)

    assert dispatch.rel_calls == 2
    for track in detail.tracks:
        assert ("Composer", "A Composer") in {(c.role_name, c.artist_name) for c in track.credits}
    assert detail.partial_failures == []


def test_work_lookup_recovers_on_the_end_of_pass_retry():
    dispatch = _ReleaseDispatcher()
    work_calls = {"n": 0}

    def _flaky_work(work_mbid, includes=None):
        work_calls["n"] += 1
        if work_calls["n"] == 1:
            raise RuntimeError("transient")
        return _work_resp(work_mbid, includes)

    with (
        patch.object(mc.musicbrainzngs, "get_release_by_id", side_effect=dispatch),
        patch.object(mc.musicbrainzngs, "get_work_by_id", side_effect=_flaky_work),
        patch.object(mc.musicbrainzngs, "get_label_by_id", side_effect=_label_resp),
    ):
        detail = mc.fetch_release_detail(_REL_ID, retry_pause=0)

    assert work_calls["n"] == 2  # failed once, retried once, succeeded
    for track in detail.tracks:
        assert {"Composer"} <= {c.role_name for c in track.credits}
    assert detail.partial_failures == []


def test_work_shared_by_two_tracks_is_recorded_once_when_it_keeps_failing():
    dispatch = _ReleaseDispatcher()
    with (
        patch.object(mc.musicbrainzngs, "get_release_by_id", side_effect=dispatch),
        patch.object(
            mc.musicbrainzngs, "get_work_by_id", side_effect=RuntimeError("down")
        ) as get_work,
        patch.object(mc.musicbrainzngs, "get_label_by_id", side_effect=_label_resp),
    ):
        detail = mc.fetch_release_detail(_REL_ID, retry_pause=0)

    # Both tracks perform the same work -> one lookup in pass 1, one retry.
    # Not once per referencing track, and not recorded twice.
    assert get_work.call_count == 2
    assert [d for d in detail.partial_failures if "work" in d] == [
        f"writing credits for work {_WORK}"
    ]
    assert all(t.credits == [] for t in detail.tracks)


def test_status_and_progress_callbacks_report_each_step():
    dispatch = _ReleaseDispatcher(rel_errors=[RuntimeError("blip")])
    statuses: list[str] = []
    progress: list[tuple[int, int]] = []
    with (
        patch.object(mc.musicbrainzngs, "get_release_by_id", side_effect=dispatch),
        patch.object(mc.musicbrainzngs, "get_work_by_id", side_effect=_work_resp),
        patch.object(mc.musicbrainzngs, "get_label_by_id", side_effect=_label_resp),
    ):
        mc.fetch_release_detail(
            _REL_ID,
            progress_callback=lambda c, t: progress.append((c, t)),
            status_callback=statuses.append,
            retry_pause=0,
        )

    joined = " | ".join(statuses)
    assert "Fetching release data" in joined
    assert "Fetching track relationships" in joined
    assert "Retrying track relationships" in joined  # the inline relation retry
    assert any("writing credits" in s for s in statuses)
    # total = 1 unique work + 1 label + 0 places, known once relations parse.
    assert progress[-1] == (2, 2)
    assert {t for _, t in progress} == {2}
