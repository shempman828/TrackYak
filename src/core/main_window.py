"""Main GUI application for the Music Library manager using PySide6."""

from pathlib import Path
import traceback

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import QApplication, QDockWidget, QMainWindow, QStackedWidget, QTreeWidgetItem, QWidget
from sqlalchemy.exc import SQLAlchemyError

# Views are imported here but built only on first navigation; to add a view,
# add one entry to self._view_factories in _create_views().
from src.album.view.album_view import AlbumView
from src.artist.view.artist_view import ArtistView
from src.award.award_view import AwardView
from src.charts.ui.charts_view import ChartsView
from src.core.menu_bar import MenuBar
from src.core.navigation_dock import NavigationDock
from src.core.status_widget import StatusBarWidget
from src.dates.dates_view import TimelineView
from src.foundation.asset_paths import icon, resolve_theme_assets, theme
from src.foundation.config_setup import app_config
from src.foundation.logger_config import logger
from src.foundation.status_utility import StatusManager
from src.genre.genre_view import GenreView
from src.influences.influences_view import InfluencesView
from src.mood.mood_view import MoodView
from src.nowplaying.nowplaying_view import NowPlayingView
from src.place.place_view import PlaceView
from src.player.ui.player_dock import PlayerUI
from src.player.ui.queue_dock import QueueDockWidget
from src.playlist.playlist_view import PlaylistView
from src.publisher.publisher_view import PublisherView
from src.role.role_view import RoleView
from src.sync.sync_view import SyncView
from src.track.view.track_view import TrackView

# View classes whose load_data() takes the full entity list, and the entity each one needs.
_LOAD_DATA_ENTITIES = ((TrackView, "Track"), (AlbumView, "Album"), (ArtistView, "Artist"), (GenreView, "Genre"), (PlaylistView, "Playlist"))

# Offset from the available screen edges that the window must stay inside.
_SCREEN_SAFE_MARGIN = 100


