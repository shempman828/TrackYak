"""AC7 -- the Track view's Artist column sorts by filing name
(Artist.sort_name) while the displayed cell keeps the plain name join.

See docs/specs/artist_sort_name_ordering.md.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_tables import Artist, Role, Track, TrackArtistRole
from src.db.db_tables.base import Base
from src.track.track_view_data import TrackViewDataMixin, _fetch_lookup_caches


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sess = sessionmaker(bind=engine)()
    yield sess
    sess.close()


def _seed(session):
    primary = Role(role_id=1, role_name="Primary Artist")
    session.add(primary)
    session.add_all([Track(track_id=tid, track_name=f"t{tid}") for tid in (10, 20, 30)])
    # track 10 -> "The Beatles" (files under B), track 20 -> "ABBA" (no
    # sort_name -> falls back to "abba"), track 30 -> "Tom Waits" (files
    # under W).
    session.add_all(
        [
            Artist(artist_id=1, artist_name="The Beatles", sort_name="Beatles, The"),
            Artist(artist_id=2, artist_name="ABBA", sort_name=None),
            Artist(artist_id=3, artist_name="Tom Waits", sort_name="Waits, Tom"),
        ]
    )
    session.add_all(
        [
            TrackArtistRole(track_id=10, artist_id=1, role_id=1),
            TrackArtistRole(track_id=20, artist_id=2, role_id=1),
            TrackArtistRole(track_id=30, artist_id=3, role_id=1),
        ]
    )
    session.commit()


def test_fetch_builds_display_and_sort_caches(session):
    _seed(session)
    _album, _disc, name_cache, sort_cache = _fetch_lookup_caches(session)

    # Display cache: plain names, unchanged behaviour.
    assert name_cache == {10: "The Beatles", 20: "ABBA", 30: "Tom Waits"}
    # Sort cache: filing names, with fallback to display name when sort_name is null.
    assert sort_cache == {10: "Beatles, The", 20: "ABBA", 30: "Waits, Tom"}


class _Track:
    def __init__(self, track_id):
        self.track_id = track_id


def test_field_value_display_vs_sort(session):
    _seed(session)
    host = TrackViewDataMixin.__new__(TrackViewDataMixin)
    (
        host._album_cache,
        host._disc_number_cache,
        host._artist_name_cache,
        host._artist_sort_cache,
    ) = _fetch_lookup_caches(session)

    tracks = [_Track(10), _Track(20), _Track(30)]

    # Display field -> plain name join.
    assert [host._field_value(t, "primary_artist_names") for t in tracks] == [
        "The Beatles",
        "ABBA",
        "Tom Waits",
    ]

    # Sort pseudo-field -> filing names; ordering them puts "The Beatles"
    # (Beatles, The) between ABBA and Tom Waits.
    keyed = sorted(tracks, key=lambda t: host._field_value(t, "primary_artist_names__sort").lower())
    assert [t.track_id for t in keyed] == [20, 10, 30]


def test_sort_pseudo_field_falls_back_when_cache_missing(session):
    host = TrackViewDataMixin.__new__(TrackViewDataMixin)
    host._artist_name_cache = {10: "Some Artist"}
    host._artist_sort_cache = {}
    assert host._field_value(_Track(10), "primary_artist_names__sort") == "Some Artist"
    assert host._field_value(_Track(99), "primary_artist_names__sort") == "Unknown Artist"
