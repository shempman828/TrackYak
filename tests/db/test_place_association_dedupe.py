"""Tests for MusicDatabase._dedupe_duplicate_place_associations() -- the
one-time cleanup that removes place_associations rows duplicating the same
entity/place/association-type combination (e.g. two "Headquarters" rows for
one publisher pointing at the same place), so the uq_place_assoc_entity_place_type
index in indexes.py can be created on an existing database file. See the bug:
publishers could get duplicate place associations.

In-memory SQLite, same shape as tests/db/conftest.py's session fixture.
The method is exercised against a minimal stand-in with just a .Session
attribute, so no full MusicDatabase (and its shared-engine wiring) is needed.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_tables.base import Base
from src.db.db_tables.database import MusicDatabase
import src.db.db_tables.indexes
from src.db.db_tables.place import Place, PlaceAssociation
from src.db.db_tables.place_association_type import PlaceAssociationType
from src.db.db_tables.publisher import Publisher

_DUPE_INDEX = next(i for i in PlaceAssociation.__table__.indexes if i.name == "uq_place_assoc_entity_place_type")


@pytest.fixture
def session():
    """A fresh engine built *without* uq_place_assoc_entity_place_type, same
    as the real database file this method targets: it runs before that index
    is (re)created, precisely so pre-existing duplicate rows don't block the
    index's creation. Indexes live on the shared Table object, not per-engine,
    so the index is discarded for create_all() and restored right after."""
    engine = create_engine("sqlite:///:memory:")
    PlaceAssociation.__table__.indexes.discard(_DUPE_INDEX)
    try:
        Base.metadata.create_all(engine)
    finally:
        PlaceAssociation.__table__.indexes.add(_DUPE_INDEX)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


class _FakeDatabase:
    """Just enough surface for _dedupe_duplicate_place_associations: a
    .Session sessionmaker bound to the test engine."""

    def __init__(self, session):
        self.Session = sessionmaker(bind=session.get_bind())


def _dedupe(session):
    MusicDatabase._dedupe_duplicate_place_associations(_FakeDatabase(session))


def _setup_publisher_and_places(session):
    publisher = Publisher(publisher_name="Test Label")
    place_a = Place(place_name="New York")
    place_b = Place(place_name="London")
    hq_type = PlaceAssociationType(type_name="Headquarters")
    other_type = PlaceAssociationType(type_name="Recording Location")
    session.add_all([publisher, place_a, place_b, hq_type, other_type])
    session.commit()
    return publisher, place_a, place_b, hq_type, other_type


def test_removes_duplicate_same_place_same_type(session):
    publisher, place_a, _, hq_type, _ = _setup_publisher_and_places(session)
    kept = PlaceAssociation(entity_type="Publisher", entity_id=publisher.publisher_id, place_id=place_a.place_id, association_type_id=hq_type.association_type_id)
    session.add(kept)
    session.commit()
    dupe = PlaceAssociation(entity_type="Publisher", entity_id=publisher.publisher_id, place_id=place_a.place_id, association_type_id=hq_type.association_type_id)
    session.add(dupe)
    session.commit()
    kept_id = kept.association_id

    _dedupe(session)

    remaining = session.query(PlaceAssociation).all()
    assert [a.association_id for a in remaining] == [kept_id]

    # The whole point: once deduped, the unique index can actually be
    # created on this (previously duplicate-laden) table.
    _DUPE_INDEX.create(bind=session.get_bind())


def test_keeps_same_place_different_type(session):
    publisher, place_a, _, hq_type, other_type = _setup_publisher_and_places(session)
    session.add_all(
        [
            PlaceAssociation(entity_type="Publisher", entity_id=publisher.publisher_id, place_id=place_a.place_id, association_type_id=hq_type.association_type_id),
            PlaceAssociation(entity_type="Publisher", entity_id=publisher.publisher_id, place_id=place_a.place_id, association_type_id=other_type.association_type_id),
        ]
    )
    session.commit()

    _dedupe(session)

    assert session.query(PlaceAssociation).count() == 2


def test_keeps_different_place_same_type(session):
    publisher, place_a, place_b, hq_type, _ = _setup_publisher_and_places(session)
    session.add_all(
        [
            PlaceAssociation(entity_type="Publisher", entity_id=publisher.publisher_id, place_id=place_a.place_id, association_type_id=hq_type.association_type_id),
            PlaceAssociation(entity_type="Publisher", entity_id=publisher.publisher_id, place_id=place_b.place_id, association_type_id=hq_type.association_type_id),
        ]
    )
    session.commit()

    _dedupe(session)

    assert session.query(PlaceAssociation).count() == 2


def test_leaves_untyped_duplicates_alone(session):
    publisher, place_a, _, _, _ = _setup_publisher_and_places(session)
    session.add_all(
        [
            PlaceAssociation(entity_type="Publisher", entity_id=publisher.publisher_id, place_id=place_a.place_id, association_type_id=None),
            PlaceAssociation(entity_type="Publisher", entity_id=publisher.publisher_id, place_id=place_a.place_id, association_type_id=None),
        ]
    )
    session.commit()

    _dedupe(session)

    assert session.query(PlaceAssociation).count() == 2


def test_second_run_is_a_noop(session):
    publisher, place_a, _, hq_type, _ = _setup_publisher_and_places(session)
    session.add(PlaceAssociation(entity_type="Publisher", entity_id=publisher.publisher_id, place_id=place_a.place_id, association_type_id=hq_type.association_type_id))
    session.add(PlaceAssociation(entity_type="Publisher", entity_id=publisher.publisher_id, place_id=place_a.place_id, association_type_id=hq_type.association_type_id))
    session.commit()

    _dedupe(session)
    assert session.query(PlaceAssociation).count() == 1

    _dedupe(session)
    assert session.query(PlaceAssociation).count() == 1
