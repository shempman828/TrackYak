"""Disposable SQLite thumbnail cache for embedded album art, validated by source-file mtime."""

# Every UI surface reads album art through this cache instead of reading audio
# tags on each repaint. A row stays valid while its source_track_path and
# source_mtime match the album's representative track, so a changed file just
# misses and regenerates. Agreement between tracks is checked only on demand by
# Tools -> "Artwork Conflicts…" (src/library/artwork/).

import contextlib
import io
from pathlib import Path
import sqlite3
import struct
import threading
import time

from PIL import Image
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from src.foundation.asset_paths import IMAGECACHE_DIR
from src.foundation.logger_config import logger
from src.image.image_blur import blur_enabled, blur_pixmap
from src.metadata.readers.metadata_artwork import ArtworkExtractor

DEFAULT_MAX_DIMENSION = 1024
DEFAULT_JPEG_QUALITY = 95

# After a failed write (cache file removed under the open handle, read-only
# remount), serve cached rows only for this long instead of re-reading and
# re-decoding art on every lookup just to fail the write again.
_DEGRADED_BACKOFF_SEC = 60.0

# PIL raises any of these for truncated, malformed or oversized image data.
_DECODE_ERRORS = (OSError, ValueError, SyntaxError, Image.DecompressionBombError)


def all_album_tracks(album) -> list:
    """Return every track of `album`, from Album.tracks plus any reachable only through its discs."""
    # A track detached from its album but left on a disc still carries this
    # album's picture; the embed/clear pass must reach it too.
    by_id: dict = {}
    for t in getattr(album, "tracks", None) or []:
        tid = getattr(t, "track_id", None)
        if tid is not None:
            by_id[tid] = t
    for disc in getattr(album, "discs", None) or []:
        for t in getattr(disc, "tracks", None) or []:
            tid = getattr(t, "track_id", None)
            if tid is not None:
                by_id.setdefault(tid, t)
    return list(by_id.values())


def pick_representative_track(album):
    """Return the album's deterministic embeddable track whose art speaks for the album, or None."""
    tracks = [t for t in all_album_tracks(album) if getattr(t, "track_file_path", None) and Path(t.track_file_path).suffix.lower() in ArtworkExtractor.SUPPORTED_EXTENSIONS]
    if not tracks:
        return None
    return min(tracks, key=lambda t: (getattr(t, "disc_id", None) or 0, getattr(t, "track_number", None) or 0, t.track_id))


def _build_thumbnail(image_bytes: bytes, max_dimension: int = DEFAULT_MAX_DIMENSION, quality: int = DEFAULT_JPEG_QUALITY) -> tuple[bytes, int, int]:
    """Downscale image_bytes to max_dimension as JPEG; return (thumb_bytes, original_width, original_height)."""
    image = Image.open(io.BytesIO(image_bytes))
    image.load()
    orig_width, orig_height = image.width, image.height

    if max(orig_width, orig_height) > max_dimension:
        image.thumbnail((max_dimension, max_dimension), Image.LANCZOS)

    if image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info):
        # JPEG has no alpha; a plain convert("RGB") turns transparent areas black.
        rgba = image.convert("RGBA")
        image = Image.new("RGB", rgba.size, (255, 255, 255))
        image.paste(rgba, mask=rgba.getchannel("A"))
    elif image.mode != "RGB":
        image = image.convert("RGB")

    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=quality)
    return buf.getvalue(), orig_width, orig_height


