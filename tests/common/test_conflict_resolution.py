"""Merge conflict resolution: conflict detection, the conflict grid and pair
dialog, and full-value display in the merge dialog.
"""

from PySide6.QtWidgets import QLabel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.common.dialogs.base_merge_dialog import MergeDBDialog
from src.common.dialogs.conflict_resolution import ConflictGridWidget, PairConflictDialog, _ConflictValueCell, _format_value_for_display, get_entity_conflicts
from src.db.db_tables.album import Album
from src.db.db_tables.artist import Artist
from src.db.db_tables.base import Base

# ---------------------------------------------------------------------------
# Unit tests for the shared conflict-detection/resolution UI extracted into
# ---------------------------------------------------------------------------


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _artist_pair(session, **overrides):
    source = Artist(artist_name="Lew", biography="Session guitarist.", begin_year=1950)
    target = Artist(artist_name="Lewis", biography=None, begin_year=None)
    for attr, value in overrides.get("source", {}).items():
        setattr(source, attr, value)
    for attr, value in overrides.get("target", {}).items():
        setattr(target, attr, value)
    session.add_all([source, target])
    session.commit()
    return source, target


def test_get_entity_conflicts_detects_differing_scalar_fields():
    session = _make_session()
    source, target = _artist_pair(session)

    conflicts = get_entity_conflicts(source, target, "artist_id")

    assert conflicts["artist_name"] == ("Lew", "Lewis")
    assert conflicts["biography"] == ("Session guitarist.", None)
    assert conflicts["begin_year"] == (1950, None)


def test_get_entity_conflicts_excludes_ids_timestamps_and_relationships():
    session = _make_session()
    source, target = _artist_pair(session)

    conflicts = get_entity_conflicts(source, target, "artist_id")

    assert "artist_id" not in conflicts
    assert "tags" not in conflicts
    assert "album_roles" not in conflicts


def test_get_entity_conflicts_ignores_identical_fields():
    session = _make_session()
    source, target = _artist_pair(session, source={"gender": "female"}, target={"gender": "female"})

    conflicts = get_entity_conflicts(source, target, "artist_id")

    assert "gender" not in conflicts


def test_conflict_grid_defaults_to_source_value_when_source_is_non_blank(qapp):
    session = _make_session()
    source, target = _artist_pair(session)

    grid = ConflictGridWidget(source, target, "artist_id", "Lew", "Lewis")

    # source.biography is non-blank -> default should keep the source's value
    assert grid.get_resolved_fields()["biography"] == "Session guitarist."


def test_conflict_grid_defaults_to_target_value_when_source_is_blank(qapp):
    session = _make_session()
    # Swap which side is blank: source has no disambiguation, target does.
    source, target = _artist_pair(session, source={"disambiguation": None}, target={"disambiguation": "the elder"})

    grid = ConflictGridWidget(source, target, "artist_id", "Lew", "Lewis")

    assert grid.get_resolved_fields()["disambiguation"] == "the elder"


def test_conflict_grid_manual_choice_overrides_default(qapp):
    session = _make_session()
    source, target = _artist_pair(session)

    grid = ConflictGridWidget(source, target, "artist_id", "Lew", "Lewis")
    grid._choose("artist_name", 1)  # explicitly pick target's name instead of the default source pick

    assert grid.get_resolved_fields()["artist_name"] == "Lewis"


def test_conflict_grid_has_conflicts_false_for_identical_entities(qapp):
    session = _make_session()
    twin = Artist(artist_name="Same", biography="Same bio")
    session.add(twin)
    session.commit()

    grid = ConflictGridWidget(twin, twin, "artist_id", "Same", "Same")

    assert grid.has_conflicts() is False
    assert grid.get_resolved_fields() == {}


def test_pair_conflict_dialog_use_selected_returns_grid_choices(qapp):
    session = _make_session()
    source, target = _artist_pair(session)

    dialog = PairConflictDialog(source, target, "artist_id", "Lew", "Lewis", 1, 1)
    try:
        dialog._finish(PairConflictDialog.USE_SELECTED)
        resolved = dialog.resolved_fields()
        assert resolved["biography"] == "Session guitarist."
    finally:
        dialog.deleteLater()


def test_pair_conflict_dialog_skip_pair_returns_empty_resolved_fields(qapp):
    session = _make_session()
    source, target = _artist_pair(session)

    dialog = PairConflictDialog(source, target, "artist_id", "Lew", "Lewis", 1, 1)
    try:
        dialog._finish(PairConflictDialog.SKIP_PAIR)
        assert dialog.resolved_fields() == {}
    finally:
        dialog.deleteLater()


def test_pair_conflict_dialog_skip_all_returns_empty_resolved_fields(qapp):
    session = _make_session()
    source, target = _artist_pair(session)

    dialog = PairConflictDialog(source, target, "artist_id", "Lew", "Lewis", 1, 3)
    try:
        dialog._finish(PairConflictDialog.SKIP_ALL)
        assert dialog.resolved_fields() == {}
        assert dialog.outcome == PairConflictDialog.SKIP_ALL
    finally:
        dialog.deleteLater()


