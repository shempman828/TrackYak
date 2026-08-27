"""Tests for bug/feature #236: a Track Credits tab on the album editor for
credits common to every track, plus the "convert to album-level credit"
button (the inverse of the existing "convert to per-track" button).

Covers:
  - RelationshipHelpers.convert_credit_to_album_level: the DB-write helper
    that deletes a shared per-track credit and creates an album-level one.
  - RolesTab's optional on_convert_to_album hook: the "-> Album" button
    that only appears when the hook is supplied.
  - TrackCreditsTab: the album-editor tab wiring RolesTab to album.tracks.
  - AlbumEditor._refresh_track_credits_tab: reloading the tab's RolesTab in
    place on refresh, instead of destroying/rebuilding the tab (see
    test_refresh_track_credits_tab_* below for the scroll-reset regression).
"""

from types import SimpleNamespace

from PySide6.QtWidgets import QPushButton, QTabWidget, QWidget

from src.album.album_editing_relationship_helpers import RelationshipHelpers
from src.album.base_album_edit import AlbumEditor
from src.album.base_album_edit_tabs import TrackCreditsTab
from src.track.track_edit_roles import RolesTab

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _StubTrack:
    def __init__(self, track_id):
        self.track_id = track_id
        self.artist_roles = []


class _StubRoleAssoc:
    """Stands in for both TrackArtistRole (lookup) and AlbumRoleAssociation
    (album_roles membership) results, since the tests only touch the
    attributes convert_credit_to_album_level actually reads."""

    def __init__(self, artist_id=None, role_id=None, sort_order=0, credited_alias_id=None):
        self.artist_id = artist_id
        self.role_id = role_id
        self.sort_order = sort_order
        self.credited_alias_id = credited_alias_id


class _StubAlbum:
    def __init__(self, tracks, album_roles=None, album_id=1):
        self.album_id = album_id
        self.tracks = tracks
        self.album_roles = album_roles or []


class _StubGet:
    """Minimal controller.get -- covers both the entity_completer_edit
    preload path (count_entities/get_all_entities) hit when RolesTab builds
    its artist-search widget, and the single-row lookup
    convert_credit_to_album_level uses to preserve credited_alias_id."""

    def __init__(self, entity_object_return=None):
        self.entity_object_return = entity_object_return
        self.get_entity_object_calls = []

    def get_entity_object(self, model_name, **filters):
        self.get_entity_object_calls.append((model_name, filters))
        return self.entity_object_return

    def get_all_entities(self, model_name, **kwargs):
        return []

    def count_entities(self, model_name):
        return 0


class _StubAdd:
    def __init__(self, add_entity_return="__default__"):
        self._add_entity_return = add_entity_return
        self.add_entity_calls = []

    def add_entity(self, model_name, **kwargs):
        self.add_entity_calls.append((model_name, kwargs))
        if self._add_entity_return == "__default__":
            return _StubRoleAssoc(**{k: v for k, v in kwargs.items() if k in ("artist_id", "role_id", "sort_order", "credited_alias_id")})
        return self._add_entity_return


class _StubDelete:
    def __init__(self):
        self.delete_entity_calls = []

    def delete_entity(self, model_name, **kwargs):
        self.delete_entity_calls.append((model_name, kwargs))
        return True


class _StubController:
    def __init__(self, get=None, add=None, delete=None):
        self.get = get or _StubGet()
        self.add = add or _StubAdd()
        self.delete = delete or _StubDelete()


# ---------------------------------------------------------------------------
# RelationshipHelpers.convert_credit_to_album_level
# ---------------------------------------------------------------------------


def test_convert_credit_to_album_level_creates_album_credit_and_removes_track_rows(qapp):
    tracks = [_StubTrack(1), _StubTrack(2), _StubTrack(3)]
    # An unrelated existing album-level credit sharing role_id=5, so the new
    # credit's sort_order should land after it (max + 1), not default to 0.
    existing_sibling = _StubRoleAssoc(artist_id=99, role_id=5, sort_order=0)
    album = _StubAlbum(tracks, album_roles=[existing_sibling], album_id=7)

    existing_track_row = _StubRoleAssoc(credited_alias_id=42)
    controller = _StubController(get=_StubGet(entity_object_return=existing_track_row))

    refresh_calls = []
    helper = RelationshipHelpers(
        controller, album, lambda: refresh_calls.append(True), widget=QWidget()
    )

    helper.convert_credit_to_album_level(artist_id=10, role_id=5)

    # New album-level credit created with the preserved alias and correct
    # sort_order (after the existing sibling in the same role group).
    assert len(controller.add.add_entity_calls) == 1
    model_name, kwargs = controller.add.add_entity_calls[0]
    assert model_name == "AlbumRoleAssociation"
    assert kwargs == {
        "album_id": 7,
        "artist_id": 10,
        "role_id": 5,
        "sort_order": 1,
        "credited_alias_id": 42,
    }

    # Per-track rows removed across every track in one filter-based delete.
    assert len(controller.delete.delete_entity_calls) == 1
    del_model, del_kwargs = controller.delete.delete_entity_calls[0]
    assert del_model == "TrackArtistRole"
    assert del_kwargs == {"track_id": [1, 2, 3], "artist_id": 10, "role_id": 5}

    assert refresh_calls == [True]


