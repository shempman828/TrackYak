"""AwardsTab and PlacesTab against a real in-memory DB: link, show, de-duplicate and remove."""

from PySide6.QtCore import Qt
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.common.widgets.entity_completer_edit import invalidate_entity_cache
from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.delete import DeleteDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.award import Award, AwardAssociation
from src.db.db_tables.base import Base
from src.db.db_tables.place import Place, PlaceAssociation
from src.db.db_tables.place_association_type import PlaceAssociationType
from src.db.db_tables.track import Track
from src.track.edit.track_edit_awards import AwardsTab
from src.track.edit.track_edit_places import PlacesTab


@pytest.fixture
def session(qapp):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    invalidate_entity_cache()
    yield s
    qapp.processEvents()  # flush deferred add_to_index() calls while widgets are alive
    invalidate_entity_cache()
    s.close()


class _Controller:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.add = AddToDB(session)
        self.delete = DeleteDB(session)


def _tracks(session, n=2):
    tracks = [Track(track_name=f"T{i}") for i in range(n)]
    session.add_all(tracks)
    session.commit()
    return tracks


def _rows(table):
    return [tuple(table.item(r, c).text() for c in range(table.columnCount() - 1)) for r in range(table.rowCount())]


# ── Awards ───────────────────────────────────────────────────────────────────


def test_awards_load_shows_category_and_year_from_award(qapp, session):
    tracks = _tracks(session, 1)
    award = Award(award_name="Grammy", award_category="Best Song", award_year=1999)
    session.add(award)
    session.commit()
    session.add(AwardAssociation(award_id=award.award_id, entity_id=tracks[0].track_id, entity_type="Track"))
    session.commit()

    tab = AwardsTab(tracks, _Controller(session))
    tab.load(tracks)

    assert _rows(tab._table) == [("Grammy", "Best Song", "1999")]
    assert not tab._table.item(0, 0).flags() & Qt.ItemIsEditable


def test_awards_link_creates_award_once_per_track_without_duplicates(qapp, session):
    tracks = _tracks(session, 2)
    tab = AwardsTab(tracks, _Controller(session))
    tab.load(tracks)

    for _ in range(2):  # the second link of the same award must not add duplicate rows
        tab._search.setText("Mercury Prize")
        tab._add()

    assocs = session.query(AwardAssociation).all()
    assert sorted(a.entity_id for a in assocs) == sorted(t.track_id for t in tracks)
    assert _rows(tab._table) == [("Mercury Prize", "", "")]


def test_awards_remove_unlinks_only_that_award(qapp, session):
    tracks = _tracks(session, 2)
    a1, a2 = Award(award_name="A1"), Award(award_name="A2")
    session.add_all([a1, a2])
    session.commit()
    for t in tracks:
        session.add_all([AwardAssociation(award_id=a.award_id, entity_id=t.track_id, entity_type="Track") for a in (a1, a2)])
    session.commit()

    tab = AwardsTab(tracks, _Controller(session))
    tab.load(tracks)
    tab._remove_award(a1.award_id)

    assert {a.award_id for a in session.query(AwardAssociation).all()} == {a2.award_id}
    assert _rows(tab._table) == [("A2", "", "")]


# ── Places ───────────────────────────────────────────────────────────────────


def _place_with_two_types(session, tracks):
    place = Place(place_name="Abbey Road")
    rec, origin = PlaceAssociationType(type_name="Recording Location"), PlaceAssociationType(type_name="Origin")
    session.add_all([place, rec, origin])
    session.commit()
    for t in tracks:
        for typ in (rec, origin):
            session.add(PlaceAssociation(place_id=place.place_id, entity_id=t.track_id, entity_type="Track", association_type_id=typ.association_type_id))
    session.commit()
    return place, rec, origin


def test_places_remove_matches_association_type(qapp, session):
    tracks = _tracks(session, 2)
    _place, rec, origin = _place_with_two_types(session, tracks)

    tab = PlacesTab(tracks, _Controller(session))
    tab.load(tracks)
    assert sorted(_rows(tab._table)) == [("Abbey Road", "Origin"), ("Abbey Road", "Recording Location")]

    row = next(r for r in range(tab._table.rowCount()) if tab._table.item(r, 1).text() == "Origin")
    tab._remove_row(row)

    remaining = session.query(PlaceAssociation).all()
    assert {a.association_type_id for a in remaining} == {rec.association_type_id}
    assert len(remaining) == len(tracks)
    assert _rows(tab._table) == [("Abbey Road", "Recording Location")]
    assert origin.association_type_id not in {a.association_type_id for a in remaining}


def test_places_add_skips_existing_links(qapp, session):
    tracks = _tracks(session, 2)
    _place_with_two_types(session, tracks)
    tab = PlacesTab(tracks, _Controller(session))
    tab.load(tracks)
    before = session.query(PlaceAssociation).count()

    tab._search.setText("Abbey Road")
    tab._type_edit.setText("Origin")
    tab._add()

    assert session.query(PlaceAssociation).count() == before
