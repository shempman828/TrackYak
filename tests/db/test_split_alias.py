"""Tests for SplitDB's split-alias recording (docs/specs/split_and_merge_aliases.md).

Splitting a Genre/Artist/Publisher/Role into 2+ names should record the
original combined name as a split-alias rule pointing at the resulting
entities, so the same combined name auto-splits the same way next time
it's encountered. Splitting into exactly 1 name (rename-via-duplicate)
should record nothing, and re-splitting a name that already has a rule
should replace it rather than accumulate alongside it.
"""

import pytest

from src.db.db_helpers.split import SplitDB
from src.db.db_tables.artist import Artist, ArtistSplitAlias
from src.db.db_tables.genre import Genre, GenreSplitAlias
from src.db.db_tables.publisher import Publisher, PublisherSplitAlias
from src.db.db_tables.role import Role, RoleSplitAlias
from src.db.db_tables.track import Track

# (model_name, model_class, name_field, split_method_name, split_alias_class, fk_field)
_CASES = [
    ("Genre", Genre, "genre_name", "split_genre", GenreSplitAlias, "genre_id"),
    ("Artist", Artist, "artist_name", "split_artist", ArtistSplitAlias, "artist_id"),
    (
        "Publisher",
        Publisher,
        "publisher_name",
        "split_publisher",
        PublisherSplitAlias,
        "publisher_id",
    ),
    ("Role", Role, "role_name", "split_role", RoleSplitAlias, "role_id"),
]


@pytest.mark.parametrize(
    "model_name,model_class,name_field,split_method,alias_class,fk_field", _CASES
)
def test_split_into_two_records_alias_rule(
    session, model_name, model_class, name_field, split_method, alias_class, fk_field
):
    original = model_class(**{name_field: "Viola & Violin"})
    session.add(original)
    session.commit()
    original_id = original.__table__.primary_key.columns.keys()[0]

    splitter = SplitDB(session)
    result = getattr(splitter, split_method)(getattr(original, original_id), ["Viola", "Violin"])
    assert result is True

    rows = (
        session.query(alias_class)
        .filter_by(alias_name="Viola & Violin")
        .order_by(alias_class.sort_order)
        .all()
    )
    assert len(rows) == 2
    new_entities = (
        session.query(model_class)
        .filter(getattr(model_class, name_field).in_(["Viola", "Violin"]))
        .all()
    )
    new_ids = {getattr(e, fk_field) for e in new_entities}
    assert {getattr(r, fk_field) for r in rows} == new_ids
    assert [r.sort_order for r in rows] == [0, 1]


@pytest.mark.parametrize(
    "model_name,model_class,name_field,split_method,alias_class,fk_field", _CASES
)
def test_split_into_one_records_no_alias(
    session, model_name, model_class, name_field, split_method, alias_class, fk_field
):
    original = model_class(**{name_field: "Duplicate Name"})
    session.add(original)
    session.commit()
    original_id = original.__table__.primary_key.columns.keys()[0]

    splitter = SplitDB(session)
    getattr(splitter, split_method)(getattr(original, original_id), ["Renamed"])

    rows = session.query(alias_class).filter_by(alias_name="Duplicate Name").all()
    assert rows == []


def test_resplitting_role_replaces_old_rule(session):
    original = Role(role_name="Viola & Violin")
    session.add(original)
    session.commit()

    splitter = SplitDB(session)
    splitter.split_role(original.role_id, ["Viola", "Violin"])

    # Merge the two back into one role sharing the combined name, then
    # split it a second time into a different target set.
    combined = Role(role_name="Viola & Violin")
    session.add(combined)
    session.commit()
    splitter.split_role(combined.role_id, ["String Instrument", "Bowed Instrument"])

    rows = session.query(RoleSplitAlias).filter_by(alias_name="Viola & Violin").all()
    names = {r.role.role_name for r in rows}
    assert names == {"String Instrument", "Bowed Instrument"}
    assert len(rows) == 2


def test_split_genre_reusing_existing_genre_with_tracks(session):
    # Regression test: splitting a genre into a target that already exists
    # and already has tracks used to crash with
    # AttributeError: 'Track' object has no attribute 'genre_id'
    # because Genre.tracks yields Track rows, not TrackGenre association
    # rows, but split_genre() was treating them as the latter.
    techno = Genre(genre_name="Techno")
    house = Genre(genre_name="House")
    existing_track = Track(track_name="Existing House Track")
    house.tracks.append(existing_track)
    session.add_all([techno, house, existing_track])
    session.commit()

    original = Genre(genre_name="Techno & House")
    split_track = Track(track_name="Mixed Track")
    original.tracks.append(split_track)
    session.add_all([original, split_track])
    session.commit()

    splitter = SplitDB(session)
    result = splitter.split_genre(original.genre_id, ["Techno", "House"])
    assert result is True

    session.refresh(house)
    house_track_names = {t.track_name for t in house.tracks}
    assert house_track_names == {"Existing House Track", "Mixed Track"}


def test_deleting_split_alias_member_role_cascades(session):
    original = Role(role_name="Viola & Violin")
    session.add(original)
    session.commit()

    splitter = SplitDB(session)
    splitter.split_role(original.role_id, ["Viola", "Violin"])

    viola = session.query(Role).filter_by(role_name="Viola").one()
    violin = session.query(Role).filter_by(role_name="Violin").one()

    session.delete(viola)
    session.commit()

    rows = session.query(RoleSplitAlias).filter_by(alias_name="Viola & Violin").all()
    assert len(rows) == 1
    assert rows[0].role_id == violin.role_id
