"""Tests for bug #246: resolve_area_chain built artist birthplace hierarchies
backwards. Importing an artist born in "North Carolina" produced a chain of
North Carolina -> Camden County -> Old Trap instead of North Carolina ->
United States.

Root cause: MusicBrainz's "part of" (Area/Area) relation direction is
relative to the link phrase, not to which side is bigger. Querying the
*smaller* area (e.g. North Carolina) for its link to the larger container
(United States) returns that relation tagged direction="backward" -- verified
against the live MusicBrainz API. The code had this inverted: it skipped
"backward" relations (the real parent link) and instead followed "forward"
relations, which point at a *child* area when queried from the container
side. That walked the chain downward into children while believing it was
walking upward into parents.
"""

from unittest.mock import patch

from src.musicbrainz import musicbrainz_core as mc

_NC_ID = "d4ab49e7-1d25-45e2-8659-b147e0ea3684"
_US_ID = "489ce91b-6658-3307-9877-795b68554c98"
_CAMDEN_COUNTY_ID = "11111111-1111-1111-1111-111111111111"


def _area(name, area_type, relations):
    return {
        "area": {
            "name": name,
            "type": area_type,
            "area-relation-list": relations,
        }
    }


def _get_area_by_id(area_mbid, includes=None):
    if area_mbid == _NC_ID:
        return _area(
            "North Carolina",
            "Subdivision",
            [
                # The real parent link: North Carolina is entity0 ("the
                # part"), so MusicBrainz tags this "backward" when queried
                # from North Carolina's own page.
                {
                    "type": "part of",
                    "direction": "backward",
                    "area": {"id": _US_ID, "name": "United States", "type": "Country"},
                },
                # A decoy: a contained county surfaced as a "forward" link
                # (NC is entity1/container here). Must NOT be treated as a
                # parent -- that's exactly bug #246.
                {
                    "type": "part of",
                    "direction": "forward",
                    "area": {
                        "id": _CAMDEN_COUNTY_ID,
                        "name": "Camden County",
                        "type": "County",
                    },
                },
            ],
        )
    if area_mbid == _US_ID:
        return _area("United States", "Country", [])
    raise AssertionError(f"unexpected area_mbid lookup: {area_mbid}")


class TestResolveAreaChain:
    def test_state_resolves_to_country_not_a_contained_county(self):
        with patch.object(mc.musicbrainzngs, "get_area_by_id", side_effect=_get_area_by_id):
            chain = mc.resolve_area_chain(_NC_ID, {})

        names = [node["name"] for node in chain]
        assert names == ["North Carolina", "United States"], (
            "expected North Carolina -> United States, got a chain that "
            f"walked into a child area instead: {names}"
        )

    def test_caches_by_area_mbid(self):
        cache: dict = {}
        with patch.object(
            mc.musicbrainzngs, "get_area_by_id", side_effect=_get_area_by_id
        ) as get_by_id:
            mc.resolve_area_chain(_NC_ID, cache)
            mc.resolve_area_chain(_NC_ID, cache)

        assert get_by_id.call_count == 2  # NC, then US -- not repeated on 2nd call
