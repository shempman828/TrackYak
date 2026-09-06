from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QMessageBox
from sqlalchemy.exc import SQLAlchemyError

from src.album.base_album_edit import AlbumEditor
from src.artist.artist_edit import ArtistEditor
from src.common.entity_submenu import populate_entity_submenu
from src.foundation.logger_config import logger
from src.foundation.status_utility import StatusManager, show_status_message
from src.lyrics.lyrics_format import format_lyrics_for_storage
from src.mood.mood_autotag import auto_tag_lyrics_safe
from src.track.track_edit import TrackEditDialog


class PlayerContextMenuMixin:
    """
    Right-click context menu for PlayerUI: edit track/album/artist, search
    lyrics, and add-to-playlist/mood submenus.

    Expects the host class to provide: self.controller, self.current_track,
    self._lyric_thread, self.parent_window, and to be a QWidget subclass
    (for self.parent(), self.sender(), etc).
    """

    # =========================================================================
    #  Right-click context menu
    # =========================================================================

    def contextMenuEvent(self, event):
        """
        Show a context menu when the user right-clicks anywhere on the player dock.
        Only appears when a track is loaded (self.current_track is not None).
        """
        if not self.current_track:
            return  # Nothing playing — skip the menu

        # self.current_track is set once when playback starts (track_changed)
        # and never touched again, so edits made elsewhere (Edit Track/Album/
        # Artist here, or any other tab) while this track is loaded leave it
        # stale. expire_on_commit=False means those commits don't invalidate
        # it on their own -- expire + re-fetch forces a fresh read, mirroring
        # refresh_view() in base_album_edit.py.
        session = self.controller.get.session
        track_id = self.current_track.track_id
        session.expire(self.current_track)
        updated = self.controller.get.get_entity_object("Track", track_id=track_id)
        if not updated:
            return  # Track was deleted elsewhere — skip the menu
        self.current_track = updated

        menu = QMenu(self)

        # ── Edit Track ────────────────────────────────────────────────────
        edit_action = QAction("✏️  Edit Track", self)
        edit_action.triggered.connect(self._context_edit_track)
        menu.addAction(edit_action)

        # ── Edit Album ────────────────────────────────────────────────────
        edit_album_action = QAction("💿  Edit Album", self)
        edit_album_action.triggered.connect(self._context_edit_album)
        menu.addAction(edit_album_action)

        # ── Edit Artist (submenu — one entry per primary artist) ──────────
        edit_artist_menu = QMenu("🎤  Edit Artist", self)
        self._populate_edit_artist_submenu(edit_artist_menu)
        menu.addMenu(edit_artist_menu)

        # ── Search Lyrics ─────────────────────────────────────────────────
        lyrics_action = QAction("🔍  Search Lyrics", self)
        lyrics_action.triggered.connect(self._context_search_lyrics)
        menu.addAction(lyrics_action)

        menu.addSeparator()

        # ── Add to Playlist (submenu) ─────────────────────────────────────
        playlist_menu = QMenu("➕  Add to Playlist", self)  # noqa: RUF001
        self._populate_playlist_submenu(playlist_menu)
        menu.addMenu(playlist_menu)

        # ── Add to Mood (submenu) ─────────────────────────────────────────
        mood_menu = QMenu("🎭  Add to Mood", self)
        self._populate_mood_submenu(mood_menu)
        menu.addMenu(mood_menu)

        menu.exec_(event.globalPos())

    def _current_track_member_ids(self, relation_attr, id_attr):
        """Ids of the playlists/moods the currently playing track already belongs to."""
        if self.current_track is None:
            return set()
        try:
            return {
                getattr(link, id_attr) for link in getattr(self.current_track, relation_attr, [])
            }
        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error reading {relation_attr} for context menu: {e}")
            return set()

    def _populate_playlist_submenu(self, submenu: QMenu):
        """Fill the Add to Playlist submenu with hierarchical, alphabetically sorted playlists."""
        populate_entity_submenu(
            submenu,
            controller=self.controller,
            entity_type="Playlist",
            on_trigger=self._context_add_to_playlist,
            member_ids=self._current_track_member_ids("playlists", "playlist_id"),
            connection_type=Qt.QueuedConnection,
        )

    def _populate_mood_submenu(self, submenu: QMenu):
        """Fill the Add to Mood submenu with hierarchical, alphabetically sorted moods."""
        populate_entity_submenu(
            submenu,
            controller=self.controller,
            entity_type="Mood",
            on_trigger=self._context_add_to_mood,
            member_ids=self._current_track_member_ids("moods", "mood_id"),
            connection_type=Qt.QueuedConnection,
        )

    def _context_edit_track(self):
        """Open TrackEditDialog for the currently playing track."""
        if not self.current_track:
            return
        try:
            dialog = TrackEditDialog(self.current_track, self.controller, self)
            self._track_edit_dialog = dialog
            dialog.show()
        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error opening track editor from player dock: {e}")
            QMessageBox.critical(self, "Error", f"Could not open track editor:\\n{e}")

    def _context_add_to_playlist(self):
        """Toggle the currently playing track in/out of the chosen playlist.
        If the track is already in the playlist (action is checked), remove it.
        Otherwise add it.
        """
        action = self.sender()
        if not action or not self.current_track:
            return

        playlist_id = action.data()
        track_id = self.current_track.track_id

        try:
            # Check if the track is already in the playlist
            already_in = self.controller.get.get_entity_links(
                "PlaylistTracks", playlist_id=playlist_id, track_id=track_id
            )

            if already_in:
                # Track is already in playlist — remove it
                success = self.controller.delete.delete_entity(
                    "PlaylistTracks", playlist_id=playlist_id, track_id=track_id
                )
                if not success:
                    show_status_message(self, "Could not remove track from playlist.")
                # Update the checkmark state on the action
                action.setCheckable(True)
                action.setChecked(False)
            else:
                # Track is not in playlist — add it
                existing = self.controller.get.get_entity_links(
                    "PlaylistTracks", playlist_id=playlist_id
                )
                next_position = max((t.position for t in existing), default=0) + 1

                success = self.controller.add.add_entity_link(
                    "PlaylistTracks",
                    playlist_id=playlist_id,
                    track_id=track_id,
                    position=next_position,
                )
                if not success:
                    show_status_message(self, "Could not add track to playlist.")
                # Update the checkmark state on the action
                action.setCheckable(True)
                action.setChecked(True)

        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error toggling track in playlist from player dock: {e}")
            QMessageBox.critical(self, "Error", f"Failed to update playlist:\n{e}")

    def _context_add_to_mood(self):
        """Toggle the currently playing track in/out of the chosen mood.
        If the track is already in the mood (action is checked), remove it.
        Otherwise add it.
        """
        action = self.sender()
        if not action or not self.current_track:
            return

        mood_id = action.data()
        track_id = self.current_track.track_id

        try:
            # Check if already associated
            existing = self.controller.get.get_entity_links(
                "MoodTrackAssociation", mood_id=mood_id, track_id=track_id
            )

            if existing:
                # Already in mood — remove it
                success = self.controller.delete.delete_entity(
                    "MoodTrackAssociation", mood_id=mood_id, track_id=track_id
                )
                if not success:
                    show_status_message(self, "Could not remove track from mood.")
                # Update the checkmark state on the action
                action.setCheckable(True)
                action.setChecked(False)
            else:
                # Not in mood — add it
                success = self.controller.add.add_entity_link(
                    "MoodTrackAssociation", mood_id=mood_id, track_id=track_id
                )
                if not success:
                    show_status_message(self, "Could not add track to mood.")
                # Update the checkmark state on the action
                action.setCheckable(True)
                action.setChecked(True)

        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error toggling track in mood from player dock: {e}")
            QMessageBox.critical(self, "Error", f"Failed to update mood:\n{e}")

    def _context_edit_album(self):
        """Open the AlbumEditor for the currently playing track's album."""
        if not self.current_track:
            return
        try:
            album_obj = getattr(self.current_track, "album", None)
            if album_obj is None:
                return
            album = self.controller.get.get_entity_object("Album", album_id=album_obj.album_id)
            if album:
                dialog = AlbumEditor(self.controller, album)
                dialog.exec_()
        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error opening AlbumEditor from player dock: {e}")
            QMessageBox.critical(self, "Error", f"Could not open album editor:\n{e}")

    def _populate_edit_artist_submenu(self, submenu: QMenu):
        """
        Fill the Edit Artist submenu with one entry per primary artist on the track.
        Primary artists are those with the role name 'Primary Artist'.
        Falls back to the plain artists list if no primary role is found.
        """
        if not self.current_track:
            submenu.addAction("No track loaded").setEnabled(False)
            return
        try:
            # Collect primary artists via TrackArtistRole
            primary_artists = []
            roles = getattr(self.current_track, "artist_roles", []) or []
            for role_assoc in roles:
                role = getattr(role_assoc, "role", None)
                if role and getattr(role, "role_name", "") == "Primary Artist":
                    artist = getattr(role_assoc, "artist", None)
                    if artist:
                        primary_artists.append(artist)

            # Fallback: use track.artists if no primary role entries found
            if not primary_artists:
                primary_artists = list(getattr(self.current_track, "artists", []) or [])

            if not primary_artists:
                submenu.addAction("No artists found").setEnabled(False)
                return

            for artist in primary_artists:
                artist_name = getattr(artist, "artist_name", "Unknown Artist")
                action = QAction(artist_name, submenu)
                action.setData(getattr(artist, "artist_id", None))
                action.triggered.connect(self._context_edit_artist)
                submenu.addAction(action)

        except (SQLAlchemyError, RuntimeError, AttributeError) as e:
            logger.error(f"Error building Edit Artist submenu: {e}")
            submenu.addAction("Error loading artists").setEnabled(False)

    def _context_edit_artist(self):
        """Open the ArtistEditor for the artist chosen in the submenu."""
        action = self.sender()
        if not action:
            return
        artist_id = action.data()
        if artist_id is None:
            return
        try:
            artist_obj = self.controller.get.get_entity_object("Artist", artist_id=artist_id)
            if artist_obj:
                dialog = ArtistEditor(self.controller, artist_obj, self)
                dialog.exec_()
        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error opening ArtistEditor from player dock: {e}")
            QMessageBox.critical(self, "Error", f"Could not open artist editor:\n{e}")

    def _context_search_lyrics(self):
        """Search for lyrics for the currently playing track and save them."""
        if not self.current_track:
            return
        if self._lyric_thread.is_running:
            return
        StatusManager.show_message("Searching for lyrics…", 0)
        self._lyric_search_track = self.current_track
        self._lyric_thread.search(self.current_track)

    def _on_lyrics_ready(self, lyrics) -> None:
        track = self._lyric_search_track
        try:
            lyrics_text = self._format_lyrics(lyrics)
            self.controller.update.update_entity("Track", track.track_id, lyrics=lyrics_text)
            message = "Lyrics found and saved."
            if lyrics_text:
                moods_added, _places_added = auto_tag_lyrics_safe(
                    self.controller, track.track_id, lyrics_text
                )
                if moods_added:
                    message += f" Tagged mood(s): {', '.join(moods_added)}."
            StatusManager.show_message(message, 4000)
            if self.current_track is track:
                self._reload_now_playing()
        except (SQLAlchemyError, AttributeError, TypeError) as e:
            logger.error(f"Error saving lyrics from player dock: {e}")
            StatusManager.show_message(f"Lyrics search failed: {e}", 5000)

    def _on_lyrics_not_found(self) -> None:
        StatusManager.show_message("No lyrics found for this track.", 4000)

    def _on_lyric_error(self, message: str) -> None:
        logger.error(f"Lyrics search error from player dock: {message}")
        StatusManager.show_message(f"Lyrics search failed: {message}", 5000)

    def _reload_now_playing(self):
        """
        Ask the main window to refresh the Now Playing view.
        Safe to call even if the main window or the view don't exist.
        """
        try:
            # Walk up the parent chain to find the QMainWindow
            main_win = self.parent_window
            if main_win is None:
                # Fallback: try Qt parent hierarchy
                w = self.parent()
                while w is not None:
                    from PySide6.QtWidgets import QMainWindow

                    if isinstance(w, QMainWindow):
                        main_win = w
                        break
                    w = w.parent()

            if main_win and hasattr(main_win, "update_now_playing_view"):
                track = self.current_track
                if track and hasattr(track, "track_file_path"):
                    from pathlib import Path

                    main_win.update_now_playing_view(Path(track.track_file_path))
        except (RuntimeError, TypeError) as e:
            logger.error(f"Error reloading Now Playing view after lyrics save: {e}")

    _format_lyrics = staticmethod(format_lyrics_for_storage)
