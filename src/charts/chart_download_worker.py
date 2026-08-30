"""
chart_download_worker.py

Streams the Billboard chart CSV(s) to disk off the UI thread, following the
CancellableWorker pattern (see src/musicbrainz/musicbrainz_worker.py for the
canonical shape this mirrors).
"""

from pathlib import Path

from PySide6.QtCore import Signal
import requests

from src.charts.chart_download import CHART_FILES
from src.common.cancellable_worker import CancellableWorker
from src.core.asset_paths import chart_data_path
from src.core.logger_config import logger


class ChartDownloadWorker(CancellableWorker):
    """Downloads one or more chart CSVs to CHARTS_DIR.

    Signals:
        progress(bytes_downloaded, total_bytes) -- total_bytes is 0 if the
            server didn't send a Content-Length header.
        finished(dict) -- {chart_key: Path} for each file successfully saved.
        error(message)
    """

    progress = Signal(int, int)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, chart_keys: list, parent=None):
        super().__init__(parent)
        self.chart_keys = chart_keys

    def run(self):
        results = {}
        try:
            for chart_key in self.chart_keys:
                if self.is_cancelled:
                    break
                filename, url = CHART_FILES[chart_key]
                dest = Path(chart_data_path(filename))
                tmp = dest.with_suffix(dest.suffix + ".part")

                with requests.get(url, stream=True, timeout=30) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get("Content-Length", 0))
                    downloaded = 0
                    with open(tmp, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=1 << 16):
                            if self.is_cancelled:
                                break
                            f.write(chunk)
                            downloaded += len(chunk)
                            self.progress.emit(downloaded, total)

                if self.is_cancelled:
                    tmp.unlink(missing_ok=True)
                    break

                # Atomic-ish swap so a crash/cancel mid-download never leaves
                # a half-written file at the path parse_chart_csv() reads.
                tmp.replace(dest)
                results[chart_key] = dest

            if not self.is_cancelled:
                self.finished.emit(results)
        except Exception as e:
            logger.error(f"ChartDownloadWorker failed: {e}", exc_info=True)
            self.error.emit(str(e))
        finally:
            self._release_db_session()
