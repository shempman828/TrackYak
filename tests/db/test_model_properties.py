"""ORM model properties and delete cascades in src/db/db_tables."""

import pytest
from sqlalchemy import Column, Integer, String, create_engine, inspect, text

from src.db.db_tables import (
    Album,
    AlbumRoleAssociation,
    AlbumVirtualTrack,
    Artist,
    ArtistInfluence,
    Award,
    AwardAssociation,
    Base,
    Disc,
    Genre,
    GroupMembership,
    Place,
    PlaceAssociation,
    Playlist,
    PlaylistTracks,
    Role,
    SmartPlaylist,
    SmartPlaylistCriteria,
    Tag,
    TagType,
    Track,
    TrackArtistRole,
    migrations,
)
from src.db.db_tables.base import oxford_join


def _role(session, name):
    role = Role(role_name=name)
    session.add(role)
    session.flush()
    return role


# --- Delete cascades (older DB files have no ON DELETE on these FKs) ---


@pytest.mark.parametrize("relationship_attr", [Artist.member_memberships, Artist.group_memberships, Artist.influencer_relations, Artist.influenced_relations, Award.associations, Place.associations])
def test_collections_without_db_cascade_are_not_passive(relationship_attr):
    assert relationship_attr.property.passive_deletes is False


def test_deleting_group_member_removes_membership_and_influence_rows(session):
    group, member = Artist(artist_name="Band"), Artist(artist_name="Member")
    session.add_all([group, member])
    session.flush()
    session.add(GroupMembership(group_id=group.artist_id, member_id=member.artist_id))
    session.add(ArtistInfluence(influencer_id=member.artist_id, influenced_id=group.artist_id))
    session.commit()
    member_id = member.artist_id
    session.expunge_all()

    session.delete(session.get(Artist, member_id))
    session.commit()

    assert session.query(GroupMembership).count() == 0
    assert session.query(ArtistInfluence).count() == 0


def test_deleting_award_and_place_removes_their_associations(session):
    award, place = Award(award_name="Grammy"), Place(place_name="Paris")
    session.add_all([award, place])
    session.flush()
    session.add(AwardAssociation(award_id=award.award_id, entity_id=1, entity_type="Artist"))
    session.add(PlaceAssociation(place_id=place.place_id, entity_id=1, entity_type="Artist"))
    session.commit()
    award_id, place_id = award.award_id, place.place_id
    session.expunge_all()

    session.delete(session.get(Award, award_id))
    session.delete(session.get(Place, place_id))
    session.commit()

    assert session.query(AwardAssociation).count() == 0
    assert session.query(PlaceAssociation).count() == 0


def test_deleting_track_removes_its_virtual_album_links(session):
    album, track = Album(album_name="Hits"), Track(track_name="Song")
    session.add_all([album, track])
    session.flush()
    session.add(AlbumVirtualTrack(album_id=album.album_id, track_id=track.track_id))
    session.commit()

    session.delete(track)
    session.commit()

    assert session.execute(text("SELECT COUNT(*) FROM album_virtual_tracks")).scalar() == 0


def test_deleting_smart_playlist_removes_its_criteria(session):
    playlist = Playlist(playlist_name="Smart", is_smart=1)
    playlist.smart_playlist = SmartPlaylist(criteria=[SmartPlaylistCriteria(field_name="bpm", comparison=">", value="120")])
    session.add(playlist)
    session.commit()

    assert [c.field_name for c in playlist.smart_playlist.criteria] == ["bpm"]
    session.delete(playlist)
    session.commit()

    assert session.query(SmartPlaylistCriteria).count() == 0


# --- Artist ---


def test_artist_track_count_counts_distinct_tracks(session):
    artist, track = Artist(artist_name="A"), Track(track_name="T")
    session.add_all([artist, track])
    session.flush()
    for name in ("Primary Artist", "Composer"):
        session.add(TrackArtistRole(track_id=track.track_id, artist_id=artist.artist_id, role_id=_role(session, name).role_id))
    session.commit()

    assert artist.track_count == 1


