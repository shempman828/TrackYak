"""Regression tests for the merge-conflict value display (used by Album and
other entity merges via MergeDBDialog).

A hardcoded card width plus a 50-character truncation in
_format_value_for_display used to silently cut off long conflicting values
(e.g. a full album description or URL), defeating the point of a dialog
whose whole job is to let the user compare the two values.
"""

from PySide6.QtWidgets import QLabel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.common.dialogs.base_merge_dialog import MergeDBDialog
from src.common.dialogs.conflict_resolution import _ConflictValueCell, _format_value_for_display
from src.db.db_tables.album import Album
from src.db.db_tables.base import Base


class _StubMergeHelper:
    def merge_entities(self, *args, **kwargs):
        raise AssertionError("not expected to be called in this test")


class _StubController:
    def __init__(self):
        self.merge = _StubMergeHelper()


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


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
