"""
chart_import_worker.py

Parses an already-downloaded chart CSV and bulk-inserts new ChartEntry rows,
off the UI thread. "New" means chart_week strictly after Chart.last_synced_week
-- every row is filtered by date before insertion (not "stop at the first old
row"), so a future upstream reordering of the CSV can't silently skip rows.
"""

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Signal
from sqlalchemy import insert

from src.charts.chart_download import CHART_FILES, parse_chart_csv
from src.common.cancellable_worker import CancellableWorker
from src.core.asset_paths import chart_data_path
from src.core.logger_config import logger
from src.db.db_tables.chart import ChartEntry

CHUNK_SIZE = 5000


class ChartImportWorker(CancellableWorker):
    """
    Imports one chart's CSV into ChartEntry rows.

    Signals:
        progress(rows_imported, rows_total_estimate)
        finished(rows_imported)
        error(message)
    """

    progress = Signal(int, int)
    finished = Signal(int)
    error = Signal(str)

    def __init__(self, controller, chart_key: str, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.chart_key = chart_key

    def run(self):
        try:
            chart = self.controller.get.get_entity_object("Chart", chart_key=self.chart_key)
            if chart is None:
                raise ValueError(f"No Chart row registered for chart_key={self.chart_key!r}")

            cutoff = chart.last_synced_week
            filename, _ = CHART_FILES[self.chart_key]
            csv_path = Path(chart_data_path(filename))
            if not csv_path.exists():
                raise FileNotFoundError(f"Chart CSV not downloaded yet: {csv_path}")

            rows_total_estimate = self._count_data_rows(csv_path)

            buffer = []
            imported = 0
            max_week_seen = cutoff

            for row in parse_chart_csv(csv_path):
                if self.is_cancelled:
                    break
                if cutoff is not None and row["chart_week"] <= cutoff:
                    continue
                row["chart_id"] = chart.chart_id
                buffer.append(row)
                if max_week_seen is None or row["chart_week"] > max_week_seen:
                    max_week_seen = row["chart_week"]

                if len(buffer) >= CHUNK_SIZE:
                    imported += self._insert_chunk(buffer)
                    self.progress.emit(imported, rows_total_estimate)
                    buffer.clear()

            if buffer and not self.is_cancelled:
                imported += self._insert_chunk(buffer)
                self.progress.emit(imported, rows_total_estimate)

            if not self.is_cancelled and max_week_seen != cutoff:
                self.controller.update.update_entity(
                    "Chart",
                    chart.chart_id,
                    last_synced_week=max_week_seen,
                    last_downloaded_at=datetime.now(),
                )

            if not self.is_cancelled:
                self.finished.emit(imported)
        except Exception as e:
            logger.error(f"ChartImportWorker failed for {self.chart_key}: {e}", exc_info=True)
            self.error.emit(str(e))
        finally:
            self._release_db_session()

    @staticmethod
    def _count_data_rows(path: Path) -> int:
        """Cheap line count for a progress-bar total; off by the header row."""
        with open(path, "rb") as f:
            return sum(1 for _ in f) - 1

    def _insert_chunk(self, rows: list) -> int:
        """Bulk-insert one chunk via the house add_entities() helper. Falls
        back to a raw SQLite INSERT OR IGNORE for this chunk only if the
        chunk collides with uq_chart_entry_week_position (e.g. an
        interrupted prior import re-run) -- add_entities() rolls back and
        returns [] on any collision, since ChartEntry's surrogate PK means
        its own row-level dedup never fires here (see chart.py's docstring).
        """
        entities = self.controller.add.add_entities("ChartEntry", rows)
        if len(entities) == len(rows):
            return len(entities)

        logger.warning(
            f"add_entities rolled back a {len(rows)}-row ChartEntry chunk "
            "(likely a unique-constraint collision); retrying with INSERT OR IGNORE"
        )
        session = self.controller.add.session
        session.execute(insert(ChartEntry).prefix_with("OR IGNORE"), rows)
        session.commit()
        # SQLAlchemy's SQLite dialect uses implicit RETURNING for an
        # executemany insert against an autoincrement PK, which yields an
        # IteratorResult that doesn't reliably expose .rowcount -- and even
        # a plain rowcount wouldn't distinguish "inserted" from "ignored"
        # for OR IGNORE. Report the chunk size as an upper bound; this only
        # affects the progress-bar figure for this rare collision-recovery
        # path, not correctness of what was actually inserted.
        return len(rows)
