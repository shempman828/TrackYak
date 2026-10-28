"""Background download of the Billboard chart CSVs to CHARTS_DIR."""

from pathlib import Path

from PySide6.QtCore import Signal
import requests

from src.charts.chart_download import CHART_FILES
from src.common.cancellable_worker import CancellableWorker
from src.foundation.asset_paths import chart_data_path
from src.foundation.logger_config import logger


class ChartDownloadWorker(CancellableWorker):
    """Download one or more chart CSVs to CHARTS_DIR."""

    progress = Signal(int, int)  # bytes_downloaded, total_bytes (0 without Content-Length)
    finished = Signal(dict)  # {chart_key: Path} for each file saved
    error = Signal(str)

    def __init__(self, chart_keys: list, parent=None):
        super().__init__(parent)
        self.chart_keys = chart_keys

    def run(self):
        """Stream each chart CSV to a .part file, then move it into place."""
        results = {}
        tmp = None
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
                    with tmp.open("wb") as f:
                        for chunk in resp.iter_content(chunk_size=1 << 16):
                            if self.is_cancelled:
                                break
                            f.write(chunk)
                            downloaded += len(chunk)
                            self.progress.emit(downloaded, total)

                if self.is_cancelled:
                    tmp.unlink(missing_ok=True)
                    break

                # Swap in whole, so parse_chart_csv() never reads a half-written file.
                tmp.replace(dest)
                tmp = None
                results[chart_key] = dest

            if not self.is_cancelled:
                self.finished.emit(results)
        except Exception as e:
            logger.error(f"ChartDownloadWorker failed: {e}", exc_info=True)
            if tmp is not None:
                tmp.unlink(missing_ok=True)
            self.error.emit(str(e))
        finally:
            self._release_db_session()
