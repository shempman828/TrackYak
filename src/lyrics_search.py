from typing import Optional

import lyriq

from src.logger_config import logger


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

    def get_lyrics(self, none_char: str = "♪") -> Optional[str]:
        """
        Search for lyrics using track metadata.

        Args:
            none_char (str): Character to use when lyrics are not found (default: "♪")

        Returns:
            Optional[str]: Lyrics if found, None otherwise
        """
        try:
            # Extract track information
            song_name = str(self.track.track_name) if self.track.track_name else ""
            artist_name = self._get_artist_name()
            album_name = str(self.track.album_name) if self.track.album_name else None
            logger.debug(
                f"searching lyrics for track {song_name} by {artist_name} from {album_name}"
            )

            # Validate required fields
            if not song_name or not artist_name:
                return None

            # Search for lyrics using lyriq
            lyrics = self.lyrics_client.get_lyrics(
                song_name=song_name,
                artist_name=artist_name,
                album_name=album_name,
                none_char=none_char,
            )
            logger.debug(f"lyrics found: {lyrics}")
            # Return None if no lyrics found (lyriq returns none_char when not found)
            if lyrics == none_char or not lyrics:
                return None

            return lyrics

        except Exception as e:
            logger.error(f"Error searching for lyrics: {e}")
            return None

    def search_with_fallback(self, none_char: str = "♪") -> Optional[str]:
        """
        Search for lyrics with fallback strategies if initial search fails.

        Args:
            none_char (str): Character to use when lyrics are not found

        Returns:
            Optional[str]: Lyrics if found, None otherwise
        """
        # Try initial search
        lyrics = self.get_lyrics(none_char=none_char)

        if lyrics:
            return lyrics

        # Fallback: Try without album name
        try:
            song_name = str(self.track.track_name) if self.track.track_name else ""
            artist_name = self._get_artist_name()

            if song_name and artist_name:
                lyrics = self.lyrics_client.get_lyrics(
                    song_name=song_name,
                    artist_name=artist_name,
                    album_name=None,  # Omit album name
                    duration=None,
                    none_char=none_char,
                )

                if lyrics != none_char and lyrics:
                    return lyrics

        except Exception as e:
            logger.error(f"Fallback search failed: {e}")

        return None


# Alternative function-based approach for simpler use cases
def search_lyrics_for_track(track_orm, none_char: str = "♪") -> Optional[str]:
    """
    Convenience function to search lyrics for a track ORM object.

    Args:
        track_orm: ORM object with track metadata
        none_char (str): Character to use when lyrics are not found

    Returns:
        Optional[str]: Lyrics if found, None otherwise
    """
    searcher = LyricSearch(track_orm)
    return searcher.get_lyrics(none_char=none_char)
