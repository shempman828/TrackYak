"""
Module for writing database metadata to audio files.
Supports ID3v2.3/2.4 (MP3) and Vorbis comments (FLAC, OGG).

MetadataWriter only orchestrates: it pulls a track's data out of the
database (TrackDataAssembler), maps it to the tag vocabulary for the
file's format (ID3FrameBuilder / VorbisCommentBuilder), and hands that
off to the format-specific file writer (MP3FileWriter / FlacFileWriter /
OggFileWriter) that actually rewrites the file on disk. None of those
collaborators need the database controller except TrackDataAssembler.
"""

import os
import shutil
from typing import Any, Dict, List

from src.metadata.metadata_flac_file_writer import FlacFileWriter
from src.metadata.metadata_id3_frame_builder import ID3FrameBuilder
from src.metadata.metadata_mp3_file_writer import MP3FileWriter
from src.metadata.metadata_ogg_file_writer import OggFileWriter
from src.metadata.metadata_track_data import TrackDataAssembler
from src.metadata.metadata_vorbis_comment_builder import VorbisCommentBuilder
from src.metadata.metadata_writer_types import AudioFormat, WriteMode
from src.core.logger_config import logger
from src.core.status_utility import StatusManager

__all__ = ["MetadataWriter", "WriteMode", "AudioFormat"]


