"""Tests for bug #242: resolve_place_chain's MBID-place parent walk-up.

Covers:
  - Building a brand-new chain top-down when nothing exists yet.
  - Stopping the walk only on a genuine existing MBID match -- that's the
    only case trusted to already have a correct parent chain above it.
  - A name-only match (no MBID on file) does NOT stop the walk: its MBID
    gets backfilled, but climbing continues until a real MBID match is
    found or the chain runs out, and its own parent_id gets wired/repaired
    once that resolution completes.
  - Refusing to merge a name match whose existing MBID belongs to a
    different real-world place.
"""

from src.place.place_chain_resolver import resolve_place_chain


class _FakePlace:
    def __init__(
        self,
        place_id,
        place_name,
        place_type=None,
        MBID=None,
        parent_id=None,
        place_latitude=None,
        place_longitude=None,
    ):
        self.place_id = place_id
        self.place_name = place_name
        self.place_type = place_type
        self.MBID = MBID
        self.parent_id = parent_id
        self.place_latitude = place_latitude
        self.place_longitude = place_longitude


class _StubController:
    """Minimal in-memory stand-in for controller.get/.add/.update, scoped to
    the Place model, so this logic can be tested without a real DB session."""

    def __init__(self, existing_places=None):
        self._places = list(existing_places or [])
        self._next_id = max((p.place_id for p in self._places), default=0) + 1
        self.get = self
        self.add = self
        self.update = self

    def get_entity_object(self, model_name, **filters):
        assert model_name == "Place"
        mbid = filters.get("MBID")
        if mbid is None:
            return None
        return next((p for p in self._places if p.MBID == mbid), None)

    def get_all_entities(self, model_name, load_options=None, **filters):
        assert model_name == "Place"
        return list(self._places)

    def add_entity(self, model_name, **kwargs):
        assert model_name == "Place"
        place = _FakePlace(place_id=self._next_id, **kwargs)
        self._next_id += 1
        self._places.append(place)
        return place

    def update_entity(self, model_name, entity_id, **kwargs):
        assert model_name == "Place"
        place = next((p for p in self._places if p.place_id == entity_id), None)
        if place is None:
            return False
        for key, value in kwargs.items():
            setattr(place, key, value)
        return True

    def by_name(self, name):
        return next(p for p in self._places if p.place_name == name)


def _node(mbid, name, type_=None, lat=None, lon=None):
    return {
        "mbid": mbid,
        "name": name,
        "type": type_,
        "latitude": lat,
        "longitude": lon,
    }


STUDIO = _node("mbid-studio", "A&M Studios", "Studio", 34.09, -118.36)
HOLLYWOOD = _node("mbid-hollywood", "Hollywood", "District")
LOS_ANGELES = _node("mbid-la", "Los Angeles", "City")
CALIFORNIA = _node("mbid-california", "California", "Subdivision")
USA = _node("mbid-usa", "United States", "Country")

CHAIN = [STUDIO, HOLLYWOOD, LOS_ANGELES, CALIFORNIA, USA]


def test_creates_full_chain_when_nothing_exists():
    controller = _StubController()

    result = resolve_place_chain(controller, CHAIN, {})

    assert result.place_name == "A&M Studios"
    assert len(controller._places) == 5

    usa = controller.by_name("United States")
    california = controller.by_name("California")
    la = controller.by_name("Los Angeles")
    hollywood = controller.by_name("Hollywood")
    studio = controller.by_name("A&M Studios")

    assert usa.parent_id is None
    assert california.parent_id == usa.place_id
    assert la.parent_id == california.place_id
    assert hollywood.parent_id == la.place_id
    assert studio.parent_id == hollywood.place_id
    assert studio.MBID == "mbid-studio"


def test_name_only_match_backfills_mbid_but_keeps_climbing():
    """California exists locally without an MBID. Since it was only matched
    by name (not trusted the way an MBID match is), the walk must NOT stop
    there: it should still climb to United States, create it, and wire
    California's parent_id to it -- in addition to backfilling California's
    MBID and attaching LA/Hollywood/Studio underneath."""
    existing_california = _FakePlace(place_id=1, place_name="California", MBID=None)
    controller = _StubController([existing_california])

    result = resolve_place_chain(controller, CHAIN, {})

    assert result.place_name == "A&M Studios"
    # LA, Hollywood, Studio, and USA are new -- California reused.
    assert len(controller._places) == 5
    assert existing_california.MBID == "mbid-california"

    usa = controller.by_name("United States")
    la = controller.by_name("Los Angeles")
    hollywood = controller.by_name("Hollywood")
    studio = controller.by_name("A&M Studios")
    assert usa.parent_id is None
    assert existing_california.parent_id == usa.place_id
    assert la.parent_id == existing_california.place_id
    assert hollywood.parent_id == la.place_id
    assert studio.parent_id == hollywood.place_id


