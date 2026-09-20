"""Tests for the Tag/TagType ORM models (docs/specs/artist_tags.md).

Covers AC1 (hierarchy + full_tag_path), AC4 (name uniqueness is scoped to
one TagType, not global), and AC10 (a fresh DB seeds no TagType/Tag rows).
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from src.db.db_defaults import Defaults
from src.db.db_tables import Artist, ArtistTagAssociation, Tag, TagType
from src.db.db_tables.base import Base


def test_fresh_db_seeds_no_tag_types_or_tags(session):
    assert session.query(TagType).count() == 0
    assert session.query(Tag).count() == 0


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_insert_defaults_does_not_seed_tags(session_factory):
    Defaults(session_factory).insert_defaults()

    session = session_factory()
    try:
        assert session.query(TagType).count() == 0
        assert session.query(Tag).count() == 0
    finally:
        session.close()


def test_tag_full_path_walks_up_the_parent_chain(session):
    tag_type = TagType(type_name="Religion")
    session.add(tag_type)
    session.flush()

    christian = Tag(tag_name="Christian", tag_type_id=tag_type.tag_type_id)
    session.add(christian)
    session.flush()

    catholic = Tag(
        tag_name="Catholic", tag_type_id=tag_type.tag_type_id, parent_id=christian.tag_id
    )
    session.add(catholic)
    session.flush()

    assert christian.full_tag_path == "Christian"
    assert catholic.full_tag_path == "Christian > Catholic"


def test_same_name_allowed_in_two_different_tag_types(session):
    vibe = TagType(type_name="Vibe")
    era = TagType(type_name="Era")
    session.add_all([vibe, era])
    session.flush()

    session.add_all(
        [
            Tag(tag_name="Classic", tag_type_id=vibe.tag_type_id),
            Tag(tag_name="Classic", tag_type_id=era.tag_type_id),
        ]
    )
    session.commit()  # must not raise

    assert session.query(Tag).count() == 2


def test_duplicate_name_within_same_tag_type_rejected(session):
    tag_type = TagType(type_name="Vibe")
    session.add(tag_type)
    session.flush()

    session.add(Tag(tag_name="Classic", tag_type_id=tag_type.tag_type_id))
    session.commit()

    session.add(Tag(tag_name="Classic", tag_type_id=tag_type.tag_type_id))
    try:
        session.commit()
        raise AssertionError("expected an IntegrityError for the duplicate tag name")
    except IntegrityError:
        session.rollback()


def test_deleting_tag_type_cascades_to_its_tags_and_artist_associations(session):
    artist = Artist(artist_name="Miles Davis")
    tag_type = TagType(type_name="Religion")
    session.add_all([artist, tag_type])
    session.flush()

    tag = Tag(tag_name="Jewish", tag_type_id=tag_type.tag_type_id)
    session.add(tag)
    session.flush()
    session.add(ArtistTagAssociation(artist_id=artist.artist_id, tag_id=tag.tag_id))
    session.commit()

    session.delete(tag_type)
    session.commit()

    assert session.query(Tag).count() == 0
    assert session.query(ArtistTagAssociation).count() == 0
    # the artist itself is untouched -- it just loses the tag
    assert session.query(Artist).count() == 1


def test_artist_tags_relationship_is_many_to_many(session):
    artist_a = Artist(artist_name="Miles Davis")
    artist_b = Artist(artist_name="Bing Crosby")
    tag_type = TagType(type_name="Religion")
    session.add_all([artist_a, artist_b, tag_type])
    session.flush()

    tag = Tag(tag_name="Catholic", tag_type_id=tag_type.tag_type_id)
    session.add(tag)
    session.flush()

    artist_a.tags.append(tag)
    artist_b.tags.append(tag)
    session.commit()

    session.expire_all()
    assert {t.tag_name for t in artist_a.tags} == {"Catholic"}
    assert {a.artist_name for a in tag.artists} == {"Miles Davis", "Bing Crosby"}
