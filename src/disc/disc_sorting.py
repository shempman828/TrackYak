from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QMenu,
    QMessageBox,
    QTreeWidget,
    QTreeWidgetItem,
)
from sqlalchemy.exc import SQLAlchemyError

from src.core.logger_config import logger
from src.core.status_utility import show_status_message


class TrackSortingDisplay(QTreeWidget):
    """
    Displays tracks with intelligent hierarchy based on available metadata.
    Only shows organizational levels that actually exist in the data.
    """

    # Emitted when a track edit dialog is saved and closed.
    # The parent DiscManagementView connects to this to trigger a full reload.
    track_edited = Signal()

    # Emitted after tracks are deleted so the parent view can reload.
    track_deleted = Signal()

    def __init__(
        self, tracks, discs=None, virtual_links=None, controller=None, parent=None
    ):
        super().__init__(parent)
        self.physical_tracks = tracks
        self.discs = discs or []
        self.virtual_links = virtual_links or []
        self.controller = controller  # Needed to open the edit dialog

        # Create a combined list with metadata for sorting
        self.all_track_items = self._prepare_track_items()
        self.grouped_tracks = self._organize_tracks()
        self.init_ui()
        self.populate_tree()

    def _prepare_track_items(self):
        # Look discs up by id from self.discs (freshly queried by the parent
        # view's load_data()) instead of the track.disc relationship -- once
        # lazily loaded, that relationship is cached on the Track instance
        # and the app's session is opened with expire_on_commit=False (see
        # src/db/db_engine.py), so a plain commit() after a disc_id change
        # (e.g. drag-and-drop reassignment) doesn't invalidate it: track.disc
        # keeps returning whatever it resolved to the first time it was
        # accessed, even though track.disc_id (the scalar FK column) is
        # correctly updated and re-synced. A track dragged onto a disc would
        # still group under "Unassigned" here despite disc_id being correct.
        discs_by_id = {disc.disc_id: disc for disc in self.discs}
        items = []
        for track in self.physical_tracks:
            disc = discs_by_id.get(track.disc_id)
            items.append({
                "track": track,
                "is_virtual": False,
                "disc_id": track.disc_id,
                "disc_number": disc.disc_number if disc else None,
                "track_number": track.track_number,
                "side": track.side,
            })
        for link in self.virtual_links:
            if link.track:
                items.append({
                    "track": link.track,
                    "is_virtual": True,
                    "link": link,
                    "disc_number": link.virtual_disc_number,
                    "track_number": link.virtual_track_number,
                    "side": link.virtual_side,
                })
        return items

    def _organize_tracks(self):
        discs = {}
        for disc in self.discs:
            num = disc.disc_number or 0
            discs[num] = {"disc": disc, "sides": {}, "tracks": []}

        for item in self.all_track_items:
            num = item["disc_number"] or 0
            if num not in discs:
                discs[num] = {"disc": None, "sides": {}, "tracks": []}

            if item["side"]:
                side = item["side"]
                if side not in discs[num]["sides"]:
                    discs[num]["sides"][side] = []
                discs[num]["sides"][side].append(item)
            else:
                discs[num]["tracks"].append(item)
        return discs

    def init_ui(self):
        # 1. Configure Columns and Auto-Resizing
        self.setHeaderLabels(["#", "Track Name", "Duration"])
        header = self.header()

        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # #
        header.setSectionResizeMode(1, QHeaderView.Stretch)  # Track Name
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)  # Duration

        # 2. Enable Native Multi-Drag and Drop
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)

        self.setSelectionMode(QAbstractItemView.ExtendedSelection)

        self.setIndentation(20)
        self.setAlternatingRowColors(True)

        # 3. Enable right-click context menu
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

    # -------------------------------------------------------------------------
    # Context menu
    # -------------------------------------------------------------------------

    def show_context_menu(self, position):
        """Show a context menu when the user right-clicks on a track row."""
        item = self.itemAt(position)
        if item is None:
            return

        # track_id is stored in column 0 as an int (set in _create_track_node).
        # Disc and side header items store a disc object instead — skip those.
        track_id = item.data(0, Qt.UserRole)
        if not isinstance(track_id, int):
            return  # User right-clicked a disc or side header, not a track

        # Collect all selected physical track IDs (the user may have
        # highlighted multiple rows before right-clicking).
        physical_ids = []
        has_virtual_selected = False
        for sel_item in self.selectedItems():
            sel_id = sel_item.data(0, Qt.UserRole)
            if not isinstance(sel_id, int):
                continue  # skip disc/side header rows
            if sel_item.data(1, Qt.UserRole):  # is_virtual
                has_virtual_selected = True
            else:
                physical_ids.append(sel_id)

        # If the right-clicked row isn't already in the selection, treat it
        # as a single-item selection so the menu still works intuitively.
        if track_id not in physical_ids and not item.data(1, Qt.UserRole):
            physical_ids = [track_id]

        menu = QMenu(self)

        if physical_ids:
            count = len(physical_ids)
            label = "✏️  Edit Track" if count == 1 else f"✏️  Edit {count} Tracks"
            edit_action = QAction(label, self)
            # Capture the list so the lambda always sees the right value
            edit_action.triggered.connect(
                lambda checked=False, ids=physical_ids: self._edit_track(ids)
            )
            menu.addAction(edit_action)

        if has_virtual_selected:
            # Still show the option but disabled so the user knows why
            note = QAction("✏️  Edit Track  (virtual — open source album)", self)
            note.setEnabled(False)
            menu.addAction(note)

        if physical_ids:
            menu.addSeparator()
            count = len(physical_ids)
            del_label = "🗑️  Delete Track" if count == 1 else f"🗑️  Delete {count} Tracks"
            delete_action = QAction(del_label, self)
            delete_action.triggered.connect(
                lambda checked=False, ids=physical_ids: self._delete_tracks(ids)
            )
            menu.addAction(delete_action)

        menu.exec_(self.viewport().mapToGlobal(position))

    def _edit_track(self, track_ids):
        """
        Open TrackEditDialog for one or more track IDs and reload on save.

        track_ids: a list of int track IDs (single-track edit passes a 1-item list).
        """
        # Resolve controller — it may have been passed in directly, or we can
        # walk up the parent chain to find the DiscManagementView that holds it.
        controller = self.controller
        if controller is None:
            parent = self.parent()
            while parent and not hasattr(parent, "controller"):
                parent = parent.parent()
            if parent:
                controller = parent.controller

        if controller is None:
            logger.error("Cannot open track editor: no controller found")
            QMessageBox.warning(self, "Error", "Could not open track editor.")
            return

        try:
            # Fetch all track objects; skip any IDs that can't be resolved.
            tracks = []
            for tid in track_ids:
                track = controller.get.get_entity_object("Track", track_id=tid)
                if track:
                    tracks.append(track)
                else:
                    logger.warning(f"Track ID {tid} not found — skipping.")

            if not tracks:
                show_status_message(self, "No tracks could be loaded.")
                return

            # Import here to avoid circular imports at module level
            from src.track.track_edit import TrackEditDialog

            # TrackEditDialog accepts a single track or a list — pass the list
            # directly so it can handle both single and multi-track cases.
            dialog = TrackEditDialog(tracks, controller, self)
            # Tell the parent view to reload so changes are visible
            dialog.accepted.connect(self.track_edited.emit)
            self._track_edit_dialog = dialog
            dialog.show()

        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error opening track editor from disc view: {e}")
            QMessageBox.warning(self, "Error", f"Could not open track editor:\n{e}")

    # -------------------------------------------------------------------------
    # Track deletion
    # -------------------------------------------------------------------------

    def _delete_tracks(self, track_ids):
        """
        Delete one or more physical tracks after user confirmation.

        Offers two choices: remove from the library (DB only) or also delete
        the audio file(s) from disk.  Emits track_deleted on success so the
        parent view can reload.
        """
        controller = self.controller
        if controller is None:
            parent = self.parent()
            while parent and not hasattr(parent, "controller"):
                parent = parent.parent()
            if parent:
                controller = parent.controller

        if controller is None:
            logger.error("Cannot delete tracks: no controller found")
            QMessageBox.warning(self, "Error", "Could not delete tracks.")
            return

        # Resolve track objects so we have names and file paths.
        tracks = []
        for tid in track_ids:
            track = controller.get.get_entity_object("Track", track_id=tid)
            if track:
                tracks.append(track)
            else:
                logger.warning(f"Track ID {tid} not found — skipping.")

        if not tracks:
            show_status_message(self, "No tracks could be loaded.")
            return

        count = len(tracks)
        names = ", ".join(
            getattr(t, "track_name", None) or f"ID {t.track_id}" for t in tracks[:3]
        )
        if count > 3:
            names += f" … and {count - 3} more"

        # --- Confirmation dialog: DB only / also delete file(s) / cancel ---
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

        # Collect file paths BEFORE the DB delete — ORM objects may go stale.
        file_paths = []
        if delete_files:
            for track in tracks:
                fp = getattr(track, "track_file_path", None)
                if fp:
                    file_paths.append(fp)

        # --- Batch delete from DB ---
        entity_ids = [track.track_id for track in tracks]
        ok = controller.delete.delete_entity("Track", entity_ids=entity_ids)
        if ok:
            logger.info(f"Batch-deleted {count} track(s) from DB")
        else:
            logger.error(
                "Batch delete returned False — some tracks may not have been removed"
            )

        # --- Optionally remove files from disk ---
        if delete_files and file_paths:
            removed = 0
            failed_paths = []
            for fp in file_paths:
                try:
                    if controller.delete.delete_file(file_path=fp):
                        removed += 1
                    else:
                        failed_paths.append(fp)
                except OSError as e:
                    logger.error(f"Error deleting file {fp}: {e}")
                    failed_paths.append(fp)
            logger.info(f"Removed {removed}/{len(file_paths)} file(s) from disk")
            if failed_paths:
                QMessageBox.warning(
                    self,
                    "Some Files Not Deleted",
                    f"{len(failed_paths)} of {len(file_paths)} file(s) could not be "
                    "deleted from disk (e.g. permission denied or already removed). "
                    "The library entries were still removed.\n\n"
                    + "\n".join(failed_paths),
                )

        self.track_deleted.emit()

    # -------------------------------------------------------------------------
    # Tree population
    # -------------------------------------------------------------------------

    def populate_tree(self):
        self.clear()
        for disc_num, data in sorted(self.grouped_tracks.items()):
            # disc_num == 0 means tracks have no disc assignment
            if disc_num == 0:
                disc_title = "Unassigned Tracks"
            else:
                disc_title = f"Disc {disc_num}"
                if data["disc"] and data["disc"].disc_title:
                    disc_title += f": {data['disc'].disc_title}"

            disc_item = QTreeWidgetItem(self, [disc_title])
            disc_item.setData(0, Qt.UserRole, data["disc"])
            disc_item.setFlags(disc_item.flags() & ~Qt.ItemIsDragEnabled)
            disc_item.setExpanded(True)

            for side_name, side_tracks in sorted(data["sides"].items()):
                side_item = QTreeWidgetItem(disc_item, [f"Side {side_name}"])
                # Column 0 otherwise holds a Disc object (disc headers) or a
                # track_id int (track rows) -- a str here is how dropEvent
                # tells "this branch is a side group" apart from those.
                side_item.setData(0, Qt.UserRole, side_name)
                side_item.setFlags(side_item.flags() & ~Qt.ItemIsDragEnabled)
                side_item.setExpanded(True)
                for t in sorted(side_tracks, key=lambda x: x["track_number"] or 0):
                    self._create_track_node(side_item, t)

            for t in sorted(data["tracks"], key=lambda x: x["track_number"] or 0):
                self._create_track_node(disc_item, t)

    def _create_track_node(self, parent_item, item_dict):
        track = item_dict["track"]
        is_v = item_dict["is_virtual"]

        duration = track.duration_formatted
        # Append a ghost emoji to virtual track names so they're visually
        # distinct without needing a whole dedicated column.
        track_name = (track.track_name or "Unknown") + (" 👻" if is_v else "")

        node = QTreeWidgetItem(
            parent_item,
            [
                str(item_dict["track_number"] or "?"),
                track_name,
                duration,
            ],
        )
        # Store track_id as an int so show_context_menu can identify track rows
        node.setData(0, Qt.UserRole, int(track.track_id))
        node.setData(1, Qt.UserRole, is_v)

        if is_v:
            # Virtual tracks are borrowed from another album's row -- dragging
            # them here wouldn't mean anything, so don't let them be dragged.
            node.setFlags(node.flags() & ~Qt.ItemIsDragEnabled)
            for i in range(3):
                node.setForeground(i, Qt.gray)
            node.setToolTip(1, "Virtual track — borrowed from another album")

    # -------------------------------------------------------------------------
    # Absolute track numbering
    # -------------------------------------------------------------------------

    def assign_absolute_track_numbers(self):
        """
        Walk the tree in its current visible order (discs, then sides, then
        tracks — reflecting any manual drag-and-drop reordering) and assign
        both track numbers to physical tracks:

        - track_number: position within the current disc/side group,
          restarting at 1 for every disc and every side.
        - absolute_track_number: overall position, counting continuously
          across the whole tree without resetting.

        Virtual tracks still occupy a position in both counts (so numbering
        stays correct around them) but are not written to, since they belong
        to another album's track row.
        """
        controller = self.controller
        if controller is None:
            parent = self.parent()
            while parent and not hasattr(parent, "controller"):
                parent = parent.parent()
            if parent:
                controller = parent.controller

        if controller is None:
            logger.error("Cannot renumber tracks: no controller found")
            QMessageBox.warning(self, "Error", "Could not renumber tracks.")
            return

        updates = {}
        absolute_position = 0

        def visit(item):
            nonlocal absolute_position
            group_position = 0
            for i in range(item.childCount()):
                child = item.child(i)
                track_id = child.data(0, Qt.UserRole)
                if isinstance(track_id, int):
                    absolute_position += 1
                    group_position += 1
                    if not child.data(1, Qt.UserRole):  # skip virtual tracks
                        updates[track_id] = (group_position, absolute_position)
                else:
                    visit(child)

        for i in range(self.topLevelItemCount()):
            visit(self.topLevelItem(i))

        if not updates:
            show_status_message(self, "No tracks to renumber.")
            return

        rows = [
            {
                "track_id": track_id,
                "track_number": track_number,
                "absolute_track_number": absolute_number,
            }
            for track_id, (track_number, absolute_number) in updates.items()
        ]
        updated, failed = controller.update.update_entities_bulk_with_fallback(
            "Track", rows
        )
        for row in failed:
            logger.error(f"Failed to renumber track {row['track_id']}")

        logger.info(f"Assigned track numbers to {updated} track(s)")
        show_status_message(self, f"Renumbered {updated} track(s).")
        self.track_edited.emit()

    # -------------------------------------------------------------------------
    # Drag-and-drop
    # -------------------------------------------------------------------------

    def dropEvent(self, event):
        """Let Qt perform the actual internal move (reparenting the dragged
        row(s) into whichever disc/side group and position they were dropped
        on), then persist that new layout -- disc, side, track_number and
        absolute_track_number for every physical track -- in a single batched
        write. No full reload afterward: the tree is already visually correct
        from Qt's own move, and the write already re-syncs the in-memory
        Track objects the parent view holds, so only the stats bar needs a
        (cheap, DB-free) refresh.
        """
        super().dropEvent(event)
        if not event.isAccepted():
            return

        # QAbstractItemView's internal-move drop only *inserts* the dragged
        # row(s) into their new parent inside dropEvent() -- the original
        # row(s) aren't removed from their old parent until the base
        # class's startDrag() calls its own clearOrRemove(), which happens
        # later, after this dropEvent() call (and the QDrag::exec() it's
        # nested inside) returns. Walking the tree synchronously here would
        # therefore see BOTH the new copy and the not-yet-removed old copy
        # of every dragged track and write both -- a duplicate-row race
        # where whichever happens to come last in traversal order wins,
        # regardless of which one is actually correct. Deferring to the
        # next event-loop tick guarantees clearOrRemove() has already run,
        # so the tree is fully settled (no duplicates) by the time we walk it.
        QTimer.singleShot(0, self._persist_drop)

    def _persist_drop(self):
        controller = self.controller
        if controller is None:
            parent_view = self.parent()
            while parent_view and not hasattr(parent_view, "controller"):
                parent_view = parent_view.parent()
            if parent_view:
                controller = parent_view.controller

        if not controller:
            return

        rows = []
        absolute_position = 0

        def visit(item, disc_obj, side_name):
            nonlocal absolute_position
            group_position = 0
            for i in range(item.childCount()):
                child = item.child(i)
                track_id = child.data(0, Qt.UserRole)
                if isinstance(track_id, int):
                    absolute_position += 1
                    group_position += 1
                    child.setText(0, str(group_position))
                    if not child.data(1, Qt.UserRole):  # skip virtual tracks
                        rows.append({
                            "track_id": track_id,
                            "track_number": group_position,
                            "absolute_track_number": absolute_position,
                            "side": side_name,
                            "disc_id": disc_obj.disc_id if disc_obj else None,
                        })
                else:
                    child_side = child.data(0, Qt.UserRole)
                    visit(child, disc_obj, child_side if isinstance(child_side, str) else None)

        for i in range(self.topLevelItemCount()):
            disc_item = self.topLevelItem(i)
            visit(disc_item, disc_item.data(0, Qt.UserRole), None)

        if not rows:
            return

        _, failed = controller.update.update_entities_bulk_with_fallback("Track", rows)
        for row in failed:
            logger.error(
                f"Failed to persist drag-and-drop reorder for track {row['track_id']}"
            )

        # Counts (Unassigned/per-disc) may have shifted if a track changed
        # discs -- update_stats() reads the parent's already-synced in-memory
        # track list, so this is free of any DB round trip.
        parent_view = self.parent()
        while parent_view and not hasattr(parent_view, "update_stats"):
            parent_view = parent_view.parent()
        if parent_view:
            parent_view.update_stats()
