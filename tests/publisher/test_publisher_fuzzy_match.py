"""Regression test: the "Merge Publishers" dialog fed raw publisher names
straight into QRadioButton text, so Qt consumed the '&' as a mnemonic prefix
and a name like "Sony & ATV" rendered as "Sony  ATV". _display_name now
escapes '&' (after eliding), and still exposes the raw name via tooltip.
"""

from types import SimpleNamespace

from PySide6.QtWidgets import QPushButton

from src.common.dialogs import fuzzy_match_dialog
from src.common.dismissed_duplicates import dismiss_pair, load_dismissed_pairs
from src.publisher.publisher_fuzzy_match import _MAX_NAME_CHARS, PublisherFuzzyMatchDialog

_display_name = PublisherFuzzyMatchDialog._display_name


def _publisher(publisher_id, name):
    return SimpleNamespace(publisher_id=publisher_id, publisher_name=name, MBID=None)


def test_display_name_doubles_ampersands():
    assert _display_name("Sony & ATV") == "Sony && ATV"


def test_display_name_handles_none():
    assert _display_name(None) == ""


def test_display_name_escapes_after_eliding():
    name = "A & " + "x" * _MAX_NAME_CHARS
    out = _display_name(name)
    # Elided on the real length, then the surviving '&' is doubled.
    assert out.endswith("…")
    assert "&&" in out
    assert len(out.replace("&&", "&")) <= _MAX_NAME_CHARS


def test_merge_dialog_radio_text_keeps_ampersand(qapp):
    a = _publisher(1, "Sony & ATV")
    b = _publisher(2, "Sony and ATV")
    dialog = PublisherFuzzyMatchDialog([(a, b, 95)], controller=None)
    try:
        radio_a = dialog.match_widgets[0][1]
        assert radio_a.text() == "Sony && ATV"
        assert radio_a.toolTip() == "Sony & ATV"
    finally:
        dialog.deleteLater()


# ---- dismiss duplicate suggestions ------------------------------------------
# Acceptance criteria for docs/specs/dismiss_duplicate_suggestions.md:
# AC3 (dismiss removes the row immediately and persists it), AC7 (a
# dismissed pair is filtered out of a later scan's dialog). Publisher's row
# is grid-based (no single per-row frame like Artist/Place), so the shared
# _dismiss_pair helper is exercised here with a list of row widgets instead
# of one container.


def test_dismiss_button_removes_row_and_persists_the_pair(qapp, tmp_path, monkeypatch):
    dismissed_path = tmp_path / "dismissed_duplicates.json"
    monkeypatch.setattr(fuzzy_match_dialog, "DEFAULT_DISMISSED_DUPLICATES_PATH", dismissed_path)

    a = _publisher(1, "Sony")
    b = _publisher(2, "Sonny")
    dialog = PublisherFuzzyMatchDialog([(a, b, 95)], controller=None)
    try:
        assert len(dialog.match_widgets) == 1
        dismiss_buttons = [btn for btn in dialog.findChildren(QPushButton) if btn not in (dialog.btn_merge, dialog.btn_cancel)]
        assert len(dismiss_buttons) == 1

        dismiss_buttons[0].click()

        assert dialog.match_widgets == []
        assert load_dismissed_pairs(dismissed_path, "Publisher") == {(1, 2)}
    finally:
        dialog.deleteLater()


def test_dismissed_pair_is_excluded_from_a_later_scans_dialog(qapp, tmp_path, monkeypatch):
    dismissed_path = tmp_path / "dismissed_duplicates.json"
    monkeypatch.setattr(fuzzy_match_dialog, "DEFAULT_DISMISSED_DUPLICATES_PATH", dismissed_path)
    dismiss_pair(dismissed_path, "Publisher", 1, 2)

    a = _publisher(1, "Sony")
    b = _publisher(2, "Sonny")
    dialog = PublisherFuzzyMatchDialog([(a, b, 95)], controller=None)
    try:
        assert dialog.matches == []
        assert dialog.match_widgets == []
    finally:
        dialog.deleteLater()


# ---- docs/specs/artist_duplicate_reconciliation.md AC9: same reconciliation
# step works for Publisher, via the shared BaseFuzzyMatchDialog._resolve_conflicts.


def test_resolved_field_lands_on_the_merged_survivor(qapp):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from src.common.dialogs.fuzzy_match_dialog import BaseMergeWorker
    from src.db.db_helpers.merge import MergeDB
    from src.db.db_tables.base import Base
    from src.db.db_tables.publisher import Publisher

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    source = Publisher(publisher_name="Sony", description="A record label.")
    target = Publisher(publisher_name="Sonny", description=None)
    session.add_all([source, target])
    session.commit()

    class _StubController:
        merge = MergeDB(session)

    worker = BaseMergeWorker(_StubController(), "Publisher", "publisher_id", "publisher_name", [(source, target, {"description": "A record label.", "publisher_name": "Sony"})])
    worker.run()

    survivor = session.get(Publisher, target.publisher_id)
    assert survivor.description == "A record label."
    assert survivor.publisher_name == "Sony"
    assert session.get(Publisher, source.publisher_id) is None