def test_pair_conflict_dialog_defaults_to_cancel_outcome(qapp):
    session = _make_session()
    source, target = _artist_pair(session)

    dialog = PairConflictDialog(source, target, "artist_id", "Lew", "Lewis", 1, 1)
    try:
        assert dialog.outcome == PairConflictDialog.CANCEL
        assert dialog.resolved_fields() == {}
    finally:
        dialog.deleteLater()


def test_pair_conflict_dialog_buttons_produce_expected_outcomes(qapp):
    from PySide6.QtWidgets import QPushButton

    session = _make_session()
    source, target = _artist_pair(session)

    dialog = PairConflictDialog(source, target, "artist_id", "Lew", "Lewis", 1, 1)
    try:
        buttons = {btn.text().replace("&", ""): btn for btn in dialog.findChildren(QPushButton)}
        assert "Use Selected Values" in buttons
        assert "Skip This Pair (Keep Canonical)" in buttons
        assert "Skip All Remaining" in buttons
        assert "Cancel Merge" in buttons

        buttons["Use Selected Values"].click()
        assert dialog.outcome == PairConflictDialog.USE_SELECTED
    finally:
        dialog.deleteLater()


def test_conflict_grid_shows_checkbox_box_on_both_sides(qapp):
    from src.common.dialogs.conflict_resolution import _ConflictValueCell

    cell = _ConflictValueCell("value", lambda: None)
    cell.set_chosen(False)
    assert not cell._check.isHidden()
    assert cell._check.text() == ""

    cell.set_chosen(True)
    assert not cell._check.isHidden()
    assert cell._check.text() == "✓"


# ---------------------------------------------------------------------------
# Regression tests for the merge-conflict value display (used by Album and
# ---------------------------------------------------------------------------


class _StubMergeHelper:
    def merge_entities(self, *args, **kwargs):
        raise AssertionError("not expected to be called in this test")


class _StubController:
    def __init__(self):
        self.merge = _StubMergeHelper()


def test_format_value_for_display_does_not_truncate_long_values():
    long_value = "A" * 200
    result = _format_value_for_display(long_value)
    assert result == long_value


def test_conflict_card_shows_full_value_and_has_no_fixed_width(qapp):
    session = _make_session()
    long_description = "This is a very long album description. " * 10

    source = Album(album_name="Source Album", album_description=long_description)
    target = Album(album_name="Target Album", album_description="short")
    session.add_all([source, target])
    session.commit()

    dialog = MergeDBDialog(_StubController(), "Album", preload_source=source, preload_target=target)
    try:
        resolve_page = dialog.stack.widget(1)
        labels = [w for w in resolve_page.findChildren(QLabel) if w.text() and long_description in w.text()]
        assert labels, "expected a label containing the full, untruncated value"
        assert labels[0].wordWrap()

        conflict_cell = labels[0].parentWidget()
        while conflict_cell is not None and not isinstance(conflict_cell, _ConflictValueCell):
            conflict_cell = conflict_cell.parentWidget()
        assert conflict_cell is not None
        assert conflict_cell.maximumWidth() >= 16777215  # Qt's QWIDGETSIZE_MAX default

        cells = resolve_page.findChildren(_ConflictValueCell)
        assert cells, "conflict options should still be selectable"
    finally:
        dialog.deleteLater()
        session.close()


def test_conflict_cell_is_keyboard_selectable(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    session = _make_session()
    source, target = _artist_pair(session)
    grid = ConflictGridWidget(source, target, "artist_id", "Lew", "Lewis")
    try:
        _source_cell, target_cell = grid._cells["biography"]
        assert target_cell.focusPolicy() == Qt.StrongFocus

        QTest.keyClick(target_cell, Qt.Key_Space)

        assert grid.get_resolved_fields()["biography"] is None
    finally:
        grid.deleteLater()
        session.close()


def test_next_after_back_shows_the_new_pairs_conflict_page(qapp):
    session = _make_session()
    first_source, target = _artist_pair(session)
    second_source = Artist(artist_name="Lou", biography="Other bio.", begin_year=1960)
    session.add(second_source)
    session.commit()

    dialog = MergeDBDialog(_StubController(), "Artist", preload_source=first_source, preload_target=target)
    try:
        dialog.source_entity = second_source
        dialog._build_conflict_ui()

        assert dialog.stack.count() == 2
        assert dialog.stack.currentWidget().isAncestorOf(dialog._conflict_grid)
        assert dialog._conflict_grid.source_entity is second_source
    finally:
        dialog.deleteLater()
        session.close()


def test_find_similar_excludes_the_entity_it_starts_from(qapp):
    session = _make_session()
    source, target = _artist_pair(session)
    dialog = MergeDBDialog(_StubController(), "Artist")
    try:
        results = dialog._find_similar_entities_by_name("Lewis", [source, target], exclude_id=target.artist_id)

        assert [entity for entity, _score in results] == [source]
    finally:
        dialog.deleteLater()
        session.close()
