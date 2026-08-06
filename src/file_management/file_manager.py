"""Module for managing music library files and directories with enhanced UX"""

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from PySide6.QtCore import Signal
from sqlalchemy.exc import SQLAlchemyError

from src.common.cancellable_worker import CancellableWorker
from src.core.asset_paths import config
from src.core.logger_config import logger

CONFIG_FILE = config("import_paths.json")

# Progress-bar allocation across the three phases of organization. Analysis
# only does a cheap stat() per track, so it gets a small slice; execution
# (mkdir + move + verify + batched DB update) is the real bottleneck and
# gets the bulk of the bar; cleanup gets the remainder.
ANALYSIS_PROGRESS_SHARE = 10
EXECUTION_PROGRESS_SHARE = 85
CLEANUP_PROGRESS_START = ANALYSIS_PROGRESS_SHARE + EXECUTION_PROGRESS_SHARE  # 95


class FileOrganizer(CancellableWorker):
    """Background worker for file organization with preview and confirmation"""

    progress_updated = Signal(int, str)  # percent, current file
    analysis_complete = Signal(list)  # operations
    finished = Signal(bool, int)  # success, files_moved
    cleanup_progress = Signal(int, str)  # percent, current directory

    # Physical file moves happen one at a time (fast — usually a rename), but
    # DB updates and move-log writes are batched at these sizes so a large
    # organize run doesn't pay a full commit/log-rewrite per track.
    DB_BATCH_SIZE = 50
    LOG_BATCH_SIZE = 50

    def __init__(self, root: Path, controller):
        super().__init__()
        self.root = root
        self.controller = controller
        self.operations = []
        self.approved_operations = []
        self._waiting_for_approval = False
        self._approval_received = False

    def run(self):
        """Main organization process with analysis and execution phases"""
        success = False
        files_moved = 0

        try:
            logger.info("FileOrganizer: Starting analysis phase")
            # Phase 1: Analysis - use controller to get tracks
            tracks = self.controller.get.get_all_entities("Track")

            self.progress_updated.emit(0, "Analyzing file organization...")
            operations = self._analyze_organization(tracks)
            logger.info(
                f"FileOrganizer: Analysis complete - {len(operations)} operations"
            )

            # Store operations for later execution
            self.operations = operations

            # Emit analysis complete for preview dialog
            self.analysis_complete.emit(operations)

            logger.info("FileOrganizer: Waiting for user approval...")

            # Wait for user approval
            self._waiting_for_approval = True
            while self._waiting_for_approval and not self.is_cancelled:
                self.msleep(100)  # Sleep briefly to avoid busy waiting

            logger.info(f"FileOrganizer: Approval received, cancelled: {self.is_cancelled}")

            # Phase 2: Execution
            if not self.is_cancelled and self.approved_operations:
                logger.info(
                    f"FileOrganizer: Starting execution phase with {len(self.approved_operations)} approved operations"
                )
                files_moved = self._execute_organization()
                logger.info(
                    f"FileOrganizer: Execution complete - moved {files_moved} files"
                )

                # Phase 3: Cleanup empty directories
                if not self.is_cancelled:
                    logger.info("FileOrganizer: Starting cleanup phase")
                    self._cleanup_empty_directories()

            success = not self.is_cancelled
            logger.info(
                f"FileOrganizer: Process complete - success: {success}, files_moved: {files_moved}"
            )

        except Exception as e:
            # Intentional broad boundary catch: this is the top-level run()
            # loop of a QThread worker and must not let an exception kill the
            # thread silently — log it (with traceback) and let `finally`
            # still report completion to the UI.
            logger.error(f"Organization failed: {e}")
            import traceback

            logger.error(traceback.format_exc())
        finally:
            self._release_db_session()
            self.finished.emit(success, files_moved)

    def user_approval_received(self, approved_ops: List[Dict]):
        """Called when user makes a decision in the preview dialog"""
        logger.info(f"FileOrganizer: User approved {len(approved_ops)} operations")
        self.approved_operations = approved_ops
        self._waiting_for_approval = False
        self._approval_received = True

    def user_cancelled(self):
        """Called when user cancels in the preview dialog"""
        logger.info("FileOrganizer: User cancelled organization")
        self._waiting_for_approval = False
        self.request_cancel()

    def _analyze_organization(self, tracks) -> List[Dict]:
        """Analyze organization needs and build the list of pending move operations."""
        operations = []
        total = len(tracks)

        for idx, track in enumerate(tracks):
            if self.is_cancelled:
                break

            self.progress_updated.emit(
                # Analysis is a cheap per-track stat() check, not the real
                # bottleneck — give it a small slice so the bar doesn't race
                # to the halfway point and then crawl through the actually
                # expensive move/DB work below.
                int((idx + 1) / total * ANALYSIS_PROGRESS_SHARE),
                f"Analyzing: {track.track_name or 'Unknown'}",
            )

            if not track.track_file_path or not Path(track.track_file_path).exists():
                continue  # Skip tracks without files

            current_path = Path(track.track_file_path)
            expected_path = self._get_expected_path(track)

            if self._paths_match_exactly(current_path, expected_path):
                continue  # Skip - already in correct location

            operations.append(
                {
                    "track": track,
                    "current_path": current_path,
                    "expected_path": expected_path,
                }
            )

        return operations

    def _get_expected_path(self, track) -> Path:
        """Build expected path according to schema"""
        # Get album artist (fallback to first track artist)
        album_artists = track.album.album_artists if track.album else []
        if album_artists:
            album_artist = album_artists[0].artist_name
        elif track.artists:
            album_artist = track.artists[0].artist_name
        else:
            album_artist = "Unknown Artist"

        # Get album title (fallback to "Unknown Album")
        album_name = track.album.album_name if track.album else "Unknown Album"

        # Sanitize folder names
        album_artist = self._sanitize_filename(album_artist)
        album_name = self._sanitize_filename(album_name)

        # Build target path
        target_dir = self.root / album_artist / album_name

        # Build filename
        track_num = f"{track.track_number:02d}" if track.track_number else "01"
        track_name = self._sanitize_filename(track.track_name or "Unknown Track")
        extension = (
            Path(track.track_file_path).suffix if track.track_file_path else ".mp3"
        )

        return target_dir / f"{track_num} - {track_name}{extension}"

    def _paths_match_exactly(self, current: Path, expected: Path) -> bool:
        """Check if file is already in exact correct location"""
        return current.resolve() == expected.resolve()

    def _execute_organization(self) -> int:
        """Execute all approved file moves.

        Physical moves happen one at a time, but DB updates and move-log
        writes are batched (see DB_BATCH_SIZE / LOG_BATCH_SIZE) instead of
        committing/rewriting the log after every single track — with a few
        thousand tracks, per-track commits and full-log rewrites dominate
        runtime far more than the actual file moves do.
        """
        files_moved = 0
        total = len(self.approved_operations)
        logger.info(
            f"FileOrganizer._execute_organization: Starting with {total} operations"
        )

        pending_updates = []  # [(track, final_target_path, log_base), ...]
        log_buffer = []  # completed log entries not yet flushed to disk

        def flush_db_batch():
            nonlocal files_moved
            if not pending_updates:
                return

            bulk_values = [
                {"track_id": track.track_id, "track_file_path": str(final_path)}
                for track, final_path, _ in pending_updates
            ]
            batch_ok = self.controller.update.update_entities_bulk(
                "Track", bulk_values
            )

            if batch_ok:
                for track, final_path, log_base in pending_updates:
                    logger.info(f"Database updated for track_id: {track.track_id}")
                    log_buffer.append({**log_base, "status": "success"})
                    files_moved += 1
            else:
                # Batch failed as a whole — fall back to per-row updates so a
                # single bad row doesn't lose DB updates for the rest.
                for track, final_path, log_base in pending_updates:
                    try:
                        self.controller.update.update_entity(
                            "Track", track.track_id, track_file_path=str(final_path)
                        )
                        log_buffer.append({**log_base, "status": "success"})
                        files_moved += 1
                    except SQLAlchemyError as db_err:
                        logger.error(
                            f"DB update failed for track_id {track.track_id} after "
                            f"successful move to {final_path}: {db_err}"
                        )
                        log_buffer.append(
                            {
                                **log_base,
                                "status": "db_update_failed",
                                "reason": str(db_err),
                                "action_required": (
                                    f"File is at '{final_path}'. DB still points to "
                                    f"the old path. Manual fix needed."
                                ),
                            }
                        )
            pending_updates.clear()

        for idx, operation in enumerate(self.approved_operations):
            if self.is_cancelled:
                logger.info(
                    "FileOrganizer._execute_organization: Cancelled during execution"
                )
                break

            track = operation["track"]
            current_path = operation["current_path"]
            expected_path = operation["expected_path"]

            self.progress_updated.emit(
                ANALYSIS_PROGRESS_SHARE
                + int((idx + 1) / total * EXECUTION_PROGRESS_SHARE),
                f"Moving: {track.track_name or 'Unknown'}",
            )

            logger.info(f"FileOrganizer: Moving {current_path} -> {expected_path}")

            try:
                final_path, log_base = self._move_file_only(track, expected_path)
                if final_path is not None:
                    pending_updates.append((track, final_path, log_base))
                else:
                    logger.warning(
                        f"FileOrganizer: Failed to move file {idx + 1}/{total}"
                    )
                    log_buffer.append(log_base)
            except Exception:
                # Intentional broad boundary catch: _move_file_only() already
                # catches and reports all its own failures internally, so this
                # is a last-resort guard against a genuinely unexpected error
                # on one item in a large batch — it must not abort the whole
                # organize run and lose progress on everything already moved.
                logger.exception(f"FileOrganizer: Exception moving {current_path}")

            if len(pending_updates) >= self.DB_BATCH_SIZE:
                flush_db_batch()
            if len(log_buffer) >= self.LOG_BATCH_SIZE:
                self._flush_move_log(log_buffer)
                log_buffer.clear()

        # Flush whatever's left, including on cancellation, so nothing moved
        # or DB-updated so far is left unrecorded.
        flush_db_batch()
        self._flush_move_log(log_buffer)

        logger.info(
            f"FileOrganizer._execute_organization: Completed - {files_moved}/{total} files moved"
        )
        return files_moved

    def _cleanup_empty_directories(self):
        """Remove empty directories under root.

        Uses a fresh stat-based check at removal time rather than relying on
        the dirnames list from os.walk, which can become stale as siblings are
        removed during the same traversal.
        """
        self.progress_updated.emit(
            CLEANUP_PROGRESS_START, "Cleaning up empty directories..."
        )

        # Collect candidate dirs bottom-up (topdown=False) so children are
        # evaluated before their parents.
        candidate_dirs = []
        for dirpath, dirnames, filenames in os.walk(self.root, topdown=False):
            current_dir = Path(dirpath)
            if current_dir != self.root:
                candidate_dirs.append(current_dir)

        for idx, empty_dir in enumerate(candidate_dirs):
            if self.is_cancelled:
                break

            self.cleanup_progress.emit(
                int((idx + 1) / len(candidate_dirs) * 100),
                f"Removing: {empty_dir.name}",
            )

            try:
                # Re-check at removal time: rmdir() raises OSError if non-empty,
                # so this is inherently safe — it will never remove a dir that
                # still has contents.
                empty_dir.rmdir()
                logger.info(f"Removed empty directory: {empty_dir}")
            except OSError:
                # Directory is non-empty or already gone — skip silently.
                pass
            except Exception:
                # Intentional broad boundary catch: this loop can walk
                # thousands of directories left over from an organize run —
                # rmdir() itself only ever raises OSError (handled above),
                # but this is a last-resort guard so one genuinely unexpected
                # failure on a single directory doesn't abort cleanup of the
                # rest.
                logger.exception(f"Could not remove directory {empty_dir}")

    # ------------------------------------------------------------------
    # Move log
    # ------------------------------------------------------------------

    def _move_log_path(self) -> Path:
        """Path to the move log file in the library root."""
        return self.root / ".organize_log.json"

    def _flush_move_log(self, entries: List[Dict]) -> None:
        """Append a batch of move records to the persistent move log in one write.

        The log is a JSON array of objects:
          {
            "timestamp": "2026-02-23T14:05:00",
            "track_id": 42,
            "track_name": "Song Title",
            "from": "/old/path/song.mp3",
            "to": "/new/path/song.mp3",
            "status": "success" | "db_update_failed" | "failed"
          }

        Batching avoids re-reading and rewriting the whole (growing) JSON log
        file once per track, which made large organize runs effectively
        quadratic in the number of tracks moved.
        """
        if not entries:
            return

        log_path = self._move_log_path()
        try:
            if log_path.exists():
                with open(log_path, "r", encoding="utf-8") as f:
                    records = json.load(f)
            else:
                records = []
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"FileOrganizer: Corrupt move log, starting fresh: {e}")
            records = []

        records.extend(entries)

        try:
            with open(log_path, "w", encoding="utf-8") as f:
                json.dump(records, f, indent=2, default=str)
        except OSError as e:
            # Log failure is non-fatal — warn and continue
            logger.warning(f"FileOrganizer: Could not write move log: {e}")

    # ------------------------------------------------------------------
    # Core move
    # ------------------------------------------------------------------

    def _move_file_only(self, track, target_path: Path):
        """Move a track file to target_path on disk. Does not touch the DB.

        Safety guarantees:
          1. Source existence is verified before any action.
          2. The target path is verified to stay inside the library root
             before anything is created or moved.
          3. shutil.move is used for cross-device safety.
          4. Destination existence is verified after the move.

        Returns:
            (final_target_path, log_base) on success — log_base has no
            "status" yet; the caller fills it in once the DB update outcome
            (batched separately) is known.
            (None, log_entry) on failure — log_entry is already complete
            (status/reason set), since there's no DB step to wait on.
        """
        source_path = Path(track.track_file_path)
        log_base = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "track_id": track.track_id,
            "track_name": track.track_name or "Unknown",
            "from": str(source_path),
            "to": str(target_path),
        }

        try:
            # --- Pre-flight checks ---
            if not source_path.exists():
                logger.error(f"Source file does not exist: {source_path}")
                return None, {
                    **log_base,
                    "status": "failed",
                    "reason": "source not found",
                }

            # --- Containment check: never write outside the library root ---
            try:
                target_path.resolve().relative_to(self.root.resolve())
            except ValueError:
                logger.error(
                    f"Refusing to move outside library root: {target_path} "
                    f"is not under {self.root}"
                )
                return None, {
                    **log_base,
                    "status": "failed",
                    "reason": "target path escapes library root",
                }

            # --- Handle duplicate filenames at destination ---
            counter = 1
            original_target = target_path
            while target_path.exists():
                stem = original_target.stem
                target_path = original_target.with_name(
                    f"{stem}_{counter:02d}{original_target.suffix}"
                )
                counter += 1

            # Update log entry with the final (possibly de-duped) target path
            log_base["to"] = str(target_path)

            # --- Create destination directory ---
            target_path.parent.mkdir(parents=True, exist_ok=True)

            logger.info(f"Attempting to move: {source_path} -> {target_path}")
            shutil.move(str(source_path), str(target_path))

            # --- Verify destination ---
            if not target_path.exists():
                logger.error(
                    f"Move appeared to succeed but destination not found: {target_path}"
                )
                return None, {
                    **log_base,
                    "status": "failed",
                    "reason": "destination missing after move — DB NOT updated",
                }

            logger.info(f"File move verified: {source_path} -> {target_path}")
            return target_path, log_base

        except OSError as e:
            logger.error(f"Failed to move file {track.track_file_path}: {e}")
            import traceback

            logger.error(f"Move error details: {traceback.format_exc()}")
            return None, {
                **log_base,
                "status": "failed",
                "reason": str(e),
            }

    def _sanitize_filename(self, name):
        """Remove invalid characters from filenames"""
        if not name:
            return "Unknown"
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            name = name.replace(char, "_")
        name = name.strip()
        # "", ".", and ".." are valid path components that don't mean what a
        # tag value implies -- ".." in particular would walk the target
        # directory outside the library root. Refuse them rather than let
        # them silently redirect the move.
        if name in ("", ".", ".."):
            return "Unknown"
        return name

    def set_approved_operations(self, approved_ops: List[Dict]):
        """Set which operations to execute (called by preview dialog)"""
        self.approved_operations = approved_ops

    def cancel(self):
        """Request organization cancellation"""
        self.request_cancel()
