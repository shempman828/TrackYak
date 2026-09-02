from PySide6.QtWidgets import QDialog, QMenu, QMessageBox
from sqlalchemy.exc import SQLAlchemyError

from src.album.album_delete_dialog import DeleteEmptyAlbumsDialog
from src.album.album_merge import AlbumMerge
from src.album.album_new import NewAlbumDialog
from src.album.base_album_widget import AlbumWidget
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message


class AlbumContextMenuMixin:
    """
    Right-click context menu for AlbumView: new/merge/delete album(s), add
    to queue, find duplicates.

    Expects the host class to provide: self.controller, self.scroll_area,
    self.scroll_content, self.load_albums(), self._show_album_details(),
    self._get_track_count(), and to be a QWidget subclass.
    """

    # =========================================================================
    # Context Menu
    # =========================================================================

    def _show_context_menu(self, position):
        menu = QMenu(self)

        new_action = menu.addAction("➕ New Album…")
        new_action.triggered.connect(self._create_new_album)

        menu.addSeparator()

        global_pos = self.scroll_area.mapToGlobal(position)
        widget_at = self.scroll_content.childAt(self.scroll_content.mapFromGlobal(global_pos))
        target_album_widget = self._find_album_widget_ancestor(widget_at)

        if target_album_widget is not None:
            album = target_album_widget.album

            queue_action = menu.addAction(
                f"▶ Add to Queue: {getattr(album, 'album_name', 'Album')}"
            )
            queue_action.triggered.connect(lambda: self._add_album_to_queue(album))

            menu.addSeparator()

            # --- Merge submenu ---
            merge_menu = menu.addMenu(f"🔀 Merge {getattr(album, 'album_name', 'Album')}…")
            merge_this_action = merge_menu.addAction("Merge this album into another…")
            merge_this_action.triggered.connect(lambda: self._merge_album(album))

            menu.addSeparator()

            delete_action = menu.addAction(f"🗑 Delete {getattr(album, 'album_name', 'Album')}")
            delete_action.triggered.connect(lambda: self._delete_album(album))
            menu.addSeparator()

        del_empty_action = menu.addAction("🧹 Delete Empty Albums…")
        del_empty_action.triggered.connect(self._delete_empty_albums)

        find_dupes_action = menu.addAction("🔀 Find Duplicate Albums…")
        find_dupes_action.triggered.connect(self._find_duplicate_albums)

        menu.exec_(self.scroll_area.mapToGlobal(position))

    def _merge_album(self, album):
        """Open the merge dialog with this album pre-loaded as source."""
        engine = AlbumMerge(self.controller)
        merged = engine.merge_known(album, parent=self)
        if merged:
            self.load_albums()

    def _find_duplicate_albums(self):
        """Open the duplicate-finder list."""
        engine = AlbumMerge(self.controller)
        engine.open_list(parent=self)
        self.load_albums()

    def _add_album_to_queue(self, album):
        """Add all tracks of the given album to the playback queue."""
        qm = self._get_queue_manager()
        if not qm:
            return

        tracks = self._get_album_tracks(album)
        if not tracks:
            logger.warning(f"No tracks found for album '{getattr(album, 'album_name', '?')}'")
            return

        qm.add_tracks_to_queue(tracks)
        logger.info(f"Added {len(tracks)} tracks from album '{album.album_name}' to queue")

    def _get_queue_manager(self):
        """Retrieve the queue manager from the controller or media player."""
        if hasattr(self.controller, "queue_manager"):
            return self.controller.queue_manager
        if hasattr(self.controller, "mediaplayer") and hasattr(
            self.controller.mediaplayer, "queue_manager"
        ):
            return self.controller.mediaplayer.queue_manager
        logger.warning("Queue manager not found in controller")
        return None

    def _get_album_tracks(self, album):
        """
        Get all tracks belonging to an album.
        Tries album.tracks, album.track_set, and finally a direct DB query."""

        if hasattr(album, "tracks") and album.tracks is not None:
            return list(album.tracks)

        # Last resort: query all tracks and filter by album_id
        try:
            all_tracks = self.controller.get.get_all_entities("Track") or []
            return [t for t in all_tracks if getattr(t, "album_id", None) == album.album_id]
        except SQLAlchemyError as e:
            logger.error(f"Failed to fetch tracks for album {album.album_id}: {e}")
            return []

    @staticmethod
    def _find_album_widget_ancestor(widget) -> "AlbumWidget | None":
        """Walk up the widget hierarchy to find an AlbumWidget."""
        while widget is not None:
            if isinstance(widget, AlbumWidget):
                return widget
            widget = widget.parent()
        return None

    # =========================================================================
    # Album CRUD
    # =========================================================================

    def _create_new_album(self):
        """Open dialog to create a new album."""
        dlg = NewAlbumDialog(self.controller, self)
        if dlg.exec_() != QDialog.Accepted:
            return

        try:
            kwargs = {"album_name": dlg.album_name, "is_compilation": dlg.is_compilation}
            if dlg.release_year is not None:
                kwargs["release_year"] = dlg.release_year

            new_album = self.controller.add.add_entity("Album", **kwargs)

            # Optionally link an artist
            if dlg.artist_name:
                artist = self.controller.get.get_entity_object(
                    "Artist", artist_name=dlg.artist_name
                )
                if not artist:
                    artist = self.controller.add.add_entity("Artist", artist_name=dlg.artist_name)
                self.controller.add.add_entity(
                    "AlbumRoleAssociation",
                    album_id=new_album.album_id,
                    artist_id=artist.artist_id,
                    role_id=1,
                )

            logger.info(f"Created new album: {new_album.album_name}")
            self.load_albums()

            # Open the detail view immediately so the user can fill it in
            self._show_album_details(new_album)

        except SQLAlchemyError as e:
            logger.exception("Failed to create album")
            QMessageBox.critical(self, "Error", f"Could not create album:\n{e}")

    def _delete_album(self, album):
        """Confirm and delete a single album."""
        name = getattr(album, "album_name", "this album")
        track_count = self._get_track_count(album)

        msg = f'Delete "{name}"?'
        if track_count:
            msg += (
                f"\n\nThis album has {track_count} track(s). "
                "Tracks will be disassociated but not removed from your library."
            )
        msg += "\n\nThis action cannot be undone."

        reply = QMessageBox.question(
            self, "Confirm Delete", msg, QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        try:
            self.controller.delete.delete_entity("Album", album.album_id)
            logger.info(f"Deleted album: {name} (id={album.album_id})")
            self.load_albums()
        except SQLAlchemyError as e:
            logger.exception("Failed to delete album")
            QMessageBox.critical(self, "Error", f"Could not delete album:\n{e}")

    def _delete_empty_albums(self):
        """Find and delete all empty albums after user confirmation."""
        try:
            all_albums = self.controller.get.get_all_entities("Album")
            empty_albums = [a for a in all_albums if self._get_track_count(a) == 0]

            if not empty_albums:
                show_status_message(self, "No empty albums found in your library.")
                return

            confirm_dialog = DeleteEmptyAlbumsDialog(empty_albums, self)
            if confirm_dialog.exec_() != QDialog.Accepted:
                return

            album_ids = [a.album_id for a in empty_albums]
            if self.controller.delete.delete_entity("Album", entity_ids=album_ids):
                deleted = len(album_ids)
            else:
                deleted = 0
                logger.warning("Failed to delete empty albums (batch delete failed)")

            show_status_message(self, f"Deleted {deleted} empty album(s).")
            self.load_albums()

        except SQLAlchemyError as e:
            logger.exception("Failed to delete empty albums")
            QMessageBox.critical(self, "Error", f"Failed to delete empty albums:\n{e}")
