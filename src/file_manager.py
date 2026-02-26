"""Module for managing music library files and directories with enhanced UX"""

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from PySide6.QtCore import QThread, Signal

from asset_paths import config
from logger_config import logger

CONFIG_FILE = config("import_paths.json")
SUPPORTED_FORMATS = {".mp3", ".flac", ".wav", ".aac", ".ogg", ".m4a"}


class FileOrganizer(QThread):
    """Background worker for file organization with preview and confirmation"""

    progress_updated = Signal(int, str)  # percent, current file
    analysis_complete = Signal(list, list)  # auto_ops, confirm_ops
    finished = Signal(bool, int)  # success, files_moved
    cleanup_progress = Signal(int, str)  # percent, current directory

    def __init__(self, root: Path, controller):
        super().__init__()
        self.root = root
        self.controller = controller
        self._cancel = False
        self.auto_operations = []
        self.confirm_operations = []
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
            auto_ops, confirm_ops = self._analyze_organization(tracks)
            logger.info(
                f"FileOrganizer: Analysis complete - {len(auto_ops)} auto, {len(confirm_ops)} confirm"
            )

            # Store operations for later execution
            self.auto_operations = auto_ops
            self.confirm_operations = confirm_ops

            # Emit analysis complete for preview dialog
            self.analysis_complete.emit(auto_ops, confirm_ops)

            logger.info("FileOrganizer: Waiting for user approval...")

            # Wait for user approval
            self._waiting_for_approval = True
            while self._waiting_for_approval and not self._cancel:
                self.msleep(100)  # Sleep briefly to avoid busy waiting

            logger.info(f"FileOrganizer: Approval received, cancelled: {self._cancel}")

            # Phase 2: Execution
            if not self._cancel and self.approved_operations:
                logger.info(
                    f"FileOrganizer: Starting execution phase with {len(self.approved_operations)} approved operations"
                )
                files_moved = self._execute_organization()
                logger.info(
                    f"FileOrganizer: Execution complete - moved {files_moved} files"
                )

                # Phase 3: Cleanup empty directories
                if not self._cancel:
                    logger.info("FileOrganizer: Starting cleanup phase")
                    self._cleanup_empty_directories()

            success = not self._cancel
            logger.info(
                f"FileOrganizer: Process complete - success: {success}, files_moved: {files_moved}"
            )

        except Exception as e:
            logger.error(f"Organization failed: {e}")
            import traceback

            logger.error(traceback.format_exc())
        finally:
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
        self._cancel = True

    def _analyze_organization(self, tracks) -> Tuple[List[Dict], List[Dict]]:
        """Analyze organization needs and categorize operations.

        Similarity thresholds:
          < 0.9  → paths are clearly different → auto-approve (safe, obvious move)
          ≥ 0.9  → paths are nearly identical  → require confirmation (subtle rename/move)
        """
        auto_ops = []  # Low similarity  = clearly different folder  = auto-approve
        confirm_ops = []  # High similarity = subtle path difference    = needs confirmation
        total = len(tracks)

        for idx, track in enumerate(tracks):
            if self._cancel:
                break

            self.progress_updated.emit(
                int((idx + 1) / total * 50),  # First half for analysis
                f"Analyzing: {track.track_name or 'Unknown'}",
            )

            if not track.track_file_path or not Path(track.track_file_path).exists():
                continue  # Skip tracks without files

            current_path = Path(track.track_file_path)
            expected_path = self._get_expected_path(track)

            if self._paths_match_exactly(current_path, expected_path):
                continue  # Skip - already in correct location

            similarity = self._path_similarity(current_path, expected_path)
            operation = {
                "track": track,
                "current_path": current_path,
                "expected_path": expected_path,
                "similarity": similarity,
                "similarity_percent": int(similarity * 100),
            }

            if similarity < 0.9:
                # Paths are clearly different — safe to auto-approve
                auto_ops.append(operation)
            else:
                # Paths are nearly identical — subtle change, ask user to confirm
                confirm_ops.append(operation)

        return auto_ops, confirm_ops

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

    def _path_similarity(self, current: Path, expected: Path) -> float:
        """Calculate path similarity (0.0 to 1.0) using sequence matching"""
        from difflib import SequenceMatcher

        # Normalize paths for better comparison
        current_str = str(current).lower().replace("\\", "/")
        expected_str = str(expected).lower().replace("\\", "/")

        return SequenceMatcher(None, current_str, expected_str).ratio()

    def _execute_organization(self) -> int:
        """Execute all approved file moves"""
        files_moved = 0
        total = len(self.approved_operations)
        logger.info(
            f"FileOrganizer._execute_organization: Starting with {total} operations"
        )

        for idx, operation in enumerate(self.approved_operations):
            if self._cancel:
                logger.info(
                    "FileOrganizer._execute_organization: Cancelled during execution"
                )
                break

            track = operation["track"]
            current_path = operation["current_path"]
            expected_path = operation["expected_path"]

            self.progress_updated.emit(
                50 + int((idx + 1) / total * 45),
                f"Moving: {track.track_name or 'Unknown'}",
            )

            logger.info(f"FileOrganizer: Moving {current_path} -> {expected_path}")

            try:
                if self._move_track_file(track, expected_path):
                    files_moved += 1
                    logger.info(
                        f"FileOrganizer: Successfully moved file {idx + 1}/{total}"
                    )
                else:
                    logger.warning(
                        f"FileOrganizer: Failed to move file {idx + 1}/{total}"
                    )
            except Exception as e:
                logger.error(f"FileOrganizer: Exception moving {current_path}: {e}")

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
        self.progress_updated.emit(95, "Cleaning up empty directories...")

        # Collect candidate dirs bottom-up (topdown=False) so children are
        # evaluated before their parents.
        candidate_dirs = []
        for dirpath, dirnames, filenames in os.walk(self.root, topdown=False):
            current_dir = Path(dirpath)
            if current_dir != self.root:
                candidate_dirs.append(current_dir)

        for idx, empty_dir in enumerate(candidate_dirs):
            if self._cancel:
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
            except Exception as e:
                logger.warning(f"Could not remove directory {empty_dir}: {e}")

    # ------------------------------------------------------------------
    # Move log
    # ------------------------------------------------------------------

    def _move_log_path(self) -> Path:
        """Path to the move log file in the library root."""
        return self.root / ".organize_log.json"

    def _append_to_move_log(self, entry: Dict) -> None:
        """Append a single move record to the persistent move log.

        The log is a JSON array of objects:
          {
            "timestamp": "2026-02-23T14:05:00",
            "track_id": 42,
            "track_name": "Song Title",
            "from": "/old/path/song.mp3",
            "to": "/new/path/song.mp3",
            "status": "success" | "db_update_failed" | "failed"
          }
        """
        log_path = self._move_log_path()
        try:
            if log_path.exists():
                with open(log_path, "r", encoding="utf-8") as f:
                    records = json.load(f)
            else:
                records = []
        except Exception:
            records = []  # Corrupt log — start fresh rather than crash

        records.append(entry)

        try:
            with open(log_path, "w", encoding="utf-8") as f:
                json.dump(records, f, indent=2, default=str)
        except Exception as e:
            # Log failure is non-fatal — warn and continue
            logger.warning(f"FileOrganizer: Could not write move log: {e}")

    # ------------------------------------------------------------------
    # Core move
    # ------------------------------------------------------------------

    def _move_track_file(self, track, target_path: Path) -> bool:
        """Move a track file to target_path and update the database.

        Safety guarantees:
          1. Source existence is verified before any action.
          2. shutil.move is used for cross-device safety.
          3. Destination existence is verified BEFORE updating the DB —
             the DB is only changed once the file is confirmed present.
          4. Every outcome (success, partial failure, full failure) is
             written to the move log so nothing is silently lost.

        Returns True on full success (file moved + DB updated).
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
                self._append_to_move_log(
                    {**log_base, "status": "failed", "reason": "source not found"}
                )
                return False

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

            # --- Verify destination before touching DB ---
            if not target_path.exists():
                logger.error(
                    f"Move appeared to succeed but destination not found: {target_path}"
                )
                self._append_to_move_log(
                    {
                        **log_base,
                        "status": "failed",
                        "reason": "destination missing after move — DB NOT updated",
                    }
                )
                return False

            logger.info(f"File move verified: {source_path} -> {target_path}")

            # --- Update database only after file is confirmed present ---
            try:
                self.controller.update.update_entity(
                    "Track", track.track_id, track_file_path=str(target_path)
                )
                logger.info(f"Database updated for track_id: {track.track_id}")
                self._append_to_move_log({**log_base, "status": "success"})
                return True

            except Exception as db_err:
                # File has moved but DB update failed — log precisely so the
                # user can manually reconcile; do NOT attempt to move the file back.
                logger.error(
                    f"DB update failed for track_id {track.track_id} after successful "
                    f"move to {target_path}: {db_err}"
                )
                self._append_to_move_log(
                    {
                        **log_base,
                        "status": "db_update_failed",
                        "reason": str(db_err),
                        "action_required": (
                            f"File is at '{target_path}'. "
                            f"DB still points to '{source_path}'. Manual fix needed."
                        ),
                    }
                )
                return False

        except Exception as e:
            logger.error(f"Failed to move file {track.track_file_path}: {e}")
            import traceback

            logger.error(f"Move error details: {traceback.format_exc()}")
            self._append_to_move_log(
                {
                    **log_base,
                    "status": "failed",
                    "reason": str(e),
                }
            )
            return False

    def _sanitize_filename(self, name):
        """Remove invalid characters from filenames"""
        if not name:
            return "Unknown"
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            name = name.replace(char, "_")
        return name.strip()

    def set_approved_operations(self, approved_ops: List[Dict]):
        """Set which operations to execute (called by preview dialog)"""
        self.approved_operations = approved_ops

    def cancel(self):
        """Request organization cancellation"""
        self._cancel = True
