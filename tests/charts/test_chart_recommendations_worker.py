"""
Tests for ChartRecommendationsWorker (src/charts/chart_recommendations_worker.py)
and the ChartRecommendationsTab reload plumbing that drives it, against a
scratch in-memory SQLite session + offscreen Qt -- never music_library.db.

The tab used to compute get_missing_popular / get_missing_gap_fills inline
in its Qt slots, scanning every chart-entry row (~1M for "All Charts") on
the UI thread and freezing the app for seconds on every reload -- including
the refresh() right after a bulk manual match. These lock in that the
compute now happens on the worker, is deferred until the tab is shown, and
routes results back to the right sub-table.
"""

import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.charts.chart_recommendations import (
    chart_week_years,
    get_missing_gap_fills,
    get_missing_popular,
)
from src.charts.chart_recommendations_tab import ChartRecommendationsTab
from src.charts.chart_recommendations_worker import (
    MODE_GAP_FILLS,
    MODE_POPULAR,
    ChartRecommendationsWorker,
)
from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_helpers.update import UpdateDB
from src.db.db_tables.base import Base
from src.db.db_tables.chart import Chart, ChartEntry


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


def _make_chart(session, chart_key="hot-100", name="Billboard Hot 100"):
    chart = Chart(
        chart_key=chart_key,
        chart_name=name,
        source_url=f"https://example.invalid/{chart_key}.csv",
        matched_entity_type="Track",
    )
    session.add(chart)
    session.commit()
    return chart


def _seed_multi_week_song(
    session, chart, raw_title="Runaround Sue", raw_performer="Dion", weeks=3, position=1
):
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


def _seed_gap_week(session, chart):
    """One chart week: positions 1-2 owned, 3 unmatched, 4-5 owned -- a
    combined owned run of 4 around the gap."""
    week = datetime.date(2023, 12, 30)
    for pos in (1, 2, 4, 5):
        session.add(
            ChartEntry(
                chart_id=chart.chart_id,
                chart_week=week,
                position=pos,
                raw_title=f"Owned {pos}",
                raw_performer="Someone",
                entity_type="Track",
                entity_id=1000 + pos,
                match_score=1.0,
            )
        )
    session.add(
        ChartEntry(
            chart_id=chart.chart_id,
            chart_week=week,
            position=3,
            peak_position=3,
            weeks_on_chart=5,
            raw_title="Gap Song",
            raw_performer="Gap Artist",
        )
    )
    session.commit()


def _seed_song_in_year(session, chart, year, raw_title, raw_performer="Someone", position=1):
    """One unmatched ChartEntry for `raw_title` in a fixed week of `year`."""
    session.add(
        ChartEntry(
            chart_id=chart.chart_id,
            chart_week=datetime.date(year, 6, 15),
            position=position,
            peak_position=1,
            weeks_on_chart=1,
            raw_title=raw_title,
            raw_performer=raw_performer,
        )
    )
    session.commit()


def _seed_gap_week_in_year(session, chart, year, gap_title):
    """One chart week in `year`: positions 1-2 owned, 3 unmatched (`gap_title`),
    4-5 owned -- a combined owned run of 4 around the gap."""
    week = datetime.date(year, 6, 15)
    for pos in (1, 2, 4, 5):
        session.add(
            ChartEntry(
                chart_id=chart.chart_id,
                chart_week=week,
                position=pos,
                raw_title=f"{gap_title} owned {pos}",
                raw_performer="Someone",
                entity_type="Track",
                entity_id=1000 + pos,
                match_score=1.0,
            )
        )
    session.add(
        ChartEntry(
            chart_id=chart.chart_id,
            chart_week=week,
            position=3,
            peak_position=3,
            weeks_on_chart=5,
            raw_title=gap_title,
            raw_performer="Gap Artist",
        )
    )
    session.commit()