def test_artist_albums_uses_album_artist_role_name(session):
    other = _role(session, "Producer")  # takes role_id 1
    album_artist = _role(session, "Album Artist")
    artist, kept, skipped = Artist(artist_name="A"), Album(album_name="Kept"), Album(album_name="Skipped")
    session.add_all([artist, kept, skipped])
    session.flush()
    session.add(AlbumRoleAssociation(album_id=kept.album_id, artist_id=artist.artist_id, role_id=album_artist.role_id))
    session.add(AlbumRoleAssociation(album_id=skipped.album_id, artist_id=artist.artist_id, role_id=other.role_id))
    session.commit()

    assert [a.album_name for a in artist.albums] == ["Kept"]


# --- Role ---


def test_role_artist_count_handles_null_cache(session):
    role = _role(session, "Producer")
    session.commit()
    session.execute(text("UPDATE roles SET _artist_count = NULL"))
    session.commit()
    session.expire_all()

    assert role.artist_count == 0


# --- Album ---


@pytest.mark.parametrize(("sales", "expected"), [(None, "None"), (499_999, "None"), (500_000, "Gold"), (1_000_000, "Platinum"), (3_200_000, "3\u00d7 Platinum"), (10_000_000, "Diamond")])
def test_riaa_certification_levels(sales, expected):
    assert Album(estimated_sales=sales).RIAA_certification == expected


def _album_with_discs(session, *disc_track_numbers):
    album = Album(album_name="A")
    session.add(album)
    session.flush()
    for disc_number, numbers in enumerate(disc_track_numbers, start=1):
        disc = Disc(album_id=album.album_id, disc_number=disc_number)
        session.add(disc)
        session.flush()
        for n in numbers:
            session.add(Track(track_name=f"{disc_number}-{n}", track_number=n, album_id=album.album_id, disc_id=disc.disc_id))
    session.commit()
    return album


def test_possibly_incomplete_finds_gap_on_second_disc(session):
    assert _album_with_discs(session, [1, 2, 3], [1, 3]).possibly_incomplete is True


def test_possibly_incomplete_false_for_complete_multi_disc(session):
    assert _album_with_discs(session, [1, 2], [1, 2, 3]).possibly_incomplete is False


def test_full_tracklist_orders_by_disc_then_virtual_position(session):
    album = _album_with_discs(session, [1, 2], [1])
    other = Album(album_name="Source")
    borrowed = Track(track_name="borrowed", track_number=9)
    session.add_all([other, borrowed])
    session.flush()
    borrowed.album_id = other.album_id
    session.add(AlbumVirtualTrack(album_id=album.album_id, track_id=borrowed.track_id, virtual_disc_number=1, virtual_track_number=3))
    session.commit()
    session.expire_all()

    assert [t.track_name for t in album.full_tracklist] == ["1-1", "1-2", "borrowed", "2-1"]


def test_album_artist_names_oxford_join(session):
    role = _role(session, "Album Artist")
    album = Album(album_name="A")
    session.add(album)
    session.flush()
    for name in ("X", "Y", "Z"):
        artist = Artist(artist_name=name)
        session.add(artist)
        session.flush()
        session.add(AlbumRoleAssociation(album_id=album.album_id, artist_id=artist.artist_id, role_id=role.role_id))
    session.commit()

    assert album.album_artist_names == "X, Y, & Z"
    assert oxford_join(["X", "Y"]) == "X & Y"


# --- Hierarchy cycles ---


def test_genre_hierarchy_properties_stop_at_cycle():
    a, b = Genre(genre_name="A"), Genre(genre_name="B")
    a.parent, b.parent = b, a

    assert a.full_genre_path == "B > A"
    assert a.depth == 1
    assert [g.genre_name for g in a.all_subgenres] == ["B"]


def test_tag_full_path_stops_at_cycle():
    tag_type = TagType(type_name="T")
    a, b = Tag(tag_name="A", tag_type=tag_type), Tag(tag_name="B", tag_type=tag_type)
    a.parent, b.parent = b, a

    assert a.full_tag_path == "B > A"


# --- Edge cases ---


def test_composer_names_uses_credited_name_and_skips_missing_artist():
    composer = Role(role_name="Composer")
    track = Track(track_name="T", artist_roles=[TrackArtistRole(role=composer, artist=Artist(artist_name="Bach")), TrackArtistRole(role=composer)])

    assert track.composer_names == ["Bach"]


