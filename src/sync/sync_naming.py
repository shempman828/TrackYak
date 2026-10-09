"""On-device file and playlist naming shared by the sync and prune passes."""

import hashlib
from pathlib import Path

from src.sync.transcode import LOSSLESS_EXTENSIONS, is_lossless_path

# NAME_MAX is 255 bytes on ext4/vfat/exFAT; the headroom covers ".part"/MTP temp suffixes.
MAX_FILENAME_BYTES = 240

# Only these extensions are ever pruned from music/; anything else there is left alone.
_LOSSY_EXTENSIONS = {".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wma", ".mp4"}
PRUNABLE_AUDIO_EXTENSIONS = LOSSLESS_EXTENSIONS | _LOSSY_EXTENSIONS

UNKNOWN_ARTIST = "Unknown Artist"
UNTITLED = "Untitled"


def clean_component(s: str) -> str:
    """Strip a string down to chars that are safe in a filename."""
    return "".join(c for c in (s or "") if c.isalnum() or c in (" ", "-", "_")).strip()


def clamp_stem(stem: str, ext: str) -> str:
    """Return `stem + ext` clamped to MAX_FILENAME_BYTES, tagged with a stem hash when truncated."""
    budget = MAX_FILENAME_BYTES - len(ext.encode("utf-8"))
    if len(stem.encode("utf-8")) <= budget:
        return f"{stem}{ext}"
    # The hash tag keeps two different long names from collapsing onto one file.
    tag = f" ~{hashlib.md5(stem.encode('utf-8')).hexdigest()[:8]}"
    keep = budget - len(tag)
    truncated = stem.encode("utf-8")[:keep].decode("utf-8", "ignore").rstrip()
    return f"{truncated}{tag}{ext}"


def safe_filename(artist: str, title: str, ext: str) -> str:
    """Build a filesystem-safe 'Artist - Title.ext' name."""
    # An all-symbol artist/title would otherwise leave "Artist - .flac".
    stem = f"{clean_component(artist) or UNKNOWN_ARTIST} - {clean_component(title) or UNTITLED}"
    return clamp_stem(stem, ext)


def predicted_device_filename(track: dict, transcode_to_mp3: bool) -> str:
    """The on-device name for `track`; lossless becomes .mp3 only when transcoding is in effect."""
    src = track.get("file_path") or ""
    ext = ".mp3" if transcode_to_mp3 and is_lossless_path(src) else Path(src).suffix
    return safe_filename(track["artist"], track["title"], ext)


def safe_playlist_name(item: dict) -> str:
    """The on-disk stem for a playlist/mood's .m3u file."""
    cleaned = clean_component(item.get("name", ""))
    if cleaned:
        return cleaned
    # A name made only of symbols/emoji would otherwise write a hidden ".m3u".
    kind = item.get("kind", "playlist")
    return f"{kind}-{item.get(f'{kind}_id', 0)}"


def is_m3u_name(name: str) -> bool:
    """True for an .m3u file name."""
    return name.lower().endswith(".m3u")


def is_prunable_music_name(name: str) -> bool:
    """True only for names in this app's 'Artist - Title.ext' scheme, so hand-placed files survive."""
    return " - " in name and Path(name).suffix.lower() in PRUNABLE_AUDIO_EXTENSIONS
