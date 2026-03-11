from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHeaderView,
    QMenu,
    QMessageBox,
    QTreeWidget,
    QTreeWidgetItem,
)

from src.logger_config import logger


class TrackSortingDisplay(QTreeWidget):
    """
    Displays tracks with intelligent hierarchy based on available metadata.
    Only shows organizational levels that actually exist in the data.
    """

    # Emitted when a track edit dialog is saved and closed.
    # The parent DiscManagementView connects to this to trigger a full reload.
    track_edited = Signal()

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
        items = []
        for track in self.physical_tracks:
            items.append(
                {
                    "track": track,
                    "is_virtual": False,
                    "disc_id": track.disc_id,
                    "disc_number": track.disc.disc_number if track.disc else None,
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
                        "disc_number": link.virtual_disc_number,
                        "track_number": link.virtual_track_number,
                        "side": link.virtual_side,
                    }
                )
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

    def _group_by_side_or_flat(self):
        """
        Handle tracks without disc assignments
        """
        sides_exist = any(item["side"] is not None for item in self.all_track_items)

        if sides_exist:
            sides = {}
            for item in self.all_track_items:
                side = item["side"] or "Unknown"
                if side not in sides:
                    sides[side] = []
                sides[side].append(item)
            return {"flat": {"sides": sides}}
        else:
            return {"flat": {"tracks": self.all_track_items}}

    def init_ui(self):
        # 1. Configure Columns and Auto-Resizing
        self.setHeaderLabels(["#", "Track Name", "Duration", "Type"])
        header = self.header()

        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # #
        header.setSectionResizeMode(1, QHeaderView.Stretch)  # Track Name
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)  # Duration
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)  # Type

        # 2. Enable Native Multi-Drag and Drop
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)

        self.setSelectionMode(QAbstractItemView.ExtendedSelection)

        self.setIndentation(20)
        self.setAlternatingRowColors(True)

        self.setStyleSheet("""
                QTreeWidget::item { padding: 4px; }
                QTreeWidget::item:selected { background: #3498db; color: white; }
            """)

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

        is_virtual = item.data(1, Qt.UserRole)

        menu = QMenu(self)

        # Edit action — virtual tracks are read-only borrowed tracks, so we
        # only offer editing for physical tracks that live in this album.
        if not is_virtual:
            edit_action = QAction("✏️  Edit Track", self)
            edit_action.triggered.connect(lambda: self._edit_track(track_id))
            menu.addAction(edit_action)
        else:
            # Still show the option but disabled, so the user knows why
            edit_action = QAction("✏️  Edit Track  (virtual — open source album)", self)
            edit_action.setEnabled(False)
            menu.addAction(edit_action)

        menu.exec_(self.viewport().mapToGlobal(position))

    def _edit_track(self, track_id):
        """Open TrackEditDialog for the given track_id and reload on save."""
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
            track = controller.get.get_entity_object("Track", track_id=track_id)
            if not track:
                QMessageBox.warning(
                    self, "Not Found", f"Track ID {track_id} not found."
                )
                return

            # Import here to avoid circular imports at module level
            from src.track_edit import TrackEditDialog

            dialog = TrackEditDialog(track, controller, self)
            if dialog.exec_() == QDialog.Accepted:
                # Tell the parent view to reload so changes are visible
                self.track_edited.emit()

        except Exception as e:
            logger.error(f"Error opening track editor from disc view: {e}")
            QMessageBox.warning(self, "Error", f"Could not open track editor:\n{e}")

    # -------------------------------------------------------------------------
    # Tree population
    # -------------------------------------------------------------------------

    def populate_tree(self):
        self.clear()
        for disc_num, data in sorted(self.grouped_tracks.items()):
            disc_title = f"Disc {disc_num}"
            if data["disc"] and data["disc"].disc_title:
                disc_title += f": {data['disc'].disc_title}"

            disc_item = QTreeWidgetItem(self, [disc_title])
            disc_item.setData(0, Qt.UserRole, data["disc"])
            disc_item.setExpanded(True)

            for side_name, side_tracks in sorted(data["sides"].items()):
                side_item = QTreeWidgetItem(disc_item, [f"Side {side_name}"])
                side_item.setExpanded(True)
                for t in sorted(side_tracks, key=lambda x: x["track_number"] or 0):
                    self._create_track_node(side_item, t)

            for t in sorted(data["tracks"], key=lambda x: x["track_number"] or 0):
                self._create_track_node(disc_item, t)

    def _create_track_node(self, parent_item, item_dict):
        track = item_dict["track"]
        is_v = item_dict["is_virtual"]

        duration = self._format_duration(track.duration)
        node = QTreeWidgetItem(
            parent_item,
            [
                str(item_dict["track_number"] or "?"),
                track.track_name or "Unknown",
                duration,
                "Virtual" if is_v else "Physical",
            ],
        )
        # Store track_id as an int so show_context_menu can identify track rows
        node.setData(0, Qt.UserRole, int(track.track_id))
        node.setData(1, Qt.UserRole, is_v)

        if is_v:
            for i in range(4):
                node.setForeground(i, Qt.gray)

    def _format_duration(self, seconds):
        if not seconds:
            return "0:00"
        return f"{int(seconds // 60)}:{int(seconds % 60):02d}"

    # -------------------------------------------------------------------------
    # Drag-and-drop
    # -------------------------------------------------------------------------

    def dropEvent(self, event):
        """Handles multi-selection drop logic."""
        target_item = self.itemAt(event.pos())
        if not target_item:
            return

        # 1. Identify Target Disc
        curr = target_item
        disc_obj = None
        while curr:
            disc_obj = curr.data(0, Qt.UserRole)
            if hasattr(disc_obj, "disc_id"):
                break
            curr = curr.parent()

        target_disc_id = disc_obj.disc_id if disc_obj else None

        # 2. Identify Selected Tracks
        selected_items = self.selectedItems()
        if not selected_items:
            return

        # 3. Locate Controller
        controller = self.controller
        if controller is None:
            parent_view = self.parent()
            while parent_view and not hasattr(parent_view, "controller"):
                parent_view = parent_view.parent()
            if parent_view:
                controller = parent_view.controller

        if not controller:
            return

        # 4. Batch Update Database
        updated_any = False
        for item in selected_items:
            is_virtual = item.data(1, Qt.UserRole)
            if is_virtual is False:  # Only move physical tracks
                track_id = item.data(0, Qt.UserRole)
                success = controller.update.update_entity(
                    "Track", track_id, disc_id=target_disc_id
                )
                if success:
                    updated_any = True

        if updated_any:
            # Walk up to find the parent view that has refresh_view
            parent_view = self.parent()
            while parent_view and not hasattr(parent_view, "refresh_view"):
                parent_view = parent_view.parent()
            if parent_view:
                parent_view.refresh_view()
            event.acceptProposedAction()
