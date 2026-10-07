"""Online lyrics lookup via lyriq, with a non-blocking QThread wrapper."""

from contextlib import suppress
from dataclasses import dataclass
from http.client import HTTPException
from json import JSONDecodeError

import lyriq
from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal, Slot

from src.foundation.logger_config import logger

# Milliseconds to wait for each in-flight search at app quit before giving up.
_QUIT_WAIT_MS = 3000


@dataclass(frozen=True)
class LyricQuery:
    """Plain-value search terms snapshotted from a Track on the caller's thread."""

    song: str
    artist: str
    album: str | None = None
    duration: int | None = None

    @classmethod
    def from_track(cls, track) -> "LyricQuery":
        """Read the search terms from a Track ORM object (call on the ORM session's thread)."""
        song = str(track.track_name) if getattr(track, "track_name", None) else ""
        album = getattr(track, "album_name", None)
        duration = getattr(track, "duration", None)
        return cls(
            song=song,
            artist=_first_artist_name(track),
            album=str(album) if album else None,
            # lyriq drops a falsy duration, so 0 / sub-second values mean "unknown".
            duration=int(duration) if duration else None,
        )


def _first_artist_name(track) -> str:
    """Return the first primary artist's name, else the first credited artist's name."""
    # artist_roles has no ordering, so artists[0] may be a composer/producer.
    artists = list(getattr(track, "primary_artists", None) or []) or list(getattr(track, "artists", None) or [])
    if not artists:
        logger.debug("No artists found for track '%s'", getattr(track, "track_name", "<unknown>"))
        return ""
    return getattr(artists[0], "artist_name", "") or ""


class LyricSearch:
    """Search lyriq for a track's lyrics, optionally retrying with looser terms."""

    def __init__(self, track_or_query):
        """Accept a Track ORM object or a prebuilt LyricQuery."""
        self.query = track_or_query if isinstance(track_or_query, LyricQuery) else LyricQuery.from_track(track_or_query)
        self.lyrics_client = lyriq

    def _query(self, album: str | None, duration: int | None, none_char: str) -> lyriq.Lyrics | None:
        """Run one lyriq lookup; network/parse failures count as not found."""
        q = self.query
        if not q.song or not q.artist:
            return None
        logger.debug("Searching lyrics for %r by %r (album=%r, duration=%r)", q.song, q.artist, album, duration)
        try:
            lyrics = self.lyrics_client.get_lyrics(song_name=q.song, artist_name=q.artist, album_name=album, duration=duration, none_char=none_char)
        except JSONDecodeError:
            logger.debug("Lyrics API returned an empty/non-JSON response; treating as not found")
            return None
        except (OSError, HTTPException) as e:
            # OSError covers URLError, timeouts and connection resets.
            logger.error("Error searching for lyrics: %s", e)
            return None
        logger.debug("Lyrics %s for %r", "found" if lyrics else "not found", q.song)
        return lyrics or None

    def get_lyrics(self, none_char: str = "♪") -> lyriq.Lyrics | None:
        """Search with song, artist, album and duration."""
        return self._query(self.query.album, self.query.duration, none_char)

    def search_with_fallback(self, none_char: str = "♪") -> lyriq.Lyrics | None:
        """Search with full terms, then retry with song and artist only."""
        lyrics = self.get_lyrics(none_char=none_char)
        if lyrics:
            return lyrics
        if self.query.album is None and self.query.duration is None:
            return None
        return self._query(None, None, none_char)


# ---------------------------------------------------------------------------
# Async worker — lives in a background QThread
# ---------------------------------------------------------------------------

# Every started search thread -> its worker, kept alive until the thread
# finishes so an abandoned search never blocks the UI or is destroyed mid-run.
_live_threads: dict[QThread, "_LyricWorker"] = {}