def test_stops_walk_at_existing_mbid_match():
    existing_la = _FakePlace(place_id=1, place_name="Los Angeles", MBID="mbid-la")
    controller = _StubController([existing_la])

    resolve_place_chain(controller, CHAIN, {})

    assert len(controller._places) == 3  # Studio, Hollywood created; LA reused
    assert all(
        p.place_name not in ("California", "United States") for p in controller._places
    )
    studio = controller.by_name("A&M Studios")
    hollywood = controller.by_name("Hollywood")
    assert hollywood.parent_id == existing_la.place_id
    assert studio.parent_id == hollywood.place_id


def test_name_match_stops_climbing_once_it_reaches_a_real_mbid_anchor():
    """Mirrors the Springfield, OH example: Clark County exists locally by
    name only (no MBID, no parent set), and Ohio already exists with a real
    MBID. The walk backfills Clark County's MBID and repairs its parent_id
    to Ohio, then stops there -- Ohio's own ancestry (-> United States) is
    trusted and never touched."""
    clark_county = _FakePlace(
        place_id=1, place_name="Clark County", MBID=None, parent_id=None
    )
    ohio = _FakePlace(place_id=2, place_name="Ohio", MBID="mbid-ohio", parent_id=None)
    controller = _StubController([clark_county, ohio])

    springfield = _node("mbid-springfield", "Springfield")
    clark_county_node = _node("mbid-clark-county", "Clark County")
    ohio_node = _node("mbid-ohio", "Ohio")
    usa_node = _node("mbid-usa", "United States")
    chain = [springfield, clark_county_node, ohio_node, usa_node]

    result = resolve_place_chain(controller, chain, {})

    assert result.place_name == "Springfield"
    assert result.parent_id == clark_county.place_id
    assert clark_county.MBID == "mbid-clark-county"
    assert clark_county.parent_id == ohio.place_id
    assert all(p.place_name != "United States" for p in controller._places)


def test_name_only_match_backfills_missing_coordinates():
    """A&M Studios exists locally by name only, with no coordinates on file.
    The freshly-resolved MusicBrainz node for it does carry coordinates, so
    the existing row should have them backfilled rather than left NULL."""
    existing_studio = _FakePlace(
        place_id=1,
        place_name="A&M Studios",
        MBID=None,
        place_latitude=None,
        place_longitude=None,
    )
    controller = _StubController([existing_studio])

    resolve_place_chain(controller, CHAIN, {})

    assert existing_studio.MBID == "mbid-studio"
    assert existing_studio.place_latitude == 34.09
    assert existing_studio.place_longitude == -118.36


def test_name_only_match_does_not_overwrite_existing_coordinates():
    """If the local row already has coordinates (e.g. hand-entered), a
    later MusicBrainz name match must not clobber them."""
    existing_studio = _FakePlace(
        place_id=1,
        place_name="A&M Studios",
        MBID=None,
        place_latitude=1.23,
        place_longitude=4.56,
    )
    controller = _StubController([existing_studio])

    resolve_place_chain(controller, CHAIN, {})

    assert existing_studio.MBID == "mbid-studio"
    assert existing_studio.place_latitude == 1.23
    assert existing_studio.place_longitude == 4.56


def test_name_match_with_conflicting_mbid_is_not_merged():
    """A same-named row that already has a *different* MBID is a distinct
    real-world place (or a rare id reassignment) -- it must not be reused,
    and a separate new row should be created instead."""
    other_california = _FakePlace(
        place_id=1, place_name="California", MBID="some-other-mbid"
    )
    controller = _StubController([other_california])

    result = resolve_place_chain(controller, CHAIN, {})

    assert result.place_name == "A&M Studios"
    californias = [p for p in controller._places if p.place_name == "California"]
    assert len(californias) == 2
    assert other_california.MBID == "some-other-mbid"  # untouched
    new_california = next(
        p for p in californias if p.place_id != other_california.place_id
    )
    assert new_california.MBID == "mbid-california"


def test_cache_is_reused_across_calls():
    controller = _StubController()
    cache = {}

    first = resolve_place_chain(controller, CHAIN, cache)
    resolve_place_chain(controller, CHAIN, cache)

    # Second call should reuse every cached level rather than creating dupes.
    assert len(controller._places) == 5
    assert cache["mbid-studio"] is first
