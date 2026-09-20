"""import_worker.py"""

from pathlib import Path

import psutil
from PySide6.QtCore import Signal

from src.common.cancellable_worker import CancellableWorker
from src.foundation.logger_config import logger
from src.importing.library_import import ImportResult, TrackImporter
from src.library.library_artwork_consistency import ArtworkConsistencyChecker


class ImportWorker(CancellableWorker):
    """
    Background import worker with resource monitoring and graceful error handling.
    Processes audio files in batches with comprehensive progress tracking.
    """

    progress = Signal(int, int)  # current, total
    finished = Signal(int)  # successful_imports
    error_occurred = Signal(str)  # error_message
    resource_warning = Signal(str, float)  # warning_type, value
    art_conflicts = Signal(list)  # end-of-import artwork reconciliation conflicts

    def __init__(self, controller, paths: list[str]):
        super().__init__()
        self.controller = controller
        self.paths = [Path(path) for path in paths]
        self.importer = TrackImporter(controller)
        self.processed_count = 0
        self._resource_check_interval = 50
        self._memory_warning_threshold_mb = 500
        self._clear_cache_interval = 20
        # Reused across _check_resources calls: psutil.Process.cpu_percent()
        # reports usage since its *previous* call on the same instance, so a
        # fresh Process() every check would always report 0.0%.
        self._process = psutil.Process()
        # album_ids the import added at least one track to; scanned for
        # embedded-art disagreement once the file loop finishes.
        self.touched_album_ids: set[int] = set()

    def run(self):
        """Process all paths in the background with comprehensive monitoring."""
        try:
            successful_imports = self._process_all_files()
            logger.info(f"Import completed: {successful_imports} successful imports")
            self._emit_art_conflicts()
            self.finished.emit(successful_imports)
        except Exception as e:
            error_msg = f"Import worker failed: {e!s}"
            logger.exception(error_msg)
            self.error_occurred.emit(error_msg)
        finally:
            self._release_db_session()

    def _emit_art_conflicts(self):
        """Scan albums this import touched for cover-art disagreement and emit the conflicts."""
        # Runs even on a cancelled import (reconciles what was touched
        # before the cancel), and is best-effort: a scan failure must not
        # stop `finished` from being emitted.
        if not self.touched_album_ids:
            self.art_conflicts.emit([])
            return
        try:
            checker = ArtworkConsistencyChecker(self.controller, album_ids=self.touched_album_ids)
            checker.run()
            self.art_conflicts.emit(checker.conflicts)
        except Exception:
            logger.exception("Post-import artwork reconciliation scan failed")
            self.art_conflicts.emit([])

    def _process_all_files(self) -> int:
        """Collect and process all audio files, returning successful import count."""
        all_files = self._collect_audio_files()
        if not all_files:
            logger.warning("No audio files found in provided paths")
            return 0

        total_files = len(all_files)
        logger.info(f"Total files to process: {total_files}")
        self.progress.emit(0, total_files)

        successful_imports = 0
        for i, file_path in enumerate(all_files):
            if self.is_cancelled:
                logger.info("Import stopped by user request")
                break

            self.processed_count = i + 1
            successful_imports += self._process_single_file(file_path, i, total_files)
            self._perform_periodic_resource_check(i)

        return successful_imports

    def _collect_audio_files(self) -> list[Path]:
        """Collect all audio files from all provided paths."""
        all_files = []
        for path in self.paths:
            if self.is_cancelled:
                break
            if path.exists():
                files = self.importer.process_path(str(path))
                all_files.extend([Path(f) for f in files])
                logger.info(f"Found {len(files)} files in {path}")
            else:
                logger.warning(f"Path does not exist: {path}")
        return all_files

    def _process_single_file(self, file_path: Path, index: int, total: int) -> int:
        """Process a single file and return 1 if newly imported, 0 otherwise."""
        try:
            result = self.importer.add_track(str(file_path))
            if result is ImportResult.IMPORTED and self.importer.last_imported_album_id is not None:
                self.touched_album_ids.add(self.importer.last_imported_album_id)
            self.progress.emit(index + 1, total)

            # Periodically detach committed entities from the SQLAlchemy
            # session's identity map. Every add_entity()/get_entity_object()
            # call during import leaves its object cached in the session, and
            # since the session lives for the whole import (and beyond), that
            # map grows without bound over a large library.
            if index % self._clear_cache_interval == 0:
                self.controller.get.session.expunge_all()
                logger.debug("Expunged ORM session cache to free memory")

            return 1 if result is ImportResult.IMPORTED else 0
        except MemoryError:
            # Clear the session's identity map and try to continue
            self.controller.get.session.expunge_all()
            error_msg = f"Memory error processing file {index + 1}/{total}: {file_path}"
            logger.error(error_msg)
            self.error_occurred.emit(error_msg)
        except Exception as e:
            error_msg = f"Error processing {file_path.name}: {str(e)[:200]}"
            logger.exception(error_msg)
        return 0

    def _perform_periodic_resource_check(self, index: int):
        """Check system resources at specified intervals."""
        if index % self._resource_check_interval == 0:
            self._check_resources()

    def _check_resources(self):
        """Check system resources and emit warnings if thresholds exceeded."""
        memory_mb = self._process.memory_info().rss / 1024 / 1024
        cpu_percent = self._process.cpu_percent()

        logger.info(
            f"Resource check - Processed: {self.processed_count}, "
            f"Memory: {memory_mb:.1f}MB, CPU: {cpu_percent:.1f}%"
        )

        if memory_mb > self._memory_warning_threshold_mb:
            warning_msg = f"High memory usage: {memory_mb:.1f}MB"
            logger.warning(warning_msg)
            self.resource_warning.emit("memory", memory_mb)
