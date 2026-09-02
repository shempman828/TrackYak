"""Regression test for #254: Album view filter/search settings should be
restored on the next session and persisted whenever they change.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from src.album import album_filtering as album_filtering_module, album_view as album_view_module
from src.album.album_flowlayout import FlowLayout
from src.album.album_view import _ALBUM_LIST_LOAD_OPTIONS, AlbumView
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables import Album, Artist, Role, Track
from src.db.db_tables.associations import AlbumRoleAssociation
from src.db.db_tables.base import Base
from src.foundation.config_setup import app_config


# ---- test_album_view_filter_persistence.py -----------------------------------
class StubAlbumWidget_fp(QWidget):
    clicked = Signal(object)

    def __init__(self, album, size=200, parent=None):
        super().__init__(parent)
        self.album = album

    def refresh_album(self, album):
        self.album = album


class StubAlbum_fp:
    def __init__(self, album_id, album_name):
        self.album_id = album_id
        self.album_name = album_name
        self.release_year = None
        self.first_pass = False
        self.second_pass = False
        self.possibly_incomplete = False


class StubGetController_fp:
    def __init__(self, albums_by_id):
        self._albums_by_id = albums_by_id

    def get_all_entities(self, entity_type, load_options=None):
        return list(self._albums_by_id.values())


class StubController_fp:
    def __init__(self, albums_by_id):
        self.get = StubGetController_fp(albums_by_id)


class FakeAppConfig:
    """In-memory stand-in for app_config so tests never touch config.ini."""

    def __init__(self, initial=None):
        self._filters = dict(initial or {})
        self.save_calls = 0

    def get_album_view_filters(self):
        return dict(self._filters)

    def set_album_view_filters(self, filters):
        self._filters = dict(filters)

    def save(self):
        self.save_calls += 1


def _make_view_fp(monkeypatch, albums, fake_config):
    monkeypatch.setattr(album_view_module, "AlbumWidget", StubAlbumWidget_fp)
    monkeypatch.setattr(album_filtering_module, "app_config", fake_config)
    albums_by_id = {a.album_id: a for a in albums}
    view = AlbumView(StubController_fp(albums_by_id))
    view.show()
    return view


def test_restores_filters_from_previous_session(qapp, monkeypatch):
    fake_config = FakeAppConfig(
        {
            "search": "alpha",
            "year_from": 1990,
            "year_to": 1999,
            "min_tracks": 5,
            "incomplete_mode": "Possibly Incomplete",
            "fixed_mode": "Second Pass",
            "art_mode": "Has Art",
        }
    )
    albums = [StubAlbum_fp(1, "Alpha")]
    view = _make_view_fp(monkeypatch, albums, fake_config)
    try:
        assert view.search_bar.text() == "alpha"
        assert view.year_from.value() == 1990
        assert view.year_to.value() == 1999
        assert view.min_tracks.value() == 5
        assert view.incomplete_combo.currentText() == "Possibly Incomplete"
        assert view.fixed_combo.currentText() == "Second Pass"
        assert view.art_combo.currentText() == "Has Art"
    finally:
        view.close()


def test_no_persisted_state_keeps_defaults(qapp, monkeypatch):
    fake_config = FakeAppConfig()
    albums = [StubAlbum_fp(1, "Alpha")]
    view = _make_view_fp(monkeypatch, albums, fake_config)
    try:
        assert view.search_bar.text() == ""
        assert view.year_from.value() == 0
        assert view.incomplete_combo.currentText() == "Any"
    finally:
        view.close()


def test_filter_change_persists_to_config(qapp, monkeypatch):
    fake_config = FakeAppConfig()
    albums = [StubAlbum_fp(1, "Alpha")]
    view = _make_view_fp(monkeypatch, albums, fake_config)
    try:
        view.search_bar.setText("beta")
        view.fixed_combo.setCurrentText("Second Pass")

        # Force the debounced save to run immediately instead of waiting
        # out its 400ms timer.
        view._filter_save_timer.stop()
        view._save_filter_state()

        saved = fake_config.get_album_view_filters()
        assert saved["search"] == "beta"
        assert saved["fixed_mode"] == "Second Pass"
        assert fake_config.save_calls >= 1
    finally:
        view.close()


def _select_sort_fp(view, criteria, descending):
    """Pick a sort combo entry by its (criteria, descending) data, going
    through the real currentIndexChanged -> _on_sort_changed path."""
    model = view.sort_combo.model()
    for i in range(model.rowCount()):
        if model.item(i).data(Qt.UserRole) == (criteria, descending):
            view.sort_combo.setCurrentIndex(i)
            return
    raise AssertionError(f"no sort option for {(criteria, descending)}")


def test_restores_sort_from_previous_session(qapp, monkeypatch):
    fake_config = FakeAppConfig({"search": "", "sort_criteria": "year", "sort_descending": True})
    albums = [StubAlbum_fp(1, "Alpha")]
    view = _make_view_fp(monkeypatch, albums, fake_config)
    try:
        assert view._sort_criteria == "year"
        assert view._sort_descending is True
        assert view.sort_combo.currentText().strip() == "Year (Newest First)"
    finally:
        view.close()


def test_restores_sort_ignores_unknown_criteria(qapp, monkeypatch):
    fake_config = FakeAppConfig({"sort_criteria": "bogus", "sort_descending": True})
    albums = [StubAlbum_fp(1, "Alpha")]
    view = _make_view_fp(monkeypatch, albums, fake_config)
    try:
        assert view._sort_criteria == "title"
        assert view._sort_descending is False
    finally:
        view.close()


def test_sort_change_persists_to_config(qapp, monkeypatch):
    fake_config = FakeAppConfig()
    albums = [StubAlbum_fp(1, "Alpha")]
    view = _make_view_fp(monkeypatch, albums, fake_config)
    try:
        _select_sort_fp(view, "year", False)

        view._filter_save_timer.stop()
        view._save_filter_state()

        saved = fake_config.get_album_view_filters()
        assert saved["sort_criteria"] == "year"
        assert saved["sort_descending"] is False
    finally:
        view.close()


# ---- test_album_view_filter_removal.py ---------------------------------------
# Regression test for #251: an album edited so it no longer satisfies the
# active grid filter (e.g. first_pass flips while filtering "Not Started") must
# be removed from the grid in place, without a full grid rebuild.
#
# Also covers #260: that same in-place removal must not leave lazy-loading
# stuck. It used to read the scroll bar's maximum synchronously right after
# removing the widget, but QScrollArea only recomputes that range on a later
# event-loop turn, so the read was always stale. When the removal happened to
# make the grid exactly fill the viewport (no more scrollbar), the stale read
# still saw the old, positive maximum and skipped topping up the view -- and
# with no scrollbar left to scroll, nothing could ever trigger loading the
# remaining filtered albums again.
@pytest.fixture(autouse=True)
def isolated_app_config(monkeypatch):
    """AlbumView reads/writes filter state through the real, process-wide
    app_config singleton (backed by the machine-local config/config.ini):
    _restore_filter_state() on construction, and a debounced timer that
    calls app_config.save() after filter changes. Neutralize both directions
    so these tests don't inherit a leftover min_tracks filter from a real
    app run (making StubAlbums -- which have no tracks -- fail the filter
    before the test's own filter setup even runs) and don't write test
    state into the user's real config file.
    """
    monkeypatch.setattr(app_config, "get_album_view_filters", lambda: {})
    monkeypatch.setattr(app_config, "set_album_view_filters", lambda state: None)
    monkeypatch.setattr(app_config, "save", lambda: None)


class StubAlbumWidget_fr(QWidget):
    clicked = Signal(object)

    def __init__(self, album, size=200, parent=None):
        super().__init__(parent)
        self.album = album
        self.setFixedSize(900, 150)

    def refresh_album(self, album):
        self.album = album


class StubAlbum_fr:
    def __init__(self, album_id, album_name, first_pass=False, second_pass=False):
        self.album_id = album_id
        self.album_name = album_name
        self.first_pass = first_pass
        self.second_pass = second_pass
        self.release_year = None
        self.possibly_incomplete = False


class StubGetController_fr:
    def __init__(self, albums_by_id):
        self._albums_by_id = albums_by_id

    def get_all_entities(self, entity_type, load_options=None):
        return list(self._albums_by_id.values())

    def get_entity_object(self, entity_type, album_id, load_options=None):
        return self._albums_by_id[album_id]


class StubController_fr:
    def __init__(self, albums_by_id):
        self.get = StubGetController_fr(albums_by_id)


def _make_view_fr(monkeypatch, albums):
    monkeypatch.setattr("src.album.album_view.AlbumWidget", StubAlbumWidget_fr)
    albums_by_id = {a.album_id: a for a in albums}
    view = AlbumView(StubController_fr(albums_by_id))
    view.show()
    view.load_albums()
    return view, albums_by_id


def test_edit_that_fails_active_filter_removes_widget_without_full_reload(qapp, monkeypatch):
    albums = [StubAlbum_fr(1, "Alpha"), StubAlbum_fr(2, "Beta")]
    view, albums_by_id = _make_view_fr(monkeypatch, albums)
    try:
        view.fixed_combo.setCurrentText("Not Started")
        assert [a.album_id for a in view.filtered_albums] == [1, 2]
        assert view.grid_layout.count() == 2

        # Neither full-reload path should fire for this edit.
        def _fail(*args, **kwargs):
            raise AssertionError("full grid reload should not be triggered")

        monkeypatch.setattr(view, "load_albums", _fail)
        monkeypatch.setattr(view, "_apply_filters_preserve_scroll", _fail)

        # Simulate the edit: album 1 starts its first pass, no longer
        # matching the "Not Started" filter.
        albums_by_id[1].first_pass = True
        view._patch_album_after_edit(1)

        assert [a.album_id for a in view.filtered_albums] == [2]
        assert view.grid_layout.count() == 1
        remaining = view.grid_layout.itemAt(0).widget()
        assert remaining.album.album_id == 2
    finally:
        view.close()


def test_edit_that_still_matches_filter_keeps_widget(qapp, monkeypatch):
    albums = [StubAlbum_fr(1, "Alpha"), StubAlbum_fr(2, "Beta")]
    view, albums_by_id = _make_view_fr(monkeypatch, albums)
    try:
        view.fixed_combo.setCurrentText("Not Started")
        assert view.grid_layout.count() == 2

        albums_by_id[1].album_name = "Alpha Renamed"
        view._patch_album_after_edit(1)

        assert [a.album_id for a in view.filtered_albums] == [1, 2]
        assert view.grid_layout.count() == 2
    finally:
        view.close()


def test_removal_that_fills_viewport_does_not_strand_remaining_albums(qapp, monkeypatch):
    """#260: if the in-place removal happens to make the grid exactly fill
    the viewport (no scrollbar left), the filtered albums beyond the display
    window must still eventually load via the deferred viewport-fill check,
    not get stuck forever.
    """
    monkeypatch.setattr("src.album.album_view.AlbumWidget", StubAlbumWidget_fr)

    # 12 albums, zero-padded names so title-sort order matches album_id order.
    albums = [StubAlbum_fr(i, f"Album {i:03d}") for i in range(1, 13)]
    albums_by_id = {a.album_id: a for a in albums}
    view = AlbumView(StubController_fr(albums_by_id))
    try:
        view.load_chunk = 10  # display_count starts at 10, leaving 2 unloaded

        view.resize(1100, 900)
        view.show()
        qapp.processEvents()

        # 10 rows of the 900x150 stub widgets (150 + 20 v-spacing each) need
        # 1680px; 9 rows need 1510px. Fixing the viewport at 1520px means
        # showing 10 albums overflows (real scrollbar) but showing 9 exactly
        # fits (no scrollbar) -- the exact edge case that exposed the bug.
        view.scroll_area.setFixedHeight(1520)
        qapp.processEvents()

        view.load_albums()
        QTest.qWait(150)

        view.fixed_combo.setCurrentText("Not Started")
        QTest.qWait(150)
        assert view.display_count == 10
        assert len(view.filtered_albums) == 12

        # Edit the last currently-displayed album so it drops out of the
        # "Not Started" filter, shrinking the grid down to exactly 9 rows.
        target = view.filtered_albums[9]
        albums_by_id[target.album_id].first_pass = True
        view._patch_album_after_edit(target.album_id)

        assert len(view.filtered_albums) == 11
        assert view.display_count == 9

        QTest.qWait(300)

        assert view.display_count == len(view.filtered_albums) == 11
        assert view.grid_layout.count() == 11
    finally:
        view.close()


# ---- test_album_view_lazy_load.py --------------------------------------------
# Regression tests for bug #235: two albums sharing a grid slot on first load.
#
# Root cause: a freshly reparented widget's "show" is deferred to the next
# event-loop turn, so QWidget.isVisible() is still False if a layout pass runs
# synchronously right after addWidget(). FlowLayout._do_layout() skips
# positioning any item whose widget reports isVisible() == False, so such a
# widget is left stuck at its default geometry -- overlapping whatever sits at
# slot (0, 0) -- until some later, fully-settled layout pass (e.g. the one
# triggered by scrolling) recomputes every widget's position from scratch.
def test_flow_layout_skips_unshown_widget_on_synchronous_layout_pass(qapp):
    """Documents the underlying FlowLayout hazard the fix works around."""
    parent = QWidget()
    parent.resize(600, 400)
    parent.show()
    layout = FlowLayout(parent)

    shown = QWidget(parent)
    shown.setFixedSize(50, 50)
    layout.addWidget(shown)
    shown.show()

    unshown = QWidget(parent)
    unshown.setFixedSize(50, 50)
    layout.addWidget(unshown)
    # Note: unshown.show() is deliberately NOT called here -- its "shown"
    # state hasn't propagated yet, mirroring a widget added moments before
    # a forced synchronous layout/repaint pass.

    layout.setGeometry(parent.rect())

    # The unshown widget was skipped by _do_layout()'s isVisible() check, so
    # it never received a real geometry and is left overlapping the first
    # slot instead of taking its rightful second-in-row position -- this is
    # the "two albums in one grid slot" symptom from bug #235.
    assert unshown.geometry() == shown.geometry()

    # Once the widget is actually visible, the next full layout pass (e.g.
    # the one triggered by scrolling in AlbumView) positions it correctly --
    # matching the observed "self-heals after the first scroll" behavior.
    unshown.show()
    layout.invalidate()
    layout.setGeometry(parent.rect())

    assert unshown.geometry() != shown.geometry()


def test_add_album_widget_shows_widget_immediately(qapp, monkeypatch):
    """AlbumView._add_album_widget must show() each widget synchronously so
    the very next layout pass (even one forced before the event loop has
    caught up) positions it correctly instead of skipping it.
    """

    class StubAlbumWidget(QWidget):
        clicked = Signal(object)

        def __init__(self, album, size=200, parent=None):
            super().__init__(parent)
            self.album = album

    monkeypatch.setattr("src.album.album_view.AlbumWidget", StubAlbumWidget)

    class StubGetController:
        def get_all_entities(self, entity_type, load_options=None):
            return []

    class StubController:
        def __init__(self):
            self.get = StubGetController()

    view = AlbumView(StubController())
    try:
        # isVisible() reflects the whole ancestor chain, so the view itself
        # must already be shown -- exactly as it is in production by the
        # time main_window.py adds it to the (visible) stacked widget and
        # forces a synchronous repaint().
        view.show()

        view._add_album_widget(object())

        added = view.grid_layout.itemAt(view.grid_layout.count() - 1).widget()
        # No qapp.processEvents() call here: the assertion must hold
        # immediately, without waiting for a future event-loop turn.
        assert added.isVisible() is True
    finally:
        view.close()


# ---- test_album_view_search_eager_load.py ------------------------------------
# Regression: searching / non-default sorting the album view must not fire a
# lazy-load per album.
#
# AlbumView.load_albums() eager-loads (via _ALBUM_LIST_LOAD_OPTIONS) the
# relationships that the search predicate (album_filtering._album_matches_filters)
# and the sort keys (album_sorting._sort_key) walk for every album. Without that,
# a single search over a large library issues 1 + 2N SELECTs on the Qt main
# thread and freezes the UI for seconds -- reported as "thread lock while album
# searching in album view".
@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    # Match the real app engine (src/db/db_engine.py): a plain commit must not
    # expire already-loaded attributes/collections, or the eager loading this
    # test checks would be undone by get_all_entities()' own read-txn commit().
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    yield s
    s.close()


def _populate(session, n_albums=20, artists_per=2, tracks_per=5):
    role = Role(role_name="Album Artist")
    session.add(role)
    session.flush()
    for i in range(n_albums):
        album = Album(album_name=f"Album {i:03d}", release_year=1990 + i)
        session.add(album)
        session.flush()
        for j in range(artists_per):
            artist = Artist(artist_name=f"Artist {i:03d}-{j}")
            session.add(artist)
            session.flush()
            session.add(
                AlbumRoleAssociation(
                    album_id=album.album_id,
                    artist_id=artist.artist_id,
                    role_id=role.role_id,
                    sort_order=j,
                )
            )
        for t in range(tracks_per):
            session.add(Track(track_name=f"T{t}", album_id=album.album_id))
    session.commit()


def _simulate_search_and_sort(albums):
    """Touch exactly what _album_matches_filters (text search) and _sort_key
    (Artist / Track Count sort) read from each album."""
    for album in albums:
        # -> album.album_roles, assoc.artist, assoc.role.role_name
        AlbumView._get_artist_names(album)
        # -> album.tracks
        AlbumView._get_track_count(album)
        _ = (album.album_name, album.release_year)


def _count_sql(engine, fn):
    counter = {"n": 0}

    def _before(*_args, **_kwargs):
        counter["n"] += 1

    event.listen(engine, "before_cursor_execute", _before)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", _before)
    return counter["n"]


def test_eager_load_options_eliminate_per_album_queries(session):
    _populate(session)
    getter = GetFromDB(session)

    albums = getter.get_all_entities("Album", load_options=_ALBUM_LIST_LOAD_OPTIONS)
    assert len(albums) == 20

    n = _count_sql(session.bind, lambda: _simulate_search_and_sort(albums))
    assert n == 0, f"search+sort pass issued {n} lazy queries; expected 0"


def test_without_eager_load_options_the_pass_storms(session):
    """Guard for the test above: the same pass without the load options really
    does fire a per-album lazy-load storm, so the assertion has teeth."""
    _populate(session)
    getter = GetFromDB(session)

    albums = getter.get_all_entities("Album")  # no load_options

    n = _count_sql(session.bind, lambda: _simulate_search_and_sort(albums))
    assert n > 20, f"expected an N+1 storm without eager loading, got {n} queries"


# ---- album type / media format filters -------------------------------------
# Feature: the Album view's filter row gains "Type:" (release_type) and
# "Media:" (media_format) drop-downs, populated from the loaded library.
class StubAlbum_tm:
    def __init__(self, album_id, album_name, release_type=None, media_format=None):
        self.album_id = album_id
        self.album_name = album_name
        self.release_type = release_type
        self.media_format = media_format
        self.release_year = None
        self.first_pass = False
        self.second_pass = False
        self.possibly_incomplete = False


def _combo_items(combo):
    return [combo.itemText(i) for i in range(combo.count())]


def test_type_filter_restricts_grid_to_matching_release_type(qapp, monkeypatch):
    albums = [
        StubAlbum_tm(1, "Live One", release_type="Live"),
        StubAlbum_tm(2, "Studio One", release_type="Album"),
        StubAlbum_tm(3, "Live Two", release_type="Live"),
    ]
    view = _make_view_fp(monkeypatch, albums, FakeAppConfig())
    try:
        view.type_combo.setCurrentText("Live")
        assert sorted(a.album_id for a in view.filtered_albums) == [1, 3]

        view.type_combo.setCurrentText("Any")
        assert sorted(a.album_id for a in view.filtered_albums) == [1, 2, 3]
    finally:
        view.close()


def test_media_filter_restricts_grid_to_matching_media_format(qapp, monkeypatch):
    albums = [
        StubAlbum_tm(1, "A", media_format="CD"),
        StubAlbum_tm(2, "B", media_format='12" Vinyl'),
        StubAlbum_tm(3, "C", media_format="CD"),
    ]
    view = _make_view_fp(monkeypatch, albums, FakeAppConfig())
    try:
        view.media_combo.setCurrentText("CD")
        assert sorted(a.album_id for a in view.filtered_albums) == [1, 3]

        view.media_combo.setCurrentText("Any")
        assert sorted(a.album_id for a in view.filtered_albums) == [1, 2, 3]
    finally:
        view.close()


def test_type_media_combos_populated_sorted_deduped_no_blanks(qapp, monkeypatch):
    albums = [
        StubAlbum_tm(1, "A", release_type="Live", media_format="CD"),
        StubAlbum_tm(2, "B", release_type="Album", media_format="CD"),
        StubAlbum_tm(3, "C", release_type="Live", media_format=""),
        StubAlbum_tm(4, "D", release_type=None, media_format="Cassette"),
    ]
    view = _make_view_fp(monkeypatch, albums, FakeAppConfig())
    try:
        assert _combo_items(view.type_combo) == ["Any", "Album", "Live"]
        assert _combo_items(view.media_combo) == ["Any", "Cassette", "CD"]
    finally:
        view.close()


def test_type_media_selection_persisted_and_restored(qapp, monkeypatch):
    fake_config = FakeAppConfig()
    albums = [
        StubAlbum_tm(1, "A", release_type="Live", media_format="CD"),
        StubAlbum_tm(2, "B", release_type="Album", media_format="Cassette"),
    ]
    view = _make_view_fp(monkeypatch, albums, fake_config)
    try:
        view.type_combo.setCurrentText("Live")
        view.media_combo.setCurrentText("Cassette")
        view._filter_save_timer.stop()
        view._save_filter_state()
        saved = fake_config.get_album_view_filters()
        assert saved["type_mode"] == "Live"
        assert saved["media_mode"] == "Cassette"
    finally:
        view.close()

    view2 = _make_view_fp(monkeypatch, albums, fake_config)
    try:
        assert view2.type_combo.currentText() == "Live"
        assert view2.media_combo.currentText() == "Cassette"
        assert sorted(a.album_id for a in view2.filtered_albums) == []
    finally:
        view2.close()


def test_restored_type_value_absent_from_library_falls_back_to_any(qapp, monkeypatch):
    fake_config = FakeAppConfig({"type_mode": "Bootleg", "media_mode": "8-Track"})
    albums = [StubAlbum_tm(1, "A", release_type="Album", media_format="CD")]
    view = _make_view_fp(monkeypatch, albums, fake_config)
    try:
        assert view.type_combo.currentText() == "Any"
        assert view.media_combo.currentText() == "Any"
        assert [a.album_id for a in view.filtered_albums] == [1]
    finally:
        view.close()


def test_clear_filters_resets_type_and_media_combos(qapp, monkeypatch):
    albums = [
        StubAlbum_tm(1, "A", release_type="Live", media_format="CD"),
        StubAlbum_tm(2, "B", release_type="Album", media_format="Cassette"),
    ]
    view = _make_view_fp(monkeypatch, albums, FakeAppConfig())
    try:
        view.type_combo.setCurrentText("Live")
        view.media_combo.setCurrentText("CD")
        view._clear_filters()
        assert view.type_combo.currentText() == "Any"
        assert view.media_combo.currentText() == "Any"
        assert sorted(a.album_id for a in view.filtered_albums) == [1, 2]
    finally:
        view.close()
