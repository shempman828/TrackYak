from PySide6.QtCore import QMimeData, Qt
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from logger_config import logger


class TrackSortingDisplay(QTreeWidget):
    """
    Displays tracks with intelligent hierarchy based on available metadata.
    Only shows organizational levels that actually exist in the data.
    """

    def __init__(self, tracks, discs=None, virtual_links=None, parent=None):
        super().__init__(parent)
        self.physical_tracks = tracks
        self.discs = discs or []  # Add discs parameter
        self.virtual_links = virtual_links or []

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
        # Use all_track_items instead of physical_tracks
        # This ensures we're working with the dictionary format consistently
        sides_exist = any(item["side"] is not None for item in self.all_track_items)

        if sides_exist:
            # Group by side only
            sides = {}
            for item in self.all_track_items:
                side = item["side"] or "Unknown"
                if side not in sides:
                    sides[side] = []
                sides[side].append(item)  # Store the item dict, not the track object
            return {"flat": {"sides": sides}}
        else:
            # Completely flat - no discs, no sides
            return {"flat": {"tracks": self.all_track_items}}

    def init_ui(self):
        # 1. Configure Columns and Auto-Resizing
        self.setHeaderLabels(["#", "Track Name", "Duration", "Type"])
        header = self.header()

        # Auto-adjust columns to content, except Name which stretches
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # #
        header.setSectionResizeMode(1, QHeaderView.Stretch)  # Track Name
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)  # Duration
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)  # Type

        # 2. Enable Native Multi-Drag and Drop
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)

        # Change to ExtendedSelection for Shift/Ctrl multi-select
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)

        self.setIndentation(20)
        self.setAlternatingRowColors(True)

        self.setStyleSheet("""
                QTreeWidget::item { padding: 4px; }
                QTreeWidget::item:selected { background: #3498db; color: white; }
            """)

    def _add_disc_section(self, layout, disc_num, disc_data):
        """Add a disc section using the specialized header."""
        disc = disc_data["disc"]

        # Determine the header text
        header_text = f"Disc {disc_num}"
        if disc and disc.disc_title:
            header_text += f": {disc.disc_title}"

        # Find the controller from the parent view hierarchy
        parent_view = self.parent()
        while parent_view and not hasattr(parent_view, "controller"):
            parent_view = parent_view.parent()

        # Create the robust header
        header = DiscHeader(
            header_text,
            disc,
            parent_view.controller if parent_view else None,
            parent_view.refresh_view if parent_view else None,
        )
        layout.addWidget(header)

        # Add divider and tracks
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: #ddd;")
        layout.addWidget(line)

        # Handle child tracks/sides (from our previous fix)
        if disc_data.get("sides"):
            for side_name, side_tracks in sorted(disc_data["sides"].items()):
                self._add_side_section(layout, side_name, side_tracks, indent=True)
        elif disc_data.get("tracks"):
            self._add_track_list(layout, disc_data["tracks"], indent=True)

    def _add_side_section(self, layout, side_name, tracks, indent=False):
        """Add a side section"""
        if side_name and side_name != "Unknown":
            side_header = QLabel(f"Side {side_name}")
            if indent:
                side_header.setIndent(20)
            side_header.setStyleSheet("font-style: italic; color: #666; margin: 5px 0;")
            layout.addWidget(side_header)

        self._add_track_list(layout, tracks, indent=indent)

    def _add_track_list(self, layout, tracks, indent=False):
        """Display list of tracks"""

        logger.debug(
            f"_add_track_list called with {len(tracks)} tracks, indent={indent}"
        )

        for track in sorted(
            tracks,
            key=lambda t: (
                t["track_number"] if isinstance(t, dict) else (t.track_number or 0)
            ),
        ):
            track_widget = self._create_track_widget(track)
            logger.debug(f"Created track widget: {track_widget}")

            if indent:
                logger.debug("Adding with indentation")
                # Apply indent by adding left margin to the widget's layout
                if isinstance(track_widget, QWidget):
                    # Create a container with left margin
                    container = QWidget()
                    container_layout = QHBoxLayout(container)
                    container_layout.setContentsMargins(20, 0, 0, 0)
                    container_layout.addWidget(track_widget)
                    layout.addWidget(container)
            else:
                logger.debug("Adding without indentation")
                layout.addWidget(track_widget)

        logger.debug("Finished _add_track_list")

    def _create_track_widget(self, track_item):
        """Create a single track display widget (supports virtual tracks)"""
        widget = QWidget()
        widget.setAttribute(Qt.WA_OpaquePaintEvent, True)
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(5, 2, 5, 2)

        # Handle both dictionary items and track objects
        if isinstance(track_item, dict):
            track = track_item["track"]
            is_virtual = track_item.get("is_virtual", False)
            track_number = track_item["track_number"]
        else:
            track = track_item
            is_virtual = False
            track_number = track.track_number

        # Track number
        track_num = track_number or "?"
        num_label = QLabel(str(track_num))
        num_label.setFixedWidth(30)
        num_label.setAlignment(Qt.AlignRight)

        if is_virtual:
            num_label.setStyleSheet("color: #888; font-style: italic;")

        layout.addWidget(num_label)

        # Track name
        name = track.track_name or "Unknown Track"
        if is_virtual and track.album:
            name = f"{name} [from: {track.album.album_name}]"

        name_label = QLabel(name)
        name_label.setWordWrap(True)

        if is_virtual:
            name_label.setStyleSheet("color: #666; font-style: italic;")

        layout.addWidget(name_label, 1)

        if track.duration:
            duration = self._format_duration(track.duration)
            duration_label = QLabel(duration)
            duration_label.setStyleSheet("color: #666;")
            layout.addWidget(duration_label)

        if is_virtual:
            virtual_label = QLabel("📎")
            virtual_label.setToolTip("Virtual track (borrowed from another album)")
            virtual_label.setStyleSheet("color: #888; font-size: 12px;")
            layout.addWidget(virtual_label)

        # Enable dragging
        def mousePressEvent(event):
            if event.button() == Qt.LeftButton:
                widget.drag_start_position = event.pos()

        def mouseMoveEvent(event):
            if not hasattr(widget, "drag_start_position"):
                return
            if (
                event.pos() - widget.drag_start_position
            ).manhattanLength() < QApplication.startDragDistance():
                return

            drag = QDrag(widget)
            mime_data = QMimeData()
            mime_data.setText(str(track.track_id))
            drag.setMimeData(mime_data)
            drag.exec_(Qt.MoveAction)

        widget.mousePressEvent = mousePressEvent
        widget.mouseMoveEvent = mouseMoveEvent

        return widget

    def _format_duration(self, seconds):
        if not seconds:
            return "0:00"
        return f"{int(seconds // 60)}:{int(seconds % 60):02d}"

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
        node.setData(0, Qt.UserRole, track.track_id)
        node.setData(1, Qt.UserRole, is_v)

        if is_v:
            for i in range(4):
                node.setForeground(i, Qt.gray)

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
        parent_view = self.parent()
        while parent_view and not hasattr(parent_view, "controller"):
            parent_view = parent_view.parent()

        if not parent_view:
            return

        # 4. Batch Update Database
        updated_any = False
        for item in selected_items:
            is_virtual = item.data(1, Qt.UserRole)
            if is_virtual is False:  # Only move physical tracks
                track_id = item.data(0, Qt.UserRole)
                success = parent_view.controller.update.update_entity(
                    "Track", track_id, disc_id=target_disc_id
                )
                if success:
                    updated_any = True

        if updated_any:
            parent_view.refresh_view()
            event.acceptProposedAction()


