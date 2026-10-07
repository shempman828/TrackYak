"""Export a playlist to an M3U file."""

from datetime import datetime
from pathlib import Path
import re

from PySide6.QtWidgets import QMessageBox
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload

from src.db.db_tables import PlaylistTracks
from src.foundation.asset_paths import playlist_path
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message

# Characters that are illegal (or path separators) in a file name on common file systems.
_ILLEGAL_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_playlist_filename(name: str) -> str:
    """Return `name` as a safe .m3u file name."""
    cleaned = _ILLEGAL_FILENAME_CHARS.sub("_", name).strip().strip(".")
    return f"{cleaned or 'playlist'}.m3u"


class PlaylistExporter:
    """Handles exporting a single Playlist ORM object to an M3U file."""

    def __init__(self, controller, show_messages: bool = True, parent_widget=None):
        """Bind the exporter to `controller`; `parent_widget` anchors its messages."""
        # Failures use a blocking QMessageBox even with a parent widget: they
        # involve file I/O and must not be easy to miss.
        self.controller = controller
        self.show_messages = show_messages
        self.parent_widget = parent_widget

    def export_playlist(self, playlist_id: int) -> bool:
        """Write the playlist to an M3U file in the playlist directory; True on success."""
        playlist = self.controller.get.get_entity_object("Playlist", playlist_id=playlist_id)
        if not playlist:
            self._show_error("Export Error", "Playlist not found.")
            return False

        # Eager-load each row's Track instead of one query per exported track.
        playlist_tracks = self.controller.get.get_all_entities("PlaylistTracks", load_options=[selectinload(PlaylistTracks.track)], playlist_id=playlist_id)
        if not playlist_tracks:
            self._show_error("Export Error", f"No tracks found in playlist '{playlist.playlist_name}'.")
            return False
        playlist_tracks = sorted(playlist_tracks, key=lambda pt: getattr(pt, "position", 0) or 0)

        try:
            file_path = Path(playlist_path(safe_playlist_filename(playlist.playlist_name)))
            file_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.error(f"Could not resolve playlist save path: {e}")
            self._show_error("Export Error", f"Invalid save path for '{playlist.playlist_name}'.")
            return False

        failed = []

        try:
            with file_path.open("w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                f.write(f"#PLAYLIST:{playlist.playlist_name}\n")
                if playlist.playlist_description:
                    f.write(f"#DESCRIPTION:{playlist.playlist_description}\n")
                f.write(f"#EXPORT_DATE:{datetime.now().isoformat()}\n")

                for pl_track in playlist_tracks:
                    track = getattr(pl_track, "track", None)
                    if not track:
                        failed.append((pl_track.track_id, "Missing Track entity"))
                        continue

                    if not track.track_file_path:
                        failed.append((track.track_name, "No file path"))
                        continue

                    track_path = Path(track.track_file_path)
                    try:
                        # Relative when the track sits below the playlist folder...
                        entry_path = track_path.relative_to(file_path.parent)
                    except ValueError:
                        # ...otherwise absolute, so other players can still find it.
                        entry_path = track_path.resolve()

                    duration = int(getattr(track, "track_duration", 0) or 0)
                    artist_names = ", ".join(a.artist_name for a in getattr(track, "artists", []) or [])
                    if not artist_names:
                        artist_names = "Unknown Artist"
                    title = getattr(track, "track_name", None) or "Unknown Title"

                    f.write(f"#EXTINF:{duration},{artist_names} - {title}\n")
                    f.write(f"{entry_path}\n")

            if failed:
                # Partial failure - keep this as a blocking notice so the user
                # doesn't miss that some tracks were skipped.
                msg = f"Exported playlist to:\n{file_path}"
                msg += f"\n\n{len(failed)} tracks were skipped."
                self._show_warning("Export Complete", msg)
            else:
                self._show_success(f"Export complete: playlist exported to {file_path}")

            logger.info(f"Playlist '{playlist.playlist_name}' exported to {file_path} ({len(failed)} failed)")
            return True

        except (OSError, SQLAlchemyError) as e:
            logger.exception(f"Failed to export playlist: {e}")
            self._show_error("Export Error", f"Export failed:\n{e}")
            return False

    def _show_error(self, title: str, text: str):
        """Show a blocking error notice, if messages are enabled."""
        if self.show_messages:
            QMessageBox.critical(self.parent_widget, title, text)

    def _show_warning(self, title: str, text: str):
        """Show a blocking partial-failure notice, if messages are enabled."""
        if self.show_messages:
            QMessageBox.warning(self.parent_widget, title, text)

    def _show_success(self, text: str):
        """Show a non-blocking success toast, if messages are enabled."""
        if not self.show_messages:
            return
        if self.parent_widget is not None:
            show_status_message(self.parent_widget, text)
        else:
            # show_status_message needs a widget to anchor to.
            QMessageBox.information(None, "Export Complete", text)
