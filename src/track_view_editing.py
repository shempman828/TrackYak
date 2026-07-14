"""
track_view_editing.py — edit/delete dialogs, right-click context menu, and
mood assignment for TrackView.
"""

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QDialog, QMenu, QMessageBox

from src.logger_config import logger
from src.track_edit import MultiTrackEditDialog, TrackEditDialog


class TrackViewEditingMixin:
    """Edit/delete workflows plus the right-click context menu and moods submenu."""

    # =========================================================================
    #  Track editing
    # =========================================================================

    def edit_selected_track(self):
        tracks = self._get_selected_track_objects()
        if not tracks:
            return
        if len(tracks) == 1:
            try:
                dialog = TrackEditDialog(tracks[0], self.controller, self)
                if dialog.exec_() == QDialog.Accepted:
                    self._force_reload()
            except Exception as e:
                logger.error(f"Error opening track edit dialog: {e}")
        else:
            try:
                dialog = MultiTrackEditDialog(tracks, self.controller, self)
                if dialog.exec_() == QDialog.Accepted:
                    self._force_reload()
            except Exception as e:
                logger.error(f"Error opening multi-track edit dialog: {e}")

    # =========================================================================
    #  Track deletion (single or multiple)
    # =========================================================================

    def delete_selected_tracks(self):
        tracks = self._get_selected_track_objects()
        if not tracks:
            return

        count = len(tracks)
        names = ", ".join(
            getattr(t, "track_name", f"ID {t.track_id}") for t in tracks[:3]
        )
        if count > 3:
            names += f" … and {count - 3} more"

        # --- Ask: DB only, delete file too, or cancel ---
        msg = QMessageBox(self)
        msg.setWindowTitle("Delete Tracks")
        msg.setText(f"Delete {count} track(s)?\n\n{names}")
        msg.setInformativeText(
            "Remove from Library deletes the DB entry only.\n"
            "Delete File(s) Too also removes the audio file(s) from disk."
        )
        msg.setIcon(QMessageBox.Warning)

        btn_db_only = msg.addButton("Remove from Library", QMessageBox.AcceptRole)
        btn_and_file = msg.addButton("Delete File(s) Too", QMessageBox.DestructiveRole)
        msg.addButton("Cancel", QMessageBox.RejectRole)
        msg.setDefaultButton(btn_db_only)
        msg.exec_()

        clicked = msg.clickedButton()
        if clicked is None or clicked.text() == "Cancel":
            return

        delete_files = clicked is btn_and_file

        # Collect file paths BEFORE the DB delete — ORM objects may become stale after.
        file_paths = []
        if delete_files:
            for track in tracks:
                fp = getattr(track, "track_file_path", None)
                if fp:
                    file_paths.append(fp)

        # --- Batch delete from DB (single query via entity_ids) ---
        entity_ids = [track.track_id for track in tracks]
        ok = self.controller.delete.delete_entity("Track", entity_ids=entity_ids)
        if ok:
            logger.info(f"Batch-deleted {count} track(s) from DB")
        else:
            logger.error(
                "Batch delete returned False — some tracks may not have been removed"
            )

        # --- Optionally delete files from disk via the existing delete_file helper ---
        if delete_files and file_paths:
            removed = 0
            for fp in file_paths:
                try:
                    if self.controller.delete.delete_file(file_path=fp):
                        removed += 1
                except Exception as e:
                    logger.error(f"Error deleting file {fp}: {e}")
            logger.info(f"Removed {removed}/{len(file_paths)} file(s) from disk")

        self._force_reload()

    # =========================================================================
    #  Context menu
    # =========================================================================

    def show_context_menu(self, pos):
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
        play_next_action.triggered.connect(
            lambda: self.add_selected_to_queue(insert_next=True)
        )
        menu.addAction(play_next_action)

        add_queue_action = QAction("➕  Add to Queue", self)
        add_queue_action.triggered.connect(lambda: self.add_selected_to_queue(False))
        menu.addAction(add_queue_action)

        menu.addSeparator()

        # ── Edit ──────────────────────────────────────────────────────────
        edit_label = f"✏️  Edit {count} Track(s)" if count > 1 else "✏️  Edit Track"
        edit_action = QAction(edit_label, self)
        edit_action.triggered.connect(self.edit_selected_track)
        menu.addAction(edit_action)

        menu.addSeparator()

        # ── Moods submenu ─────────────────────────────────────────────────
        mood_menu = QMenu("🎭  Add to Mood", menu)
        self._populate_mood_submenu(mood_menu, track_ids)
        menu.addMenu(mood_menu)

        menu.addSeparator()

        # ── Delete ────────────────────────────────────────────────────────
        delete_label = f"🗑  Delete {count} Track(s)" if count > 1 else "🗑  Delete Track"
        delete_action = QAction(delete_label, self)
        delete_action.triggered.connect(self.delete_selected_tracks)
        menu.addAction(delete_action)

        menu.exec_(self.table.viewport().mapToGlobal(pos))

    # =========================================================================
    #  Mood helpers (unchanged logic, extracted to keep context menu tidy)
    # =========================================================================

    def _populate_mood_submenu(self, parent_menu: QMenu, track_ids: list):
        try:
            moods = self.controller.get.get_all_entities("Mood")
            if not moods:
                parent_menu.addAction("No moods found").setEnabled(False)
                return

            member_ids = set()
            if len(track_ids) == 1:
                links = self.controller.get.get_entity_links(
                    "MoodTrackAssociation", track_id=int(track_ids[0])
                )
                if links:
                    member_ids = {lnk.mood_id for lnk in links}

            for mood in moods:
                children = [
                    m
                    for m in moods
                    if getattr(m, "parent_mood_id", None) == mood.mood_id
                ]
                if children:
                    sub = QMenu(mood.mood_name, parent_menu)
                    for child in children:
                        act = QAction(child.mood_name, sub)
                        act.setData((child.mood_id, track_ids))
                        if child.mood_id in member_ids:
                            act.setCheckable(True)
                            act.setChecked(True)
                        act.triggered.connect(self.add_to_mood)
                        sub.addAction(act)
                    parent_menu.addMenu(sub)
                else:
                    act = QAction(mood.mood_name, parent_menu)
                    act.setData((mood.mood_id, track_ids))
                    if mood.mood_id in member_ids:
                        act.setCheckable(True)
                        act.setChecked(True)
                    act.triggered.connect(self.add_to_mood)
                    parent_menu.addAction(act)
        except Exception as e:
            logger.error(f"Error populating mood submenu: {e}")

    def add_to_mood(self):
        action = self.sender()
        if not action:
            return
        mood_id, track_ids = action.data()
        try:
            added = 0
            for track_id in track_ids:
                already = self.controller.get.get_entity_links(
                    "MoodTrackAssociation", mood_id=mood_id, track_id=int(track_id)
                )
                if already:
                    continue
                ok = self.controller.add.add_entity_link(
                    "MoodTrackAssociation",
                    mood_id=mood_id,
                    track_id=int(track_id),
                )
                if ok:
                    added += 1
            logger.info(f"Added {added} track(s) to mood {mood_id}")
        except Exception as e:
            logger.error(f"Error adding tracks to mood: {e}")
