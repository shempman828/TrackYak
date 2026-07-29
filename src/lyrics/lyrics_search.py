from json import JSONDecodeError

import lyriq
from PySide6.QtCore import QObject, QThread, Signal

from src.core.logger_config import logger


class LyricSearch:
    """
    A class to search for lyrics using lyriq library.
    Takes a track ORM object and searches for lyrics based on track metadata.
    """

    def __init__(self, track_orm):
        """
        Initialize with a track ORM object.

        Args:
            track_orm: ORM object with track_name, album_name, and artists attributes
        """
        self.track = track_orm
        self.lyrics_client = lyriq

    def _get_artist_name(self) -> str:
        """
        Return the first artist's name from the track's artist association.
        Logs debug info for troubleshooting.
        """
        artists = getattr(self.track, "artists", None)

        if not artists:
            logger.debug(
                "No artists found for track '%s'",
                getattr(self.track, "title", "<unknown>"),
            )
            return ""

        first_artist = artists[0]
        artist_name = getattr(first_artist, "artist_name", "")

        logger.debug(
            "Track '%s' first artist: %s (ORM object: %r)",
            getattr(self.track, "title", "<unknown>"),
            artist_name,
            first_artist,
        )

        return artist_name

    def get_lyrics(self, none_char: str = "♪") -> str | None:
        """
        Search for lyrics using track metadata.

        Args:
            none_char (str): Character to use when lyrics are not found (default: "♪")

        Returns:
            Optional[str]: Lyrics if found, None otherwise
        """
        try:
            song_name = str(self.track.track_name) if self.track.track_name else ""
            artist_name = self._get_artist_name()
            album_name = str(self.track.album_name) if self.track.album_name else None
            logger.debug(
                f"searching lyrics for track {song_name} by {artist_name} from {album_name}"
            )

            if not song_name or not artist_name:
                return None

            lyrics = self.lyrics_client.get_lyrics(
                song_name=song_name,
                artist_name=artist_name,
                album_name=album_name,
                none_char=none_char,
            )
            logger.debug(f"lyrics found: {lyrics}")

            if lyrics == none_char or not lyrics:
                return None

            return lyrics

        except JSONDecodeError:
            logger.debug(
                "Lyrics API returned an empty/non-JSON response; treating as not found"
            )
            return None
        except Exception as e:
            logger.error(f"Error searching for lyrics: {e}")
            return None

    def search_with_fallback(self, none_char: str = "♪") -> str | None:
        """
        Search for lyrics with fallback strategies if initial search fails.

        Args:
            none_char (str): Character to use when lyrics are not found

        Returns:
            Optional[str]: Lyrics if found, None otherwise
        """
        lyrics = self.get_lyrics(none_char=none_char)

        if lyrics:
            return lyrics

        try:
            song_name = str(self.track.track_name) if self.track.track_name else ""
            artist_name = self._get_artist_name()

            if song_name and artist_name:
                lyrics = self.lyrics_client.get_lyrics(
                    song_name=song_name,
                    artist_name=artist_name,
                    album_name=None,
                    duration=None,
                    none_char=none_char,
                )

                if lyrics != none_char and lyrics:
                    return lyrics

        except JSONDecodeError:
            logger.debug(
                "Lyrics API returned an empty/non-JSON response; treating as not found"
            )
        except Exception as e:
            logger.error(f"Fallback search failed: {e}")

        return None


# ---------------------------------------------------------------------------
# Async worker — lives in a background QThread
# ---------------------------------------------------------------------------


