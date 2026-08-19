"""Tests for fetch_release_detail's writing-credit (composer/lyricist/
writer/...) parsing -- a recording's embedded work-relation-list entry
carries only a work stub (id/title/type), so each unique work gets its own
get_work_by_id follow-up (see _fetch_work_by_id/_parse_work_credits) for its
artist-relation-list, same reasoning as labels needing their own
get_label_by_id follow-up.
"""

from unittest.mock import patch

from src.musicbrainz import musicbrainz_release as mc

_WORK_ID = "41c94a08-a551-3c86-bb17-d9a52e3a618b"
_RECORDING_ID = "b1a9c0e9-d987-4042-ae91-78d6a3267d69"
_COMPOSER_ID = "022589ac-7177-460d-a178-9976cf70e29f"


def _release(work_relations=None, recording_id=_RECORDING_ID):
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
                {
                    "type": "composer",
                    "artist": {"id": _COMPOSER_ID, "name": "Freddie Mercury"},
                },
                {
                    "type": "lyricist",
                    "artist": {"id": _COMPOSER_ID, "name": "Freddie Mercury"},
                },
                # Not a writing credit -- must be filtered out.
                {
                    "type": "previous attribution",
                    "artist": {"id": "cccccccc-3333-3333-3333-333333333333", "name": "Someone Else"},
                },
            ],
        }
    }


class TestFetchReleaseDetailWorkCredits:
    def test_parses_composer_and_lyricist_onto_the_track(self):
        with (
            patch.object(mc.musicbrainzngs, "get_release_by_id", return_value=_release()),
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
                mc.musicbrainzngs, "get_release_by_id", return_value=_release(work_relations=[])
            ),
            patch.object(mc.musicbrainzngs, "get_work_by_id") as get_work,
        ):
            detail = mc.fetch_release_detail("faada5d1-971b-499c-b902-5bab9e03bc1b")

        get_work.assert_not_called()
        assert detail.tracks[0].credits == []

    def test_non_performance_work_relation_is_ignored(self):
        release = _release(
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
        release = _release()
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
            patch.object(mc.musicbrainzngs, "get_release_by_id", return_value=_release()),
            patch.object(
                mc.musicbrainzngs, "get_work_by_id", side_effect=RuntimeError("boom")
            ),
        ):
            detail = mc.fetch_release_detail("faada5d1-971b-499c-b902-5bab9e03bc1b")

        assert detail.tracks[0].credits == []
