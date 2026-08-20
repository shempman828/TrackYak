"""
audio_stats_worker.py

AudioStatsWorker: runs AudioStats.get_comprehensive_audio_stats() off the
GUI thread. Runs once per dialog session for the Audio Profile tab -- see
the module docstring in stats/audio.py for why it's a separate worker from
the rest of the dialog's data.
"""

from PySide6.QtCore import Signal
from sqlalchemy.exc import SQLAlchemyError

from src.common.cancellable_worker import CancellableWorker
from src.core.logger_config import logger


class AudioStatsWorker(CancellableWorker):
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, audio_stats, parent=None):
        super().__init__(parent)
        self.audio_stats = audio_stats

    def run(self):
        try:
            stats = self.audio_stats.get_comprehensive_audio_stats()
            self.finished.emit(stats)
        except SQLAlchemyError as e:
            logger.error(f"Error computing audio statistics: {e}")
            self.error.emit(str(e))
        finally:
            self._release_db_session()
