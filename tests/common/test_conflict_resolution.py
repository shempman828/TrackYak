"""Unit tests for the shared conflict-detection/resolution UI extracted into
conflict_resolution.py (docs/specs/artist_duplicate_reconciliation.md), used
by both MergeDBDialog's manual merge page and the batch fuzzy-duplicate
merge flow.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.common.dialogs.conflict_resolution import ConflictGridWidget, PairConflictDialog, get_entity_conflicts
from src.db.db_tables.artist import Artist
from src.db.db_tables.base import Base


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
