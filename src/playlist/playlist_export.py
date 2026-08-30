from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import QMessageBox
from sqlalchemy.exc import SQLAlchemyError

from src.core.asset_paths import playlist_path
from src.core.logger_config import logger
from src.core.status_utility import show_status_message


class PlaylistExporter:
    """
    Handles exporting a single Playlist ORM object to an M3U file.
    """

    def __init__(self, controller, show_messages: bool = True, parent_widget=None):
        """
        controller: gives access to ORM getters (get_entity_object, get_all_entities)
        show_messages: whether to show user feedback notifications
        parent_widget: optional widget to anchor a non-blocking success toast to.
            Failure/partial-failure paths still use a blocking QMessageBox
            regardless, since export failures involve file I/O and shouldn't be
            easy to miss.
        """
        self.controller = controller
        self.show_messages = show_messages
        self.parent_widget = parent_widget

    def export_playlist(self, playlist_id: int) -> bool:
        """
        Export a single playlist to an M3U file in the standard playlist directory.
        Returns True if successful, False otherwise.
        """
        playlist = self.controller.get.get_entity_object("Playlist", playlist_id=playlist_id)
        if not playlist:
            self._show_message("Export Error", "Playlist not found.")
            return False

        tracks = self.controller.get.get_all_entities("PlaylistTracks", playlist_id=playlist_id)
        if not tracks:
            self._show_message(
                "Export Error", f"No tracks found in playlist '{playlist.playlist_name}'."
            )
            return False

        # Save path using the playlist() shortcut
        try:
            file_path = playlist_path(
                f"{playlist.playlist_name}.m3u"
            )  # Now calls the imported function
            Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.error(f"Could not resolve playlist save path: {e}")
            self._show_message("Export Error", f"Invalid save path for '{playlist.playlist_name}'.")
            return False

        failed = []

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                f.write(f"#PLAYLIST:{playlist.playlist_name}\n")
                if playlist.playlist_description:
                    f.write(f"#DESCRIPTION:{playlist.playlist_description}\n")
                f.write(f"#EXPORT_DATE:{datetime.now().isoformat()}\n")

                for pl_track in tracks:
                    track = self.controller.get.get_entity_object(
                        "Track", track_id=pl_track.track_id
                    )
                    if not track:
                        failed.append((pl_track.track_id, "Missing Track entity"))
                        continue

                    if not track.track_file_path:
                        failed.append((track.track_name, "No file path"))
                        continue

                    try:
                        # Compute relative path from the playlist directory
                        rel_path = Path(track.track_file_path).relative_to(Path(file_path).parent)
                    except ValueError:
                        # If not under same root, fallback to just filename
                        rel_path = Path(track.track_file_path).name

                    duration = int(getattr(track, "track_duration", 0) or 0)
                    artist_names = ", ".join(
                        a.artist_name for a in getattr(track, "artists", []) or []
                    )
                    if not artist_names:
                        artist_names = "Unknown Artist"
                    title = getattr(track, "track_name", "Unknown Title")

                    f.write(f"#EXTINF:{duration},{artist_names} - {title}\n")
                    f.write(f"{rel_path}\n")

            if failed:
                # Partial failure - keep this as a blocking notice so the user
                # doesn't miss that some tracks were skipped.
                msg = f"Exported playlist to:\n{file_path}"
                msg += f"\n\n{len(failed)} tracks were skipped."
                self._show_message("Export Complete", msg)
            else:
                self._show_success(f"Export complete: playlist exported to {file_path}")

            logger.info(
                f"Playlist '{playlist.playlist_name}' exported to {file_path} ({len(failed)} failed)"
            )
            return True

        except (OSError, SQLAlchemyError) as e:
            logger.exception(f"Failed to export playlist: {e}")
            self._show_message("Export Error", f"Export failed:\n{e}")
            return False

    def _show_message(self, title: str, text: str):
        """Helper for showing blocking user feedback (errors/partial failures) if enabled."""
        if self.show_messages:
            QMessageBox.information(None, title, text)

    def _show_success(self, text: str):
        """Helper for showing a non-blocking success toast if enabled.

        Falls back to a blocking QMessageBox when no parent widget was given
        (e.g. exporter used outside a UI context), since show_status_message
        needs a widget to anchor to.
        """
        if not self.show_messages:
            return
        if self.parent_widget is not None:
            show_status_message(self.parent_widget, text)
        else:
            QMessageBox.information(None, "Export Complete", text)
