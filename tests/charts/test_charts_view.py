"""
Smoke test for ChartsView (src/charts/charts_view.py) against a scratch
in-memory SQLite session + offscreen Qt (tests/conftest.py's qapp fixture)
-- never music_library.db, never a real MusicController. Covers the
download/import/match state machine's button visibility and that the two
tabs populate from seeded data.
"""

import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.charts.charts_view import ChartsView
from src.common.match_confidence import confidence_color
from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_helpers.update import UpdateDB
from src.db.db_tables.artist import Artist
from src.db.db_tables.associations import TrackArtistRole
from src.db.db_tables.base import Base
from src.db.db_tables.chart import Chart, ChartEntry
from src.db.db_tables.database import MusicDatabase
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
    # chart_entries_fts (used by ChartSearchTab) is created by
    # MusicDatabase's integrity-check startup path, not by create_all() --
    # build it the same way the real app does rather than duplicating its
    # DDL/trigger SQL here. Bypass __init__ so this never touches the
    # shared music_library.db engine.
    db = MusicDatabase.__new__(MusicDatabase)
    db.engine = engine
    db._ensure_chart_entries_fts()
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def controller(session):
    return StubController(session)


@pytest.fixture(autouse=True)
def no_real_csvs_on_disk(monkeypatch, tmp_path):
    """Point chart_data_path() at a scratch tmp_path for the duration of
    this test, so chart_csv_exists() never touches the real assets/charts
    directory in the repo."""
    monkeypatch.setattr(
        "src.charts.chart_download.chart_data_path", lambda name: str(tmp_path / name)
    )


def _seed_charts_no_data(session):
    session.add_all(
        [
            Chart(
                chart_key="hot-100",
                chart_name="Billboard Hot 100",
                source_url="https://example.invalid/hot-100.csv",
                matched_entity_type="Track",
            ),
            Chart(
                chart_key="billboard-200",
                chart_name="Billboard 200",
                source_url="https://example.invalid/billboard-200.csv",
                matched_entity_type="Album",
            ),
        ]
    )
    session.commit()


def _seed_charts_fully_synced(session):
    role = Role(role_name="Primary Artist")
    session.add(role)
    session.commit()

    artist = Artist(artist_name="Wham!")
    track = Track(track_name="Last Christmas")
    session.add_all([artist, track])
    session.commit()
    session.add(
        TrackArtistRole(track_id=track.track_id, artist_id=artist.artist_id, role_id=role.role_id)
    )

    hot100 = Chart(
        chart_key="hot-100",
        chart_name="Billboard Hot 100",
        source_url="https://example.invalid/hot-100.csv",
        matched_entity_type="Track",
        last_synced_week=datetime.date(2023, 12, 30),
        last_downloaded_at=datetime.datetime(2023, 12, 31, 8, 0),
    )
    bb200 = Chart(
        chart_key="billboard-200",
        chart_name="Billboard 200",
        source_url="https://example.invalid/billboard-200.csv",
        matched_entity_type="Album",
        last_synced_week=datetime.date(2023, 12, 30),
        last_downloaded_at=datetime.datetime(2023, 12, 31, 8, 0),
    )
    session.add_all([hot100, bb200])
    session.commit()

    session.add(
        ChartEntry(
            chart_id=hot100.chart_id,
            chart_week=datetime.date(2023, 12, 30),
            position=1,
            raw_title="Last Christmas",
            raw_performer="Wham!",
            entity_type="Track",
            entity_id=track.track_id,
            match_score=1.0,
        )
    )
    session.commit()
    return hot100, bb200


def test_no_data_shows_download_button(qapp, session, controller):
    _seed_charts_no_data(session)
    view = ChartsView(controller)
    view.show()  # isVisible() reflects real on-screen state, not just setVisible() calls

    assert view.download_btn.isVisible()
    assert not view.fetch_btn.isVisible()
    assert not view.match_btn.isVisible()
    assert not view.playlists_btn.isVisible()  # AC12: same rule as match_btn


def test_fully_synced_shows_fetch_and_match_buttons(qapp, session, controller, tmp_path):
    # Fully synced state requires the CSVs to also exist on disk (that's
    # part of load_charts()'s "all_downloaded" check).
    (tmp_path / "hot-100-current.csv").write_text("stub")
    (tmp_path / "billboard-200-current.csv").write_text("stub")
    _seed_charts_fully_synced(session)

    view = ChartsView(controller)
    view.show()

    assert not view.download_btn.isVisible()
    assert view.fetch_btn.isVisible()
    assert view.match_btn.isVisible()
    assert view.playlists_btn.isVisible()  # AC12: same rule as match_btn
    assert "2023-12-31" in view.status_label.text()


def test_week_browser_tab_populates_from_seeded_entry(qapp, session, controller, tmp_path):
    (tmp_path / "hot-100-current.csv").write_text("stub")
    (tmp_path / "billboard-200-current.csv").write_text("stub")
    _seed_charts_fully_synced(session)

    view = ChartsView(controller)

    assert view.week_tab.chart_combo.count() == 2
    assert view.week_tab.week_combo.count() == 1
    assert view.week_tab.table.topLevelItemCount() == 1
    item = view.week_tab.table.topLevelItem(0)
    assert item.text(1) == "Last Christmas"
    # Matched entries (match_score=1.0) are tinted via confidence_color
    # instead of a dedicated checkmark column (see chart_entry_table.py).
    assert item.foreground(1).color() == confidence_color(1.0)


def test_search_tab_finds_seeded_entry(qapp, session, controller, tmp_path):
    (tmp_path / "hot-100-current.csv").write_text("stub")
    (tmp_path / "billboard-200-current.csv").write_text("stub")
    _seed_charts_fully_synced(session)

    view = ChartsView(controller)
    view.search_tab.search_box.setText("christmas")
    view.search_tab._run_search()  # bypass the debounce timer for the test

    assert view.search_tab.table.topLevelItemCount() == 1
    assert view.search_tab.table.topLevelItem(0).text(1) == "Last Christmas"


def test_revisit_refresh_preserves_selected_chart_in_all_tabs(qapp, session, controller, tmp_path):
    # Regression: main_window's revisit dispatch re-calls ChartsView.load_charts()
    # every time the user navigates back, which re-runs each tab's set_charts().
    # A specific-chart selection in the Chart: combo must survive that.
    (tmp_path / "hot-100-current.csv").write_text("stub")
    (tmp_path / "billboard-200-current.csv").write_text("stub")
    _seed_charts_fully_synced(session)

    view = ChartsView(controller)

    view.week_tab.chart_combo.setCurrentText("Billboard 200")
    view.search_tab.chart_combo.setCurrentText("Billboard 200")
    view.recommendations_tab.chart_combo.setCurrentText("Billboard 200")

    view.load_charts()  # simulate leaving and re-entering the Charts view

    assert view.week_tab.chart_combo.currentText() == "Billboard 200"
    assert view.search_tab.chart_combo.currentText() == "Billboard 200"
    assert view.recommendations_tab.chart_combo.currentText() == "Billboard 200"