def _run_worker(
    controller,
    mode,
    chart_ids=None,
    min_gap=4,
    limit=100,
    cancel=False,
    week_from=None,
    week_to=None,
):
    """Run the worker synchronously (no QThread), capturing its emissions --
    the same call-run()-directly approach as test_chart_import_worker.py."""
    worker = ChartRecommendationsWorker(
        controller, mode, chart_ids, min_gap, limit, week_from, week_to
    )
    finished, errors = [], []
    worker.finished.connect(lambda m, items: finished.append((m, items)))
    worker.error.connect(errors.append)
    if cancel:
        worker.request_cancel()
    worker.run()
    return finished, errors


def test_worker_emits_missing_popular(qapp, session, controller):
    chart = _make_chart(session)
    _seed_multi_week_song(session, chart, weeks=3)

    finished, errors = _run_worker(controller, MODE_POPULAR)

    assert errors == []
    assert len(finished) == 1
    mode, items = finished[0]
    assert mode == MODE_POPULAR
    assert [(i.raw_title, i.raw_performer) for i in items] == [("Runaround Sue", "Dion")]


def test_worker_emits_gap_fills(qapp, session, controller):
    chart = _make_chart(session)
    _seed_gap_week(session, chart)

    finished, errors = _run_worker(controller, MODE_GAP_FILLS, min_gap=4)

    assert errors == []
    mode, items = finished[0]
    assert mode == MODE_GAP_FILLS
    assert len(items) == 1
    assert items[0].raw_title == "Gap Song"
    assert items[0].gap_run_length == 4


def test_worker_respects_chart_ids_filter(qapp, session, controller):
    chart_a = _make_chart(session, "hot-100", "Billboard Hot 100")
    chart_b = _make_chart(session, "billboard-200", "Billboard 200")
    _seed_multi_week_song(session, chart_a, raw_title="Song A", weeks=2)
    _seed_multi_week_song(session, chart_b, raw_title="Song B", weeks=2)

    finished, _ = _run_worker(controller, MODE_POPULAR, chart_ids=[chart_a.chart_id])

    _mode, items = finished[0]
    assert [i.raw_title for i in items] == ["Song A"]


def test_cancelled_worker_emits_nothing(qapp, session, controller):
    chart = _make_chart(session)
    _seed_multi_week_song(session, chart, weeks=2)

    finished, errors = _run_worker(controller, MODE_POPULAR, cancel=True)

    assert finished == []
    assert errors == []


# --- year/decade filter: query layer --------------------------------------


def test_missing_popular_respects_week_bound(qapp, session, controller):
    # AC1
    chart = _make_chart(session)
    _seed_song_in_year(session, chart, 1994, "Nineties Hit", position=1)
    _seed_song_in_year(session, chart, 2001, "Aughts Hit", position=2)

    items = get_missing_popular(
        session, week_from=datetime.date(1994, 1, 1), week_to=datetime.date(1994, 12, 31)
    )

    assert [i.raw_title for i in items] == ["Nineties Hit"]


def test_missing_popular_no_bound_considers_every_year(qapp, session, controller):
    # AC2 -- regression guard: default (None, None) is unchanged
    chart = _make_chart(session)
    _seed_song_in_year(session, chart, 1994, "Nineties Hit", position=1)
    _seed_song_in_year(session, chart, 2001, "Aughts Hit", position=2)

    items = get_missing_popular(session)

    assert {i.raw_title for i in items} == {"Nineties Hit", "Aughts Hit"}


def test_gap_fills_respects_week_bound(qapp, session, controller):
    # AC3 -- a qualifying gap outside the window does not surface
    chart = _make_chart(session)
    _seed_gap_week_in_year(session, chart, 1994, "Nineties Gap")
    _seed_gap_week_in_year(session, chart, 2001, "Aughts Gap")

    items = get_missing_gap_fills(
        session, min_gap=4, week_from=datetime.date(1994, 1, 1), week_to=datetime.date(1994, 12, 31)
    )

    assert [i.raw_title for i in items] == ["Nineties Gap"]
    assert items[0].gap_run_length == 4


