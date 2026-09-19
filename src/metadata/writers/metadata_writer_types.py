"""Shared enums for the metadata-writing subsystem.

Kept dependency-free (no imports from other metadata_writer_* modules)
so every write-path module - the per-format file writers and the
orchestrator - can import from here without circular imports.
"""

from enum import Enum


class WriteMode(Enum):
    """Modes for writing tags to files."""

    # Only add tags for fields missing on disk; never touch what's already there.
    ADD_ONLY = "add_only"
    # Drop every existing tag/frame the app itself manages; write only database tags.
    REPLACE_ALL = "replace_all"
    # Overwrite app-managed tags with database values; preserve everything else.
    UPDATE_EXISTING = "update_existing"


class AudioFormat(Enum):
    MP3 = "mp3"
    FLAC = "flac"
    OGG = "ogg"
    UNKNOWN = "unknown"