class MetadataWriter:
    """Writes database metadata (and artwork) to audio files."""

    def __init__(self, controller):
        self.controller = controller
        self.track_data = TrackDataAssembler(controller)
        self.id3_frame_builder = ID3FrameBuilder()
        self.vorbis_comment_builder = VorbisCommentBuilder()
        self.mp3_writer = MP3FileWriter()
        self.flac_writer = FlacFileWriter()
        self.ogg_writer = OggFileWriter()
        self.status_manager = StatusManager

    def detect_audio_format(self, file_path: str) -> AudioFormat:
        """Detect audio format from file extension."""
        ext = os.path.splitext(file_path)[1].lower()
        if ext in [".mp3", ".mp2", ".mp1"]:
            return AudioFormat.MP3
        elif ext in [".flac"]:
            return AudioFormat.FLAC
        elif ext in [".ogg", ".oga"]:
            return AudioFormat.OGG
        else:
            return AudioFormat.UNKNOWN

    def write_metadata_to_file(
        self, track_id: int, file_path: str, mode: WriteMode = WriteMode.UPDATE_EXISTING
    ) -> bool:
        """Write database metadata to audio file with complete file handling."""
        try:
            self.status_manager.start_task(
                f"Writing metadata to {os.path.basename(file_path)}"
            )

            if not os.path.exists(file_path):
                self.status_manager.end_task(
                    f"File not found: {os.path.basename(file_path)}", 3000
                )
                raise FileNotFoundError(f"Audio file not found: {file_path}")

            data = self.track_data.get_track_data(track_id)
            if not data or not data.get("track"):
                self.status_manager.end_task(f"No data for track ID: {track_id}", 3000)
                raise ValueError(f"No data found for track ID: {track_id}")

            audio_format = self.detect_audio_format(file_path)

            if audio_format == AudioFormat.MP3:
                result = self._write_id3(file_path, data, mode)
            elif audio_format in [AudioFormat.FLAC, AudioFormat.OGG]:
                result = self._write_vorbis(file_path, data, mode, audio_format)
            else:
                self.status_manager.end_task(
                    f"Unsupported format: {audio_format}", 3000
                )
                raise ValueError(f"Unsupported audio format: {audio_format}")

            if result:
                self.status_manager.end_task(
                    f"Updated {os.path.basename(file_path)}", 3000
                )
            else:
                self.status_manager.end_task(
                    f"Failed to update {os.path.basename(file_path)}", 3000
                )

            return result

        except Exception as e:
            logger.debug(f"Error writing metadata to {file_path}: {e}")
            self.status_manager.end_task(f"Error: {os.path.basename(file_path)}", 3000)
            return False

    def _write_id3(self, file_path: str, data: Dict[str, Any], mode: WriteMode) -> bool:
        new_frames = self.id3_frame_builder.build_frames(data)
        return self.mp3_writer.write_tags(file_path, new_frames, mode)

    def _write_vorbis(
        self,
        file_path: str,
        data: Dict[str, Any],
        mode: WriteMode,
        audio_format: AudioFormat,
    ) -> bool:
        """Write Vorbis metadata to a FLAC/OGG file.

        Backs up the file first: neither format writer manages its own
        backup for tag writes (unlike artwork writes, which do), so this
        is the one place that does.
        """
        backup_path = file_path + ".bak"
        try:
            shutil.copy2(file_path, backup_path)

            new_comments = self.vorbis_comment_builder.build_comments(data)

            if audio_format == AudioFormat.FLAC:
                success = self.flac_writer.write_tags(file_path, new_comments, mode)
            else:  # OGG
                success = self.ogg_writer.write_tags(file_path, new_comments, mode)

            if success:
                os.remove(backup_path)
            else:
                shutil.copy2(backup_path, file_path)
                os.remove(backup_path)

            return success

        except Exception as e:
            logger.debug(f"Error writing Vorbis metadata: {e}")
            if os.path.exists(backup_path):
                shutil.copy2(backup_path, file_path)
                os.remove(backup_path)
            return False

    def write_artwork_to_file(self, file_path: str, role: str, image_bytes: Any) -> bool:
        """
        Add/replace (image_bytes given) or remove (image_bytes=None) the
        `role` ("front"/"rear"/"liner") artwork in an audio file. Dispatches
        by extension - FLAC and MP3 are supported today, anything else
        returns False.
        """
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".flac":
            return self.flac_writer.write_artwork(file_path, role, image_bytes)
        elif ext == ".mp3":
            return self.mp3_writer.write_artwork(file_path, role, image_bytes)
        else:
            logger.debug(f"Unsupported format for artwork write: {file_path}")
            return False

    def write_metadata_to_track(
        self, track_id: int, mode: WriteMode = WriteMode.UPDATE_EXISTING
    ) -> bool:
        """Write metadata to a track's audio file using controller helpers."""
        try:
            track = self.controller.get.get_entity_object("Track", track_id=track_id)
            if not track or not track.track_file_path:
                self.status_manager.show_message(
                    f"Track {track_id} has no file path", 3000
                )
                logger.debug(f"Track {track_id} has no file path")
                return False

            if not os.path.exists(track.track_file_path):
                self.status_manager.show_message(
                    f"File not found: {track.track_file_path}", 3000
                )
                logger.debug(f"Track file not found: {track.track_file_path}")
                return False

            return self.write_metadata_to_file(track_id, track.track_file_path, mode)
        except Exception as e:
            logger.debug(f"Error writing metadata to track {track_id}: {e}")
            self.status_manager.show_message(f"Error updating track {track_id}", 3000)
            return False

    def batch_write_metadata(
        self, track_ids: List[int], mode: WriteMode = WriteMode.UPDATE_EXISTING
    ) -> Dict[int, bool]:
        """Write metadata to multiple tracks with progress reporting."""
        results = {}
        total = len(track_ids)

        self.status_manager.start_task(f"Updating metadata for {total} files")

        success_count = 0
        for i, track_id in enumerate(track_ids):
            logger.debug(f"Processing track {i + 1}/{total} (ID: {track_id})")

            progress_msg = f"Processing file {i + 1}/{total}"
            self.status_manager.show_message(progress_msg, 0)  # Persistent

            results[track_id] = self.write_metadata_to_track(track_id, mode)

            if results[track_id]:
                success_count += 1

        self.status_manager.end_task(
            f"Updated {success_count}/{total} files successfully", 5000
        )

        logger.debug(f"Batch write completed: {success_count}/{total} successful")

        return results
