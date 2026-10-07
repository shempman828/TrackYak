"""Shared enums for the metadata-writing subsystem."""

# Kept free of imports from other writer modules, so any write-path module can import it without a cycle.

from enum import Enum


class WriteMode(Enum):
    """How freshly-built tags combine with a file's existing tags."""

    # Only add tags for fields missing on disk; never touch what's already there.
    ADD_ONLY = "add_only"
    # Drop every existing tag/frame except embedded artwork; write only database tags.
    REPLACE_ALL = "replace_all"
    # Overwrite app-managed tags with database values; preserve everything else.
    UPDATE_EXISTING = "update_existing"


class AudioFormat(Enum):
    """Tag-writable audio container formats."""

    MP3 = "mp3"
    FLAC = "flac"
    OGG = "ogg"
    UNKNOWN = "unknown"