def test_playlist_size_skips_missing_track():
    playlist = Playlist(playlist_name="P", tracks=[PlaylistTracks(track=Track(track_name="T", file_size=5.0), position=1), PlaylistTracks(position=2)])

    assert playlist.playlist_size == 5.0


def test_place_entity_resolves_only_its_type_and_entities_skip_broken_links(session):
    artist, place = Artist(artist_name="A"), Place(place_name="P")
    session.add_all([artist, place])
    session.flush()
    session.add(PlaceAssociation(place_id=place.place_id, entity_id=artist.artist_id, entity_type="Artist"))
    session.add(PlaceAssociation(place_id=place.place_id, entity_id=999, entity_type="Track"))
    session.commit()

    assert place.entities == [artist]


def test_recursive_rated_tracks_filters_by_bounds(session):
    country, city = Place(place_name="Country"), Place(place_name="City")
    session.add_all([country, city])
    session.flush()
    city.parent_id = country.place_id
    in_range, out_of_range = Track(track_name="a", user_rating=5.0), Track(track_name="b", user_rating=0.1)
    session.add_all([in_range, out_of_range])
    session.flush()
    for track in (in_range, out_of_range):
        session.add(PlaceAssociation(place_id=city.place_id, entity_id=track.track_id, entity_type="Track"))
    session.commit()
    session.expire_all()

    assert country.recursive_rated_tracks(0.5, 10.0) == [(in_range.track_id, 5.0)]


# --- Migrations ---


def test_dedupe_album_virtual_tracks_removes_orphans_and_duplicates():
    engine = create_engine("sqlite:///:memory:")
    index = next(i for i in AlbumVirtualTrack.__table__.indexes if i.name == "uq_album_virtual_tracks_album_track")
    AlbumVirtualTrack.__table__.indexes.discard(index)
    try:
        Base.metadata.create_all(engine)
    finally:
        AlbumVirtualTrack.__table__.indexes.add(index)
    with engine.begin() as conn:
        # Older DB files have nullable album_id/track_id, which let orphans in.
        conn.execute(text("DROP TABLE album_virtual_tracks"))
        conn.execute(
            text(
                "CREATE TABLE album_virtual_tracks (virtual_id INTEGER PRIMARY KEY, album_id INTEGER, track_id INTEGER, "
                "virtual_track_number INTEGER, virtual_disc_number INTEGER, virtual_side VARCHAR, virtual_absolute_track_number INTEGER)"
            )
        )
        conn.execute(text("INSERT INTO albums (album_id) VALUES (1)"))
        conn.execute(text("INSERT INTO tracks (track_id, track_name, needs_tag_write) VALUES (1, 't', 0)"))
        for values in ("(1, 1)", "(1, 1)", "(1, NULL)"):
            conn.execute(text(f"INSERT INTO album_virtual_tracks (album_id, track_id) VALUES {values}"))

    migrations.dedupe_album_virtual_tracks(engine)
    migrations.create_missing_indexes(engine, set(inspect(engine).get_table_names()))

    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM album_virtual_tracks")).scalar() == 1
    assert "uq_album_virtual_tracks_album_track" in {i["name"] for i in inspect(engine).get_indexes("album_virtual_tracks")}


def test_try_add_column_quotes_string_default():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY)"))
        conn.execute(text("INSERT INTO t (id) VALUES (1)"))
    column = Column("label", String, nullable=False, default="it's")

    assert migrations.try_add_column(engine, "t", column) is True
    with engine.connect() as conn:
        assert conn.execute(text("SELECT label FROM t")).scalar() == "it's"


def test_try_add_column_refuses_not_null_without_default():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY)"))

    assert migrations.try_add_column(engine, "t", Column("n", Integer, nullable=False)) is False


def test_music_database_on_fresh_file_creates_every_table(tmp_path):
    from src.db.db_tables import MusicDatabase

    db = MusicDatabase(f"sqlite:///{tmp_path / 'lib.db'}")

    assert set(Base.metadata.tables) <= set(inspect(db.engine).get_table_names())
    db.engine.dispose()