def test_convert_credit_to_album_level_noop_when_already_album_level(qapp, monkeypatch):
    status_calls = []
    monkeypatch.setattr(
        "src.album.album_editing_relationship_helpers.show_status_message",
        lambda widget, msg: status_calls.append(msg),
    )

    tracks = [_StubTrack(1)]
    already_there = _StubRoleAssoc(artist_id=10, role_id=5)
    album = _StubAlbum(tracks, album_roles=[already_there])
    controller = _StubController()
    refresh_calls = []
    helper = RelationshipHelpers(
        controller, album, lambda: refresh_calls.append(True), widget=QWidget()
    )

    helper.convert_credit_to_album_level(artist_id=10, role_id=5)

    assert controller.add.add_entity_calls == []
    assert controller.delete.delete_entity_calls == []
    assert refresh_calls == []
    assert status_calls  # a status message was shown explaining the no-op


def test_convert_credit_to_album_level_does_not_delete_tracks_when_add_fails(qapp, monkeypatch):
    """If creating the album-level credit fails, the per-track credit must
    be left alone -- otherwise the artist loses the credit everywhere."""
    critical_calls = []
    monkeypatch.setattr(
        "src.album.album_editing_relationship_helpers.QMessageBox.critical",
        lambda *args, **kwargs: critical_calls.append(args),
    )

    tracks = [_StubTrack(1), _StubTrack(2)]
    album = _StubAlbum(tracks, album_roles=[])
    controller = _StubController(add=_StubAdd(add_entity_return=None))
    refresh_calls = []
    helper = RelationshipHelpers(
        controller, album, lambda: refresh_calls.append(True), widget=QWidget()
    )

    helper.convert_credit_to_album_level(artist_id=10, role_id=5)

    assert len(controller.add.add_entity_calls) == 1
    assert controller.delete.delete_entity_calls == []
    assert refresh_calls == []
    assert critical_calls


# ---------------------------------------------------------------------------
# RolesTab on_convert_to_album hook
# ---------------------------------------------------------------------------


def test_roles_cell_has_no_convert_button_by_default(qapp):
    controller = _StubController()
    tab = RolesTab([_StubTrack(1)], controller)
    try:
        cell = tab._build_roles_cell(10, "Some Artist", {5: "Producer"})
        button_texts = [
            w.text() for w in cell.findChildren(QPushButton)
        ]
        assert not any("Album" in t for t in button_texts)
    finally:
        tab.cleanup()


def test_roles_cell_convert_button_invokes_hook_with_artist_and_role(qapp):
    controller = _StubController()
    calls = []
    tab = RolesTab(
        [_StubTrack(1)], controller, on_convert_to_album=lambda aid, rid: calls.append((aid, rid))
    )
    try:
        cell = tab._build_roles_cell(10, "Some Artist", {5: "Producer"})
        convert_btn = next(
            w for w in cell.findChildren(QPushButton) if "Album" in w.text()
        )
        convert_btn.click()
        assert calls == [(10, 5)]
    finally:
        tab.cleanup()


def test_roles_cell_height_hint_tracks_real_width_not_min_width(qapp):
    """Regression: the chip cell is a plain QWidget whose sizeHint() came
    from layout.totalSizeHint(), which evaluates the FlowLayout's
    heightForWidth() at its *minimum* (one-chip-wide) width -- stacking
    every chip onto its own line. QTableView sized the row from that hint,
    so an artist whose chips wrap to two lines got a row tall enough for
    ~8. The cell must report its height at its actual width instead, so
    resizeRowsToContents() converges to the real wrapped height.
    """
    controller = _StubController()
    tab = RolesTab([_StubTrack(1)], controller)
    try:
        roles = {i: f"Role {i}" for i in range(8)}
        cell = tab._build_roles_cell(10, "Some Artist", roles)
        layout = cell.layout()

        stacked = layout.heightForWidth(layout.sizeHint().width())  # min width
        cell.resize(560, 10)
        wrapped = layout.heightForWidth(560)

        assert wrapped < stacked, "test setup: 560px should wrap fewer lines"
        # The cell's hint (what the table uses for row height) follows the
        # real width, not the one-chip-wide minimum.
        assert cell.sizeHint().height() == wrapped
        assert cell.sizeHint().height() < stacked
    finally:
        tab.cleanup()


