"""
Tests for manual chart-entry matching (docs/specs/manual_chart_matching.md)
-- ChartEntryTable's context menu, ChartManualMatchDialog, and the shared
handlers in chart_manual_match_actions.py. Against a scratch in-memory
SQLite session (tests/conftest.py's qapp fixture for offscreen Qt), never
music_library.db, following the same StubController pattern as
test_charts_view.py.

Numbered comments map each test to docs/specs/manual_chart_matching.md's
acceptance criteria.
"""

import datetime

from PySide6.QtWidgets import QDialog, QMessageBox
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.charts.chart_entry_table import ChartEntryTable
from src.charts.chart_manual_match_actions import (
    handle_clear_match_requested,
    handle_manual_match_requested,
)
from src.charts.chart_manual_match_dialog import ChartManualMatchDialog
from src.charts.chart_matching import match_chart
from src.common.entity_completer_edit import invalidate_entity_cache
from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_helpers.update import UpdateDB
from src.db.db_tables.album import Album
from src.db.db_tables.artist import Artist
from src.db.db_tables.associations import AlbumRoleAssociation, TrackArtistRole
from src.db.db_tables.base import Base
from src.db.db_tables.chart import Chart, ChartEntry
from src.db.db_tables.role import Role
from src.db.db_tables.track import Track


class StubController:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.add = AddToDB(session)
        self.update = UpdateDB(session)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def controller(session):
    return StubController(session)


@pytest.fixture(autouse=True)
def _clear_entity_completer_cache():
    """build_entity_search_widget's get_cached_entities() caches Track/Album
    rows at module scope, keyed only by model name -- with a fresh
    in-memory engine per test, a cache entry from a previous test would
    otherwise hand back ORM objects bound to an already-closed session.
    Same pattern as test_tag_association_tab.py's invalidate_entity_cache
    calls."""
    invalidate_entity_cache()
    yield
    invalidate_entity_cache()


def _seed_track_chart(session):
    """One Hot 100 chart entry ('Runaround Sue' / Dion) with no library
    match, plus two Track candidates a manual match can pick between."""
    role = Role(role_name="Primary Artist")
    session.add(role)
    session.commit()

    artist = Artist(artist_name="Dion")
    track = Track(track_name="Runaround Sue (Remastered)")
    other_track = Track(track_name="Some Other Song")
    session.add_all([artist, track, other_track])
    session.commit()
    session.add(
        TrackArtistRole(track_id=track.track_id, artist_id=artist.artist_id, role_id=role.role_id)
    )
    session.commit()

    chart = Chart(
        chart_key="hot-100",
        chart_name="Billboard Hot 100",
        source_url="https://example.invalid/hot-100.csv",
        matched_entity_type="Track",
    )
    session.add(chart)
    session.commit()

    entry = ChartEntry(
        chart_id=chart.chart_id,
        chart_week=datetime.date(2023, 12, 30),
        position=1,
        raw_title="Runaround Sue",
        raw_performer="Dion",
    )
    session.add(entry)
    session.commit()
    return chart, entry, track, other_track


def _seed_album_chart(session):
    role = Role(role_name="Album Artist")
    artist = Artist(artist_name="Michael Jackson")
    album = Album(album_name="Thriller", release_year=1982)
    session.add_all([role, artist, album])
    session.commit()
    session.add(
        AlbumRoleAssociation(
            album_id=album.album_id, artist_id=artist.artist_id, role_id=role.role_id
        )
    )
    session.commit()

    chart = Chart(
        chart_key="billboard-200",
        chart_name="Billboard 200",
        source_url="https://example.invalid/billboard-200.csv",
        matched_entity_type="Album",
    )
    session.add(chart)
    session.commit()
    return chart, album


def test_context_menu_on_unmatched_row_offers_match_but_not_clear(qapp, session):
    # AC1
    _chart, entry, _track, _other = _seed_track_chart(session)
    table = ChartEntryTable()
    table.populate([entry])

    menu = table.context_menu_for_entry(entry.chart_entry_id)
    actions = menu.actions()
    labels = [a.text() for a in actions]
    assert "Match to Track…" in labels
    clear_action = next(a for a in actions if a.text() == "Clear Match")
    assert not clear_action.isEnabled()


def test_context_menu_on_matched_row_enables_clear(qapp, session):
    # AC2
    _chart, entry, track, _other = _seed_track_chart(session)
    entry.entity_type = "Track"
    entry.entity_id = track.track_id
    entry.match_score = 0.75
    session.commit()

    table = ChartEntryTable()
    table.populate([entry])

    menu = table.context_menu_for_entry(entry.chart_entry_id)
    actions = menu.actions()
    match_action = next(a for a in actions if a.text() == "Match to Track…")
    clear_action = next(a for a in actions if a.text() == "Clear Match")
    assert match_action.isEnabled()
    assert clear_action.isEnabled()


