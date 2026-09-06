"""
artwork_cache.py

Disk-backed thumbnail cache for embedded album art. Reading embedded art
straight from an audio file (ArtworkExtractor.extract_artwork_by_role) is
expensive - it reads the whole file into memory just to reach the tag -
so every UI surface that shows album art goes through this cache instead
of touching audio files directly on every repaint.

Storage is a single SQLite file (cache/imagecache/artwork_cache.db), not
a directory of loose thumbnails - it's fully disposable/regenerable, so
"reset the cache" is just deleting one file. A row is valid as long as
its source_track_path/source_mtime still match the album's current
representative track; if the file changes (edited, re-embedded,
migrated) the next read just sees a stale mtime and regenerates - no
explicit invalidation required for correctness.

Picking "the album's representative track" (any single embeddable track
whose embedded art speaks for the whole album) assumes every track in an
album agrees on its embedded art per role. Nothing enforces that
invariant here; it is checked only on demand, by the Tools -> "Artwork
Conflicts…" tool (src/library/library_artwork_consistency.py +
artwork_consistency_dialog.py), which also lets the user re-embed one
version into every track to fix an album that has drifted.
"""

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
from src.image.image_blur import _blur_enabled, blur_pixmap
from src.metadata.metadata_artwork import ArtworkExtractor

DEFAULT_MAX_DIMENSION = 1024
DEFAULT_JPEG_QUALITY = 95

# When a cache write fails (the SQLite file or its filesystem briefly went
# read-only - an external `rm` of the cache db under the open handle, an
# ext4 emergency remount, a backup/restore swap), stop attempting the
# expensive extract+decode+write on every lookup for this long and just
# serve whatever rows are already cached. Without this a sustained
# read-only window turns every art lookup into a full audio-file read + PIL
# decode that ends in a failed write, logged once per NowPlaying repaint
# tick and once per album across the whole warmer queue - forever, with no
# recovery short of an app restart.
_DEGRADED_BACKOFF_SEC = 60.0


def all_album_tracks(album) -> list:
    """Every track that belongs to `album` - those linked directly via
    Track.album_id (Album.tracks) plus any reachable only through one of
    its discs (Album.discs -> Disc.tracks). The two sets diverge when a
    track is detached from its album but left sitting on a disc; artwork
    reads and the "Clear"/"Choose" embed pass must still see those files
    or their embedded picture is silently left in place (and later bleeds
    onto whatever album the track is next added to)."""
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


def _pick_representative_track(album):
    """Return the album's canonical embeddable track (or None), used as
    the single source of truth for that album's embedded art. Any
    embeddable track works as long as they all agree on their embedded
    art (verified on demand by the "Artwork Conflicts…" tool, not here) -
    this just needs to be deterministic."""
    tracks = [
        t
        for t in all_album_tracks(album)
        if getattr(t, "track_file_path", None)
        and Path(t.track_file_path).suffix.lower() in ArtworkExtractor.SUPPORTED_EXTENSIONS
    ]
    if not tracks:
        return None
    return min(
        tracks,
        key=lambda t: (
            getattr(t, "disc_id", None) or 0,
            getattr(t, "track_number", None) or 0,
            t.track_id,
        ),
    )