def test_worker_forwards_week_bound(qapp, session, controller):
    # AC4
    chart = _make_chart(session)
    _seed_song_in_year(session, chart, 1994, "Nineties Hit")

    excluded, _ = _run_worker(
        controller,
        MODE_POPULAR,
        week_from=datetime.date(2000, 1, 1),
        week_to=datetime.date(2000, 12, 31),
    )
    assert excluded[0][1] == []

    included, _ = _run_worker(
        controller,
        MODE_POPULAR,
        week_from=datetime.date(1994, 1, 1),
        week_to=datetime.date(1994, 12, 31),
    )
    assert [i.raw_title for i in included[0][1]] == ["Nineties Hit"]


def test_chart_week_years_distinct_sorted(qapp, session, controller):
    chart = _make_chart(session)
    for pos, year in enumerate((2024, 1958, 1994, 1958, 1990), start=1):
        _seed_song_in_year(session, chart, year, f"Song {pos}", position=pos)

    assert chart_week_years(session) == [1958, 1990, 1994, 2024]


def test_chart_week_years_empty(qapp, session, controller):
    # AC10 (query half)
    assert chart_week_years(session) == []


# --- tab plumbing -----------------------------------------------------------


@pytest.fixture
def sync_worker(monkeypatch):
    """Make ChartRecommendationsWorker.start() run on the calling thread, so
    the tab's reload path executes end-to-end without a real QThread (and
    keeps using the in-memory session, which is bound to this thread)."""
    monkeypatch.setattr(ChartRecommendationsWorker, "start", ChartRecommendationsWorker.run)


def test_tab_defers_compute_until_shown(qapp, session, controller, sync_worker):
    chart = _make_chart(session)
    _seed_multi_week_song(session, chart, weeks=2)

    tab = ChartRecommendationsTab(controller)
    tab.set_charts([chart])  # background tab, never shown -> no compute yet
    assert tab.popular_table.topLevelItemCount() == 0

    tab._ever_shown = True
    tab._reload()
    assert tab.popular_table.topLevelItemCount() == 1


def test_tab_routes_results_to_the_active_sub_table(qapp, session, controller, sync_worker):
    chart = _make_chart(session)
    _seed_gap_week(session, chart)
    _seed_multi_week_song(session, chart, raw_title="Popular Song", weeks=3, position=10)

    tab = ChartRecommendationsTab(controller)
    tab._ever_shown = True
    tab.set_charts([chart])
    assert tab.popular_table.topLevelItemCount() > 0
    assert tab.gap_table.topLevelItemCount() == 0

    tab.sub_tabs.setCurrentIndex(1)  # Gap Fills -> _on_sub_tab_changed -> _reload
    assert tab.gap_table.topLevelItemCount() == 1
    assert tab.gap_table.topLevelItem(0).text(0) == "Gap Song"


def test_tab_refresh_rebuilds_after_a_match(qapp, session, controller, sync_worker):
    chart = _make_chart(session)
    _seed_multi_week_song(session, chart, weeks=3)

    tab = ChartRecommendationsTab(controller)
    tab._ever_shown = True
    tab.set_charts([chart])
    assert tab.popular_table.topLevelItemCount() == 1

    # Resolve every row of that song, as a bulk manual match would.
    controller.update.update_entity_by_filter(
        "ChartEntry",
        {"chart_id": chart.chart_id, "raw_title": "Runaround Sue"},
        entity_type="Track",
        entity_id=42,
        match_score=1.0,
    )
    tab.refresh()
    assert tab.popular_table.topLevelItemCount() == 0


def test_tab_coalesces_reload_while_worker_running(qapp, session, controller):
    tab = ChartRecommendationsTab(controller)
    tab._ever_shown = True

    class _StillRunning:
        def isRunning(self):
            return True

    tab._worker = _StillRunning()
    tab._reload()
    assert tab._reload_pending is True


# --- year/decade filter: tab menu ----------------------------------------


def _top_level_labels(menu):
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def _submenu(menu, label):
    return next(a.menu() for a in menu.actions() if a.text() == label)


def _trigger(menu, label):
    next(a for a in menu.actions() if a.text() == label).trigger()


