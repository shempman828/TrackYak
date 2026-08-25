"""
Tests for manual matching from the Missing Popular / Gap Fills
recommendation tables (docs/specs/chart_recommendations_manual_match.md) --
ChartRecommendationTable's context menu and the bulk handler in
chart_manual_match_actions.py. Against a scratch in-memory SQLite session
(tests/conftest.py's qapp fixture for offscreen Qt), never music_library.db,
following the same StubController pattern as test_chart_manual_match.py.

Numbered comments map each test to
docs/specs/chart_recommendations_manual_match.md's acceptance criteria.
"""

import datetime

import pytest
from PySide6.QtWidgets import QDialog
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.charts.chart_manual_match_actions import handle_bulk_manual_match_requested
from src.charts.chart_manual_match_dialog import ChartManualMatchDialog
from src.charts.chart_recommendation_table import ChartRecommendationTable
from src.charts.chart_recommendations import get_missing_popular
from src.common.entity_completer_edit import invalidate_entity_cache
from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_helpers.update import UpdateDB
from src.db.db_tables.base import Base
from src.db.db_tables.chart import Chart, ChartEntry
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
    invalidate_entity_cache()
    yield
    invalidate_entity_cache()


def _seed_multi_week_song(
    session, chart, raw_title="Runaround Sue", raw_performer="Dion", weeks=3, position=1
):
    """`weeks` unmatched ChartEntry rows for the same song on `chart`, all at
    `position` (distinct songs must use distinct positions -- (chart_id,
    chart_week, position) is unique)."""
    for i in range(weeks):
        session.add(
            ChartEntry(
                chart_id=chart.chart_id,
                chart_week=datetime.date(2023, 12, 30) - datetime.timedelta(days=7 * i),
                position=position,
                peak_position=1,
                weeks_on_chart=weeks - i,
                raw_title=raw_title,
                raw_performer=raw_performer,
            )
        )
    session.commit()


def _seed_track_chart(session):
    chart = Chart(
        chart_key="hot-100",
        chart_name="Billboard Hot 100",
        source_url="https://example.invalid/hot-100.csv",
        matched_entity_type="Track",
    )
    session.add(chart)
    session.commit()
    track = Track(track_name="Runaround Sue (Remastered)")
    session.add(track)
    session.commit()
    return chart, track


def test_context_menu_on_missing_popular_row_offers_match_no_clear(qapp, session):
    # AC1
    chart, _track = _seed_track_chart(session)
    _seed_multi_week_song(session, chart)
    items = get_missing_popular(session)

    table = ChartRecommendationTable()
    table.populate(items)
    menu = table.context_menu_for_item(items[0])
    labels = [a.text() for a in menu.actions()]
    assert labels == ["Match to Track…"]


def test_context_menu_on_gap_fills_row_same_behavior(qapp, session):
    # AC2 -- same ChartRecommendationTable class backs both sub-tabs
    chart, _track = _seed_track_chart(session)
    _seed_multi_week_song(session, chart)
    items = get_missing_popular(session)

    gap_table = ChartRecommendationTable()
    gap_table.populate(items)
    labels = [a.text() for a in gap_table.context_menu_for_item(items[0]).actions()]
    assert labels == ["Match to Track…"]


def test_bulk_match_sets_every_unmatched_row_in_the_group(qapp, session, controller, monkeypatch):
    # AC3
    chart, track = _seed_track_chart(session)
    _seed_multi_week_song(session, chart, weeks=3)

    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr(ChartManualMatchDialog, "matched_entity_id", lambda self: track.track_id)
    monkeypatch.setattr(ChartManualMatchDialog, "matched_entity_type", lambda self: "Track")

    items = get_missing_popular(session)
    assert len(items) == 1  # the 3 weekly rows folded into one aggregate

    done = []
    handle_bulk_manual_match_requested(None, controller, items[0], lambda: done.append(True))

    session.expire_all()
    entries = session.query(ChartEntry).filter_by(chart_id=chart.chart_id).all()
    assert len(entries) == 3
    for entry in entries:
        assert entry.entity_type == "Track"
        assert entry.entity_id == track.track_id
        assert entry.match_score == 1.0
    assert done == [True]