class DiscHeader(QLabel):
    """A specialized header that handles track drops safely."""

    def __init__(self, text, disc, controller, refresh_callback, parent=None):
        super().__init__(text, parent)
        self.disc = disc
        self.controller = controller
        self.refresh_callback = refresh_callback
        self.setAcceptDrops(True)
        self.setStyleSheet("""
            QLabel {
                font-weight: bold; font-size: 14px; margin-top: 10px;
                padding: 5px; border-radius: 4px; background: #f8f8f8;
            }
            QLabel[hover="true"] { background: #e1f5fe; border: 1px dashed #0288d1; }
        """)

    def dragEnterEvent(self, event):
        if event.mimeData().hasText():
            self.setProperty("hover", "true")
            self.style().unpolish(self)
            self.style().polish(self)
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self.setProperty("hover", "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def dropEvent(self, event):
        self.setProperty("hover", "false")
        self.style().unpolish(self)
        self.style().polish(self)

        try:
            track_id = int(event.mimeData().text())
            target_disc_id = self.disc.disc_id if self.disc else None

            # Update database
            success = self.controller.update.update_entity(
                "Track", track_id, disc_id=target_disc_id
            )

            if success:
                self.refresh_callback()
            event.acceptProposedAction()
        except Exception as e:
            logger.error(f"Drop failed: {e}")