class _LyricWorker(QObject):
    """
    Internal worker that performs the lyric search off the main thread.
    Do not instantiate directly; use LyricSearchThread.
    """

    lyrics_ready = Signal(object)  # emitted with Lyrics object (or str) on success
    lyrics_not_found = Signal()  # emitted when search returns nothing
    error_occurred = Signal(str)  # emitted with message on exception

    def __init__(self, track_orm, use_fallback: bool = True, none_char: str = "♪"):
        super().__init__()
        self._track_orm = track_orm
        self._use_fallback = use_fallback
        self._none_char = none_char

    def run(self) -> None:
        """Called by the thread's started signal."""
        try:
            searcher = LyricSearch(self._track_orm)
            if self._use_fallback:
                lyrics = searcher.search_with_fallback(none_char=self._none_char)
            else:
                lyrics = searcher.get_lyrics(none_char=self._none_char)

            if lyrics:
                self.lyrics_ready.emit(lyrics)
            else:
                self.lyrics_not_found.emit()

        except Exception as e:
            logger.error(f"LyricWorker unhandled exception: {e}")
            self.error_occurred.emit(str(e))


class LyricSearchThread(QObject):
    """
    Thin manager that owns a persistent QThread + worker pair.

    Usage
    -----
    # In your widget's __init__:
        self._lyric_thread = LyricSearchThread(parent=self)
        self._lyric_thread.lyrics_ready.connect(self._on_lyrics_ready)
        self._lyric_thread.lyrics_not_found.connect(self._on_lyrics_not_found)
        self._lyric_thread.error_occurred.connect(self._on_lyric_error)

    # To kick off a search:
        self._lyric_thread.search(track_orm)

    # Qt cleans up the thread on parent destruction; explicit cleanup available:
        self._lyric_thread.stop()
    """

    # Re-export worker signals so callers only hold one reference
    lyrics_ready = Signal(object)
    lyrics_not_found = Signal()
    error_occurred = Signal(str)

    def __init__(self, parent: QObject = None):
        super().__init__(parent)
        self._thread = QThread(self)
        self._worker: _LyricWorker | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._thread.isRunning()

    def search(
        self,
        track_orm,
        use_fallback: bool = True,
        none_char: str = "♪",
    ) -> None:
        """
        Start a lyric search for *track_orm* in the background.
        If a search is already running it is abandoned (thread restarted).

        Args:
            track_orm:     ORM track object with track_name / album_name / artists.
            use_fallback:  When True, retries without album_name if first attempt fails.
            none_char:     Sentinel value lyriq returns when no lyrics are found.
        """
        self._stop_current()

        self._worker = _LyricWorker(
            track_orm, use_fallback=use_fallback, none_char=none_char
        )
        self._worker.moveToThread(self._thread)

        # Wire worker signals → our forwarding signals
        self._worker.lyrics_ready.connect(self.lyrics_ready)
        self._worker.lyrics_not_found.connect(self.lyrics_not_found)
        self._worker.error_occurred.connect(self.error_occurred)

        # Start worker when thread starts; quit thread when worker finishes
        self._thread.started.connect(self._worker.run)
        self._worker.lyrics_ready.connect(self._thread.quit)
        self._worker.lyrics_not_found.connect(self._thread.quit)
        self._worker.error_occurred.connect(self._thread.quit)

        self._thread.start()

    def stop(self) -> None:
        """Gracefully stop any running search and clean up."""
        self._stop_current()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _stop_current(self) -> None:
        if self._thread.isRunning():
            self._thread.quit()
            self._thread.wait()

        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

        # Disconnect started so re-use doesn't double-fire
        try:
            self._thread.started.disconnect()
        except RuntimeError:
            pass


# ---------------------------------------------------------------------------
# Convenience function (sync, unchanged)
# ---------------------------------------------------------------------------


def search_lyrics_for_track(track_orm, none_char: str = "♪") -> str | None:
    """
    Convenience function to search lyrics for a track ORM object.
    Runs synchronously — prefer LyricSearchThread for UI contexts.

    Args:
        track_orm: ORM object with track metadata
        none_char (str): Character to use when lyrics are not found

    Returns:
        Optional[str]: Lyrics if found, None otherwise
    """
    searcher = LyricSearch(track_orm)
    return searcher.get_lyrics(none_char=none_char)