class GUI(QMainWindow, MenuBar):
    """TrackYak main window: lazy views in a stacked widget, plus navigation, player and queue docks."""

    def __init__(self, controller):
        super().__init__()
        self.controller = controller

        self.mediaplayer = self.controller.mediaplayer

        # NowPlayingView is cheap to construct and is needed immediately,
        # so we keep it as an eager view.
        self.now_playing = NowPlayingView(controller)

        self.setObjectName("MainWindow")
        self.view_registry = {}

        self.mediaplayer.track_changed.connect(self.update_now_playing_view)
        self.mediaplayer.error_occurred.connect(lambda msg: StatusManager.show_message(msg, 6000))
        # The status overlay must exist before _init_ui so startup errors can be shown on it.
        self._init_status_system()
        self._init_ui()

    # =========================================================================
    #  Properties
    # =========================================================================

    @property
    def nav_tree(self):
        """Return the navigation tree, or None if the navigation dock was not built."""
        if hasattr(self, "navigation_dock"):
            return self.navigation_dock.nav_tree
        return None

    # =========================================================================
    #  UI initialisation
    # =========================================================================

    def _init_ui(self):
        """Menu creation, views, player, and navigation."""
        self.setWindowTitle("TrackYak")
        self._setup_main_window()
        self._init_menu_bar()

        try:
            self.navigation_dock = NavigationDock(self)
            self._create_player()
            QTimer.singleShot(0, self._create_queue_dock)
            QTimer.singleShot(120, self.restore_layout)
        except Exception:
            # Intentional broad boundary catch: startup wires up several
            # unrelated subsystems (nav dock, audio player, timers) and a
            # failure in any one of them must not prevent the main window
            # from opening at all — log and keep going with a degraded UI.
            logger.exception("Error creating navigation or player docks")
            StatusManager.show_message("Failed to initialize player", 5000)

        self._create_views()
        self._add_navigation_menu_actions()

    def _init_status_system(self):
        """Replace the QMainWindow status bar with the floating StatusBarWidget toast."""
        # The toast is parented to the window but never laid out, so it takes no layout space.
        self.setStatusBar(None)
        self.status_bar_widget = StatusBarWidget(self)

        StatusManager.show_status.connect(self.status_bar_widget.show_message)
        StatusManager.hide_status.connect(self.status_bar_widget.hide)
        self.status_bar_widget.hide()

    # =========================================================================
    #  View creation — LAZY
    # =========================================================================

    def _create_views(self):
        """Set up the stacked widget with a placeholder per view and build only the default Tracks view."""
        self.stacked_widget = QStackedWidget()

        # TrackView is the default landing view, so it is built now; NowPlayingView was built in __init__.
        self._track_view_instance = TrackView(self.controller, self.mediaplayer)

        # Each factory is called at most once per session, on first navigation.
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
            "Charts": lambda: ChartsView(self.controller),
            "Sync": lambda: SyncView(self.controller),
            "Timeline": lambda: TimelineView(self.controller),
        }

        self._load_navigation_state()

        # Built widgets, filled in by _ensure_view_built on first navigation.
        self._view_cache = {}

        # view_registry maps name -> stacked-widget index. Every view gets a cheap placeholder
        # slot now, so the nav tree can be filled at once; the slot is swapped on first visit.
        self.view_registry = {}
        for view_name in self._view_factories:
            index = self.stacked_widget.addWidget(QWidget())
            self.view_registry[view_name] = index

        self.setCentralWidget(self.stacked_widget)

        self._ensure_view_built("Tracks")
        self.stacked_widget.setCurrentIndex(self.view_registry["Tracks"])

        self._populate_navigation()

    def _ensure_view_built(self, view_name: str):
        """Build view_name on first use and swap it in for its placeholder."""
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
            # Intentional broad boundary catch: this dispatches to a registry
            # of unrelated, independently-implemented view constructors (one
            # per nav entry) — a bug in any single one must not prevent the
            # rest of the app's navigation from working.
            logger.error(f"Error building view '{view_name}': {e}")
            logger.error(traceback.format_exc())

    # =========================================================================
    #  Navigation
    # =========================================================================

    def _load_navigation_state(self):
        """Load the persisted nav order and hidden set; "Tracks" is never hidden."""
        # Views missing from the saved order (e.g. added in a later release) are appended, visible.
        all_views = list(self._view_factories)
        order = [name for name in app_config.get_nav_item_order() if name in all_views]
        order += [name for name in all_views if name not in order]
        self._nav_order = order

        hidden = set(app_config.get_nav_hidden_items())
        hidden.discard("Tracks")
        self._nav_hidden = hidden

    def _populate_navigation(self):
        """Populate navigation tree from the persisted order, skipping hidden entries."""
        if self.nav_tree:
            self.nav_tree.clear()
            order = getattr(self, "_nav_order", None) or list(self.view_registry)
            hidden = getattr(self, "_nav_hidden", set())
            for view_name in order:
                if view_name not in self.view_registry or view_name in hidden:
                    continue
                QTreeWidgetItem(self.nav_tree, [view_name])

    def apply_navigation_state(self, order, hidden):
        """Persist a new nav order/hidden set, rebuild the tree, and leave a view that just became hidden."""
        known = set(self.view_registry)
        order = [name for name in order if name in known]
        hidden = {name for name in hidden if name in known}
        hidden.discard("Tracks")

        current_view = next((name for name, index in self.view_registry.items() if index == self.stacked_widget.currentIndex()), None)

        self._nav_order = order
        self._nav_hidden = hidden
        app_config.set_nav_item_order(self._nav_order)
        app_config.set_nav_hidden_items(list(self._nav_hidden))
        self._populate_navigation()

        if current_view in self._nav_hidden:
            fallback = next((name for name in self._nav_order if name not in self._nav_hidden), "Tracks")
            self._ensure_view_built(fallback)
            self.stacked_widget.setCurrentIndex(self.view_registry[fallback])

    def _switch_view(self, item):
        """Called when the user clicks a nav-tree item."""
        view_name = item.text(0)
        if view_name not in self.view_registry:
            return

        # Build the view the first time it's visited
        first_visit = view_name not in self._view_cache
        self._ensure_view_built(view_name)

        self.stacked_widget.setCurrentIndex(self.view_registry[view_name])

        # Force an immediate synchronous repaint of the newly-shown view.
        # Without this, the widget's first paint can be deferred a frame,
        # leaving the previous view's pixels briefly visible underneath
        # (most noticeable behind NowPlayingView's translucent-margin art
        # card).
        self.stacked_widget.currentWidget().repaint()

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
                "load_charts",
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

            # Each entity list is queried at most once, and only if a built view needs it.
            entity_cache = {}

            def entities(name):
                """Return the cached full list of entity `name`."""
                if name not in entity_cache:
                    entity_cache[name] = self.controller.get.get_all_entities(name)
                return entity_cache[name]

            for widget in self._view_cache.values():
                if hasattr(widget, "load_data"):
                    entity_name = next((name for cls, name in _LOAD_DATA_ENTITIES if isinstance(widget, cls)), None)
                    if entity_name is not None:
                        widget.load_data(entities(entity_name))
                else:
                    for method in ("refresh", "update_data"):
                        if hasattr(widget, method):
                            getattr(widget, method)()
                            break

            queue_widget = getattr(self, "queue_widget", None)
            if queue_widget is not None and hasattr(queue_widget, "refresh_queue"):
                queue_widget.refresh_queue()

            logger.info("All built views refreshed successfully")
        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Refresh error: {e!s}")

    # =========================================================================
    #  Now Playing
    # =========================================================================

    def update_now_playing_view(self, file_path: Path):
        """Show the database track for file_path in NowPlayingView, or clear it if not found."""
        try:
            logger.info(f"Updating now playing view for: {file_path}")
            track = self.controller.get.get_entity_object("Track", track_file_path=str(file_path))
            if track:
                self.now_playing.updateUI(track)
            else:
                logger.warning(f"Track not found in database: {file_path}")
                self.now_playing.clearUI()
        except Exception:
            # Intentional broad boundary catch: this is a Qt slot wired to
            # mediaplayer.track_changed and fires on every track change — it
            # must never propagate and interrupt playback.
            logger.exception("Error updating NowPlayingView")

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
        """Show the player dock on track change unless Now Playing is in cinema mode."""
        if getattr(self.now_playing, "cinema_mode", False):
            return
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
        self.queue_dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable | QDockWidget.DockWidgetClosable)
        self.queue_dock.setAllowedAreas(Qt.RightDockWidgetArea | Qt.LeftDockWidgetArea)
        self.queue_dock.setMinimumWidth(300)
        self.queue_dock.setMaximumWidth(500)
        self.addDockWidget(Qt.RightDockWidgetArea, self.queue_dock)
        self.queue_widget.track_double_clicked.connect(self._on_queue_track_double_clicked)
        self.queue_dock.hide()
        logger.info("Queue dock created and hidden by default")

    def set_queue_visible(self, visible: bool):
        """Show or hide the queue dock; the single source of truth for its visibility."""
        # Only touches visibility, never re-adds the dock, so a dragged or floated placement survives.
        if not hasattr(self, "queue_dock"):
            return
        nav_dock = getattr(self, "navigation_dock", None)
        nav_width = nav_dock.width() if nav_dock is not None else None
        self.queue_dock.setVisible(visible)
        if nav_dock is not None and nav_width:
            # QMainWindow's dock layout otherwise reclaims the queue dock's
            # space by shrinking sibling docks (the nav dock has the lowest
            # width floor). Re-pinning the nav dock's width here forces the
            # reflow to take/give that space from the central widget instead.
            self.resizeDocks([nav_dock], [nav_width], Qt.Horizontal)
        if visible:
            self.queue_dock.raise_()
        if hasattr(self, "toggle_queue_action"):
            self.toggle_queue_action.setChecked(visible)

    def _on_queue_track_double_clicked(self, file_path):
        """Load and play the double-clicked queue track."""
        if self.mediaplayer.load_track(file_path):
            self.mediaplayer.play()

    # =========================================================================
    #  Window setup / layout
    # =========================================================================

    def _setup_main_window(self):
        """Apply the saved window size, position and maximized state, then the theme."""
        self.resize(app_config.get_window_size())
        self.move(app_config.get_window_position())
        self.setContentsMargins(10, 10, 10, 10)
        if app_config.is_window_maximized():
            QTimer.singleShot(0, lambda: self.setWindowState(self.windowState() | Qt.WindowMaximized))
        QTimer.singleShot(100, self.ensure_window_in_screen)
        self._load_theme()

    def _load_theme(self):
        """Apply the theme through DisplaySettings, or load the theme QSS directly as a fallback."""
        display_settings = getattr(QApplication.instance(), "display_settings", None)
        if display_settings is not None:
            # DisplaySettings.apply_all() is the scale-aware loader; the raw
            # setStyleSheet() path below would drop the UI-scale setting.
            display_settings.apply_all()
            return

        try:
            theme_file = app_config.get_theme_file()
            theme_path = app_config.get_theme_path(theme_file)
            if theme_path.exists():
                with theme_path.open(encoding="utf-8") as f:
                    QApplication.instance().setStyleSheet(resolve_theme_assets(f.read()))
                logger.debug(f"Theme applied: {theme_file}")
            else:
                default_theme = theme("dark_mode.qss")
                if Path(default_theme).exists():
                    with Path(default_theme).open(encoding="utf-8") as f:
                        QApplication.instance().setStyleSheet(resolve_theme_assets(f.read()))
                    logger.debug("Fallback theme applied")
                else:
                    logger.warning("No theme file found")
                    QApplication.instance().setStyleSheet("")
        except (OSError, UnicodeDecodeError) as e:
            logger.error(f"Error loading theme: {e}")
            QApplication.instance().setStyleSheet("")

    def _add_navigation_menu_actions(self):
        """Add the Toggle Navigation action to the View menu."""
        nav_dock = getattr(self, "navigation_dock", None)
        if nav_dock is None:
            return
        toggle_nav_action = QAction("Toggle Navigation", self)
        toggle_nav_action.setShortcut(QKeySequence("Ctrl+Shift+N"))
        toggle_nav_action.triggered.connect(nav_dock.toggle_navigation)
        toggle_nav_action.setIcon(QIcon(icon("toggle_navigation.svg")))
        self.view_menu.addAction(toggle_nav_action)

    def restore_layout(self):
        """Restore saved QMainWindow state including floating docks."""
        nav_dock = getattr(self, "navigation_dock", None)
        window_state = app_config.get_window_state()
        if window_state and not window_state.isEmpty():
            try:
                self.restoreState(window_state)
                logger.debug("Window state restored successfully")
                if nav_dock is not None:
                    QTimer.singleShot(40, nav_dock.ensure_proper_navigation_size)
                return
            except RuntimeError as e:
                logger.warning(f"Failed to restore window state: {e}")
        if nav_dock is not None:
            QTimer.singleShot(40, nav_dock.size_navigation_to_content)

    def ensure_window_in_screen(self):
        """Move the window back inside the safe area of the screen it is on."""
        # A maximized or full-screen window is placed by the window manager; moving it would unmaximize it.
        if self.isMaximized() or self.isFullScreen():
            return
        try:
            window_geometry = self.geometry()
            screen = QApplication.screenAt(window_geometry.center()) or self.screen() or QApplication.primaryScreen()
            if screen is None:
                return
            m = _SCREEN_SAFE_MARGIN
            safe_rect = screen.availableGeometry().adjusted(m, m, -m, -m)
            if not safe_rect.contains(window_geometry):
                new_x = max(safe_rect.left(), min(window_geometry.x(), safe_rect.right() - window_geometry.width()))
                new_y = max(safe_rect.top(), min(window_geometry.y(), safe_rect.bottom() - window_geometry.height()))
                self.move(new_x, new_y)
                logger.debug("Adjusted window position to stay within safe screen area")
        except (AttributeError, RuntimeError) as e:
            logger.error(f"Error ensuring window in screen: {e}")

    def _reset_ui_layout(self):
        """Reset window and dock positions using config."""
        self.resize(app_config.get_window_size())
        self.move(app_config.get_window_position())

        queue_dock = getattr(self, "queue_dock", None)
        queue_was_visible = queue_dock is not None and queue_dock.isVisible()

        for dock in self.findChildren(QDockWidget):
            dock.setFloating(False)
            dock.hide()

        dock_config = [("navigation_dock", Qt.LeftDockWidgetArea, "expand_navigation"), ("player_dock", Qt.BottomDockWidgetArea, None), ("queue_dock", Qt.RightDockWidgetArea, None)]
        for attr_name, area, expand_method in dock_config:
            dock = getattr(self, attr_name, None)
            if dock is None:
                continue
            self.addDockWidget(area, dock)
            if expand_method and hasattr(dock, expand_method):
                getattr(dock, expand_method)()
            if attr_name != "queue_dock":
                dock.show()

        # The queue keeps the visibility it had before the reset.
        self.set_queue_visible(queue_was_visible)

        if not getattr(self, "player_ui", None):
            self._create_player()

    # =========================================================================
    #  Close
    # =========================================================================

    def closeEvent(self, event):
        """Persist window and queue state, stop background work, and release the player and DB session."""
        app_config.set_window_size(self.size())
        app_config.set_window_position(self.pos())
        app_config.set_window_maximized(self.isMaximized())
        app_config.set_window_state(self.saveState())

        # Save queue state (history + upcoming) before writing config to disk.
        try:
            self.mediaplayer.queue_manager.save_queue_to_config()
        except AttributeError as exc:
            logger.error(f"closeEvent: failed to save queue: {exc}")

        app_config.save()

        # A running QThread destroyed with the window aborts the process.
        self._stop_explicit_recalc_worker()
        # Child views never receive closeEvent, so views that own worker threads expose shutdown().
        for widget in getattr(self, "_view_cache", {}).values():
            shutdown = getattr(widget, "shutdown", None)
            if callable(shutdown):
                shutdown()
        # A parentless mini player would otherwise keep the app alive after the main window closes.
        self._close_miniplayer()

        self.mediaplayer.cleanup()
        # Docks may be missing if startup failed or the window closed before the queue timer ran.
        player_ui = getattr(self, "player_ui", None)
        if player_ui is not None:
            player_ui.cleanup()
        queue_widget = getattr(self, "queue_widget", None)
        if queue_widget is not None:
            queue_widget.clear()
        self.controller.close_session()
