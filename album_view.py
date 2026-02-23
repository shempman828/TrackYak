"""UI view for albums in music library"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from album_delete_dialog import DeleteEmptyAlbumsDialog
from album_detail import AlbumDetailView
from album_flowlayout import FlowLayout
from base_album_widget import AlbumWidget
from logger_config import logger


class AlbumView(QWidget):
    """Enhanced album view with responsive grid layout, interactive controls,
    search functionality, and lazy loading.
    """

    def __init__(self, controller):
        """
        Initialize the album view.

        Args:
            controller: Controller to interact with music database/logic.
        """
        super().__init__()
        self.controller = controller
        self.current_size = 200  # Default album art size

        # For lazy loading and searching:
        self.all_albums = []
        self.filtered_albums = []
        self.display_count = 20  # Number of albums to display initially
        self.load_chunk = 20  # Number of albums to add on each lazy load

        # Sorting
        self.sort_order = Qt.AscendingOrder
        self.sort_criteria = "title"  # Default sort by title

        self.init_ui()
        self.load_albums()

    def init_ui(self):
        """Initialize UI components and layout hierarchy."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        # Top control panel: search bar, size slider, and refresh button
        control_layout = QHBoxLayout()

        # Search bar for filtering albums
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search albums...")
        self.search_bar.textChanged.connect(self.filter_albums)
        control_layout.addWidget(self.search_bar)

        # Size control slider
        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setRange(100, 400)
        self.size_slider.setValue(self.current_size)
        self.size_slider.valueChanged.connect(self.resize_art)
        control_layout.addWidget(QLabel("Cover Size:"))
        control_layout.addWidget(self.size_slider)

        # Sorting controls
        control_layout.addWidget(QLabel("Sort by:"))

        self.sort_combo = QComboBox()
        self.sort_combo.addItems(
            [
                "Title (A-Z)",
                "Title (Z-A)",
                "Artist (A-Z)",
                "Artist (Z-A)",
                "Year (Newest First)",
                "Year (Oldest First)",
                "Date Added (Newest First)",
                "Date Added (Oldest First)",
                "Track Count",
                "Most Played",
            ]
        )
        self.sort_combo.currentTextChanged.connect(self.apply_sorting)
        control_layout.addWidget(self.sort_combo)

        # Refresh albums button
        self.refresh_button = QPushButton("Refresh Albums")
        self.refresh_button.clicked.connect(self.load_albums)
        control_layout.addWidget(self.refresh_button)

        # Delete empty albums button
        self.delete_empty_button = QPushButton("Delete Empty Albums")
        self.delete_empty_button.clicked.connect(self.delete_empty_albums)
        control_layout.addWidget(self.delete_empty_button)

        main_layout.addLayout(control_layout)

        # Scrollable album grid with lazy loading
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.grid_layout = FlowLayout(self.scroll_content)
        self.scroll_content.setLayout(self.grid_layout)
        self.scroll_area.setWidget(self.scroll_content)
        main_layout.addWidget(self.scroll_area)

        # Connect the scroll bar to check for lazy loading
        self.scroll_area.verticalScrollBar().valueChanged.connect(
            self.check_scroll_position
        )

        # Enable context menu for the scroll area
        self.scroll_area.setContextMenuPolicy(Qt.CustomContextMenu)
        self.scroll_area.customContextMenuRequested.connect(self.show_context_menu)

    def show_context_menu(self, position):
        """Show context menu with album management options."""
        context_menu = QMenu(self)

        # Add delete empty albums action
        delete_empty_action = context_menu.addAction("Delete Empty Albums")
        delete_empty_action.triggered.connect(self.delete_empty_albums)

        # Show context menu at cursor position
        context_menu.exec_(self.scroll_area.mapToGlobal(position))

    def delete_empty_albums(self):
        """Find and delete all empty albums after user confirmation."""
        try:
            # Get all albums from controller
            all_albums = self.controller.get.get_all_entities("Album")

            # Find empty albums (albums with no tracks)
            empty_albums = []
            for album in all_albums:
                # Check if album has no tracks
                tracks = getattr(album, "tracks", None)
                track_count = getattr(album, "track_count", 0)

                # Consider album empty if tracks is None/empty or track_count is 0
                if not tracks and track_count == 0:
                    empty_albums.append(album)
                elif hasattr(tracks, "__len__") and len(tracks) == 0:
                    empty_albums.append(album)

            if not empty_albums:
                QMessageBox.information(
                    self, "No Empty Albums", "No empty albums found in your library."
                )
                return

            # Show confirmation dialog with list of albums to be deleted
            confirm_dialog = DeleteEmptyAlbumsDialog(empty_albums, self)
            result = confirm_dialog.exec_()

            if result == QDialog.Accepted:
                # Delete the empty albums
                deleted_count = 0
                for album in empty_albums:
                    try:
                        album_id = getattr(album, "album_id", None)
                        if album_id:
                            self.controller.delete.delete_entity("Album", album_id)
                            deleted_count += 1
                    except Exception as e:
                        logger.warning(
                            f"Failed to delete album {album.album_name}: {e}"
                        )

                # Show result and refresh the view
                QMessageBox.information(
                    self,
                    "Deletion Complete",
                    f"Successfully deleted {deleted_count} empty album(s).",
                )
                self.load_albums()  # Refresh the album view

        except Exception as e:
            logger.exception("Failed to delete empty albums")
            QMessageBox.critical(
                self, "Error", f"Failed to delete empty albums: {str(e)}"
            )

    def load_albums(self):
        """Load all albums from the music controller and refresh the grid."""
        try:
            # Load all albums and reset filtering and lazy loading count.
            self.all_albums = self.controller.get.get_all_entities("Album")
            self.apply_sorting()  # Apply current sorting
            self.display_count = self.load_chunk
            self.refresh_album_widgets()

            # Check if we need to load more albums immediately
            QTimer.singleShot(100, self.check_viewport_fill)
        except Exception as e:
            logger.exception("Failed to load albums")
            QMessageBox.critical(self, "Error", f"Failed to load albums: {str(e)}")

    def check_viewport_fill(self):
        """Check if viewport is not filled and load more albums if needed."""
        scroll_bar = self.scroll_area.verticalScrollBar()
        # If no scrollbar is needed (content fits), but we have more albums to show
        if scroll_bar.maximum() == 0 and self.display_count < len(self.filtered_albums):
            self.append_more_album_widgets()
            # Recursively check until viewport is filled or no more albums
            QTimer.singleShot(100, self.check_viewport_fill)

    def apply_sorting(self, sort_text=None):
        """Apply sorting based on the selected criteria."""
        if sort_text:
            # Parse sort criteria from combo box text
            if sort_text.startswith("Title (A-Z)"):
                self.sort_criteria = "title"
                self.sort_order = Qt.AscendingOrder
            elif sort_text.startswith("Title (Z-A)"):
                self.sort_criteria = "title"
                self.sort_order = Qt.DescendingOrder
            elif sort_text.startswith("Artist (A-Z)"):
                self.sort_criteria = "artist"
                self.sort_order = Qt.AscendingOrder
            elif sort_text.startswith("Artist (Z-A)"):
                self.sort_criteria = "artist"
                self.sort_order = Qt.DescendingOrder
            elif sort_text.startswith("Year (Newest First)"):
                self.sort_criteria = "year"
                self.sort_order = Qt.DescendingOrder
            elif sort_text.startswith("Year (Oldest First)"):
                self.sort_criteria = "year"
                self.sort_order = Qt.AscendingOrder
            elif sort_text.startswith("Most Played"):
                self.sort_criteria = "total_plays"
                self.sort_order = Qt.DescendingOrder

        # Sort the albums
        self.all_albums.sort(
            key=self.get_sort_key, reverse=(self.sort_order == Qt.DescendingOrder)
        )

        # Re-apply filtering if needed and refresh display
        current_filter = self.search_bar.text()
        if current_filter:
            self.filter_albums(current_filter)
        else:
            self.filtered_albums = self.all_albums.copy()
            self.refresh_album_widgets()

        # Additional forced update after a short delay to ensure layout completion
        from PySide6.QtCore import QTimer

        QTimer.singleShot(50, self.force_layout_update)

    def force_layout_update(self):
        """Force a complete layout update to fix overlapping issues."""
        self.scroll_content.update()
        self.grid_layout.update()
        self.scroll_area.viewport().update()
        # Force resize event to trigger layout recalculation
        self.scroll_content.resize(
            self.scroll_content.size().width() + 1, self.scroll_content.size().height()
        )
        self.scroll_content.resize(
            self.scroll_content.size().width() - 1, self.scroll_content.size().height()
        )

    def get_sort_key(self, album):
        """Get the appropriate sort key for an album based on current criteria."""
        try:
            if self.sort_criteria == "title":
                return getattr(album, "album_name", "").lower()

            elif self.sort_criteria == "artist":
                # Get primary artist name for sorting
                artists = getattr(album, "album_artists", []) or getattr(
                    album, "artists", []
                )
                if artists:
                    # Use first artist for sorting
                    first_artist = artists[0]
                    if hasattr(first_artist, "artist_name"):
                        return first_artist.artist_name.lower()
                    elif isinstance(first_artist, str):
                        return first_artist.lower()
                    elif isinstance(first_artist, dict):
                        return (
                            first_artist.get("artist_name", "").lower()
                            or first_artist.get("name", "").lower()
                        )
                return ""

            elif self.sort_criteria == "year":
                year = getattr(album, "release_year", 0)
                return year if isinstance(year, int) else 0

            elif self.sort_criteria == "track_count":
                return getattr(album, "track_count", 0)

            elif self.sort_criteria == "play_count":
                return getattr(album, "total_plays", 0) or getattr(
                    album, "total_plays", 0
                )

            elif self.sort_criteria == "rating":
                return getattr(album, "average_rating", 0) or getattr(
                    album, "user_rating", 0
                )

            elif self.sort_criteria == "length":
                return getattr(album, "total_duration", 0) or getattr(
                    album, "duration", 0
                )

            # Default fallback
            return getattr(album, "album_name", "").lower()

        except Exception as e:
            logger.warning(f"Error getting sort key for album: {e}")
            return ""

    def clear_layout(self, layout):
        """Completely clear all widgets from a layout."""
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            else:
                self.clear_layout(item.layout())

    def on_album_clicked(self, album):
        """Open detail view when album is single-clicked."""
        self.show_album_details(album)

    def show_album_details(self, album):
        """Show the album detail view in a dialog."""
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Album Details: {album.album_name}")
        dialog.setMinimumSize(800, 600)

        layout = QVBoxLayout(dialog)
        detail_view = AlbumDetailView(
            album, self.controller, editable=True
        )  # Add editable=True
        layout.addWidget(detail_view)

        # Add close button
        button_box = QDialogButtonBox(QDialogButtonBox.Close)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        dialog.exec_()

    def filter_albums(self, text):
        """
        Filter the album list based on the search text.
        The filtering considers album title, release year, and artist names.
        """
        text = text.lower().strip()
        if not text:
            self.filtered_albums = self.all_albums.copy()
        else:
            filtered = []
            for album in self.all_albums:  # Search through ALL albums
                title = getattr(album, "album_name", "").lower()
                release_year = str(getattr(album, "release_year", "")).lower()

                # Get artist names for this album
                artist_names = []
                artists = getattr(album, "artists", []) or []

                for a in artists:
                    name = None
                    if hasattr(a, "artist_name"):
                        name = getattr(a, "artist_name")
                    else:
                        try:
                            if isinstance(a, dict):
                                name = a.get("artist_name") or a.get("name")
                            elif isinstance(a, str):
                                name = a
                            else:
                                name = str(a)
                        except Exception:
                            name = None

                    if name:
                        artist_names.append(name.lower())

                # Check if search text matches any field
                if (
                    text in title
                    or text in release_year
                    or any(text in artist for artist in artist_names)
                ):
                    filtered.append(album)

            self.filtered_albums = filtered

        # Reset lazy loading and refresh widgets
        self.display_count = self.load_chunk
        self.refresh_album_widgets()

        # Check if we need to load more albums to fill the viewport
        QTimer.singleShot(100, self.check_viewport_fill)

    def resize_art(self, size):
        """Resize all album art widgets when the slider value changes."""
        self.current_size = size
        for i in range(self.grid_layout.count()):
            if widget := self.grid_layout.itemAt(i).widget():
                widget.update_size(size)
        self.grid_layout.update()  # Trigger layout update

    def refresh_album_widgets(self):
        """Clear the current grid and populate with filtered albums up to display_count."""
        # Clear existing widgets from the grid more thoroughly
        self.clear_layout(self.grid_layout)

        # Update displayed_albums to reflect what's actually shown
        self.displayed_albums = self.filtered_albums[: self.display_count]

        # Add album widgets up to the current display count
        for album in self.displayed_albums:
            widget = AlbumWidget(album, self.current_size)
            widget.clicked.connect(self.on_album_clicked)
            self.grid_layout.addWidget(widget)

        # Force a complete layout update
        self.scroll_content.updateGeometry()
        self.grid_layout.update()
        self.scroll_area.viewport().update()

    def append_more_album_widgets(self):
        """
        Append more album widgets if available when user scrolls near the bottom.
        This function adds a chunk of new albums to the grid.
        """
        previous_count = self.display_count
        self.display_count = min(
            self.display_count + self.load_chunk, len(self.filtered_albums)
        )

        # Add only the new albums
        for album in self.filtered_albums[previous_count : self.display_count]:
            widget = AlbumWidget(album, self.current_size)
            widget.clicked.connect(self.on_album_clicked)
            self.grid_layout.addWidget(widget)

        # Update displayed_albums
        self.displayed_albums = self.filtered_albums[: self.display_count]

        # Trigger a layout update
        self.grid_layout.update()

    def check_scroll_position(self, value):
        """
        Check if the user has scrolled near the bottom and trigger lazy loading.
        """
        scroll_bar = self.scroll_area.verticalScrollBar()
        if value >= scroll_bar.maximum() - 50:
            if self.display_count < len(self.filtered_albums):
                self.append_more_album_widgets()
