"""Chart CSV download parsing and ChartImportWorker incremental import."""

import csv
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from src.charts.chart_download import _safe_int, parse_chart_csv
from src.charts.workers.chart_import_worker import ChartImportWorker
from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_helpers.update import UpdateDB
from src.db.db_tables.base import Base
from src.db.db_tables.chart import Chart, ChartEntry

# ---------------------------------------------------------------------------
# Tests for chart_download.py's CSV parsing -- specifically _safe_int()'s
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value,expected", [("42", 42), ("-", None), ("NA", None), ("", None), (None, None)])
def test_safe_int_handles_all_observed_sentinels(value, expected):
    assert _safe_int(value) == expected


def test_parse_chart_csv_handles_mixed_sentinels(tmp_path):
    csv_path = tmp_path / "mixed.csv"
    with csv_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["chart_week", "current_week", "title", "performer", "last_week", "peak_pos", "wks_on_chart"])
        writer.writerow(["2023-12-30", "1", "Song A", "Artist A", "-", "1", "5"])
        writer.writerow(["2023-12-30", "2", "Song B", "Artist B", "NA", "2", "1"])
        writer.writerow(["2023-12-30", "3", "Song C", "Artist C", "3", "1", "10"])

    rows = list(parse_chart_csv(Path(csv_path)))

    assert rows[0]["last_week_position"] is None  # "-" sentinel
    assert rows[1]["last_week_position"] is None  # "NA" sentinel
    assert rows[2]["last_week_position"] == 3  # real value


# ---------------------------------------------------------------------------
# Tests for ChartImportWorker (src/charts/workers/chart_import_worker.py) against a
# ---------------------------------------------------------------------------


FIXTURE_CSV = Path(__file__).parent / "fixtures" / "hot-100-sample.csv"


class StubController:
    """Minimal controller stand-in exposing .get/.add/.update, backed
    directly by a scratch session -- never a real MusicController."""

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
def patch_csv_path(monkeypatch):
    """Point chart_data_path() at the fixture CSV regardless of chart_key,
    so the worker's normal file-lookup path is exercised unchanged."""
    monkeypatch.setattr(
        "src.charts.workers.chart_import_worker.chart_data_path",
        lambda name: str(FIXTURE_CSV),
    )


def _make_chart(session, last_synced_week=None):
    chart = Chart(
        chart_key="hot-100",
        chart_name="Billboard Hot 100",
        source_url="https://example.invalid/hot-100-current.csv",
        matched_entity_type="Track",
        last_synced_week=last_synced_week,
    )
    session.add(chart)
    session.commit()
    return chart


def test_first_import_loads_all_fixture_rows(session, controller):
    _make_chart(session)

    worker = ChartImportWorker(controller, "hot-100")
    worker.run()  # call synchronously -- no QThread/event loop needed to test run()'s logic

    entries = session.scalars(select(ChartEntry)).all()
    assert len(entries) == 6  # every row in the fixture CSV

    chart = session.scalar(select(Chart).where(Chart.chart_key == "hot-100"))
    assert chart.last_synced_week is not None
    assert chart.last_synced_week.isoformat() == "2023-12-30"
    assert chart.last_downloaded_at is not None


def test_fetch_updates_only_imports_rows_newer_than_cutoff(session, controller):
    import datetime

    # Simulate a prior import that already landed the 2023-12-23 week.
    _make_chart(session, last_synced_week=datetime.date(2023, 12, 23))

    worker = ChartImportWorker(controller, "hot-100")
    worker.run()

    entries = session.scalars(select(ChartEntry)).all()
    # Only the three 2023-12-30 rows should have been inserted.
    assert len(entries) == 3
    assert all(e.chart_week.isoformat() == "2023-12-30" for e in entries)


def test_imported_entry_fields_match_csv(session, controller):
    _make_chart(session)
    worker = ChartImportWorker(controller, "hot-100")
    worker.run()

    entry = session.scalar(
        select(ChartEntry).where(
            ChartEntry.chart_week == __import__("datetime").date(2023, 12, 23),
            ChartEntry.position == 1,
        )
    )
    assert entry.raw_title == "Last Christmas"
    assert entry.raw_performer == "Wham!"
    assert entry.last_week_position == 4
    assert entry.peak_position == 1
    assert entry.weeks_on_chart == 36
    assert entry.entity_id is None  # matching hasn't run yet
    assert entry.is_matched is False


def test_rerunning_import_is_idempotent_via_cutoff_not_dedup(session, controller):
    """A second run with no new upstream data should import nothing new,
    since last_synced_week already covers everything in the fixture."""
    _make_chart(session)
    ChartImportWorker(controller, "hot-100").run()
    first_count = len(session.scalars(select(ChartEntry)).all())

    ChartImportWorker(controller, "hot-100").run()
    second_count = len(session.scalars(select(ChartEntry)).all())

    assert first_count == second_count == 6