class ArtworkCache:
    """Self-healing, mtime-validated thumbnail cache for embedded album art."""

    _SCHEMA_SQL = """
        CREATE TABLE IF NOT EXISTS artwork_thumbnails (
            album_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            source_track_path TEXT,
            source_mtime REAL,
            width INTEGER,
            height INTEGER,
            thumb_data BLOB,
            updated_at TEXT,
            PRIMARY KEY (album_id, role)
        )
        """

    def __init__(self, db_path: str | None = None):
        self.db_path = Path(db_path) if db_path else (IMAGECACHE_DIR / "artwork_cache.db")
        self._lock = threading.Lock()
        self._extractor = ArtworkExtractor()
        # 0.0 == healthy; otherwise a time.monotonic() deadline until which
        # writes are assumed to fail and lookups serve cached rows only.
        self._degraded_until = 0.0
        self._read_error_logged = False
        # Set == background warmers may run; cleared while a foreground writer
        # (the album editor's embed) needs this cache's single connection.
        self._warmers_runnable = threading.Event()
        self._warmers_runnable.set()
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = self._connect_file()
        except (OSError, sqlite3.Error) as exc:
            # Unwritable cache folder or file: keep art working for this session.
            logger.warning(f"ArtworkCache: cannot open {self.db_path} ({exc}); caching in memory for this session")
            self._conn = self._new_conn(":memory:")
            self._conn.execute(self._SCHEMA_SQL)
            self._conn.commit()

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def get_pixmap(self, album, role: str, is_explicit: bool = False) -> QPixmap:
        """Return the cached art for album/role, blurred if explicit, or a null QPixmap if none."""
        row = self._lookup_or_refresh(album, role)
        if row is None or row["thumb_data"] is None:
            return QPixmap()

        pixmap = QPixmap()
        if not pixmap.loadFromData(row["thumb_data"]):
            return QPixmap()

        if is_explicit and blur_enabled():
            return blur_pixmap(pixmap)
        return pixmap

    def get_dimensions(self, album, role: str) -> tuple[int, int] | None:
        """Return the original (width, height) of the embedded art for album/role, or None."""
        row = self._lookup_or_refresh(album, role)
        if row is None or row["width"] is None:
            return None
        return (row["width"], row["height"])

    def has_art(self, album, role: str = "front") -> bool:
        """Return True if the album has embedded art for role (may read the audio file)."""
        row = self._lookup_or_refresh(album, role)
        return row is not None and row["thumb_data"] is not None

    def is_degraded(self) -> bool:
        """Return True while the cache is in its post-write-failure read-only window."""
        return time.monotonic() < self._degraded_until

    def peek_has_art(self, album, role: str = "front") -> bool | None:
        """Like has_art, but never reads the audio file; None means the answer needs a refresh."""
        known, row = self._peek_row(album, role)
        if not known:
            return None
        return row is not None and row["thumb_data"] is not None

    def peek_dimensions(self, album, role: str = "front") -> tuple[bool, tuple[int, int] | None]:
        """Like get_dimensions, but never reads the audio file; returns (known, dims)."""
        known, row = self._peek_row(album, role)
        if not known:
            return False, None
        if row is None or row["width"] is None:
            return True, None
        return True, (row["width"], row["height"])

    def store(self, album, role: str, image_bytes: bytes | None) -> None:
        """Populate the cache row for album/role from image bytes the caller already holds."""
        source = self._current_source(album)
        if source is None:
            return
        track, mtime = source

        if image_bytes is None:
            self._upsert(album.album_id, role, track.track_file_path, mtime, None, None, None)
            return

        try:
            thumb_bytes, width, height = _build_thumbnail(image_bytes)
        except _DECODE_ERRORS as e:
            # The files already hold these bytes; drop the row so the next read
            # re-derives it from the file instead of failing the whole embed.
            logger.error(f"ArtworkCache: cannot build thumbnail for album {album.album_id} ({role}): {e}")
            self.invalidate(album.album_id, role)
            return
        self._upsert(album.album_id, role, track.track_file_path, mtime, width, height, thumb_bytes)

    def pause_warmers(self) -> None:
        """Ask background cache warmers to stop starting new albums until resume_warmers()."""
        self._warmers_runnable.clear()

    def resume_warmers(self) -> None:
        """Undo pause_warmers(); safe to call when not paused."""
        self._warmers_runnable.set()

    def warmers_wait_if_paused(self, stop, poll: float = 0.2) -> None:
        """Block while warmers are paused, returning early once the caller's stop() is true."""
        while not self._warmers_runnable.wait(poll):
            if stop():
                return

    def invalidate(self, album_id: int, role: str | None = None) -> None:
        """Delete the cached rows for album_id (one role, or all roles)."""
        with self._lock:
            try:
                if role is None:
                    self._conn.execute("DELETE FROM artwork_thumbnails WHERE album_id = ?", (album_id,))
                else:
                    self._conn.execute("DELETE FROM artwork_thumbnails WHERE album_id = ? AND role = ?", (album_id, role))
                self._conn.commit()
            except sqlite3.Error as exc:
                # A surviving stale row is harmless: the mtime check catches a changed file.
                self._note_write_failure(exc)

    def close(self) -> None:
        """Close the cache connection; later lookups return no art."""
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ #
    #  Internal                                                            #
    # ------------------------------------------------------------------ #

    def _current_source(self, album) -> tuple[object, float] | None:
        """Return (representative_track, its mtime), or None if no readable embeddable track exists."""
        track = pick_representative_track(album)
        if track is None:
            return None
        try:
            return track, Path(track.track_file_path).stat().st_mtime
        except OSError:
            return None

    @staticmethod
    def _row_is_current(row, track, mtime: float) -> bool:
        """Return True if row was built from track at mtime."""
        return row is not None and row["source_track_path"] == track.track_file_path and row["source_mtime"] == mtime

    def _peek_row(self, album, role: str) -> tuple[bool, sqlite3.Row | None]:
        """Return (known, row) from the cache and a stat only; known=False means a refresh is needed."""
        source = self._current_source(album)
        if source is None:
            return True, None  # confirmed "no art"
        track, mtime = source
        row = self._select(album.album_id, role)
        if self._row_is_current(row, track, mtime):
            return True, row
        return False, None

    def _lookup_or_refresh(self, album, role: str) -> sqlite3.Row | None:
        """Return the current row for album/role, re-reading the audio file on a miss."""
        source = self._current_source(album)
        if source is None:
            return None
        track, mtime = source

        row = self._select(album.album_id, role)
        if self._row_is_current(row, track, mtime):
            return row

        if self.is_degraded():
            # Writes are failing; serve the cached row as-is (maybe stale or None).
            return row

        return self._refresh(album.album_id, role, track, mtime)

    def _refresh(self, album_id: int, role: str, track, mtime: float) -> sqlite3.Row | None:
        """Read track's embedded art once and cache every role from that single read."""
        ext = Path(track.track_file_path).suffix.lower()
        try:
            embedded = self._extractor.extract_artwork_by_role(track.track_file_path, ext)
        except (OSError, struct.error) as e:
            logger.error(f"ArtworkCache: error extracting artwork from {track.track_file_path}: {e}")
            return None

        for r in ArtworkExtractor.PICTURE_TYPE_ROLES.values():
            picture = embedded.get(r)
            if picture is None:
                self._upsert(album_id, r, track.track_file_path, mtime, None, None, None)
                continue
            try:
                thumb_bytes, width, height = _build_thumbnail(picture["data"])
            except _DECODE_ERRORS as e:
                # Cache as "no art" so later lookups don't re-read the file for undecodable data.
                logger.error(f"ArtworkCache: error building thumbnail for {track.track_file_path} ({r}): {e}")
                self._upsert(album_id, r, track.track_file_path, mtime, None, None, None)
                continue
            self._upsert(album_id, r, track.track_file_path, mtime, width, height, thumb_bytes)

        return self._select(album_id, role)

    def _select(self, album_id: int, role: str) -> sqlite3.Row | None:
        """Return the cached row for album_id/role, or None if absent or unreadable."""
        with self._lock:
            try:
                row = self._conn.execute("SELECT * FROM artwork_thumbnails WHERE album_id = ? AND role = ?", (album_id, role)).fetchone()
            except sqlite3.Error as exc:
                # Corrupt or closed db: callers run on the GUI thread, so never raise.
                if not self._read_error_logged:
                    logger.warning(f"ArtworkCache: read from {self.db_path} failed ({exc}); showing no cached art")
                    self._read_error_logged = True
                return None
            self._read_error_logged = False
            return row

    _UPSERT_SQL = """
        INSERT INTO artwork_thumbnails
            (album_id, role, source_track_path, source_mtime, width,
             height, thumb_data, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(album_id, role) DO UPDATE SET
            source_track_path = excluded.source_track_path,
            source_mtime = excluded.source_mtime,
            width = excluded.width,
            height = excluded.height,
            thumb_data = excluded.thumb_data,
            updated_at = excluded.updated_at
        """

    def _upsert(self, album_id: int, role: str, source_track_path: str, source_mtime: float, width: int | None, height: int | None, thumb_data: bytes | None) -> None:
        """Write one cache row; on failure reconnect once, then enter the read-only window."""
        params = (album_id, role, source_track_path, source_mtime, width, height, thumb_data, time.strftime("%Y-%m-%d %H:%M:%S"))
        with self._lock:
            if time.monotonic() < self._degraded_until:
                return  # retry only once the window expires, not per write
            try:
                self._conn.execute(self._UPSERT_SQL, params)
                self._conn.commit()
            except sqlite3.Error as exc:
                # The reconnect recovers SQLITE_READONLY_DBMOVED (file swapped under the handle).
                if not (self._reconnect_locked() and self._retry_write_locked(params)):
                    self._note_write_failure(exc)
                    return
            self._clear_degraded_locked()

    def _retry_write_locked(self, params: tuple) -> bool:
        """Retry one upsert on the fresh connection; caller holds self._lock."""
        try:
            self._conn.execute(self._UPSERT_SQL, params)
            self._conn.commit()
            return True
        except sqlite3.Error as exc:
            logger.debug(f"ArtworkCache: write retry after reconnect failed: {exc}")
            return False

    @staticmethod
    def _new_conn(target: str) -> sqlite3.Connection:
        """Open a row-factory connection usable from any thread."""
        conn = sqlite3.connect(target, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _connect_file(self) -> sqlite3.Connection:
        """Open the cache file with its schema, deleting and recreating it once if it is corrupt."""
        conn = self._new_conn(str(self.db_path))
        try:
            conn.execute(self._SCHEMA_SQL)
            conn.commit()
            return conn
        except sqlite3.OperationalError:
            conn.close()
            raise  # locked / read-only: not corruption
        except sqlite3.DatabaseError as exc:
            conn.close()
            logger.warning(f"ArtworkCache: {self.db_path} is corrupt ({exc}); recreating it")
        for suffix in ("", "-journal", "-wal", "-shm"):
            Path(f"{self.db_path}{suffix}").unlink(missing_ok=True)
        conn = self._new_conn(str(self.db_path))
        conn.execute(self._SCHEMA_SQL)
        conn.commit()
        return conn

    def _reconnect_locked(self) -> bool:
        """Close and reopen the cache file; caller holds self._lock. Return True on success."""
        with contextlib.suppress(sqlite3.Error):
            self._conn.close()
        try:
            self._conn = self._connect_file()
            return True
        except (OSError, sqlite3.Error) as exc:
            logger.debug(f"ArtworkCache: reconnect to {self.db_path} failed: {exc}")
            return False

    def _note_write_failure(self, exc: Exception) -> None:
        """Enter or extend the read-only window, logging only on entry; caller holds self._lock."""
        now = time.monotonic()
        already_degraded = now < self._degraded_until
        self._degraded_until = now + _DEGRADED_BACKOFF_SEC
        if not already_degraded:
            logger.warning(f"ArtworkCache: write to {self.db_path} failed ({exc}); serving cached art read-only, retrying in {_DEGRADED_BACKOFF_SEC:.0f}s")

    def _clear_degraded_locked(self) -> None:
        """Leave the read-only window after a successful write; caller holds self._lock."""
        if self._degraded_until:
            self._degraded_until = 0.0
            logger.info(f"ArtworkCache: {self.db_path} writable again, caching resumed")


def get_artwork_cache() -> ArtworkCache | None:
    """Return the app-wide ArtworkCache attached to the QApplication, if any."""
    app = QApplication.instance()
    return getattr(app, "artwork_cache", None)