class _ThreadReaper(QObject):
    """Main-thread owner that drops finished search threads and waits on them at quit."""

    @Slot()
    def reap(self) -> None:
        """Release the finished thread that sent this signal."""
        _live_threads.pop(self.sender(), None)

    @Slot()
    def wait_all(self) -> None:
        """Give in-flight searches a bounded chance to finish before the app exits."""
        for thread in list(_live_threads):
            if not thread.wait(_QUIT_WAIT_MS):
                logger.warning("Lyrics search still running at quit; abandoning it")


_reaper: _ThreadReaper | None = None


def _get_reaper() -> _ThreadReaper:
    """Create the reaper on first use, on the calling (main) thread."""
    global _reaper
    if _reaper is None:
        _reaper = _ThreadReaper()
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(_reaper.wait_all)
    return _reaper


class _LyricWorker(QObject):
    """Run one lyric search off the main thread; use LyricSearchThread, not this."""

    lyrics_ready = Signal(object)  # lyriq Lyrics object
    lyrics_not_found = Signal()
    error_occurred = Signal(str)

    def __init__(self, query: LyricQuery, use_fallback: bool = True, none_char: str = "♪"):
        super().__init__()
        self._query = query
        self._use_fallback = use_fallback
        self._none_char = none_char

    @Slot()
    def run(self) -> None:
        """Search and emit exactly one result signal."""
        try:
            searcher = LyricSearch(self._query)
            search = searcher.search_with_fallback if self._use_fallback else searcher.get_lyrics
            lyrics = search(none_char=self._none_char)

            if lyrics:
                self.lyrics_ready.emit(lyrics)
            else:
                self.lyrics_not_found.emit()

        except Exception as e:
            # Intentional broad boundary catch: LyricSearch already handles
            # known API failures, so this only stops an unexpected bug from
            # killing the thread silently.
            logger.exception("LyricWorker unhandled exception")
            self.error_occurred.emit(str(e) or type(e).__name__)


class LyricSearchThread(QObject):
    """Run lyric searches in the background and re-emit their results."""

    lyrics_ready = Signal(object)
    lyrics_not_found = Signal()
    error_occurred = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._thread: QThread | None = None
        self._worker: _LyricWorker | None = None
        _get_reaper()

    @property
    def is_running(self) -> bool:
        """True while the current search has not finished."""
        return self._thread is not None and self._thread.isRunning()

    def search(self, track_or_query, use_fallback: bool = True, none_char: str = "♪") -> None:
        """Start a background search, abandoning any search already running."""
        self._stop_current()
        # Snapshot ORM values here: the worker thread must never lazy-load
        # through the main thread's session.
        query = track_or_query if isinstance(track_or_query, LyricQuery) else LyricQuery.from_track(track_or_query)

        thread = QThread()  # unparented: an abandoned search may outlive this object
        worker = _LyricWorker(query, use_fallback=use_fallback, none_char=none_char)
        worker.moveToThread(thread)

        worker.lyrics_ready.connect(self.lyrics_ready)
        worker.lyrics_not_found.connect(self.lyrics_not_found)
        worker.error_occurred.connect(self.error_occurred)

        thread.started.connect(worker.run)
        worker.lyrics_ready.connect(thread.quit)
        worker.lyrics_not_found.connect(thread.quit)
        worker.error_occurred.connect(thread.quit)
        thread.finished.connect(_get_reaper().reap)

        _live_threads[thread] = worker
        self._thread, self._worker = thread, worker
        thread.start()

    def stop(self) -> None:
        """Abandon any running search without blocking; its result is discarded."""
        self._stop_current()

    def _stop_current(self) -> None:
        """Detach the current worker so a late result never reaches our signals."""
        worker, self._worker, self._thread = self._worker, None, None
        if worker is None:
            return
        for source, target in ((worker.lyrics_ready, self.lyrics_ready), (worker.lyrics_not_found, self.lyrics_not_found), (worker.error_occurred, self.error_occurred)):
            with suppress(RuntimeError, TypeError):  # already finished and disconnected
                source.disconnect(target)
        # The worker still quits its own thread when run() returns; the
        # reaper then releases it. No wait() here -- the HTTP call has no timeout.
