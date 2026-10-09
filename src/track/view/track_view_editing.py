"""Edit/delete dialogs, the right-click context menu, and playlist/mood adds for TrackView."""

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QMessageBox
from sqlalchemy.exc import SQLAlchemyError

from src.common.dialogs.delete_confirmation import confirm_delete_with_file_option
from src.common.widgets.entity_submenu import populate_entity_submenu, selection_membership
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message
from src.track.edit.track_edit import MultiTrackEditDialog, TrackEditDialog

# ── Shared by TrackView and BaseTrackView ─────────────────────────────────────


def track_names_preview(tracks: list, limit: int = 3) -> str:
    """Return "A, B, C … and N more" for a delete prompt."""
    names = ", ".join((t.track_name or f"ID {t.track_id}") for t in tracks[:limit])
    if len(tracks) > limit:
        names += f" … and {len(tracks) - limit} more"
    return names


def delete_tracks_with_prompt(parent, controller, tracks: list, confirm=confirm_delete_with_file_option) -> list[int] | None:
    """Ask, then delete `tracks` (and optionally their files); return the deleted ids, or None if cancelled."""
    choice = confirm(parent, "Delete Tracks", f"Delete {len(tracks)} track(s)?\n\n{track_names_preview(tracks)}")
    if choice is None:
        return None
    delete_files = choice == "db_and_file"

    # Collect file paths BEFORE the DB delete — ORM objects may become stale after.
    file_paths = [fp for t in tracks if (fp := getattr(t, "track_file_path", None))] if delete_files else []

    entity_ids = [t.track_id for t in tracks]
    if not controller.delete.delete_entity("Track", entity_ids=entity_ids):
        # Keep the files: deleting them for tracks that are still in the library loses data.
        logger.error(f"Batch delete of {len(entity_ids)} track(s) failed; no files were deleted")
        QMessageBox.warning(parent, "Delete Tracks", "The track(s) could not be removed from the library. No files were deleted. See the log for details.")
        return []
    logger.info(f"Batch-deleted {len(entity_ids)} track(s) from DB")

    failed_paths = []
    for fp in file_paths:
        try:
            if not controller.delete.delete_file(file_path=fp):
                failed_paths.append(fp)
        except OSError as e:
            logger.error(f"Error deleting file {fp}: {e}")
            failed_paths.append(fp)
    if file_paths:
        logger.info(f"Removed {len(file_paths) - len(failed_paths)}/{len(file_paths)} file(s) from disk")
    if failed_paths:
        QMessageBox.warning(
            parent,
            "Some Files Not Deleted",
            f"{len(failed_paths)} of {len(file_paths)} file(s) could not be deleted from disk "
            "(e.g. permission denied or already removed). The library entries were still removed.\n\n" + "\n".join(failed_paths),
        )
    return entity_ids


def add_tracks_to_playlist(controller, playlist_id: int, track_ids: list) -> tuple[int, int, int]:
    """Append tracks to the end of a playlist; return (added, already_there, failed)."""
    ids = [int(t) for t in track_ids]
    existing = controller.get.get_entity_links("PlaylistTracks", playlist_id=playlist_id) or []
    present = {link.track_id for link in existing}
    next_position = max((link.position or 0 for link in existing), default=0) + 1
    new_ids = list(dict.fromkeys(i for i in ids if i not in present))
    rows = [{"playlist_id": playlist_id, "track_id": tid, "position": next_position + n} for n, tid in enumerate(new_ids)]
    added, failed = controller.add.add_entities_with_fallback("PlaylistTracks", rows)
    return len(added), len(ids) - len(new_ids), len(failed)


def add_tracks_to_mood(controller, mood_id: int, track_ids: list) -> tuple[int, int, int]:
    """Tag tracks with a mood; return (added, already_there, failed)."""
    ids = list(dict.fromkeys(int(t) for t in track_ids))
    existing = controller.get.get_entity_links("MoodTrackAssociation", mood_id=mood_id, track_id__in=ids) or []
    present = {link.track_id for link in existing}
    rows = [{"mood_id": mood_id, "track_id": tid} for tid in ids if tid not in present]
    added, failed = controller.add.add_entities_with_fallback("MoodTrackAssociation", rows)
    return len(added), len(ids) - len(rows), len(failed)


def report_add_result(parent, target: str, added: int, already: int, failed: int) -> None:
    """Show the result of a playlist/mood add as a toast, or a warning if rows failed."""
    parts = [f"Added {added} track(s) to {target}."]
    if already:
        parts.append(f"{already} already there.")
    if failed:
        parts.append(f"{failed} could not be added (see the log).")
        QMessageBox.warning(parent, "Some Tracks Not Added", " ".join(parts))
        return
    show_status_message(parent, " ".join(parts))


