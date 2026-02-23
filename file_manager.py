"""Module for managing music library files and directories with enhanced UX"""

import os
import shutil
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
        self._approval_received = False

    def _analyze_organization(self, tracks) -> Tuple[List[Dict], List[Dict]]:
        """Analyze organization needs and categorize operations"""
        auto_ops = []  # <90% similarity = obvious moves
        confirm_ops = []  # ≥90% similarity = needs confirmation
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
                auto_ops.append(operation)  # Very different = auto-approve
            else:
                confirm_ops.append(operation)  # Very similar = needs confirmation

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
        """Remove empty directories from root"""
        self.progress_updated.emit(95, "Cleaning up empty directories...")

        empty_dirs = []
        for dirpath, dirnames, filenames in os.walk(self.root, topdown=False):
            current_dir = Path(dirpath)
            if current_dir != self.root and not any(dirnames + filenames):
                empty_dirs.append(current_dir)

        for idx, empty_dir in enumerate(empty_dirs):
            if self._cancel:
                break

            self.cleanup_progress.emit(
                int((idx + 1) / len(empty_dirs) * 100), f"Removing: {empty_dir.name}"
            )

            try:
                empty_dir.rmdir()
                logger.info(f"Removed empty directory: {empty_dir}")
            except Exception as e:
                logger.warning(f"Could not remove directory {empty_dir}: {e}")

    def _move_track_file(self, track, target_path: Path) -> bool:
        """Move track file and update database - returns success"""
        try:
            source_path = Path(track.track_file_path)

            # Check if source file exists
            if not source_path.exists():
                logger.error(f"Source file does not exist: {source_path}")
                return False

            # Handle duplicate filenames
            counter = 1
            original_target = target_path
            while target_path.exists():
                stem = original_target.stem
                target_path = original_target.with_name(
                    f"{stem}_{counter:02d}{original_target.suffix}"
                )
                counter += 1

            # Create target directory if needed
            target_path.parent.mkdir(parents=True, exist_ok=True)

            logger.info(f"Attempting to move: {source_path} -> {target_path}")

            # Use shutil.move() instead of Path.rename() for cross-device moves
            shutil.move(str(source_path), str(target_path))
            logger.info(f"File move successful: {source_path} -> {target_path}")

            # Update database using controller's update method
            self.controller.update.update_entity(
                "Track", track.track_id, track_file_path=str(target_path)
            )
            logger.info(f"Database updated for track_id: {track.track_id}")

            return True

        except Exception as e:
            logger.error(f"Failed to move file {track.track_file_path}: {e}")
            import traceback

            logger.error(f"Move error details: {traceback.format_exc()}")
            return False

        except Exception as e:
            logger.error(f"Failed to move file {track.track_file_path}: {e}")
            import traceback

            logger.error(f"Move error details: {traceback.format_exc()}")
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
