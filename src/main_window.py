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

# ── Views are imported here for type-checking but NOT instantiated until
#    the user navigates to them.  Adding a new view only requires adding
#    one entry to _VIEW_FACTORIES inside _create_views(). ─────────────────
from src.album_view import AlbumView
from src.artist_view import ArtistView
from src.asset_paths import icon, theme
from src.award_view import AwardView
from src.config_setup import app_config
from src.dates_view import TimelineView
from src.file_manager_dialog import FileManager
from src.genre_view import GenreView
from src.import_dialog import ImportDialog
from src.influences_view import InfluencesView
from src.logger_config import logger
from src.menu_bar import MenuBar
from src.moods_view import MoodView
from src.navigation_dock import NavigationDock
from src.nowplaying_view import NowPlayingView
from src.place_view import PlaceView
from src.player_dock import PlayerUI
from src.playlist_view import PlaylistView
from src.publisher_view import PublisherView
from src.queue_dock import QueueDockWidget
from src.role_view import RoleView
from src.status_utility import StatusManager
from src.status_widget import StatusBarWidget
from src.sync_view import SyncView
from src.track_view import TrackView


class GUI(QMainWindow, MenuBar):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller

        self.mediaplayer = self.controller.mediaplayer
        self.file_manager = FileManager(controller)

        # NowPlayingView is cheap to construct and is needed immediately,
        # so we keep it as an eager view.
        self.now_playing = NowPlayingView(controller)

        self.setObjectName("MainWindow")
        self.view_registry = {}
        self.importer = ImportDialog(controller)
        self.import_worker = None

        self.mediaplayer.track_changed.connect(self.update_now_playing_view)
        self._init_ui()
        self._init_status_system()

    # =========================================================================
    #  Properties
    # =========================================================================

    @property
    def nav_tree(self):
        if hasattr(self, "navigation_dock"):
            return self.navigation_dock.nav_tree
        return None

    # =========================================================================
    #  UI initialisation
    # =========================================================================

    def _init_ui(self):
        """Menu creation, views, player, and navigation."""
        self.setWindowTitle("TrackYak")
        self.setObjectName("MainWindow")
        self._setup_main_window()
        self._init_menu_bar()

        try:
            self.navigation_dock = NavigationDock(self)
            self._create_player()
            QTimer.singleShot(0, self._create_queue_dock)
            QTimer.singleShot(120, self.restore_layout)
            self._populate_navigation()
        except Exception as e:
            logger.error(f"Error creating player: {e}")
            self.statusBar().showMessage("Failed to initialize player", 5000)

        self._create_views()
        self._add_navigation_menu_actions()

    def _init_status_system(self):
        """Initialize the status manager and status bar."""
        self.setStatusBar(None)
        self.status_bar_widget = StatusBarWidget(self)

        old_central = self.centralWidget()
        if old_central:
            main_container = QWidget()
            main_layout = QVBoxLayout(main_container)
            main_layout.setContentsMargins(0, 0, 0, 0)
            main_layout.setSpacing(0)
            main_layout.addWidget(old_central, 1)
            main_layout.addWidget(self.status_bar_widget, 0)
            self.setCentralWidget(main_container)
        else:
            self.status_bar_widget.setParent(self)

        StatusManager.show_status.connect(self.status_bar_widget.show_message)
        StatusManager.hide_status.connect(self.status_bar_widget.hide)
        self.status_bar_widget.hide()

    # =========================================================================
    #  View creation — LAZY
    # =========================================================================

    def _create_views(self):
        """
        Set up the stacked widget and register view factories.

        Nothing is instantiated here except TrackView (the default landing
        view) and NowPlayingView (already created in __init__).  Every other
        view is represented by a zero-argument lambda that will be called the
        first time the user navigates to it.

        To add a new view, add one line to _view_factories below.
        """
        self.stacked_widget = QStackedWidget()

        # ── Eager views (created immediately) ────────────────────────────────
        # TrackView is the default view shown on startup, so we build it now.
        self._track_view_instance = TrackView(self.controller, self.mediaplayer)

        # ── Factory registry ──────────────────────────────────────────────────
        # Each value is a callable that returns a fresh widget.
        # The callable is invoked at most once per session.
        self._view_factories = {
            "Tracks": lambda: self._track_view_instance,
            "Now Playing": lambda: self.now_playing,
            "Albums": lambda: AlbumView(self.controller),
            "Artists": lambda: ArtistView(self.controller),
            "Playlists": lambda: PlaylistView(self.controller),
            "Genres": lambda: GenreView(self.controller),
            "Places": lambda: PlaceView(self.controller),
            "Publishers": lambda: PublisherView(self.controller),
            "Roles": lambda: RoleView(self.controller),
            "Moods": lambda: MoodView(self.controller),
            "Influences": lambda: InfluencesView(self.controller),
            "Awards": lambda: AwardView(self.controller),
            "Sync": lambda: SyncView(self.controller),
            "Timeline": lambda: TimelineView(self.controller),
        }

        # ── Cached instances (populated on first navigation) ──────────────────
        # Pre-populate the two eager views so they're ready immediately.
        self._view_cache = {}

        # ── view_registry maps name → stacked-widget index ───────────────────
        # We add placeholder slots for every view so the stacked widget has
        # the right count and the nav tree can be populated immediately.
        # The placeholder is swapped out for the real widget on first visit.
        self.view_registry = {}
        for view_name in self._view_factories:
            placeholder = QWidget()  # tiny, empty, costs nothing
            index = self.stacked_widget.addWidget(placeholder)
            self.view_registry[view_name] = index

        self.setCentralWidget(self.stacked_widget)

        # Navigate to the default view (Tracks) immediately so the user
        # sees something useful on startup.
        self._ensure_view_built("Tracks")
        self.stacked_widget.setCurrentIndex(self.view_registry["Tracks"])

        if hasattr(self, "nav_tree"):
            self._populate_navigation()

    def _ensure_view_built(self, view_name: str):
        """
        Build and cache the real widget for view_name if it hasn't been yet.
        Replaces the placeholder in the stacked widget with the real widget.
        """
        if view_name in self._view_cache:
            return  # Already built

        factory = self._view_factories.get(view_name)
        if factory is None:
            logger.warning(f"No factory registered for view: {view_name}")
            return

        try:
            logger.info(f"Building view on first visit: {view_name}")
            widget = factory()
            self._view_cache[view_name] = widget

            # Swap placeholder → real widget at the same stacked-widget index
            index = self.view_registry[view_name]
            old_placeholder = self.stacked_widget.widget(index)
            self.stacked_widget.insertWidget(index, widget)
            self.stacked_widget.removeWidget(old_placeholder)
            old_placeholder.deleteLater()

        except Exception as e:
            logger.error(f"Error building view '{view_name}': {e}")
            logger.error(traceback.format_exc())

    # =========================================================================
    #  Navigation
    # =========================================================================

    def _populate_navigation(self):
        """Populate navigation tree from registry."""
        if self.nav_tree:
            self.nav_tree.clear()
            for view_name in self.view_registry:
                QTreeWidgetItem(self.nav_tree, [view_name])

    def _switch_view(self, item):
        """Called when the user clicks a nav-tree item."""
        view_name = item.text(0)
        if view_name not in self.view_registry:
            return

        # Build the view the first time it's visited
        first_visit = view_name not in self._view_cache
        self._ensure_view_built(view_name)

        self.stacked_widget.setCurrentIndex(self.view_registry[view_name])

        # On revisits, trigger a data refresh.  On the first visit the view's
        # own __init__ already loaded data, so we skip the extra round-trip.
        if not first_visit:
            current_widget = self.stacked_widget.currentWidget()
            for method_name in [
                "load_artists",
                "load_tracks",
                "load_tracks_on_startup",
                "load_albums",
                "load_genres",
                "load_places",
                "refresh_views",
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

    # =========================================================================
    #  View refresh (menu action / keyboard shortcut)
    # =========================================================================

    def _refresh_all_views(self):
        """Reload data for all views that have already been built."""
        try:
            logger.info("Refreshing all views...")

            all_tracks = self.controller.get.get_all_entities("Track")
            all_albums = self.controller.get.get_all_entities("Album")
            all_artists = self.controller.get.get_all_entities("Artist")
            all_genres = self.controller.get.get_all_entities("Genre")
            all_playlists = self.controller.get.get_all_entities("Playlist")

            # Only refresh views that have actually been built
            for view_name, widget in self._view_cache.items():
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
                else:
                    for method in ("refresh", "update_data"):
                        if hasattr(widget, method):
                            getattr(widget, method)()
                            break

            if hasattr(self, "queue_widget") and self.queue_widget:
                if hasattr(self.queue_widget, "refresh_queue"):
                    self.queue_widget.refresh_queue()

            logger.info("All built views refreshed successfully")
        except Exception as e:
            logger.error(f"Refresh error: {str(e)}")

    # =========================================================================
    #  Now Playing
    # =========================================================================

    def update_now_playing_view(self, file_path: Path):
        try:
            logger.info(f"Updating now playing view for: {file_path}")
            track = self.controller.get.get_entity_object(
                "Track", track_file_path=str(file_path)
            )
            if track:
                self.now_playing.updateUI(track)
            else:
                logger.warning(f"Track not found in database: {file_path}")
                self.now_playing.clearUI()
        except Exception as e:
            logger.error(f"Error updating NowPlayingView: {e}")
            logger.error(traceback.format_exc())

    # =========================================================================
    #  Player dock
    # =========================================================================

    def _create_player(self):
        """Create the bottom player dock."""
        logger.debug("Creating player dock")
        self.player_ui = PlayerUI(self.controller, self)
        self.player_dock = QDockWidget("Player", self)
        self.player_dock.setObjectName("PlayerDock")
        self.player_dock.setWidget(self.player_ui)
        self.player_dock.setFeatures(QDockWidget.DockWidgetMovable)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.player_dock)
        self.player_dock.show()
        self.mediaplayer.track_changed.connect(self._ensure_player_dock_visible)
        self.player_dock.setTitleBarWidget(QWidget())
        self.player_dock.raise_()
        logger.info("Player dock created")

    def _ensure_player_dock_visible(self):
        if hasattr(self, "player_dock") and not self.player_dock.isVisible():
            self.player_dock.show()
            self.player_dock.raise_()

    # =========================================================================
    #  Queue dock
    # =========================================================================

    def _create_queue_dock(self):
        """Create the queue dock widget (right side)."""
        logger.debug("Creating queue dock")
        self.queue_widget = QueueDockWidget(self.controller, self)
        self.queue_dock = QDockWidget("Queue", self)
        self.queue_dock.setObjectName("QueueDock")
        self.queue_dock.setWidget(self.queue_widget)
        self.queue_dock.setFeatures(
            QDockWidget.DockWidgetMovable
            | QDockWidget.DockWidgetFloatable
            | QDockWidget.DockWidgetClosable
        )
        self.queue_dock.setAllowedAreas(Qt.RightDockWidgetArea | Qt.LeftDockWidgetArea)
        self.queue_dock.setMinimumWidth(300)
        self.queue_dock.setMaximumWidth(500)
        self.addDockWidget(Qt.RightDockWidgetArea, self.queue_dock)
        self.queue_widget.track_double_clicked.connect(
            self._on_queue_track_double_clicked
        )
        self.queue_dock.hide()
        logger.info("Queue dock created and hidden by default")

    def _on_queue_track_double_clicked(self, file_path):
        if self.mediaplayer.load_track(file_path):
            self.mediaplayer.play()

    # =========================================================================
    #  Window setup / layout
    # =========================================================================

    def _setup_main_window(self):
        size = app_config.get_window_size()
        pos = app_config.get_window_position()
        self.resize(size)
        self.move(pos)
        self.setContentsMargins(10, 10, 10, 10)
        if app_config.is_window_maximized():
            QTimer.singleShot(
                0, lambda: self.setWindowState(self.windowState() | Qt.WindowMaximized)
            )
        QTimer.singleShot(100, self.ensure_window_in_screen)
        self._load_theme()

    def _load_theme(self):
        try:
            theme_file = app_config.get_theme_file()
            theme_path = app_config.get_theme_path(theme_file)
            if theme_path.exists():
                with open(theme_path, "r", encoding="utf-8") as f:
                    QApplication.instance().setStyleSheet(f.read())
                logger.debug(f"Theme applied: {theme_file}")
            else:
                default_theme = theme("dark_mode.qss")
                if Path(default_theme).exists():
                    with open(default_theme, "r", encoding="utf-8") as f:
                        QApplication.instance().setStyleSheet(f.read())
                    logger.debug("Fallback theme applied")
                else:
                    logger.warning("No theme file found")
                    QApplication.instance().setStyleSheet("")
        except Exception as e:
            logger.error(f"Error loading theme: {e}")
            QApplication.instance().setStyleSheet("")

    def _add_navigation_menu_actions(self):
        toggle_nav_action = QAction("Toggle Navigation", self)
        toggle_nav_action.setShortcut(QKeySequence("Ctrl+Shift+N"))
        toggle_nav_action.triggered.connect(self.navigation_dock.toggle_navigation)
        toggle_nav_action.setIcon(QIcon(icon("toggle_navigation.svg")))
        self.view_menu.addAction(toggle_nav_action)

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

    def ensure_window_in_screen(self):
        try:
            screen = QApplication.primaryScreen()
            screen_geometry = screen.availableGeometry()
            window_geometry = self.geometry()
            safe_margin = 100
            safe_rect = screen_geometry.adjusted(
                safe_margin, safe_margin, -safe_margin, -safe_margin
            )
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

    def adjust_dock_size(self):
        """Adjust dock size to fit content safely."""
        try:
            ideal_size = self.sizeHint()
            if ideal_size.isValid():
                height = max(60, ideal_size.height() + 20)
                width = max(400, ideal_size.width())
                dock = self.parent().parent() if self.parent() else None
                if isinstance(dock, QDockWidget):
                    dock.setMinimumHeight(height)
                    dock.setMinimumWidth(width)
                    self.setMinimumHeight(max(50, ideal_size.height()))
                    logger.debug(f"Adjusted dock size: {width}x{height}")
            else:
                dock = self.parent().parent() if self.parent() else None
                if isinstance(dock, QDockWidget):
                    dock.setMinimumHeight(60)
                    dock.setMinimumWidth(400)
        except Exception as e:
            logger.error(f"Error adjusting player dock size: {e}")
            try:
                dock = self.parent().parent() if self.parent() else None
                if isinstance(dock, QDockWidget):
                    dock.setMinimumHeight(60)
                    dock.setMinimumWidth(400)
            except Exception:
                pass

    def _reset_ui_layout(self):
        """Reset window and dock positions using config."""
        try:
            default_size = app_config.get_window_size()
            default_pos = app_config.get_window_position()
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

        for dock in self.findChildren(QDockWidget):
            dock.setFloating(False)
            dock.hide()

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
                dock.show()
                if attr_name == "queue_dock" and not getattr(
                    app_config, "queue_visible", True
                ):
                    dock.hide()

        if hasattr(self, "_restore_player"):
            self._restore_player()

    def _restore_player(self):
        if not getattr(self, "player_ui", None):
            self._create_player()
        player_dock = next(
            (d for d in self.findChildren(QDockWidget) if d.widget() == self.player_ui),
            None,
        )
        if player_dock:
            player_dock.setFloating(False)
            self.addDockWidget(Qt.BottomDockWidgetArea, player_dock)
            player_dock.show()
            if hasattr(app_config, "player_dock_size"):
                player_dock.resize(QSize(*app_config.player_dock_size))

    # =========================================================================
    #  Close
    # =========================================================================

    def closeEvent(self, event):
        app_config.set_window_size(self.size())
        app_config.set_window_position(self.pos())
        app_config.set_window_maximized(self.isMaximized())
        app_config.set_window_state(self.saveState())

        # Save queue state (history + upcoming) before writing config to disk.
        try:
            self.mediaplayer.queue_manager.save_queue_to_config()
        except Exception as exc:
            logger.error(f"closeEvent: failed to save queue: {exc}")

        app_config.save()
        self.mediaplayer.cleanup()