def test_manual_match_dialog_ok_disabled_until_candidate_picked(qapp, session, controller):
    # AC5
    _chart, entry, track, _other = _seed_track_chart(session)
    dialog = ChartManualMatchDialog(controller, "Track", entry.raw_title, entry.raw_performer)

    assert not dialog._ok_button.isEnabled()

    dialog._search.setText("Runaround Sue")  # typed, not picked -- and not the
    assert (
        not dialog._ok_button.isEnabled()
    )  # full candidate text, so the pick below actually changes the field

    # Simulate picking the suggestion from the completer popup (click or
    # Enter-on-highlighted) via the real activated signal rather than calling
    # _on_completion_picked directly -- setText() inside it fires textChanged
    # synchronously, which is what the OK button listens to, so the pick must
    # go through that same signal chain to catch ordering bugs between
    # setText() and matched_id being recorded. (The field's text must
    # actually change here -- QLineEdit.setText() is a no-op, and emits no
    # textChanged, when the new text equals the old.)
    dialog._search._completer.activated.emit("Runaround Sue (Remastered)")
    assert dialog._ok_button.isEnabled()
    assert dialog.matched_entity_id() == track.track_id
    assert dialog.matched_entity_type() == "Track"


def test_manual_match_dialog_ok_enables_when_picked_text_equals_typed_text(
    qapp, session, controller
):
    # Regression: typing an album's full name exactly, then picking the sole
    # matching suggestion, left OK greyed -- _on_completion_picked's
    # setText(picked) is a no-op when it equals the field's current text, so
    # textChanged never fired and the OK-enable check never re-ran. The
    # widget's picked signal fires regardless. ("Cloud Nine" / George
    # Harrison in the field report.)
    _chart, album = _seed_album_chart(session)
    dialog = ChartManualMatchDialog(controller, "Album", "Thriller", "Michael Jackson")

    assert not dialog._ok_button.isEnabled()

    dialog._search.setText("Thriller")  # the full, exact candidate text
    assert not dialog._ok_button.isEnabled()  # typed, not yet picked

    dialog._search._completer.activated.emit("Thriller")  # pick -> setText no-ops
    assert dialog._ok_button.isEnabled()
    assert dialog.matched_entity_id() == album.album_id


def test_manual_match_dialog_track_suggestions_carry_context(qapp, session, controller):
    # The completer popup must show the shared dimmed secondary context
    # (primary artist / album) so same-named tracks are distinguishable --
    # the chart dialog was the one site build_entity_search_widget's
    # context channel wasn't wired into.
    _chart, _entry, track, _other = _seed_track_chart(session)
    dialog = ChartManualMatchDialog(controller, "Track", "Runaround Sue", "Dion")

    assert dialog._search._display_to_context.get(track.track_name) == "Dion"


def test_manual_match_dialog_album_suggestions_carry_context(qapp, session, controller):
    _chart, album = _seed_album_chart(session)
    dialog = ChartManualMatchDialog(controller, "Album", "Thriller", "Michael Jackson")

    assert dialog._search._display_to_context.get(album.album_name) == "Michael Jackson · 1982"


def test_accepting_dialog_sets_match_in_db(qapp, session, controller, monkeypatch):
    # AC3
    _chart, entry, track, _other = _seed_track_chart(session)

    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.Accepted)

    def _fake_init(self, controller_, entity_type, raw_title, raw_performer, parent=None):
        QDialog.__init__(self, parent)
        self._entity_type = "Track"
        self._picked_id = track.track_id

    monkeypatch.setattr(ChartManualMatchDialog, "__init__", _fake_init)
    monkeypatch.setattr(ChartManualMatchDialog, "matched_entity_id", lambda self: self._picked_id)
    monkeypatch.setattr(
        ChartManualMatchDialog, "matched_entity_type", lambda self: self._entity_type
    )

    done = []
    handle_manual_match_requested(None, controller, entry.chart_entry_id, lambda: done.append(True))

    session.expire_all()
    refreshed = session.get(ChartEntry, entry.chart_entry_id)
    assert refreshed.entity_type == "Track"
    assert refreshed.entity_id == track.track_id
    assert refreshed.match_score == 1.0
    assert done == [True]


def test_cancelling_dialog_leaves_entry_unchanged(qapp, session, controller, monkeypatch):
    # AC4
    _chart, entry, _track, _other = _seed_track_chart(session)

    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.Rejected)

    done = []
    handle_manual_match_requested(None, controller, entry.chart_entry_id, lambda: done.append(True))

    session.expire_all()
    refreshed = session.get(ChartEntry, entry.chart_entry_id)
    assert refreshed.entity_type is None
    assert refreshed.entity_id is None
    assert refreshed.match_score is None
    assert done == []


def test_rematching_overwrites_previous_match(qapp, session, controller, monkeypatch):
    # AC6
    _chart, entry, track, other_track = _seed_track_chart(session)
    entry.entity_type = "Track"
    entry.entity_id = track.track_id
    entry.match_score = 0.75
    session.commit()

    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr(
        ChartManualMatchDialog, "matched_entity_id", lambda self: other_track.track_id
    )
    monkeypatch.setattr(ChartManualMatchDialog, "matched_entity_type", lambda self: "Track")

    handle_manual_match_requested(None, controller, entry.chart_entry_id, lambda: None)

    session.expire_all()
    refreshed = session.get(ChartEntry, entry.chart_entry_id)
    assert refreshed.entity_id == other_track.track_id
    assert refreshed.match_score == 1.0


