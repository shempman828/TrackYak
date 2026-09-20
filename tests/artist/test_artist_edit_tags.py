"""Tests for src/artist/edit/artist_edit_tags.py::ArtistTagsWidget.

Covers AC5: adding a tag (creating it on the fly if new), seeing it as a
chip labeled with its type and full hierarchy path, and removing it --
writes commit immediately, same as ArtistTypesWidget.

Runs against a real in-memory DB through the actual db_helpers (not fakes),
since the widget's add/remove paths go through find-or-create + association
inserts/deletes that are worth exercising for real.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

from src.artist.edit.artist_edit_tags import ArtistTagsWidget
from src.db.db_helpers import AddToDB, DeleteDB, GetFromDB, UpdateDB
from src.db.db_tables import Artist, Tag, TagType
from src.db.db_tables.base import Base


class _Controller:
    def __init__(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(engine)
        self.SessionFactory = scoped_session(sessionmaker(bind=engine))
        self.get = GetFromDB(self.SessionFactory)
        self.add = AddToDB(self.SessionFactory)
        self.update = UpdateDB(self.SessionFactory)
        self.delete = DeleteDB(self.SessionFactory)


def _make_artist(controller, name="Miles Davis"):
    session = controller.SessionFactory
    artist = Artist(artist_name=name)
    session.add(artist)
    session.commit()
    return artist


def _make_tag_type(controller, name="Religion"):
    session = controller.SessionFactory
    tag_type = TagType(type_name=name)
    session.add(tag_type)
    session.commit()
    return tag_type


def test_no_tag_types_shows_placeholder_and_disables_search(qapp):
    controller = _Controller()
    artist = _make_artist(controller)
    widget = ArtistTagsWidget(controller, artist)
    try:
        assert widget._no_types_label.isVisibleTo(widget)
        assert not widget._search.isEnabled()
    finally:
        widget.deleteLater()


def test_add_creates_tag_and_chip(qapp):
    controller = _Controller()
    artist = _make_artist(controller)
    _make_tag_type(controller, "Religion")
    widget = ArtistTagsWidget(controller, artist)
    try:
        widget.load(artist)
        widget._search.setText("Catholic")
        widget._add()

        session = controller.SessionFactory
        session.expire_all()
        tag = session.query(Tag).filter_by(tag_name="Catholic").one()
        assert tag.tag_type.type_name == "Religion"
        assert tag.tag_id in widget._chips

        refreshed = session.get(Artist, artist.artist_id)
        assert [t.tag_name for t in refreshed.tags] == ["Catholic"]
    finally:
        widget.deleteLater()


def test_add_reuses_existing_tag_of_same_name_and_type(qapp):
    controller = _Controller()
    artist = _make_artist(controller)
    tag_type = _make_tag_type(controller, "Religion")
    session = controller.SessionFactory
    existing = Tag(tag_name="Jewish", tag_type_id=tag_type.tag_type_id)
    session.add(existing)
    session.commit()
    existing_id = existing.tag_id

    widget = ArtistTagsWidget(controller, artist)
    try:
        widget.load(artist)
        widget._search.setText("Jewish")
        widget._add()

        session.expire_all()
        assert session.query(Tag).filter_by(tag_name="Jewish").count() == 1
        assert existing_id in widget._chips
    finally:
        widget.deleteLater()


def test_remove_deletes_association_and_chip(qapp):
    controller = _Controller()
    artist = _make_artist(controller)
    _make_tag_type(controller, "Religion")
    widget = ArtistTagsWidget(controller, artist)
    try:
        widget.load(artist)
        widget._search.setText("Catholic")
        widget._add()
        session = controller.SessionFactory
        session.expire_all()
        tag_id = session.query(Tag).filter_by(tag_name="Catholic").one().tag_id

        widget._remove(tag_id)

        session.expire_all()
        refreshed = session.get(Artist, artist.artist_id)
        assert refreshed.tags == []
        assert tag_id not in widget._chips
    finally:
        widget.deleteLater()


def test_chip_label_shows_type_and_full_hierarchy_path(qapp):
    controller = _Controller()
    artist = _make_artist(controller)
    tag_type = _make_tag_type(controller, "Religion")
    session = controller.SessionFactory
    christian = Tag(tag_name="Christian", tag_type_id=tag_type.tag_type_id)
    session.add(christian)
    session.commit()
    catholic = Tag(
        tag_name="Catholic", tag_type_id=tag_type.tag_type_id, parent_id=christian.tag_id
    )
    session.add(catholic)
    session.commit()
    artist.tags.append(catholic)
    session.commit()

    widget = ArtistTagsWidget(controller, artist)
    try:
        widget.load(artist)
        chip = widget._chips[catholic.tag_id]
        assert "Religion: Christian > Catholic" in chip.text()
    finally:
        widget.deleteLater()
