"""Tests for the album editor's "Track Places" section — places common to
every track on the album, surfaced (and edited, trickle-down) inside the
Publishers & Places tab. Mirrors the Track Credits tab pattern but reuses
src/track/track_edit_places.py's PlacesTab verbatim.

See docs/specs/album-edit-track-places.md. Acceptance criteria 1-9 map 1:1
to the tests below.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QGroupBox, QLabel

from src.album.album_tab import AlbumTabBuilder
from src.common.entity_completer_edit import invalidate_entity_cache
from src.track.track_edit_places import PlacesTab as TrackPlacesTab

# ---------------------------------------------------------------------------
# Stateful fake controller / DB
# ---------------------------------------------------------------------------


class _FakeDB:
    def __init__(self):
        self.places = {}          # place_id -> ns(place_id, place_name, MBID)
        self.assoc_types = {}     # id -> ns(association_type_id, type_name)
        self.place_assocs = []    # ns(association_id, entity_id, entity_type, ...)
        self._next = {"place": 1, "type": 1, "assoc": 1}

    # -- lookup surface -----------------------------------------------------

    def rows_for(self, model_name):
        if model_name == "Place":
            return list(self.places.values())
        if model_name == "PlaceAssociationType":
            return list(self.assoc_types.values())
        return []  # AlbumPublisher, etc.

    # -- mutation helpers -------------------------------------------------- --

    def add_place(self, name):
        pid = self._next["place"]
        self._next["place"] += 1
        p = SimpleNamespace(place_id=pid, place_name=name, MBID=None)
        self.places[pid] = p
        return p

    def _ensure_place(self, name):
        for p in self.places.values():
            if p.place_name.lower() == name.lower():
                return p
        return self.add_place(name)

    def add_assoc_type(self, name):
        tid = self._next["type"]
        self._next["type"] += 1
        t = SimpleNamespace(association_type_id=tid, type_name=name)
        self.assoc_types[tid] = t
        return t

    def _ensure_type(self, name):
        if not name:
            return None
        for t in self.assoc_types.values():
            if t.type_name.lower() == name.lower():
                return t
        return self.add_assoc_type(name)

    def add_place_assoc(self, entity_id, entity_type, place_id,
                        association_type_id=None):
        aid = self._next["assoc"]
        self._next["assoc"] += 1
        self.place_assocs.append(SimpleNamespace(
            association_id=aid,
            entity_id=entity_id,
            entity_type=entity_type,
            place_id=place_id,
            association_type_id=association_type_id,
            association_type=self.assoc_types.get(association_type_id),
        ))

    def delete_by_assoc_ids(self, assoc_ids):
        ids = set(assoc_ids)
        self.place_assocs = [
            a for a in self.place_assocs if a.association_id not in ids
        ]

    # -- test convenience -------------------------------------------------- --

    def link(self, track_id, place_name, type_name=None):
        """Directly associate a place (by name) with one track."""
        p = self._ensure_place(place_name)
        t = self._ensure_type(type_name)
        self.add_place_assoc(track_id, "Track", p.place_id,
                             t.association_type_id if t else None)


class _FakeGet:
    def __init__(self, db):
        self.db = db

    def count_entities(self, model_name):
        return len(self.db.rows_for(model_name))

    def get_all_entities(self, model_name, **_kwargs):
        return list(self.db.rows_for(model_name))

    def get_entity_links(self, model_name, entity_id=None, entity_type=None, **_kw):
        assert model_name == "PlaceAssociation"
        return [
            a for a in self.db.place_assocs
            if a.entity_id == entity_id and a.entity_type == entity_type
        ]

    def get_entity_object(self, model_name, **filters):
        if model_name == "Place":
            return self.db.places.get(filters.get("place_id"))
        return None


class _FakeAdd:
    def __init__(self, db):
        self.db = db

    def add_entity(self, model_name, **kwargs):
        if model_name == "Place":
            return self.db.add_place(kwargs["place_name"])
        if model_name == "PlaceAssociationType":
            return self.db.add_assoc_type(kwargs["type_name"])
        raise AssertionError(f"unexpected add_entity({model_name})")

    def add_entities(self, model_name, rows):
        assert model_name == "PlaceAssociation"
        for r in rows:
            self.db.add_place_assoc(**r)


class _FakeDelete:
    def __init__(self, db):
        self.db = db

    def delete_entity(self, model_name, entity_ids=None, **kwargs):
        assert model_name == "PlaceAssociation"
        assert entity_ids is not None, (
            "track-place removal must batch-delete by association_id"
        )
        self.db.delete_by_assoc_ids(entity_ids)
        return True


class _FakeController:
    def __init__(self, db):
        self.get = _FakeGet(db)
        self.add = _FakeAdd(db)
        self.delete = _FakeDelete(db)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_entity_cache(qapp):
    # get_cached_entities caches per-model at module scope; each test builds
    # a fresh fake DB, so the cache must not leak between them.
    invalidate_entity_cache()
    yield
    # PlacesTab._add() defers an EntityCompleterEdit.add_to_index() via
    # QTimer.singleShot(0, ...); drain it now while the widget is still
    # alive, otherwise it fires mid-teardown of a later test on a
    # already-deleted C++ object.
    qapp.processEvents()
    invalidate_entity_cache()


@pytest.fixture
def db():
    return _FakeDB()


def _track(track_id):
    return SimpleNamespace(track_id=track_id)


def _make_builder(db, tracks):
    album = SimpleNamespace(album_id=1, tracks=list(tracks))
    helper = SimpleNamespace(
        add_publisher=lambda *a: None,
        remove_publisher=lambda *a: None,
        add_place=lambda *a: None,
        remove_place=lambda *a: None,
    )
    view = SimpleNamespace(
        album=album,
        controller=_FakeController(db),
        helper=helper,
        get_album_place_associations=list,
    )
    return AlbumTabBuilder(view)


def _places_widget(section):
    return section.findChild(TrackPlacesTab)


def _table_rows(widget):
    table = widget._table
    return [
        (table.item(r, 0).text(), table.item(r, 1).text())
        for r in range(table.rowCount())
    ]


# ---------------------------------------------------------------------------
# 1. build_relationships_tab exposes the section
# ---------------------------------------------------------------------------


def test_relationships_tab_includes_track_places_group(qapp, db):
    builder = _make_builder(db, [_track(1)])
    tab = builder.build_relationships_tab()
    titles = {g.title() for g in tab.findChildren(QGroupBox)}
    assert "Track Places (common to all tracks)" in titles
    # still alongside the pre-existing album-level sections
    assert {"Publishers", "Place Associations"} <= titles


# ---------------------------------------------------------------------------
# 2. common place (with type) is listed
# ---------------------------------------------------------------------------


def test_place_common_to_all_tracks_is_listed_with_type(qapp, db):
    tracks = [_track(1), _track(2)]
    db.link(1, "Abbey Road", "Recording Location")
    db.link(2, "Abbey Road", "Recording Location")

    section = _make_builder(db, tracks)._build_track_places_section()
    widget = _places_widget(section)

    assert _table_rows(widget) == [("Abbey Road", "Recording Location")]


# ---------------------------------------------------------------------------
# 3. place on only some tracks is NOT listed (intersection, not union)
# ---------------------------------------------------------------------------


def test_place_on_subset_of_tracks_is_not_listed(qapp, db):
    tracks = [_track(1), _track(2)]
    db.link(1, "Abbey Road", "Recording Location")  # track 1 only

    section = _make_builder(db, tracks)._build_track_places_section()

    assert _table_rows(_places_widget(section)) == []


# ---------------------------------------------------------------------------
# 4. adding a place writes it to every track
# ---------------------------------------------------------------------------


def test_adding_place_writes_association_to_every_track(qapp, db):
    tracks = [_track(1), _track(2), _track(3)]
    section = _make_builder(db, tracks)._build_track_places_section()
    widget = _places_widget(section)

    widget._search.setText("Studio X")
    widget._type_edit.setText("Recording Location")
    widget._add()
    qapp.processEvents()  # flush _add()'s deferred add_to_index()

    assert {(a.entity_id, a.entity_type) for a in db.place_assocs} == {
        (1, "Track"), (2, "Track"), (3, "Track")
    }
    assert len({a.place_id for a in db.place_assocs}) == 1
    assert all(
        a.association_type.type_name == "Recording Location"
        for a in db.place_assocs
    )
    # and it now shows as common
    assert _table_rows(widget) == [("Studio X", "Recording Location")]


# ---------------------------------------------------------------------------
# 5. removing a listed place removes it from every track
# ---------------------------------------------------------------------------


def test_removing_place_deletes_association_from_every_track(qapp, db):
    tracks = [_track(1), _track(2)]
    db.link(1, "Abbey Road", "Recording Location")
    db.link(2, "Abbey Road", "Recording Location")

    section = _make_builder(db, tracks)._build_track_places_section()
    widget = _places_widget(section)
    assert _table_rows(widget) == [("Abbey Road", "Recording Location")]

    widget._remove_row(0)

    assert db.place_assocs == []
    assert _table_rows(widget) == []


# ---------------------------------------------------------------------------
# 6. album with no tracks -> label, no embedded widget
# ---------------------------------------------------------------------------


def test_no_tracks_shows_label_and_omits_widget(qapp, db):
    section = _make_builder(db, [])._build_track_places_section()

    assert _places_widget(section) is None
    label_text = " ".join(
        lbl.text().lower() for lbl in section.findChildren(QLabel)
    )
    assert "no tracks" in label_text


def test_no_tracks_case_also_holds_through_relationships_tab(qapp, db):
    tab = _make_builder(db, []).build_relationships_tab()
    assert tab.findChild(TrackPlacesTab) is None


# ---------------------------------------------------------------------------
# 7. section rebuilds against the album's current track set
# ---------------------------------------------------------------------------


def test_section_reflects_current_track_set_on_rebuild(qapp, db):
    t1, t2, t3 = _track(1), _track(2), _track(3)
    db.link(1, "Abbey Road", "Recording Location")
    db.link(2, "Abbey Road", "Recording Location")
    # t3 has no such place -> not common while it's on the album

    builder = _make_builder(db, [t1, t2, t3])
    section = builder._build_track_places_section()
    assert _table_rows(_places_widget(section)) == []

    # simulate a track removed on the Tracks tab + refresh_view() rebuild
    builder.album.tracks = [t1, t2]
    section2 = builder._build_track_places_section()
    assert _table_rows(_places_widget(section2)) == [
        ("Abbey Road", "Recording Location")
    ]


# ---------------------------------------------------------------------------
# 8. track-place edits never touch album-level (entity_type="Album") rows
# ---------------------------------------------------------------------------


def test_track_place_edits_only_write_track_scoped_rows(qapp, db):
    tracks = [_track(1), _track(2)]
    section = _make_builder(db, tracks)._build_track_places_section()
    widget = _places_widget(section)

    widget._search.setText("Studio X")
    widget._add()
    qapp.processEvents()  # flush _add()'s deferred add_to_index()
    assert db.place_assocs
    assert all(a.entity_type == "Track" for a in db.place_assocs)

    widget._remove_row(0)
    assert all(a.entity_type == "Track" for a in db.place_assocs)


# ---------------------------------------------------------------------------
# 9. help_guide documents the section
# ---------------------------------------------------------------------------


def test_help_guide_documents_track_places_section():
    text = Path(__file__).resolve().parents[2] / "help_guide.md"
    assert "Track Places" in text.read_text()