def test_clear_match_confirmed_resets_entry(qapp, session, controller, monkeypatch):
    # AC7
    _chart, entry, track, _other = _seed_track_chart(session)
    entry.entity_type = "Track"
    entry.entity_id = track.track_id
    entry.match_score = 1.0
    entry.last_match_attempt_at = datetime.datetime(2024, 1, 1)
    session.commit()

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)

    done = []
    handle_clear_match_requested(None, controller, entry.chart_entry_id, lambda: done.append(True))

    session.expire_all()
    refreshed = session.get(ChartEntry, entry.chart_entry_id)
    assert refreshed.entity_type is None
    assert refreshed.entity_id is None
    assert refreshed.match_score is None
    assert refreshed.last_match_attempt_at is None
    assert done == [True]


def test_clear_match_declined_leaves_entry_unchanged(qapp, session, controller, monkeypatch):
    # AC8
    _chart, entry, track, _other = _seed_track_chart(session)
    entry.entity_type = "Track"
    entry.entity_id = track.track_id
    entry.match_score = 1.0
    session.commit()

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)

    done = []
    handle_clear_match_requested(None, controller, entry.chart_entry_id, lambda: done.append(True))

    session.expire_all()
    refreshed = session.get(ChartEntry, entry.chart_entry_id)
    assert refreshed.entity_id == track.track_id
    assert refreshed.match_score == 1.0
    assert done == []


def test_auto_matcher_never_touches_a_manual_match(qapp, session):
    # AC9
    chart, entry, _track, other_track = _seed_track_chart(session)
    entry.entity_type = "Track"
    entry.entity_id = other_track.track_id  # deliberately "wrong" vs. auto's pick
    entry.match_score = 1.0
    session.commit()

    match_chart(session, chart)

    session.expire_all()
    refreshed = session.get(ChartEntry, entry.chart_entry_id)
    assert refreshed.entity_id == other_track.track_id
    assert refreshed.match_score == 1.0


def test_cleared_match_is_eligible_for_rematch_without_library_change(qapp, session):
    # AC10
    chart, entry, track, _other = _seed_track_chart(session)

    # First pass: auto-matches the exact "Runaround Sue (Remastered)" title
    # (note: raw_title "Runaround Sue" is a substring of the track's actual
    # title, so the FTS shortlist path finds it) and records the fingerprint.
    match_chart(session, chart)
    session.expire_all()
    matched = session.get(ChartEntry, entry.chart_entry_id)
    assert matched.entity_id == track.track_id
    assert matched.last_match_attempt_at is not None

    # Simulate the user clearing it by hand.
    matched.entity_type = None
    matched.entity_id = None
    matched.match_score = None
    matched.last_match_attempt_at = None
    session.commit()

    # Re-run with no library change: the "already attempted, library
    # unchanged" short-circuit must NOT skip this entry, since clearing
    # reset last_match_attempt_at.
    stats = match_chart(session, chart)
    assert stats.matched == 1
    session.expire_all()
    rematched = session.get(ChartEntry, entry.chart_entry_id)
    assert rematched.entity_id == track.track_id


def test_search_and_week_tabs_share_identical_context_menu_behavior(qapp, session, controller):
    # AC11 -- both tabs use the same ChartEntryTable, so this documents that
    # the context-menu construction is table-level, not tab-specific.
    _chart, entry, _track, _other = _seed_track_chart(session)
    table_a = ChartEntryTable()
    table_a.populate([entry])
    table_b = ChartEntryTable()
    table_b.populate([entry])

    labels_a = [a.text() for a in table_a.context_menu_for_entry(entry.chart_entry_id).actions()]
    labels_b = [a.text() for a in table_b.context_menu_for_entry(entry.chart_entry_id).actions()]
    assert labels_a == labels_b


def test_manual_match_dialog_is_wide_enough_for_context_hints(qapp, session, controller):
    # Regression: the dialog set no width and collapsed to the form's
    # minimum, so the completer popup was too narrow for a suggestion's
    # name plus its right-aligned dimmed context hint -- names got
    # hard-elided to a few characters.
    dialog = ChartManualMatchDialog(controller, "Track", "Runaround Sue", "Dion")
    assert dialog.minimumWidth() >= 520


def test_album_chart_menu_labels_match_to_album(qapp, session):
    chart, _album = _seed_album_chart(session)
    entry = ChartEntry(
        chart_id=chart.chart_id,
        chart_week=datetime.date(2023, 12, 30),
        position=1,
        raw_title="Thriller",
        raw_performer="Michael Jackson",
    )
    session.add(entry)
    session.commit()

    table = ChartEntryTable()
    table.populate([entry])
    labels = [a.text() for a in table.context_menu_for_entry(entry.chart_entry_id).actions()]
    assert "Match to Album…" in labels
