"""Writes database metadata to audio files: ID3v2.3 for MP3, Vorbis comments for FLAC and Ogg Vorbis."""

from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from src.foundation.logger_config import logger
from src.metadata.writers.id3.metadata_id3_frame_builder import ID3FrameBuilder
from src.metadata.writers.id3.metadata_mp3_file_writer import MP3FileWriter
from src.metadata.writers.metadata_track_data import TrackDataAssembler
from src.metadata.writers.metadata_writer_backup import backup_file, discard_backup, restore_backup
from src.metadata.writers.metadata_writer_merge import id3_frame_key
from src.metadata.writers.metadata_writer_types import AudioFormat, WriteMode
from src.metadata.writers.vorbis.metadata_flac_file_writer import FlacFileWriter
from src.metadata.writers.vorbis.metadata_ogg_file_writer import OggFileWriter
from src.metadata.writers.vorbis.metadata_vorbis_comment_builder import VorbisCommentBuilder

__all__ = ["AudioFormat", "MetadataWriter", "WriteMode"]


class MetadataWriter:
    """Orchestrates database reads, tag building, and the per-format file writers."""

    def __init__(self, controller):
        """Create the data assembler, tag builders and per-format file writers."""
        self.controller = controller
        self.track_data = TrackDataAssembler(controller)
        self.id3_frame_builder = ID3FrameBuilder()
        self.vorbis_comment_builder = VorbisCommentBuilder()
        self.mp3_writer = MP3FileWriter()
        self.flac_writer = FlacFileWriter()
        self.ogg_writer = OggFileWriter()

    def detect_audio_format(self, file_path: str) -> AudioFormat:
        """Detect audio format from file extension."""
        ext = Path(file_path).suffix.lower()
        if ext in [".mp3", ".mp2", ".mp1"]:
            return AudioFormat.MP3
        if ext in [".flac"]:
            return AudioFormat.FLAC
        if ext in [".ogg", ".oga"]:
            return AudioFormat.OGG
        return AudioFormat.UNKNOWN

    def write_metadata_to_file(self, track_id: int, file_path: str, mode: WriteMode = WriteMode.UPDATE_EXISTING) -> bool:
        """Write a track's database metadata to file_path and clear its needs_tag_write flag on success."""
        # No per-file StatusManager message: callers loop over many tracks and show one summary.
        try:
            if not Path(file_path).exists():
                raise FileNotFoundError(f"Audio file not found: {file_path}")

            data = self.track_data.get_track_data(track_id)
            if not data or not data.get("track"):
                raise ValueError(f"No data found for track ID: {track_id}")

            audio_format = self.detect_audio_format(file_path)

            if audio_format == AudioFormat.MP3:
                result = self._write_id3(file_path, data, mode)
            elif audio_format in [AudioFormat.FLAC, AudioFormat.OGG]:
                result = self._write_vorbis(file_path, data, mode, audio_format)
            else:
                raise ValueError(f"Unsupported audio format: {audio_format}")

            if result:
                self.controller.update.update_entity("Track", track_id, needs_tag_write=0)

            return result

        except (FileNotFoundError, ValueError, KeyError, AttributeError, SQLAlchemyError) as e:
            logger.debug(f"Error writing metadata to {file_path}: {e}")
            return False

    def _write_id3(self, file_path: str, data: dict[str, Any], mode: WriteMode) -> bool:
        """Write ID3 frames to an MP3 file (MP3FileWriter manages its own backup)."""
        new_frames = self.id3_frame_builder.build_frames(data)
        return self.mp3_writer.write_tags(file_path, new_frames, mode)

    def _write_vorbis(self, file_path: str, data: dict[str, Any], mode: WriteMode, audio_format: AudioFormat) -> bool:
        """Write Vorbis comments to a FLAC/Ogg file inside one backup/restore."""
        backup_path = None
        try:
            # Refuses to overwrite a stale .bak, which may be the last good copy of the file.
            backup_path = backup_file(file_path)

            new_comments = self.vorbis_comment_builder.build_comments(data)
            writer = self.flac_writer if audio_format == AudioFormat.FLAC else self.ogg_writer
            success = writer.write_tags(file_path, new_comments, mode)

            if success:
                discard_backup(backup_path)
            else:
                restore_backup(file_path, backup_path)
            return success

        except (OSError, KeyError, AttributeError, SQLAlchemyError) as e:
            logger.debug(f"Error writing Vorbis metadata: {e}")
            if backup_path and Path(backup_path).exists():
                restore_backup(file_path, backup_path)
            return False

    def get_changed_tags(self, track_id: int, file_path: str) -> list[str]:
        """Return the sorted tag keys whose on-disk value differs from what the database would write."""
        if not Path(file_path).exists():
            return []

        data = self.track_data.get_track_data(track_id)
        if not data or not data.get("track"):
            return []

        audio_format = self.detect_audio_format(file_path)
        if audio_format == AudioFormat.MP3:
            return self._diff_id3(file_path, data)
        if audio_format in (AudioFormat.FLAC, AudioFormat.OGG):
            return self._diff_vorbis(file_path, data)
        return []

    def _diff_id3(self, file_path: str, data: dict[str, Any]) -> list[str]:
        """Return the frame keys (e.g. "TIT2", "TXXX:PLAYLIST") whose bytes differ on disk."""
        new_frames = self.id3_frame_builder.build_frames(data)
        new_by_key = {id3_frame_key(f): f for f in new_frames if len(f) >= 10}
        existing_by_key = self.mp3_writer.get_existing_frame_map(file_path)

        return sorted(key for key, frame_bytes in new_by_key.items() if existing_by_key.get(key) != frame_bytes)

    def _diff_vorbis(self, file_path: str, data: dict[str, Any]) -> list[str]:
        """Return the Vorbis comment names whose values differ on disk."""
        new_comments = self.vorbis_comment_builder.build_comments(data)

        file_data = Path(file_path).read_bytes()
        ext = Path(file_path).suffix.lower()
        existing_comments = self.flac_writer.raw_tag_extractor.extract_raw_tags(file_data, ext)

        sanitize = self.flac_writer.vorbis_writer.sanitize_value

        def _as_list(value, apply_sanitize=False):
            """Normalise a tag value to a list of non-empty strings, optionally sanitised."""
            if value is None:
                return []
            values = value if isinstance(value, list) else [value]
            values = [str(v) for v in values]
            if apply_sanitize:
                values = [sanitize(v) for v in values]
            return [v for v in values if v]

        changed = [tag for tag, value in new_comments.items() if _as_list(value, apply_sanitize=True) != _as_list(existing_comments.get(tag))]
        return sorted(changed)

    def sync_metadata_to_track(self, track_id: int) -> dict[str, Any]:
        """Write a track's file only when its tags differ; return {"success", "changed", "message"}."""
        try:
            track = self.controller.get.get_entity_object("Track", track_id=track_id)
            if not track or not track.track_file_path:
                return {"success": False, "changed": [], "message": "No file path on record"}

            if not Path(track.track_file_path).exists():
                return {"success": False, "changed": [], "message": "File not found on disk"}

            if self.detect_audio_format(track.track_file_path) == AudioFormat.UNKNOWN:
                return {"success": False, "changed": [], "message": "Unsupported file format"}

            changed = self.get_changed_tags(track_id, track.track_file_path)
            if not changed:
                return {"success": True, "changed": [], "message": "Already up to date"}

            success = self.write_metadata_to_file(track_id, track.track_file_path)
            return {"success": success, "changed": changed, "message": "Updated" if success else "Write failed"}
        except (OSError, KeyError, AttributeError, SQLAlchemyError) as e:
            logger.debug(f"Error syncing metadata for track {track_id}: {e}")
            return {"success": False, "changed": [], "message": str(e)}

    def write_artwork_to_file(self, file_path: str, role: str, image_bytes: Any) -> bool:
        """Add/replace (image_bytes given) or remove (None) role artwork in a FLAC or MP3 file; False otherwise."""
        ext = Path(file_path).suffix.lower()
        if ext == ".flac":
            return self.flac_writer.write_artwork(file_path, role, image_bytes)
        if ext == ".mp3":
            return self.mp3_writer.write_artwork(file_path, role, image_bytes)
        logger.debug(f"Unsupported format for artwork write: {file_path}")
        return False

    def write_metadata_to_track(self, track_id: int, mode: WriteMode = WriteMode.UPDATE_EXISTING) -> bool:
        """Write a track's database metadata to its own file."""
        try:
            track = self.controller.get.get_entity_object("Track", track_id=track_id)
            if not track or not track.track_file_path:
                logger.debug(f"Track {track_id} has no file path")
                return False

            if not Path(track.track_file_path).exists():
                logger.debug(f"Track file not found: {track.track_file_path}")
                return False

            return self.write_metadata_to_file(track_id, track.track_file_path, mode)
        except SQLAlchemyError as e:
            logger.debug(f"Error writing metadata to track {track_id}: {e}")
            return False
