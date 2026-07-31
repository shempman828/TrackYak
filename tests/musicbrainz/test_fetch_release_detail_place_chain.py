"""Tests for bug #248: a recording location's place got added during
MusicBrainz album track matching, but no parent area was ever attached --
e.g. importing faada5d1-971b-499c-b902-5bab9e03bc1b added "Church of the
Holy Trinity" with no parent.

Root cause: MusicBrainz's "recorded at" relation embeds a reduced place
stub on the recording (id/name/type/coordinates only) that never carries
the place's containing area, even though a direct place lookup does. The
code trusted the embedded stub's (always-empty) "area" field instead of
doing that direct lookup, so `pending_areas` was always empty for
recording locations and `resolve_area_chain` was never invoked for them --
`place_chains` ended up with a chain of exactly one node (the place
itself, no parent) every time.
"""

from unittest.mock import patch

from src.musicbrainz import musicbrainz_client as mc

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


def _get_area_by_id(area_mbid, includes=None):
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
            patch.object(
                mc.musicbrainzngs, "get_place_by_id", side_effect=_get_place_by_id
            ),
            patch.object(mc.musicbrainzngs, "get_area_by_id", side_effect=_get_area_by_id),
        ):
            detail = mc.fetch_release_detail("faada5d1-971b-499c-b902-5bab9e03bc1b")

        chain = detail.place_chains.get(_CHURCH_ID)
        assert chain is not None, "place chain missing entirely for the recorded-at place"
        names = [node["name"] for node in chain]
        assert names == ["Church of the Holy Trinity", "Toronto", "Ontario"], (
            "expected the church's chain to include its parent areas, but got "
            f"a chain with no parent walked: {names}"
        )