def _seed_years(session, chart, years):
    for pos, year in enumerate(years, start=1):
        _seed_song_in_year(session, chart, year, f"Song {year}", position=pos)


def test_tab_builds_decade_submenus_from_populated_years(qapp, session, controller):
    # AC5
    chart = _make_chart(session)
    _seed_years(session, chart, [1958, 1959, 1990, 1991, 1994, 2024])

    tab = ChartRecommendationsTab(controller)
    tab.set_charts([chart])

    assert _top_level_labels(tab._year_menu) == ["All Years", "1950s", "1990s", "2020s"]
    assert _top_level_labels(_submenu(tab._year_menu, "1990s")) == [
        "Entire decade",
        "1990",
        "1991",
        "1994",
    ]


def test_tab_select_specific_year_sets_bound_and_reloads(qapp, session, controller, sync_worker):
    # AC6
    chart = _make_chart(session)
    _seed_song_in_year(session, chart, 1994, "Nineties Hit", position=1)
    _seed_song_in_year(session, chart, 2001, "Aughts Hit", position=2)

    tab = ChartRecommendationsTab(controller)
    tab._ever_shown = True
    tab.set_charts([chart])
    assert tab.popular_table.topLevelItemCount() == 2

    _trigger(_submenu(tab._year_menu, "1990s"), "1994")

    assert tab._week_from == datetime.date(1994, 1, 1)
    assert tab._week_to == datetime.date(1994, 12, 31)
    assert tab.year_button.text() == "1994"
    assert tab.popular_table.topLevelItemCount() == 1
    assert tab.popular_table.topLevelItem(0).text(0) == "Nineties Hit"


def test_tab_select_entire_decade(qapp, session, controller, sync_worker):
    # AC7
    chart = _make_chart(session)
    _seed_years(session, chart, [1991, 1996])

    tab = ChartRecommendationsTab(controller)
    tab._ever_shown = True
    tab.set_charts([chart])

    _trigger(_submenu(tab._year_menu, "1990s"), "Entire decade")

    assert tab._week_from == datetime.date(1990, 1, 1)
    assert tab._week_to == datetime.date(1999, 12, 31)
    assert tab.year_button.text() == "1990s"


def test_tab_select_all_years_clears_bound(qapp, session, controller, sync_worker):
    # AC8
    chart = _make_chart(session)
    _seed_song_in_year(session, chart, 1994, "Nineties Hit", position=1)
    _seed_song_in_year(session, chart, 2001, "Aughts Hit", position=2)

    tab = ChartRecommendationsTab(controller)
    tab._ever_shown = True
    tab.set_charts([chart])
    _trigger(_submenu(tab._year_menu, "1990s"), "1994")
    assert tab.popular_table.topLevelItemCount() == 1

    _trigger(tab._year_menu, "All Years")

    assert tab._week_from is None and tab._week_to is None
    assert tab.year_button.text() == "All Years"
    assert tab.popular_table.topLevelItemCount() == 2


def test_tab_year_selection_survives_set_charts(qapp, session, controller, sync_worker):
    # AC9
    chart = _make_chart(session)
    _seed_song_in_year(session, chart, 1994, "Nineties Hit", position=1)
    _seed_song_in_year(session, chart, 2001, "Aughts Hit", position=2)

    tab = ChartRecommendationsTab(controller)
    tab._ever_shown = True
    tab.set_charts([chart])
    _trigger(_submenu(tab._year_menu, "1990s"), "1994")

    tab.set_charts([chart])  # revisit-refresh rebuilds the menu

    assert tab._week_from == datetime.date(1994, 1, 1)
    assert tab._week_to == datetime.date(1994, 12, 31)
    assert tab.year_button.text() == "1994"
    assert tab.popular_table.topLevelItemCount() == 1


def test_tab_year_menu_empty_data(qapp, session, controller):
    # AC10 (tab half)
    tab = ChartRecommendationsTab(controller)
    assert _top_level_labels(tab._year_menu) == ["All Years"]
