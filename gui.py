"""
Main GUI application for the Music Library manager using PySide6.
"""

import traceback
from pathlib import Path

from PySide6.QtCore import QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QMainWindow,
    QStackedWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from album_view import AlbumView
from artist_view import ArtistView
from asset_paths import icon, theme
from award_view import AwardView
from config_setup import app_config
from dates_view import TimelineView
from file_manager_dialog import FileManager
from genre_view import GenreView
from import_dialog import ImportDialog
from influences_view import InfluencesView
from logger_config import logger
from menu_bar import MenuBar
from moods_view import MoodView
from navigation_dock import NavigationDock
from nowplaying_view import NowPlayingView
from place_view import PlaceView
from player_dock import PlayerUI
from playlist_view import PlaylistView
from publisher_view import PublisherView
from queue_dock import QueueDockWidget
from role_view import RoleView
from status_utility import StatusManager
from status_widget import StatusBarWidget
from sync_view import SyncView
from track_view import TrackView


class GUI(QMainWindow, MenuBar):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller

        self.mediaplayer = self.controller.mediaplayer
        self.file_manager = FileManager(controller)

        # Initialize NowPlayingView AFTER mediaplayer is set
        self.now_playing = NowPlayingView(controller)
        self.now_playing.setVisible(True)

        self.setObjectName("MainWindow")
        self.view_registry = {}
        self.importer = ImportDialog(controller)
        self.import_worker = None

        # Connect signal AFTER NowPlayingView is created
        self.mediaplayer.track_changed.connect(self.update_now_playing_view)
        self._init_ui()
        self._init_status_system()

    @property
    def nav_tree(self):
        """Provide access to navigation tree from navigation dock"""
        if hasattr(self, "navigation_dock"):
            return self.navigation_dock.nav_tree
        return None

    def _init_ui(self):
        """Menu creation, views, player, and navigation."""
        self.setWindowTitle("TrackYak")
        self.setObjectName("MainWindow")
        self._setup_main_window()

        # Create menu bar
        self._init_menu_bar()
        try:
            # Order matters: left (nav) → bottom (player) → right (queue)
            self.navigation_dock = NavigationDock(self)  # Create navigation dock
            self._create_player()

            # Create queue after Qt lays out the others
            QTimer.singleShot(0, self._create_queue_dock)
            QTimer.singleShot(120, self.restore_layout)

            # Populate navigation AFTER navigation dock is created
            self._populate_navigation()

        except Exception as e:
            logger.error(f"Error creating player: {e}")
            self.statusBar().showMessage("Failed to initialize player", 5000)

        # Create main content area last
        self._create_views()

        # Now that navigation_dock exists, add navigation actions to menu
        self._add_navigation_menu_actions()

    def _init_status_system(self):
        """Initialize the status manager and status bar"""
        # Remove Qt's default status bar completely
        self.setStatusBar(None)

        # Create our custom status bar widget
        self.status_bar_widget = StatusBarWidget(self)

        # Get the current central widget and layout
        old_central = self.centralWidget()

        if old_central:
            # Create a new main container
            main_container = QWidget()
            main_layout = QVBoxLayout(main_container)
            main_layout.setContentsMargins(0, 0, 0, 0)
            main_layout.setSpacing(0)

            # Add the old central widget
            main_layout.addWidget(old_central, 1)  # stretch factor 1 - takes most space

            # Add status bar at the bottom
            main_layout.addWidget(
                self.status_bar_widget, 0
            )  # stretch factor 0 - minimal space

            # Set the new central widget
            self.setCentralWidget(main_container)
        else:
            # Fallback if no central widget exists yet
            self.status_bar_widget.setParent(self)

        # Connect status manager signals
        StatusManager.show_status.connect(self.status_bar_widget.show_message)
        StatusManager.hide_status.connect(self.status_bar_widget.hide)

        # Start hidden
        self.status_bar_widget.hide()

    def restore_layout(self):
        """Restore saved QMainWindow state including floating docks."""
        window_state = app_config.get_window_state()
        if window_state and not window_state.isEmpty():
            try:
                self.restoreState(window_state)

                logger.debug("Window state restored successfully")
                QTimer.singleShot(
                    40, self.navigation_dock.ensure_proper_navigation_size
                )
            except Exception as e:
                logger.warning(f"Failed to restore window state: {e}")
                QTimer.singleShot(40, self.navigation_dock.size_navigation_to_content)
        else:
            QTimer.singleShot(40, self.navigation_dock.size_navigation_to_content)

    def update_now_playing_view(self, file_path: Path):
        try:
            logger.info(f"Updating now playing view for: {file_path}")

            # Get track from database - ensure we're using the correct path format
            track = self.controller.get.get_entity_object(
                "Track", track_file_path=str(file_path)
            )

            if track:
                logger.info(
                    f"Track found: {getattr(track, 'title', getattr(track, 'track_name', 'Unknown'))}"
                )

                # Update the NowPlayingView UI
                self.now_playing.updateUI(track)
            else:
                logger.warning(f"Track not found in database: {file_path}")
                # Try to find by filename as fallback
                filename = file_path.name
                tracks = self.controller.get.get_all_entities("Track")
                for t in tracks:
                    if hasattr(t, "track_file_path") and filename in str(
                        t.track_file_path
                    ):
                        self.now_playing.updateUI(t)
                        self.now_playing.check_visibility()
                        logger.info(f"Found track by filename: {filename}")
                        return
                self.now_playing.clearUI()

        except Exception as e:
            logger.error(f"Error updating NowPlayingView: {e}")

            logger.error(traceback.format_exc())

    def _create_queue_dock(self):
        """Create the queue dock widget (right side)."""
        logger.debug("Creating queue dock")

        # Step 1: Create the inner queue UI
        self.queue_widget = QueueDockWidget(self.controller, self)

        # Step 2: Wrap it in a QDockWidget
        self.queue_dock = QDockWidget("Queue", self)
        self.queue_dock.setObjectName("QueueDock")
        self.queue_dock.setWidget(self.queue_widget)

        # Step 3: Configure dock behavior
        self.queue_dock.setFeatures(
            QDockWidget.DockWidgetMovable
            | QDockWidget.DockWidgetFloatable
            | QDockWidget.DockWidgetClosable
        )
        self.queue_dock.setAllowedAreas(Qt.RightDockWidgetArea | Qt.LeftDockWidgetArea)
        self.queue_dock.setMinimumWidth(300)
        self.queue_dock.setMaximumWidth(500)

        # Step 4: Add dock to main window
        self.addDockWidget(Qt.RightDockWidgetArea, self.queue_dock)

        # Step 5: Connect signals (from the inner queue widget)
        self.queue_widget.track_double_clicked.connect(
            self._on_queue_track_double_clicked
        )

        # Step 6: Start hidden
        self.queue_dock.hide()

        logger.info("Queue dock created and hidden by default")

    def _on_queue_track_double_clicked(self, file_path):
        """Handle double-click on track in queue."""
        if self.mediaplayer.load_track(file_path):
            self.mediaplayer.play()

    def _setup_main_window(self):
        """Set up the main window with size, position, and theme."""
        # Use new config methods
        size = app_config.get_window_size()
        pos = app_config.get_window_position()

        # Apply window geometry BEFORE docks are created
        # so restoreState (later) has a stable main window size to base splitter geometry on.
        self.resize(size)
        self.move(pos)

        # Add margin to prevent dock from being cut off
        self.setContentsMargins(10, 10, 10, 10)

        # Check if window should be maximized
        if app_config.is_window_maximized():
            # do not showMaximized immediately; show when UI ready
            self.showMaximized()

        # Ensure window is within screen bounds (especially important for Wayland)
        QTimer.singleShot(100, self.ensure_window_in_screen)

        # Apply theme and show ready message
        self._load_theme()

    def _load_theme(self):
        """Load the QSS theme file using new config system."""
        try:
            # Use config to get theme file
            theme_file = app_config.get_theme_file()
            theme_path = app_config.get_theme_path(theme_file)

            if theme_path.exists():
                with open(theme_path, "r", encoding="utf-8") as f:
                    stylesheet = f.read()
                    # Apply to entire application
                    QApplication.instance().setStyleSheet(stylesheet)
                    logger.debug(f"Theme applied: {theme_file}")
            else:
                # Fallback to default theme
                default_theme = theme("dark_mode.qss")
                if Path(default_theme).exists():
                    with open(default_theme, "r", encoding="utf-8") as f:
                        stylesheet = f.read()
                        QApplication.instance().setStyleSheet(stylesheet)
                    logger.debug("Fallback theme applied")
                else:
                    logger.warning("No theme file found")
                    QApplication.instance().setStyleSheet("")

        except Exception as e:
            logger.error(f"Error loading theme: {e}")
            QApplication.instance().setStyleSheet("")

    def _add_navigation_menu_actions(self):
        """Add navigation-specific actions to menu after navigation_dock is created."""
        # Add navigation toggle to View menu - call method on navigation_dock
        toggle_nav_action = QAction("Toggle Navigation", self)
        toggle_nav_action.setShortcut(QKeySequence("Ctrl+Shift+N"))
        toggle_nav_action.triggered.connect(self.navigation_dock.toggle_navigation)
        toggle_nav_action.setIcon(QIcon(icon("toggle_navigation.svg")))
        self.view_menu.addAction(toggle_nav_action)

    def _create_player(self):
        """Create the bottom player dock with mini-player capabilities."""
        logger.debug("Creating player dock")

        # Create player UI
        self.player_ui = PlayerUI(self.controller, self)

        # Create dock
        self.player_dock = QDockWidget("Player", self)
        self.player_dock.setObjectName("PlayerDock")
        self.player_dock.setWidget(self.player_ui)

        # Enable ALL features including floating
        self.player_dock.setFeatures(
            QDockWidget.DockWidgetMovable
            | QDockWidget.DockWidgetFloatable
            | QDockWidget.DockWidgetClosable
        )

        # Set a small title bar
        self.player_dock.setTitleBarWidget(QWidget())

        # Add to main window
        self.addDockWidget(Qt.BottomDockWidgetArea, self.player_dock)
        self.player_dock.show()
        self.player_dock.raise_()

        logger.info("Player dock created")

    def adjust_dock_size(self):
        """Adjust dock size to fit content safely."""
        try:
            ideal_size = self.sizeHint()
            if ideal_size.isValid():
                height = max(60, ideal_size.height() + 20)  # Ensure minimum height
                width = max(400, ideal_size.width())  # Ensure minimum width

                dock = self.parent().parent() if self.parent() else None
                if isinstance(dock, QDockWidget):
                    dock.setMinimumHeight(height)
                    dock.setMaximumHeight(height + 10)  # Small buffer
                    dock.setMinimumWidth(width)
                    self.setMinimumHeight(max(50, ideal_size.height()))

                    logger.debug(f"Adjusted dock size: {width}x{height}")
            else:
                # Fallback sizes when sizeHint is invalid
                dock = self.parent().parent() if self.parent() else None
                if isinstance(dock, QDockWidget):
                    dock.setMinimumHeight(60)
                    dock.setMinimumWidth(400)

        except Exception as e:
            logger.error(f"Error adjusting player dock size: {e}")
            # Set safe fallback sizes
            try:
                dock = self.parent().parent() if self.parent() else None
                if isinstance(dock, QDockWidget):
                    dock.setMinimumHeight(60)
                    dock.setMinimumWidth(400)
            except:  # noqa: E722
                pass

    def ensure_window_in_screen(self):
        """Ensure the window is within screen bounds, especially for Wayland."""
        try:
            screen = QApplication.primaryScreen()
            screen_geometry = screen.availableGeometry()
            window_geometry = self.geometry()

            # Calculate safe area (leave margin for docks and taskbars)
            safe_margin = 100
            safe_rect = screen_geometry.adjusted(
                safe_margin, safe_margin, -safe_margin, -safe_margin
            )

            # If window is outside safe area, move it
            if not safe_rect.contains(window_geometry):
                new_x = max(
                    safe_rect.left(),
                    min(
                        window_geometry.x(), safe_rect.right() - window_geometry.width()
                    ),
                )
                new_y = max(
                    safe_rect.top(),
                    min(
                        window_geometry.y(),
                        safe_rect.bottom() - window_geometry.height(),
                    ),
                )
                self.move(new_x, new_y)
                logger.debug("Adjusted window position to stay within safe screen area")

        except Exception as e:
            logger.error(f"Error ensuring window in screen: {e}")

    def _reset_ui_layout(self):
        """Reset window and dock positions using config, restoring all main docks."""

        # --- Step 1: Restore main window size and position ---
        try:
            default_size = app_config.get_window_size()  # e.g., (width, height)
            default_pos = app_config.get_window_position()  # e.g., (x, y)

            self.resize(
                QSize(*default_size)
                if isinstance(default_size, tuple)
                else default_size
            )
            self.move(
                QPoint(*default_pos) if isinstance(default_pos, tuple) else default_pos
            )
        except Exception:
            logger.exception("Failed to apply window size/position, using defaults")
            self.resize(1280, 720)
            self.move(100, 100)

        # --- Step 2: Hide all existing docks first ---
        for dock in self.findChildren(QDockWidget):
            dock.setFloating(False)
            dock.hide()

        # --- Step 3: Restore specific docks with proper state ---
        dock_config = [
            ("navigation_dock", Qt.LeftDockWidgetArea, "expand_navigation"),
            ("player_dock", Qt.BottomDockWidgetArea, None),
            ("queue_dock", Qt.RightDockWidgetArea, None),
        ]

        for attr_name, area, expand_method in dock_config:
            dock = getattr(self, attr_name, None)
            if dock:
                self.addDockWidget(area, dock)
                if expand_method and hasattr(dock, expand_method):
                    getattr(dock, expand_method)()
                dock.show()  # Restore visibility
                # Optional: respect stored visibility in config
                if attr_name == "queue_dock" and not getattr(
                    app_config, "queue_visible", True
                ):
                    dock.hide()

        # --- Step 4: Restore player controls if needed ---
        if hasattr(self, "_restore_player"):
            self._restore_player()

    def _restore_player(self):
        """Ensure the player dock is created, docked at the bottom, and visible."""

        # Step 1: Ensure player UI exists
        if not getattr(self, "player_ui", None):
            self._create_player()

        # Step 2: Find the dock containing player_ui
        player_dock = next(
            (
                dock
                for dock in self.findChildren(QDockWidget)
                if dock.widget() == self.player_ui
            ),
            None,
        )

        if player_dock:
            player_dock.setFloating(False)
            self.addDockWidget(Qt.BottomDockWidgetArea, player_dock)
            player_dock.show()
            # Optional: restore size if stored in config
            if hasattr(app_config, "player_dock_size"):
                player_dock.resize(QSize(*app_config.player_dock_size))

    def _create_views(self):
        # main tool
        self.stacked_widget = QStackedWidget()
        self.view_registry = {}  # List of (name, index)

        view_components = [
            ("Tracks", TrackView(self.controller, self.mediaplayer)),
            ("Now Playing", self.now_playing),
            ("Albums", AlbumView(self.controller)),
            ("Artists", ArtistView(self.controller)),
            ("Playlists", PlaylistView(self.controller)),
            ("Genres", GenreView(self.controller)),
            ("Places", PlaceView(self.controller)),
            ("Publishers", PublisherView(self.controller)),
            ("Roles", RoleView(self.controller)),
            ("Moods | Folksonomy", MoodView(self.controller)),
            ("Influences", InfluencesView(self.controller)),
            ("Awards", AwardView(self.controller)),
            ("Sync", SyncView(self.controller)),
            ("Timeline", TimelineView(self.controller)),
        ]

        for view_name, widget in view_components:
            # Store both index and reference if needed later
            index = self.stacked_widget.addWidget(widget)
            self.view_registry[view_name] = index

        self.setCentralWidget(self.stacked_widget)

        if hasattr(self, "nav_tree"):
            self._populate_navigation()

    def _add_view(self, name, widget):
        widget.view_name = name  # Set view name as attribute
        self.view_registry.append((name, widget))
        self.stacked_widget.addWidget(widget)

    def _populate_navigation(self):
        """Populate navigation tree from registry."""
        if self.nav_tree:
            self.nav_tree.clear()
            for view_name in self.view_registry:
                QTreeWidgetItem(self.nav_tree, [view_name])

    def _refresh_all_views(self):
        """Reload data for all registered views"""
        try:
            logger.info("Refreshing all views...")

            # Get fresh data from DB
            all_tracks = self.controller.get.get_all_entities("Track")
            all_albums = self.controller.get.get_all_entities("Album")
            all_artists = self.controller.get.get_all_entities("Artist")
            all_genres = self.controller.get.get_all_entities("Genre")
            all_playlists = self.controller.get.get_all_entities("Playlist")
            # Add other entities as needed

            # Update each view
            for view_name, index in self.view_registry.items():
                widget = self.stacked_widget.widget(index)
                if hasattr(widget, "load_data"):
                    if isinstance(widget, TrackView):
                        widget.load_data(all_tracks)
                    elif isinstance(widget, AlbumView):
                        widget.load_data(all_albums)
                    elif isinstance(widget, ArtistView):
                        widget.load_data(all_artists)
                    elif isinstance(widget, GenreView):
                        widget.load_data(all_genres)
                    elif isinstance(widget, PlaylistView):
                        widget.load_data(all_playlists)
                    # Add other view types as needed
                else:
                    # For views that don't have load_data, try refresh method
                    if hasattr(widget, "refresh"):
                        widget.refresh()
                    elif hasattr(widget, "update_data"):
                        widget.update_data()

            # Refresh queue if it exists
            if hasattr(self, "queue_widget") and self.queue_widget:
                if hasattr(self.queue_widget, "refresh_queue"):
                    self.queue_widget.refresh_queue()

            logger.info("All views refreshed successfully")

        except Exception as e:
            logger.error(f"Refresh error: {str(e)}")
            # Don't show message box here to avoid interrupting user workflow

    def _switch_view(self, item):
        view_name = item.text(0)
        if view_name in self.view_registry:
            index = self.view_registry[view_name]
            self.stacked_widget.setCurrentIndex(index)

            # Simple refresh attempt - try common refresh methods
            current_widget = self.stacked_widget.currentWidget()
            for method_name in [
                "load_artists",
                "load_tracks",
                "load_tracks_on_startup",
                "load_albums",
                "load_genres",
                "load_places",
                "refresh_views",  # places refresh
                "load_moods",
                "load_awards",
                "load_groups",
                "load_publishers",
                "load_roles",
                "load_influences",
                "load_playlists",
            ]:
                if hasattr(current_widget, method_name):
                    getattr(current_widget, method_name)()
                    break

    def closeEvent(self, event):
        # Save state
        app_config.set_window_size(self.size())
        app_config.set_window_position(self.pos())
        app_config.set_window_maximized(self.isMaximized())

        # Save dock state
        state_bytes = self.saveState()
        app_config.set_window_state(state_bytes)

        # Save config
        app_config.save()

        self.controller.close_session()
        super().closeEvent(event)
