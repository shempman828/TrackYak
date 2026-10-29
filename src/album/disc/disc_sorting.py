"""Track tree for the album Tracks tab: disc/side grouping, drag-and-drop, edit, delete and renumber."""

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QPalette
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QMenu, QMessageBox, QTreeWidget, QTreeWidgetItem
from sqlalchemy.exc import SQLAlchemyError

from src.album.release_type_utils import is_single
from src.common.dialogs.delete_confirmation import confirm_delete_with_file_option
from src.core.status_widget import show_status_message
from src.foundation.logger_config import logger

# Column 0: int track_id on track rows, Disc (or None) on group rows.
_TRACK_ID_ROLE = Qt.UserRole
# Column 0: side label on track rows. It moves with the row on drag-and-drop.
_SIDE_ROLE = Qt.UserRole + 1
# Column 1: True on virtual track rows.
_VIRTUAL_ROLE = Qt.UserRole

# Group key of the "Unassigned Tracks" row. Real discs use (disc_number, disc_id).
_UNASSIGNED_KEY = (0, 0)

# Fields that track renumbering and drag-and-drop write; used to skip unchanged rows.
_LAYOUT_FIELDS = ("track_number", "absolute_track_number", "side", "disc_id")

# Differs from every real side value (None too), so the first row always starts a new run.
_NO_SIDE_YET = object()


def _side_sort_key(side_name: str):
    """Numeric-aware sort key so side labels like "1", "2", "10" order naturally."""
    return (0, int(side_name)) if side_name.isdigit() else (1, side_name)


def _is_track_row(item) -> bool:
    """True for a track row, False for a disc/unassigned group row."""
    return isinstance(item.data(0, _TRACK_ID_ROLE), int)


def _numbered_track_rows(group_item):
    """Yield (row, track_number, side) per track row, restarting the number on each run of one side."""
    position = 0
    current_side = _NO_SIDE_YET
    for i in range(group_item.childCount()):
        row = group_item.child(i)
        if not _is_track_row(row):
            continue
        side = row.data(0, _SIDE_ROLE)
        if side != current_side:
            current_side = side
            position = 0
        position += 1
        yield row, position, side