def _build_thumbnail(
    image_bytes: bytes,
    max_dimension: int = DEFAULT_MAX_DIMENSION,
    quality: int = DEFAULT_JPEG_QUALITY,
) -> tuple[bytes, int, int]:
    """Decode image_bytes, downscale if it exceeds max_dimension, and
    re-encode as JPEG at a high quality level. Quality 95 is close to
    visually lossless while staying far smaller than PNG for photographic
    album art - the visible softness reported at quality 85 was from
    over-compression, not from JPEG itself, so this keeps a single
    consistent encoding path rather than branching on image size (which
    made the cache larger than the original loose-file directory).
    Returns (thumb_bytes, original_width, original_height)."""
    image = Image.open(io.BytesIO(image_bytes))
    image.load()
    orig_width, orig_height = image.width, image.height

    if max(orig_width, orig_height) > max_dimension:
        image.thumbnail((max_dimension, max_dimension), Image.LANCZOS)

    if image.mode != "RGB":
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
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._extractor = ArtworkExtractor()
        # 0.0 == healthy. Otherwise a time.monotonic() deadline: until then,
        # writes are assumed to keep failing, so lookups skip _refresh and
        # serve cached rows only. See _note_write_failure / _DEGRADED_BACKOFF_SEC.
        self._degraded_until = 0.0
        # Set == background warmers may run; cleared == a foreground writer
        # (the album editor embedding freshly-picked art into every track)
        # has asked them to back off so the two threads aren't contending on
        # this cache's single connection/lock. Starts runnable.
        self._warmers_runnable = threading.Event()
        self._warmers_runnable.set()
        self._init_schema()

    def _init_schema(self):
        with self._lock:
            try:
                self._conn.execute(self._SCHEMA_SQL)
                self._conn.commit()
            except sqlite3.Error as exc:
                # Constructed while the cache file/filesystem is read-only:
                # degrade instead of crashing the app at startup.
                self._note_write_failure(exc)

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def get_pixmap(self, album, role: str, is_explicit: bool = False) -> QPixmap:
        """Return a QPixmap for album/role, or a null QPixmap if there's
        no art for that role. Mirrors load_art_pixmap's null-on-missing
        and explicit-art-blur behavior."""
        row = self._lookup_or_refresh(album, role)
        if row is None or row["thumb_data"] is None:
            return QPixmap()

        pixmap = QPixmap()
        if not pixmap.loadFromData(row["thumb_data"]):
            return QPixmap()

        if is_explicit and _blur_enabled():
            return blur_pixmap(pixmap)
        return pixmap

    def get_dimensions(self, album, role: str) -> tuple[int, int] | None:
        """Return (width, height) of the original (pre-resize) embedded
        image for album/role, or None if there's no art for that role."""
        row = self._lookup_or_refresh(album, role)
        if row is None or row["width"] is None:
            return None
        return (row["width"], row["height"])

    def has_art(self, album, role: str = "front") -> bool:
        row = self._lookup_or_refresh(album, role)
        return row is not None and row["thumb_data"] is not None

    def is_degraded(self) -> bool:
        """True while the cache is in a post-write-failure read-only window
        (see _DEGRADED_BACKOFF_SEC). Background warmers check this to stop
        grinding a whole album list on a database that can't be written."""
        return time.monotonic() < self._degraded_until

    def _peek_row(self, album, role: str) -> tuple[bool, sqlite3.Row | None]:
        """Shared lookup for peek_has_art/peek_dimensions: consults only
        the cache row and a cheap stat, never the audio file itself.
        Returns (known, row). known=False means the answer requires a real
        _refresh() (cache miss or stale mtime) - callers should hand the
        album to a background has_art()/get_dimensions() call instead of
        calling it inline. When known=True, row may still be None, which is
        a confirmed "no art" (e.g. no embeddable track at all)."""
        track = _pick_representative_track(album)
        if track is None:
            return True, None

        try:
            current_mtime = Path(track.track_file_path).stat().st_mtime
        except OSError:
            return True, None

        row = self._select(album.album_id, role)
        if (
            row is not None
            and row["source_track_path"] == track.track_file_path
            and row["source_mtime"] == current_mtime
        ):
            return True, row
        return False, None

    def peek_has_art(self, album, role: str = "front") -> bool | None:
        """Like has_art, but never reads/decodes the audio file. Returns
        True/False when the cached row is confirmed still valid, or None
        when the answer would require a real _refresh(). Callers on the UI
        thread should use this to avoid blocking on extraction, and hand
        anything that comes back None off to a background has_art()/
        get_dimensions() call instead."""
        known, row = self._peek_row(album, role)
        if not known:
            return None
        return row is not None and row["thumb_data"] is not None

    def peek_dimensions(self, album, role: str = "front") -> tuple[bool, tuple[int, int] | None]:
        """Like get_dimensions, but never reads/decodes the audio file.
        Returns (known, dims). known=False means resolving this requires a
        real get_dimensions() call off the UI thread; when known=True,
        dims is (width, height) or None if there's confirmed to be no art
        for this role."""
        known, row = self._peek_row(album, role)
        if not known:
            return False, None
        if row is None or row["width"] is None:
            return True, None
        return True, (row["width"], row["height"])

    def store(self, album, role: str, image_bytes: bytes | None) -> None:
        """Directly populate the cache for album/role from image_bytes the
        caller already has in hand (e.g. right after the editor embeds new
        art into every track) - skips the redundant re-read/re-extract
        that _lookup_or_refresh would otherwise do on the next read."""
        track = _pick_representative_track(album)
        if track is None:
            return
        try:
            mtime = Path(track.track_file_path).stat().st_mtime
        except OSError:
            return

        if image_bytes is None:
            self._upsert(album.album_id, role, track.track_file_path, mtime, None, None, None)
            return

        thumb_bytes, width, height = _build_thumbnail(image_bytes)
        self._upsert(album.album_id, role, track.track_file_path, mtime, width, height, thumb_bytes)

    def pause_warmers(self) -> None:
        """Ask background cache-warming workers (ArtCacheWorker) to stop
        starting new albums until resume_warmers() is called. Used by the
        album editor while it embeds new art into every track, so a
        full-library Art-filter warm pass isn't queued ahead of the
        editor's own writes on this cache's single connection."""
        self._warmers_runnable.clear()

    def resume_warmers(self) -> None:
        """Undo pause_warmers(). Safe to call when not paused."""
        self._warmers_runnable.set()

    def warmers_wait_if_paused(self, stop, poll: float = 0.2) -> None:
        """Called by background warmers between albums: while paused, blocks
        in `poll`-second slices, re-checking the caller's `stop()` predicate
        each slice so a cancel still takes effect promptly. Returns at once
        when runnable."""
        while not self._warmers_runnable.wait(poll):
            if stop():
                return

    def invalidate(self, album_id: int, role: str | None = None) -> None:
        with self._lock:
            try:
                if role is None:
                    self._conn.execute(
                        "DELETE FROM artwork_thumbnails WHERE album_id = ?", (album_id,)
                    )
                else:
                    self._conn.execute(
                        "DELETE FROM artwork_thumbnails WHERE album_id = ? AND role = ?",
                        (album_id, role),
                    )
                self._conn.commit()
            except sqlite3.Error as exc:
                # Stale rows surviving is harmless - the source_mtime check
                # in _lookup_or_refresh catches a changed file anyway.
                self._note_write_failure(exc)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ #
    #  Internal                                                            #
    # ------------------------------------------------------------------ #

    def _lookup_or_refresh(self, album, role: str) -> sqlite3.Row | None:
        track = _pick_representative_track(album)
        if track is None:
            return None

        try:
            current_mtime = Path(track.track_file_path).stat().st_mtime
        except OSError:
            return None

        row = self._select(album.album_id, role)
        if (
            row is not None
            and row["source_track_path"] == track.track_file_path
            and row["source_mtime"] == current_mtime
        ):
            return row

        if self.is_degraded():
            # Writes are failing; _refresh would read+decode the whole audio
            # file only to fail again at _upsert. Serve the cached row as-is
            # (possibly stale, possibly None) until the window expires.
            return row

        return self._refresh(album.album_id, role, track, current_mtime)

    def _refresh(self, album_id: int, role: str, track, mtime: float) -> sqlite3.Row | None:
        """
        Reads track.track_file_path once and populates the cache row for
        every role (front/rear/liner), not just the one that was asked
        for - extract_artwork_by_role already returns all roles present
        in one pass, and the read of the whole file is the expensive
        part, so a miss on any one role should save the other two from
        also missing on their next lookup.
        """
        ext = Path(track.track_file_path).suffix.lower()
        try:
            embedded = self._extractor.extract_artwork_by_role(track.track_file_path, ext)
        except (OSError, struct.error) as e:
            logger.error(
                f"ArtworkCache: error extracting artwork from {track.track_file_path}: {e}"
            )
            return None

        for r in ArtworkExtractor.PICTURE_TYPE_ROLES.values():
            picture = embedded.get(r)
            if picture is None:
                self._upsert(album_id, r, track.track_file_path, mtime, None, None, None)
                continue
            try:
                thumb_bytes, width, height = _build_thumbnail(picture["data"])
            except (OSError, Image.DecompressionBombError) as e:
                logger.error(
                    f"ArtworkCache: error building thumbnail for {track.track_file_path} ({r}): {e}"
                )
                continue
            self._upsert(album_id, r, track.track_file_path, mtime, width, height, thumb_bytes)

        return self._select(album_id, role)

    def _select(self, album_id: int, role: str) -> sqlite3.Row | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM artwork_thumbnails WHERE album_id = ? AND role = ?", (album_id, role)
            )
            return cur.fetchone()

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

    def _upsert(
        self,
        album_id: int,
        role: str,
        source_track_path: str,
        source_mtime: float,
        width: int | None,
        height: int | None,
        thumb_data: bytes | None,
    ) -> None:
        params = (
            album_id,
            role,
            source_track_path,
            source_mtime,
            width,
            height,
            thumb_data,
            time.strftime("%Y-%m-%d %H:%M:%S"),
        )
        with self._lock:
            if time.monotonic() < self._degraded_until:
                # Known read-only window - don't retry per write, just per
                # window expiry (a caller past the _lookup_or_refresh guard,
                # e.g. store(), still lands here).
                return
            try:
                self._conn.execute(self._UPSERT_SQL, params)
                self._conn.commit()
            except sqlite3.Error as exc:
                # One reconnect + retry: recovers SQLITE_READONLY_DBMOVED
                # (cache file swapped/removed under the open handle). If it
                # still fails, drop into the read-only window instead of
                # raising - this is called from NowPlayingView.updateUI and
                # the whole-library warmer, both of which would otherwise
                # spam a traceback / warning per lookup.
                if not (self._reconnect_locked() and self._retry_write_locked(params)):
                    self._note_write_failure(exc)
                    return
            self._clear_degraded_locked()

    def _retry_write_locked(self, params: tuple) -> bool:
        try:
            self._conn.execute(self._UPSERT_SQL, params)
            self._conn.commit()
            return True
        except sqlite3.Error as exc:
            logger.debug(f"ArtworkCache: write retry after reconnect failed: {exc}")
            return False

    def _reconnect_locked(self) -> bool:
        """Close and reopen the connection. Recovers a connection latched
        read-only because its backing file was moved/removed. Caller holds
        self._lock. Returns True if a fresh, schema-ready connection opened."""
        with contextlib.suppress(sqlite3.Error):
            self._conn.close()
        try:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute(self._SCHEMA_SQL)
            self._conn.commit()
            return True
        except sqlite3.Error as exc:
            logger.debug(f"ArtworkCache: reconnect to {self.db_path} failed: {exc}")
            return False

    def _note_write_failure(self, exc: Exception) -> None:
        """Enter (or extend) the read-only window after a failed write.
        Logs once on the transition into a degraded state, not per failure.
        Caller holds self._lock."""
        now = time.monotonic()
        already_degraded = now < self._degraded_until
        self._degraded_until = now + _DEGRADED_BACKOFF_SEC
        if not already_degraded:
            logger.warning(
                f"ArtworkCache: write to {self.db_path} failed ({exc}); serving "
                f"cached art read-only, retrying in {_DEGRADED_BACKOFF_SEC:.0f}s"
            )

    def _clear_degraded_locked(self) -> None:
        """A write just succeeded - leave the read-only window if we were in
        one. Caller holds self._lock."""
        if self._degraded_until:
            self._degraded_until = 0.0
            logger.info(f"ArtworkCache: {self.db_path} writable again, caching resumed")


def get_artwork_cache() -> ArtworkCache | None:
    """Resolve the app-wide ArtworkCache instance, same convention as
    app.display_settings (see run.py / image_blur.py's _blur_enabled)."""
    app = QApplication.instance()
    return getattr(app, "artwork_cache", None)