def test_roles_table_wheel_step_is_fixed_not_row_height_derived(qapp):
    """Regression: ScrollPerPixel mode alone still lets Qt derive the
    scrollbar's wheel-notch step (singleStep) from row height, and this
    table's rows vary a lot (chips wrap to multiple lines) -- so a single
    wheel notch could still jump as far as the tallest visible row, which
    still reads as "scrolling by row" rather than smoothly. singleStep
    must be pinned to a small fixed value independent of row height."""
    controller = _StubController()
    tab = RolesTab([_StubTrack(1)], controller)
    try:
        table = tab._table
        table.insertRow(0)
        table.setRowHeight(0, 36)
        table.insertRow(1)
        table.setRowHeight(1, 90)  # a row with several wrapped chip lines
        table.insertRow(2)
        table.setRowHeight(2, 36)

        step = table.verticalScrollBar().singleStep()
        assert step < 40, (
            f"wheel step ({step}px) scales with the 90px tall row -- "
            "still feels like scrolling by row, not by pixel"
        )
    finally:
        tab.cleanup()


# ---------------------------------------------------------------------------
# TrackCreditsTab
# ---------------------------------------------------------------------------


class _StubHelper:
    def __init__(self):
        self.convert_calls = []

    def convert_credit_to_album_level(self, artist_id, role_id):
        self.convert_calls.append((artist_id, role_id))


class _StubEditor:
    def __init__(self, album, controller):
        self.album = album
        self.controller = controller
        self.helper = _StubHelper()


def test_track_credits_tab_shows_message_when_album_has_no_tracks(qapp):
    editor = _StubEditor(_StubAlbum(tracks=[]), _StubController())
    tab = TrackCreditsTab(editor)
    built = tab.build()
    labels = [c for c in built.findChildren(QWidget) if hasattr(c, "text")]
    assert any(
        "no tracks" in getattr(w, "text", lambda: "")().lower() for w in labels
    )


def test_track_credits_tab_forwards_conversion_to_editor_helper(qapp, monkeypatch):
    # Avoid spinning up RolesTab's real background-thread DB load -- this
    # test only checks the tab's own wiring of the convert-hook callback.
    monkeypatch.setattr(
        "src.album.base_album_edit_tabs.TrackRolesTab.load", lambda self, tracks: None
    )

    editor = _StubEditor(_StubAlbum(tracks=[_StubTrack(1)]), _StubController())
    tab = TrackCreditsTab(editor)
    built = tab.build()
    try:
        roles_widget = next(
            c for c in built.findChildren(RolesTab)
        )
        roles_widget._on_convert_to_album(10, 5)
        assert editor.helper.convert_calls == [(10, 5)]
    finally:
        for w in built.findChildren(RolesTab):
            w.cleanup()


# ---------------------------------------------------------------------------
# AlbumEditor._refresh_track_credits_tab (scroll-reset regression)
# ---------------------------------------------------------------------------


def test_refresh_track_credits_tab_reuses_existing_roles_widget_in_place(
    qapp, monkeypatch
):
    """Refreshing Track Credits (e.g. after an edit made anywhere in the
    album editor -- Publishers & Places, Album credit, Awards, etc. all
    route through refresh_view()) must reload the existing RolesTab in
    place rather than destroying/rebuilding the tab.

    Rebuilding raced RolesTab's async (QThread-based) load against the
    generic tab-rebuild's scroll capture/restore: the restore fired via
    QTimer.singleShot(0, ...) before the new RolesTab's load finished, so
    it always found an empty table and clamped the restore to 0, silently
    resetting the user's scroll position on every refresh.
    """
    load_calls = []
    monkeypatch.setattr(
        "src.album.base_album_edit_tabs.TrackRolesTab.load",
        lambda self, tracks: load_calls.append(tracks),
    )

    tracks = [_StubTrack(1), _StubTrack(2)]
    editor = _StubEditor(_StubAlbum(tracks=tracks), _StubController())
    tab = TrackCreditsTab(editor)
    built = tab.build()  # build() itself calls load() once -- not under test.
    load_calls.clear()

    tabs = QTabWidget()
    tabs.addTab(built, "Track Credits")
    fake_editor = SimpleNamespace(tabs=tabs, album=editor.album)

    try:
        result = AlbumEditor._refresh_track_credits_tab(fake_editor, 0)

        assert result is True
        # The same tab widget is still there -- not removed/replaced.
        assert tabs.widget(0) is built
        assert load_calls == [tracks]
    finally:
        for w in built.findChildren(RolesTab):
            w.cleanup()
        tabs.deleteLater()


def test_refresh_track_credits_tab_falls_back_when_no_roles_widget(qapp):
    """When the tab was last built with no tracks (placeholder label, no
    RolesTab child), there's nothing to reuse -- the caller should fall
    back to a full rebuild instead of silently doing nothing."""
    editor = _StubEditor(_StubAlbum(tracks=[]), _StubController())
    tab = TrackCreditsTab(editor)
    built = tab.build()

    tabs = QTabWidget()
    tabs.addTab(built, "Track Credits")
    fake_editor = SimpleNamespace(tabs=tabs, album=editor.album)

    try:
        result = AlbumEditor._refresh_track_credits_tab(fake_editor, 0)
        assert result is False
    finally:
        tabs.deleteLater()