class TrackViewEditingMixin:
    """Edit/delete workflows plus the right-click context menu and playlist/mood submenus."""

    # =========================================================================
    #  Track editing
    # =========================================================================

    def edit_selected_track(self):
        """Open the track editor for the selection (one track or many)."""
        tracks = self._get_selected_track_objects()
        if not tracks:
            return
        try:
            dialog = TrackEditDialog(tracks[0], self.controller, self) if len(tracks) == 1 else MultiTrackEditDialog(tracks, self.controller, self)
            dialog.accepted.connect(self._force_reload)
            self._track_edit_dialog = dialog
            dialog.show()
        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error opening track edit dialog: {e}")

    # =========================================================================
    #  Track deletion (single or multiple)
    # =========================================================================

    def delete_selected_tracks(self):
        """Delete the selected tracks after the DB-only / DB-and-file prompt."""
        tracks = self._get_selected_track_objects()
        if not tracks:
            return
        deleted = delete_tracks_with_prompt(self, self.controller, tracks, confirm=confirm_delete_with_file_option)
        if deleted:
            self._force_reload()

    # =========================================================================
    #  Context menu
    # =========================================================================

    def show_context_menu(self, pos):
        """Show the play/queue/edit/playlist/mood/delete menu for the selection."""
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return

        tracks = self._get_selected_track_objects()
        if not tracks:
            return

        track_ids = [str(t.track_id) for t in tracks]
        count = len(tracks)

        menu = QMenu(self)

        # ── Playback ──────────────────────────────────────────────────────
        play_next_action = QAction("▶  Play Next", self)
        play_next_action.triggered.connect(lambda: self.add_selected_to_queue(insert_next=True))
        menu.addAction(play_next_action)

        add_queue_action = QAction("➕  Add to Queue", self)  # noqa: RUF001
        add_queue_action.triggered.connect(lambda: self.add_selected_to_queue(False))
        menu.addAction(add_queue_action)

        menu.addSeparator()

        # ── Edit ──────────────────────────────────────────────────────────
        edit_label = f"✏️  Edit {count} Track(s)" if count > 1 else "✏️  Edit Track"
        edit_action = QAction(edit_label, self)
        edit_action.triggered.connect(self.edit_selected_track)
        menu.addAction(edit_action)

        menu.addSeparator()

        # ── Add to Playlist / Add to Mood submenus ───────────────────────
        playlist_menu = QMenu("➕  Add to Playlist", menu)  # noqa: RUF001
        self._populate_playlist_submenu(playlist_menu, tracks, track_ids)
        menu.addMenu(playlist_menu)

        mood_menu = QMenu("🎭  Add to Mood", menu)
        self._populate_mood_submenu(mood_menu, tracks, track_ids)
        menu.addMenu(mood_menu)

        menu.addSeparator()

        # ── Delete ────────────────────────────────────────────────────────
        delete_label = f"🗑  Delete {count} Track(s)" if count > 1 else "🗑  Delete Track"
        delete_action = QAction(delete_label, self)
        delete_action.triggered.connect(self.delete_selected_tracks)
        menu.addAction(delete_action)

        menu.exec_(self.table.viewport().mapToGlobal(pos))

    # =========================================================================
    #  Playlist / mood submenu helpers (shared hierarchical builder)
    # =========================================================================

    def _populate_playlist_submenu(self, parent_menu: QMenu, tracks: list, track_ids: list):
        """Fill the "Add to Playlist" submenu."""
        full, partial = selection_membership(tracks, "playlists", "playlist_id")
        populate_entity_submenu(
            parent_menu,
            controller=self.controller,
            entity_type="Playlist",
            on_trigger=self.add_to_playlist,
            member_ids=full,
            partial_ids=partial,
            make_action_data=lambda entity_id: (entity_id, track_ids),
        )

    def _populate_mood_submenu(self, parent_menu: QMenu, tracks: list, track_ids: list):
        """Fill the "Add to Mood" submenu."""
        full, partial = selection_membership(tracks, "moods", "mood_id")
        populate_entity_submenu(
            parent_menu, controller=self.controller, entity_type="Mood", on_trigger=self.add_to_mood, member_ids=full, partial_ids=partial, make_action_data=lambda entity_id: (entity_id, track_ids)
        )

    def add_to_playlist(self):
        """Add the menu action's tracks to its playlist."""
        action = self.sender()
        if not action:
            return
        playlist_id, track_ids = action.data()
        try:
            added, already, failed = add_tracks_to_playlist(self.controller, playlist_id, track_ids)
        except (SQLAlchemyError, ValueError) as e:
            logger.error(f"Error adding tracks to playlist: {e}")
            QMessageBox.critical(self, "Error", f"Failed to add tracks to playlist:\n{e}")
            return
        logger.info(f"Added {added} track(s) to playlist {playlist_id}")
        report_add_result(self, "the playlist", added, already, failed)

    def add_to_mood(self):
        """Tag the menu action's tracks with its mood."""
        action = self.sender()
        if not action:
            return
        mood_id, track_ids = action.data()
        try:
            added, already, failed = add_tracks_to_mood(self.controller, mood_id, track_ids)
        except (SQLAlchemyError, ValueError) as e:
            logger.error(f"Error adding tracks to mood: {e}")
            QMessageBox.critical(self, "Error", f"Failed to add tracks to mood:\n{e}")
            return
        logger.info(f"Added {added} track(s) to mood {mood_id}")
        report_add_result(self, "the mood", added, already, failed)
