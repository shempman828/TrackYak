"""Tests for src/artist/edit/artist_edit_tags.py::_TagTypeSection/ArtistTagsTab.

Covers docs/specs/artist_tags_tab.md AC5-AC8: the Tags tab builds one
section per TagType in (sort_order, type_name) order; within a section,
assigned tags are removable chips and unassigned tags of that type are
click-to-add suggestion pills; typing a new name into the search box still
finds-or-creates and assigns it; a type with no tags yet shows an empty
suggestion state rather than erroring.

Runs against a real in-memory DB through the actual db_helpers (not fakes),
since add/remove go through find-or-create + association inserts/deletes
that are worth exercising for real.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

from src.artist.edit.artist_edit_tags import ArtistTagsTab, _TagTypeSection
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


def _make_tag_type(controller, name="Religion", sort_order=0):
    session = controller.SessionFactory
    tag_type = TagType(type_name=name, sort_order=sort_order)
    session.add(tag_type)
    session.commit()
    return tag_type


def _make_tag(controller, tag_type, name, parent_id=None):
    session = controller.SessionFactory
    tag = Tag(tag_name=name, tag_type_id=tag_type.tag_type_id, parent_id=parent_id)
    session.add(tag)
    session.commit()
    return tag


# ── ArtistTagsTab: section layout ────────────────────────────────────────


def test_no_tag_types_shows_placeholder(qapp):
    controller = _Controller()
    artist = _make_artist(controller)
    tab = ArtistTagsTab(controller, artist)
    try:
        tab.load(artist)
        assert tab._no_types_label.isVisibleTo(tab)
        assert tab._sections == {}
    finally:
        tab.deleteLater()


def test_one_section_built_per_tag_type_in_sort_order(qapp):
    controller = _Controller()
    artist = _make_artist(controller)
    _make_tag_type(controller, "Religion", sort_order=1)
    _make_tag_type(controller, "Era", sort_order=0)

    tab = ArtistTagsTab(controller, artist)
    try:
        tab.load(artist)
        assert not tab._no_types_label.isVisibleTo(tab)
        titles = [tab._sections_layout.itemAt(i).widget().title() for i in range(2)]
        assert titles == ["Era", "Religion"]
    finally:
        tab.deleteLater()


# ── _TagTypeSection: search/add, reuse, remove ───────────────────────────


def test_add_creates_tag_and_chip(qapp):
    controller = _Controller()
    artist = _make_artist(controller)
    tag_type = _make_tag_type(controller, "Religion")
    section = _TagTypeSection(controller, artist, tag_type)
    try:
        section.load(artist)
        section._search.setText("Catholic")
        section._add()

        session = controller.SessionFactory
        session.expire_all()
        tag = session.query(Tag).filter_by(tag_name="Catholic").one()
        assert tag.tag_id in section._assigned_chips

        refreshed = session.get(Artist, artist.artist_id)
        assert [t.tag_name for t in refreshed.tags] == ["Catholic"]
    finally:
        section.deleteLater()


def test_add_reuses_existing_tag_of_same_name_and_type(qapp):
    controller = _Controller()
    artist = _make_artist(controller)
    tag_type = _make_tag_type(controller, "Religion")
    existing = _make_tag(controller, tag_type, "Jewish")

    section = _TagTypeSection(controller, artist, tag_type)
    try:
        section.load(artist)
        section._search.setText("Jewish")
        section._add()

        session = controller.SessionFactory
        session.expire_all()
        assert session.query(Tag).filter_by(tag_name="Jewish").count() == 1
        assert existing.tag_id in section._assigned_chips
    finally:
        section.deleteLater()


def test_remove_deletes_association_and_chip(qapp):
    controller = _Controller()
    artist = _make_artist(controller)
    tag_type = _make_tag_type(controller, "Religion")
    section = _TagTypeSection(controller, artist, tag_type)
    try:
        section.load(artist)
        section._search.setText("Catholic")
        section._add()
        session = controller.SessionFactory
        session.expire_all()
        tag_id = session.query(Tag).filter_by(tag_name="Catholic").one().tag_id

        section._remove(tag_id)

        session.expire_all()
        refreshed = session.get(Artist, artist.artist_id)
        assert refreshed.tags == []
        assert tag_id not in section._assigned_chips
    finally:
        section.deleteLater()


def test_chip_label_is_full_hierarchy_path(qapp):
    controller = _Controller()
    artist = _make_artist(controller)
    tag_type = _make_tag_type(controller, "Religion")
    christian = _make_tag(controller, tag_type, "Christian")
    catholic = _make_tag(controller, tag_type, "Catholic", parent_id=christian.tag_id)
    session = controller.SessionFactory
    artist.tags.append(catholic)
    session.commit()

    section = _TagTypeSection(controller, artist, tag_type)
    try:
        section.load(artist)
        chip = section._assigned_chips[catholic.tag_id]
        assert "Christian > Catholic" in chip.text()
    finally:
        section.deleteLater()


# ── _TagTypeSection: one-click suggestion pills ──────────────────────────


def test_unassigned_tags_of_this_type_show_as_suggestion_pills(qapp):
    controller = _Controller()
    artist = _make_artist(controller)
    tag_type = _make_tag_type(controller, "Religion")
    _make_tag(controller, tag_type, "Catholic")

    section = _TagTypeSection(controller, artist, tag_type)
    try:
        section.load(artist)
        assert not section._no_suggestions_label.isVisibleTo(section)
        assert len(section._suggestion_pills) == 1
        assert section._assigned_chips == {}
    finally:
        section.deleteLater()


def test_clicking_a_suggestion_pill_assigns_the_tag_without_typing(qapp):
    controller = _Controller()
    artist = _make_artist(controller)
    tag_type = _make_tag_type(controller, "Religion")
    tag = _make_tag(controller, tag_type, "Catholic")

    section = _TagTypeSection(controller, artist, tag_type)
    try:
        section.load(artist)
        assert section._search.text() == ""  # never typed anything

        section._add_existing(tag.tag_id)

        assert tag.tag_id not in section._suggestion_pills
        assert tag.tag_id in section._assigned_chips

        session = controller.SessionFactory
        session.expire_all()
        refreshed = session.get(Artist, artist.artist_id)
        assert [t.tag_name for t in refreshed.tags] == ["Catholic"]
    finally:
        section.deleteLater()


def test_removing_an_assigned_tag_moves_it_back_to_suggestions(qapp):
    controller = _Controller()
    artist = _make_artist(controller)
    tag_type = _make_tag_type(controller, "Religion")
    tag = _make_tag(controller, tag_type, "Catholic")
    session = controller.SessionFactory
    artist.tags.append(tag)
    session.commit()

    section = _TagTypeSection(controller, artist, tag_type)
    try:
        section.load(artist)
        assert tag.tag_id in section._assigned_chips
        assert tag.tag_id not in section._suggestion_pills

        section._remove(tag.tag_id)

        assert tag.tag_id not in section._assigned_chips
        assert tag.tag_id in section._suggestion_pills
    finally:
        section.deleteLater()


def test_tag_type_with_no_tags_shows_empty_suggestion_state(qapp):
    controller = _Controller()
    artist = _make_artist(controller)
    tag_type = _make_tag_type(controller, "Religion")

    section = _TagTypeSection(controller, artist, tag_type)
    try:
        section.load(artist)
        assert section._no_suggestions_label.isVisibleTo(section)
        assert section._suggestion_pills == {}
        assert section._search.isEnabled()
    finally:
        section.deleteLater()