def test_bulk_match_removes_song_from_missing_popular(qapp, session, controller, monkeypatch):
    # AC4
    chart, track = _seed_track_chart(session)
    _seed_multi_week_song(session, chart, weeks=3)

    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr(ChartManualMatchDialog, "matched_entity_id", lambda self: track.track_id)
    monkeypatch.setattr(ChartManualMatchDialog, "matched_entity_type", lambda self: "Track")

    items = get_missing_popular(session)
    handle_bulk_manual_match_requested(None, controller, items[0], lambda: None)

    session.expire_all()
    assert get_missing_popular(session) == []


def test_ui_refresh_wired_after_bulk_match(qapp, session, controller, monkeypatch):
    # AC5 -- ChartRecommendationsTab wiring calls refresh() via on_done
    chart, track = _seed_track_chart(session)
    _seed_multi_week_song(session, chart, weeks=2)

    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr(ChartManualMatchDialog, "matched_entity_id", lambda self: track.track_id)
    monkeypatch.setattr(ChartManualMatchDialog, "matched_entity_type", lambda self: "Track")

    calls = []
    handle_bulk_manual_match_requested(None, controller, get_missing_popular(session)[0], lambda: calls.append(True))
    assert calls == [True]  # on_done is what ChartRecommendationsTab wires to self.refresh


def test_dialog_ok_disabled_until_candidate_picked_for_bulk_match(qapp, session, controller):
    # AC7 -- same ChartManualMatchDialog class/behavior as the single-entry
    # flow (test_chart_manual_match.py), constructed via the raw-field
    # signature a recommendation row uses.
    dialog = ChartManualMatchDialog(controller, "Track", "Runaround Sue", "Dion")
    assert not dialog._ok_button.isEnabled()


def test_cancelling_bulk_match_dialog_leaves_entries_unchanged(qapp, session, controller, monkeypatch):
    # AC6
    chart, _track = _seed_track_chart(session)
    _seed_multi_week_song(session, chart, weeks=2)

    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.Rejected)

    done = []
    handle_bulk_manual_match_requested(
        None, controller, get_missing_popular(session)[0], lambda: done.append(True)
    )

    session.expire_all()
    entries = session.query(ChartEntry).filter_by(chart_id=chart.chart_id).all()
    for entry in entries:
        assert entry.entity_id is None
    assert done == []


def test_bulk_match_does_not_touch_other_songs_or_charts(qapp, session, controller, monkeypatch):
    # AC8
    chart, track = _seed_track_chart(session)
    _seed_multi_week_song(session, chart, raw_title="Runaround Sue", raw_performer="Dion", weeks=2)

    # Same title/performer, but on a different chart -- must NOT be touched.
    other_chart = Chart(
        chart_key="billboard-200",
        chart_name="Billboard 200",
        source_url="https://example.invalid/billboard-200.csv",
        matched_entity_type="Track",
    )
    session.add(other_chart)
    session.commit()
    _seed_multi_week_song(session, other_chart, raw_title="Runaround Sue", raw_performer="Dion", weeks=1)

    # A different song on the same chart -- must NOT be touched.
    _seed_multi_week_song(
        session, chart, raw_title="Some Other Song", raw_performer="Someone Else", weeks=1, position=2
    )

    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr(ChartManualMatchDialog, "matched_entity_id", lambda self: track.track_id)
    monkeypatch.setattr(ChartManualMatchDialog, "matched_entity_type", lambda self: "Track")

    items = get_missing_popular(session, chart_ids=[chart.chart_id])
    target = next(i for i in items if i.raw_title == "Runaround Sue")
    handle_bulk_manual_match_requested(None, controller, target, lambda: None)

    session.expire_all()
    unmatched_still = (
        session.query(ChartEntry).filter(ChartEntry.entity_id.is_(None)).all()
    )
    # The other chart's identically-titled entry, and the same chart's
    # different song, both remain untouched.
    assert len(unmatched_still) == 2
    remaining_titles = {(e.chart_id, e.raw_title) for e in unmatched_still}
    assert (other_chart.chart_id, "Runaround Sue") in remaining_titles
    assert (chart.chart_id, "Some Other Song") in remaining_titles