class TrackSortingDisplay(QTreeWidget):
    """Tree of an album's tracks grouped by disc, with sides folded into the number column."""

    # A track edit dialog was saved, or tracks were renumbered.
    track_edited = Signal()
    # Tracks were deleted.
    track_deleted = Signal()
    # A drag-and-drop changed and saved track numbers, sides or discs.
    layout_changed = Signal()
    # The user asked to edit or remove the Disc from a group row.
    edit_disc_requested = Signal(object)
    remove_disc_requested = Signal(object)

    def __init__(self, tracks, discs=None, virtual_links=None, controller=None, release_type=None, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.init_ui()
        self.set_data(tracks, discs=discs, virtual_links=virtual_links, release_type=release_type)

    def set_data(self, tracks, discs=None, virtual_links=None, release_type=None):
        """Replace the shown data and rebuild the rows, keeping the selection and scroll position."""
        selected_track_ids, selected_disc_ids = self._selection_ids()
        scroll = self.verticalScrollBar().value()

        self.physical_tracks = list(tracks or [])
        self.discs = list(discs or [])
        self.virtual_links = list(virtual_links or [])
        self._tracks_by_id = {track.track_id: track for track in self.physical_tracks}
        self.is_single = is_single(release_type, len(self.physical_tracks), len(self.discs))
        self.all_track_items = self._prepare_track_items()
        self.grouped_tracks = self._organize_tracks()
        self.populate_tree()

        self._restore_selection(selected_track_ids, selected_disc_ids)
        self.verticalScrollBar().setValue(scroll)

    def _prepare_track_items(self):
        """Build a flat list of per-track display dicts, physical and virtual alike."""
        # Use self.discs, not track.disc: the session has expire_on_commit=False, so a cached
        # track.disc stays stale after a drag-and-drop changes disc_id.
        discs_by_id = {disc.disc_id: disc for disc in self.discs}
        items = []
        for track in self.physical_tracks:
            disc = discs_by_id.get(track.disc_id)
            items.append(
                {
                    "track": track,
                    "is_virtual": False,
                    "disc_id": track.disc_id if disc else None,
                    "disc_number": disc.disc_number if disc else None,
                    "track_number": track.track_number,
                    "side": track.side,
                }
            )
        for link in self.virtual_links:
            if link.track:
                items.append(
                    {
                        "track": link.track,
                        "is_virtual": True,
                        "link": link,
                        "disc_id": None,
                        "disc_number": link.virtual_disc_number,
                        "track_number": link.virtual_track_number,
                        "side": link.virtual_side,
                    }
                )
        return items

    def _organize_tracks(self):
        """Group track items by (disc_number, disc_id); virtual tracks join the first disc with their number."""
        groups = {}
        first_key_by_number = {}
        for disc in sorted(self.discs, key=lambda d: (d.disc_number or 0, d.disc_id)):
            number = disc.disc_number or 0
            key = (number, disc.disc_id)
            if number in first_key_by_number:
                # No DB unique constraint on disc_number: show both discs, so a drop keeps each track's disc.
                logger.warning(f"Duplicate disc_number {number} on album discs {first_key_by_number[number][1]} and {disc.disc_id}")
            first_key_by_number.setdefault(number, key)
            groups[key] = {"disc": disc, "items": []}

        if self.discs:
            # Always present, so a track can be dragged back out of a disc.
            groups.setdefault(_UNASSIGNED_KEY, {"disc": None, "items": []})

        for item in self.all_track_items:
            if item["is_virtual"]:
                number = item["disc_number"] or 0
                key = first_key_by_number.get(number, (number, 0))
            elif item["disc_id"] is not None:
                key = (item["disc_number"] or 0, item["disc_id"])
            else:
                key = _UNASSIGNED_KEY
            groups.setdefault(key, {"disc": None, "items": []})["items"].append(item)
        return groups

    def init_ui(self):
        """Configure tree columns, drag-and-drop, selection mode, and the context menu."""
        self.setHeaderLabels(["#", "Track Name", "Duration"])
        header = self.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)

        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        # Tracks may only land inside a group row, never at the top level.
        root = self.invisibleRootItem()
        root.setFlags(root.flags() & ~Qt.ItemIsDropEnabled)

        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setIndentation(20)
        self.setAlternatingRowColors(True)
        self.setExpandsOnDoubleClick(False)
        self.setAccessibleName("Album tracks by disc")
        self.setToolTip("Drag tracks to reorder them or move them to another disc. Right-click, F2 or Enter edits; Delete removes.")

        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)
        self.itemDoubleClicked.connect(self._on_item_double_clicked)

    # -------------------------------------------------------------------------
    # Controller and track lookup
    # -------------------------------------------------------------------------

    def _require_controller(self, action):
        """Return the controller passed in or the nearest ancestor's, warning the user when there is none."""
        controller = self.controller
        if controller is None:
            parent = self.parent()
            while parent is not None and not hasattr(parent, "controller"):
                parent = parent.parent()
            controller = parent.controller if parent is not None else None
        if controller is None:
            logger.error(f"Cannot {action}: no controller found")
            QMessageBox.warning(self, "Error", f"Could not {action}.")
        return controller

    def _load_tracks(self, controller, track_ids):
        """Return Track objects for track_ids, from the loaded rows where possible."""
        tracks = []
        for tid in track_ids:
            track = self._tracks_by_id.get(tid) or controller.get.get_entity_object("Track", track_id=tid)
            if track:
                tracks.append(track)
            else:
                logger.warning(f"Track ID {tid} not found — skipping.")
        return tracks

    # -------------------------------------------------------------------------
    # Selection
    # -------------------------------------------------------------------------

    def _selected_track_ids(self):
        """Return (physical track IDs, any virtual row selected) for the current selection."""
        physical_ids = []
        has_virtual = False
        for item in self.selectedItems():
            if not _is_track_row(item):
                continue
            if item.data(1, _VIRTUAL_ROLE):
                has_virtual = True
            else:
                physical_ids.append(item.data(0, _TRACK_ID_ROLE))
        return physical_ids, has_virtual

    def _selection_ids(self):
        """Return (selected track IDs, selected disc IDs) so a rebuild can restore them."""
        track_ids, disc_ids = set(), set()
        for item in self.selectedItems():
            value = item.data(0, _TRACK_ID_ROLE)
            if isinstance(value, int):
                track_ids.add(value)
            elif value is not None:
                disc_ids.add(value.disc_id)
        return track_ids, disc_ids

    def _restore_selection(self, track_ids, disc_ids):
        """Reselect rows whose track or disc ID was selected before a rebuild."""
        if not track_ids and not disc_ids:
            return
        for group in self._group_items():
            disc = group.data(0, _TRACK_ID_ROLE)
            if disc is not None and disc.disc_id in disc_ids:
                group.setSelected(True)
            for i in range(group.childCount()):
                row = group.child(i)
                if row.data(0, _TRACK_ID_ROLE) in track_ids:
                    row.setSelected(True)

    def selected_disc(self):
        """Return the Disc of the first selected row (group or track), or None."""
        items = self.selectedItems()
        if not items:
            return None

        item = items[0]
        while item.parent() is not None:
            item = item.parent()

        disc = item.data(0, _TRACK_ID_ROLE)
        return disc if not isinstance(disc, (int, str)) else None

    def _current_group_disc(self):
        """Return the Disc of the current row when it is a disc group row, else None."""
        item = self.currentItem()
        if item is None or _is_track_row(item):
            return None
        return item.data(0, _TRACK_ID_ROLE)

    # -------------------------------------------------------------------------
    # Context menu, double-click, keys
    # -------------------------------------------------------------------------

    def show_context_menu(self, position):
        """Show the track or disc menu for the right-clicked row."""
        item = self.itemAt(position)
        if item is None:
            return

        if not _is_track_row(item):
            self._show_disc_menu(item.data(0, _TRACK_ID_ROLE), position)
            return

        track_id = item.data(0, _TRACK_ID_ROLE)
        physical_ids, has_virtual_selected = self._selected_track_ids()

        # A right-clicked row outside the selection acts as a one-row selection.
        if item.data(1, _VIRTUAL_ROLE):
            if item not in self.selectedItems():
                has_virtual_selected = True
        elif track_id not in physical_ids:
            physical_ids = [track_id]

        menu = QMenu(self)

        if physical_ids:
            count = len(physical_ids)
            label = "✏️  Edit Track" if count == 1 else f"✏️  Edit {count} Tracks"
            edit_action = QAction(label, self)
            edit_action.triggered.connect(lambda checked=False, ids=physical_ids: self._edit_track(ids))
            menu.addAction(edit_action)

        if has_virtual_selected:
            # Shown disabled so the user knows why a virtual row cannot be edited here.
            note = QAction("✏️  Edit Track  (virtual — open source album)", self)
            note.setEnabled(False)
            menu.addAction(note)

        if physical_ids:
            menu.addSeparator()
            count = len(physical_ids)
            del_label = "🗑️  Delete Track" if count == 1 else f"🗑️  Delete {count} Tracks"
            delete_action = QAction(del_label, self)
            delete_action.triggered.connect(lambda checked=False, ids=physical_ids: self._delete_tracks(ids))
            menu.addAction(delete_action)

        menu.exec(self.viewport().mapToGlobal(position))

    def _show_disc_menu(self, disc, position):
        """Show Edit/Remove Disc for a real disc group row."""
        if disc is None:
            return
        menu = QMenu(self)
        edit_action = QAction("✏️  Edit Disc", self)
        edit_action.triggered.connect(lambda checked=False: self.edit_disc_requested.emit(disc))
        menu.addAction(edit_action)
        remove_action = QAction("🗑️  Remove Disc", self)
        remove_action.triggered.connect(lambda checked=False: self.remove_disc_requested.emit(disc))
        menu.addAction(remove_action)
        menu.exec(self.viewport().mapToGlobal(position))

    def _on_item_double_clicked(self, item, _column):
        """Double-click edits the disc of a group row or the physical track of a track row."""
        if _is_track_row(item):
            if not item.data(1, _VIRTUAL_ROLE):
                self._edit_track([item.data(0, _TRACK_ID_ROLE)])
            return
        disc = item.data(0, _TRACK_ID_ROLE)
        if disc is not None:
            self.edit_disc_requested.emit(disc)

    def keyPressEvent(self, event):
        """Delete removes and F2/Enter edits the selected tracks, or the current disc group row."""
        key = event.key()
        if key in (Qt.Key_Delete, Qt.Key_F2, Qt.Key_Return, Qt.Key_Enter):
            physical_ids, _ = self._selected_track_ids()
            if physical_ids:
                (self._delete_tracks if key == Qt.Key_Delete else self._edit_track)(physical_ids)
                return
            disc = self._current_group_disc()
            if disc is not None:
                (self.remove_disc_requested if key == Qt.Key_Delete else self.edit_disc_requested).emit(disc)
                return
        super().keyPressEvent(event)

    # -------------------------------------------------------------------------
    # Track edit and delete
    # -------------------------------------------------------------------------

    def _edit_track(self, track_ids):
        """Open TrackEditDialog for the given track IDs; emit track_edited when it is saved."""
        controller = self._require_controller("open track editor")
        if controller is None:
            return

        try:
            tracks = self._load_tracks(controller, track_ids)
            if not tracks:
                show_status_message(self, "No tracks could be loaded.")
                return

            # Imported here to avoid a circular import.
            from src.track.edit.track_edit import TrackEditDialog

            dialog = TrackEditDialog(tracks, controller, self)
            dialog.accepted.connect(self.track_edited.emit)
            self._track_edit_dialog = dialog
            dialog.show()

        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error opening track editor from disc view: {e}")
            QMessageBox.warning(self, "Error", f"Could not open track editor:\n{e}")

    def _delete_tracks(self, track_ids):
        """Delete physical tracks (and optionally their files) after confirmation; emit track_deleted."""
        controller = self._require_controller("delete tracks")
        if controller is None:
            return

        tracks = self._load_tracks(controller, track_ids)
        if not tracks:
            show_status_message(self, "No tracks could be loaded.")
            return

        count = len(tracks)
        names = ", ".join(getattr(t, "track_name", None) or f"ID {t.track_id}" for t in tracks[:3])
        if count > 3:
            names += f" … and {count - 3} more"

        choice = confirm_delete_with_file_option(self, "Delete Tracks", f"Delete {count} track(s)?\n\n{names}")
        if choice is None:
            return

        delete_files = choice == "db_and_file"

        # Read file paths before the DB delete: the ORM objects go stale after it.
        file_paths = [fp for fp in (getattr(t, "track_file_path", None) for t in tracks) if fp] if delete_files else []

        entity_ids = [track.track_id for track in tracks]
        ok = controller.delete.delete_entity("Track", entity_ids=entity_ids)
        if not ok:
            logger.error("Batch delete returned False — tracks were not removed")
            QMessageBox.warning(self, "Error", "Could not delete the selected track(s) from the library.")
            return
        logger.info(f"Batch-deleted {count} track(s) from DB")

        if file_paths:
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
                    "The library entries were still removed.\n\n" + "\n".join(failed_paths),
                )

        self.track_deleted.emit()

    # -------------------------------------------------------------------------
    # Tree population
    # -------------------------------------------------------------------------

    def _group_items(self):
        """Return the top-level disc/unassigned group rows in display order."""
        return [self.topLevelItem(i) for i in range(self.topLevelItemCount())]

    def populate_tree(self):
        """Rebuild the tree's group and track rows from self.grouped_tracks."""
        self.clear()
        for key, data in sorted(self.grouped_tracks.items()):
            disc = data["disc"]
            if key == _UNASSIGNED_KEY:
                group_title = "Unassigned Tracks"
            else:
                group_title = f"Disc {key[0]}"
                if disc is not None and disc.disc_title:
                    group_title += f": {disc.disc_title}"

            group_item = QTreeWidgetItem(self, [group_title])
            group_item.setData(0, _TRACK_ID_ROLE, disc)
            flags = group_item.flags() & ~Qt.ItemIsDragEnabled
            if disc is None and key != _UNASSIGNED_KEY:
                # Virtual-only disc number: no Disc row, so a dropped track could not be saved here.
                flags &= ~Qt.ItemIsDropEnabled
                group_item.setToolTip(0, "Virtual tracks only — this disc number has no disc on this album")
            elif disc is None:
                group_item.setToolTip(0, "Tracks with no disc. Drop a track here to remove it from its disc.")
            group_item.setFlags(flags)
            group_item.setExpanded(True)

            # Side rows first (side folded into the number column, e.g. "A1"), then bare rows.
            side_items = sorted((t for t in data["items"] if t["side"]), key=lambda x: (_side_sort_key(x["side"]), x["track_number"] or 0))
            bare_items = sorted((t for t in data["items"] if not t["side"]), key=lambda x: x["track_number"] or 0)
            for t in side_items + bare_items:
                self._create_track_node(group_item, t)

    def _create_track_node(self, parent_item, item_dict):
        """Add one track row under parent_item, styled distinctly if it's virtual."""
        track = item_dict["track"]
        is_v = item_dict["is_virtual"]
        side = item_dict.get("side")
        track_number = item_dict.get("track_number")
        number_label = f"{side}{track_number}" if side and track_number is not None else str(track_number or "?")

        track_name = (track.track_name or "Unknown") + (" 👻" if is_v else "")

        node = QTreeWidgetItem(parent_item, [number_label, track_name, track.duration_formatted])
        node.setData(0, _TRACK_ID_ROLE, int(track.track_id))
        node.setData(0, _SIDE_ROLE, side)
        node.setData(1, _VIRTUAL_ROLE, is_v)
        # Dropping onto a track row would nest it; Qt then drops above/below it instead.
        node.setFlags(node.flags() & ~Qt.ItemIsDropEnabled)

        if is_v:
            # Virtual rows belong to another album's track, so they cannot be dragged.
            node.setFlags(node.flags() & ~Qt.ItemIsDragEnabled)
            muted = self.palette().color(QPalette.PlaceholderText)
            for i in range(3):
                node.setForeground(i, muted)
            node.setToolTip(1, "Virtual track — borrowed from another album")

    # -------------------------------------------------------------------------
    # Writing the layout
    # -------------------------------------------------------------------------

    def _drop_unchanged(self, rows):
        """Return only the rows that differ from the loaded track values."""
        changed = []
        for row in rows:
            track = self._tracks_by_id.get(row["track_id"])
            if track is None or any(getattr(track, field, None) != row[field] for field in _LAYOUT_FIELDS if field in row):
                changed.append(row)
        return changed

    def _write_rows(self, controller, rows, action):
        """Bulk-write track rows; warn about failures. Return the updated count, or None on error."""
        try:
            updated, failed = controller.update.update_entities_bulk_with_fallback("Track", rows)
        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Could not {action}: {e}")
            QMessageBox.warning(self, "Error", f"Could not {action}:\n{e}")
            return None
        if failed:
            for row in failed:
                logger.error(f"Could not {action} for track {row['track_id']}")
            QMessageBox.warning(self, "Some Tracks Not Saved", f"Could not {action} for {len(failed)} of {len(rows)} track(s). See the log for details.")
        return updated

    def assign_absolute_track_numbers(self):
        """Number physical tracks per disc/side run and across the album, in the order shown."""
        rows = []
        absolute_position = 0
        for group in self._group_items():
            numbered = list(_numbered_track_rows(group))
            # Detected single with two bare tracks: write an A1/B1 pair, the common 7" layout.
            split_single = self.is_single and len(numbered) == 2 and not any(side for _, _, side in numbered)
            if split_single:
                numbered = [(row, 1, side) for (row, _, _), side in zip(numbered, ("A", "B"), strict=True)]
            for row, number, side in numbered:
                # Virtual rows take a position but are not written.
                absolute_position += 1
                if row.data(1, _VIRTUAL_ROLE):
                    continue
                update = {"track_id": row.data(0, _TRACK_ID_ROLE), "track_number": number, "absolute_track_number": absolute_position}
                if split_single:
                    update["side"] = side
                rows.append(update)

        if not rows:
            show_status_message(self, "No tracks to renumber.")
            return

        rows = self._drop_unchanged(rows)
        if not rows:
            show_status_message(self, "Track numbers are already in order.")
            return

        controller = self._require_controller("renumber tracks")
        if controller is None:
            return
        updated = self._write_rows(controller, rows, "renumber tracks")
        if updated is None:
            return

        logger.info(f"Assigned track numbers to {updated} track(s)")
        show_status_message(self, f"Renumbered {updated} track(s).")
        self.track_edited.emit()

    def dropEvent(self, event):
        """Let Qt perform the internal move, then persist the new layout."""
        super().dropEvent(event)
        if not event.isAccepted():
            return
        # Deferred: Qt can remove the source rows after dropEvent returns, so walking now may see duplicates.
        QTimer.singleShot(0, self._persist_drop)

    def _persist_drop(self):
        """Write the changed disc/track-number/side values of the tree's layout to the DB."""
        rows = []
        absolute_position = 0
        for group in self._group_items():
            disc = group.data(0, _TRACK_ID_ROLE)
            for row, number, side in _numbered_track_rows(group):
                absolute_position += 1
                if row.data(1, _VIRTUAL_ROLE):
                    continue
                row.setText(0, f"{side}{number}" if side else str(number))
                rows.append(
                    {"track_id": row.data(0, _TRACK_ID_ROLE), "track_number": number, "absolute_track_number": absolute_position, "side": side, "disc_id": disc.disc_id if disc is not None else None}
                )

        rows = self._drop_unchanged(rows)
        if not rows:
            return

        controller = self._require_controller("save the new track order")
        if controller is None:
            return
        if self._write_rows(controller, rows, "save the new track order") is None:
            return
        self.layout_changed.emit()
