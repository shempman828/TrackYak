from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QMessageBox
from sqlalchemy.exc import SQLAlchemyError

from src.album.base_album_edit import AlbumEditor
from src.artist.artist_edit import ArtistEditor
from src.core.logger_config import logger
from src.core.status_utility import StatusManager, show_status_message
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
        playlist_menu = QMenu("➕  Add to Playlist", self)
        self._populate_playlist_submenu(playlist_menu)
        menu.addMenu(playlist_menu)

        # ── Add to Mood (submenu) ─────────────────────────────────────────
        mood_menu = QMenu("🎭  Add to Mood", self)
        self._populate_mood_submenu(mood_menu)
        menu.addMenu(mood_menu)

        menu.exec_(event.globalPos())

    def _populate_playlist_submenu(self, submenu: QMenu):
        """Fill the Add to Playlist submenu with hierarchical, alphabetically sorted playlists."""

        try:
            # Fetch all playlists with their relationships
            playlists = self.controller.get.get_all_entities("Playlist") or []
            playlists = [p for p in playlists if not getattr(p, "is_smart", 0)]
            if not playlists:
                submenu.addAction("No playlists available").setEnabled(False)
                return

            # Get current track's playlist IDs
            track_playlist_ids = set()
            if self.current_track and hasattr(self.current_track, "playlists"):
                track_playlist_ids = {
                    pt.playlist_id for pt in self.current_track.playlists
                }

            # Build hierarchy map
            {p.playlist_id: p for p in playlists}
            children_map = {}
            for playlist in playlists:
                parent_id = getattr(playlist, "parent_id", None)
                if parent_id not in children_map:
                    children_map[parent_id] = []
                children_map[parent_id].append(playlist)

            # Sort playlists alphabetically at each level
            for children in children_map.values():
                children.sort(key=lambda x: x.playlist_name.lower())

            # Build hierarchical menu starting from root (None parent)
            self._build_playlist_hierarchy(
                submenu, None, children_map, track_playlist_ids
            )

        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error populating playlist submenu: {e}")
            submenu.addAction("Error loading playlists").setEnabled(False)

    def _build_playlist_hierarchy(
        self, parent_menu: QMenu, parent_id, children_map, track_playlist_ids, depth=0
    ):
        """Recursively build playlist hierarchy in the menu."""
        MAX_DEPTH = 8  # Prevent infinite recursion

        if depth > MAX_DEPTH:
            return

        children = children_map.get(parent_id, [])

        for playlist in children:
            # Check if this playlist has children
            has_children = bool(children_map.get(playlist.playlist_id, []))

            if has_children:
                # Create a submenu for playlists with children
                playlist_menu = QMenu(playlist.playlist_name, parent_menu)

                # Recursively add children
                self._build_playlist_hierarchy(
                    playlist_menu,
                    playlist.playlist_id,
                    children_map,
                    track_playlist_ids,
                    depth + 1,
                )

                # Add separator and option to add to this parent playlist
                playlist_menu.addSeparator()
                action = QAction(f"Add to '{playlist.playlist_name}'", playlist_menu)
                action.setData(playlist.playlist_id)

                # Add checkmark if track is in this playlist
                if playlist.playlist_id in track_playlist_ids:
                    action.setCheckable(True)
                    action.setChecked(True)

                action.triggered.connect(
                    self._context_add_to_playlist, Qt.QueuedConnection
                )
                playlist_menu.addAction(action)

                parent_menu.addMenu(playlist_menu)
            else:
                # Direct action for leaf playlists
                action = QAction(playlist.playlist_name, parent_menu)
                action.setData(playlist.playlist_id)

                # Add checkmark if track is in this playlist
                if playlist.playlist_id in track_playlist_ids:
                    action.setCheckable(True)
                    action.setChecked(True)

                action.triggered.connect(
                    self._context_add_to_playlist, Qt.QueuedConnection
                )
                parent_menu.addAction(action)

    def _populate_mood_submenu(self, submenu: QMenu):
        """Fill the Add to Mood submenu with hierarchical, alphabetically sorted moods."""
        try:
            # Fetch all moods with their relationships
            moods = self.controller.get.get_all_entities("Mood") or []

            if not moods:
                submenu.addAction("No moods available").setEnabled(False)
                return

            # Get current track's mood IDs
            track_mood_ids = set()
            if self.current_track and hasattr(self.current_track, "moods"):
                track_mood_ids = {mood.mood_id for mood in self.current_track.moods}

            # Build hierarchy map
            {m.mood_id: m for m in moods}
            children_map = {}
            for mood in moods:
                parent_id = getattr(mood, "parent_id", None)
                if parent_id not in children_map:
                    children_map[parent_id] = []
                children_map[parent_id].append(mood)

            # Sort moods alphabetically at each level
            for children in children_map.values():
                children.sort(key=lambda x: x.mood_name.lower())

            # Build hierarchical menu starting from root (None parent)
            self._build_mood_hierarchy(submenu, None, children_map, track_mood_ids)

        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error populating mood submenu: {e}")
            submenu.addAction("Error loading moods").setEnabled(False)

    def _build_mood_hierarchy(
        self, parent_menu: QMenu, parent_id, children_map, track_mood_ids, depth=0
    ):
        """Recursively build mood hierarchy in the menu."""
        MAX_DEPTH = 8  # Prevent infinite recursion

        if depth > MAX_DEPTH:
            return

        children = children_map.get(parent_id, [])

        for mood in children:
            # Check if this mood has children
            has_children = bool(children_map.get(mood.mood_id, []))

            if has_children:
                # Create a submenu for moods with children
                mood_menu = QMenu(mood.mood_name, parent_menu)

                # Recursively add children
                self._build_mood_hierarchy(
                    mood_menu, mood.mood_id, children_map, track_mood_ids, depth + 1
                )

                # Add separator and option to add to this parent mood
                mood_menu.addSeparator()
                action = QAction(f"Add to '{mood.mood_name}'", mood_menu)
                action.setData(mood.mood_id)

                # Add checkmark if track has this mood
                if mood.mood_id in track_mood_ids:
                    action.setCheckable(True)
                    action.setChecked(True)

                action.triggered.connect(self._context_add_to_mood, Qt.QueuedConnection)
                mood_menu.addAction(action)

                parent_menu.addMenu(mood_menu)
            else:
                # Direct action for leaf moods
                action = QAction(mood.mood_name, parent_menu)
                action.setData(mood.mood_id)

                # Add checkmark if track has this mood
                if mood.mood_id in track_mood_ids:
                    action.setCheckable(True)
                    action.setChecked(True)

                action.triggered.connect(self._context_add_to_mood, Qt.QueuedConnection)
                parent_menu.addAction(action)

    def _context_edit_track(self):
        """Open TrackEditDialog for the currently playing track."""
        if not self.current_track:
            return
        try:
            dialog = TrackEditDialog(self.current_track, self.controller, self)
            dialog.exec_()
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
                    "PlaylistTracks",
                    playlist_id=playlist_id,
                    track_id=track_id,
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
                    "MoodTrackAssociation",
                    mood_id=mood_id,
                    track_id=track_id,
                )
                if not success:
                    show_status_message(self, "Could not remove track from mood.")
                # Update the checkmark state on the action
                action.setCheckable(True)
                action.setChecked(False)
            else:
                # Not in mood — add it
                success = self.controller.add.add_entity_link(
                    "MoodTrackAssociation",
                    mood_id=mood_id,
                    track_id=track_id,
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
            album = self.controller.get.get_entity_object(
                "Album", album_id=album_obj.album_id
            )
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
            artist_obj = self.controller.get.get_entity_object(
                "Artist", artist_id=artist_id
            )
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
            self.controller.update.update_entity(
                "Track",
                track.track_id,
                lyrics=lyrics_text,
            )
            StatusManager.show_message("Lyrics found and saved.", 4000)
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

    @staticmethod
    def _format_lyrics(lyrics_obj) -> str:
        """Convert a Lyrics object or string to a plain storable string."""
        if isinstance(lyrics_obj, str):
            return lyrics_obj

        # Unwrap object wrapper if present (lyriq returns a Lyrics object)
        lyrics_dict = getattr(lyrics_obj, "lyrics", lyrics_obj)

        if isinstance(lyrics_dict, dict):
            lines = []
            for ts in sorted(lyrics_dict.keys()):
                line = lyrics_dict[ts]
                if str(line).strip() == "♪":
                    lines.append("")
                else:
                    lines.append(f"[{ts}] {line}")
            return "\n".join(lines)

        # Fallback: stringify whatever we got
        return str(lyrics_obj)
