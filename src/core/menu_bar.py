import os

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QCursor, QDesktopServices, QIcon, QKeySequence
from PySide6.QtWidgets import QApplication, QMessageBox

from src.common.alias_management_dialog import AliasManagementDialog
from src.core.asset_paths import ASSETS_DIR, icon
from src.core.config_setup import app_config
from src.core.logger_config import logger
from src.core.status_utility import show_status_message
from src.core.version import get_version
from src.equalizer.equalizer_dialog import EqualizerDialog
from src.file_management.file_manager_dialog import FileManager
from src.importing.import_dialog import ImportDialog
from src.library.duplicate_finder import DuplicateFinderDialog
from src.library.missing_tracks import MissingTracks
from src.lyrics.explicit_recalc_worker import ExplicitRecalcWorker
from src.mood.mood_autotag_dialog import MoodAutoTagDialog
from src.player.player_mini import MiniPlayerWindow
from src.statistics.analysis_dialog import AudioAnalysisDialog
from src.statistics.statistics_dialog import MusicStatsDialog


class MenuBar:
    def add_action(
        self,
        menu,
        text,
        icon_name=None,
        slot=None,
        shortcut=None,
        *,
        tooltip=None,
        checkable=False,
        checked=False,
        shortcut_context=None,
    ):
        """Build a QAction, wire it up, and append it to `menu` in one call."""
        action = QAction(text, self)
        if icon_name and self._icon_exists(icon_name):
            action.setIcon(QIcon(icon(icon_name)))
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        if shortcut_context is not None:
            action.setShortcutContext(shortcut_context)
        if tooltip:
            action.setToolTip(tooltip)
        if checkable:
            action.setCheckable(True)
            action.setChecked(checked)
        if slot is not None:
            action.triggered.connect(slot)
        menu.addAction(action)
        return action

    def _init_menu_bar(self):
        """Create the main menu bar without navigation-specific actions."""
        menu_bar = self.menuBar()

        # File menu
        file_menu = menu_bar.addMenu("File")

        self.add_action(
            file_menu, "Import Directory", "import.svg", self.show_import_dialog
        )
        self.add_action(
            file_menu, "Manage Library", "manage_library.svg", self.show_file_manager
        )
        self.add_action(
            file_menu,
            "View Library Statistics",
            "statistics.svg",
            self.show_statistics_dialog,
        )
        self.add_action(
            file_menu,
            "Find Duplicate Tracks",
            slot=self.show_duplicate_finder,
            tooltip="Scan library for possible duplicate tracks",
        )
        self.add_action(
            file_menu,
            "Find Missing Tracks",
            slot=self.show_missing_tracks,
            tooltip="Scan library for missing tracks",
        )

        file_menu.addSeparator()

        # General Settings — opens the full ConfigDialog
        self.add_action(
            file_menu,
            "General Settings",
            "settings.svg",
            self.show_general_settings_dialog,
        )

        file_menu.addSeparator()

        self.add_action(
            file_menu, "Exit", "exit.svg", self.close, shortcut="Ctrl+Q"
        )

        # Audio Menu
        audio_menu = menu_bar.addMenu("Audio")

        self.add_action(
            audio_menu, "Equalizer Settings", "equalizer.svg", self.show_equalizer_dialog
        )
        self.add_action(
            audio_menu,
            "Audio File Analysis",
            "audio_analysis.svg",
            self.show_audio_properties,
        )

        # Tools menu
        tools_menu = menu_bar.addMenu("Tools")

        self.add_action(
            tools_menu,
            "Manage Aliases…",
            slot=self.show_alias_management_dialog,
            tooltip="View and edit merge/split aliases and skipped genres",
        )
        self.add_action(
            tools_menu,
            "Recalculate Explicit Flags…",
            slot=self.show_explicit_recalc,
            tooltip="Scan every track with lyrics but no Explicit setting yet, "
            "and flag it against assets/explicit_words.txt",
        )
        self.add_action(
            tools_menu,
            "Mood Tagging…",
            slot=self.show_mood_autotag_dialog,
            tooltip="Auto-tag tracks with moods and known places from their "
            "lyrics, and review lyrics words not yet assigned to a mood",
        )

        # View menu
        self.view_menu = menu_bar.addMenu("View")

        self.toggle_queue_action = self.add_action(
            self.view_menu,
            "Show Queue",
            "toggle_queue.svg",
            self.toggle_queue_visibility,
            shortcut="Shift+Q",
            checkable=True,
            checked=False,
            shortcut_context=Qt.ApplicationShortcut,
        )

        self.add_action(
            self.view_menu,
            "Full Screen",
            "fullscreen.svg",
            self.toggle_fullscreen,
            shortcut="F11",
        )
        self.add_action(
            self.view_menu, "Mini Player", slot=self.open_miniplayer, shortcut="Ctrl+M"
        )

        self.view_menu.addSeparator()
        self.add_action(
            self.view_menu,
            "Reset Layout",
            slot=self._reset_ui_layout,
            tooltip="Restore the navigation, queue, and player panels to their default positions",
        )

        # Help menu
        help_menu = menu_bar.addMenu("Help")

        self.add_action(help_menu, "About", slot=self.show_about_dialog)
        self.add_action(help_menu, "Support this Project")

        wikipedia_url = "https://wikimediafoundation.org/give/?rdfrom=%2F%2Fdonate.wikimedia.org%2Fw%2Findex.php%3Ftitle%3DWays_to_Give%26redirect%3Dno#ways-to-give"
        self.add_action(
            help_menu,
            "Support Wikipedia",
            slot=lambda: QDesktopServices.openUrl(QUrl(wikipedia_url)),
        )

        # --- Menu bar auto-hide setup ---
        # A timer is used to add a small delay before hiding so the bar doesn't
        # flicker when the user moves between menus.
        self._menu_bar_hide_timer = QTimer(self)
        self._menu_bar_hide_timer.setSingleShot(True)
        self._menu_bar_hide_timer.setInterval(300)  # 300 ms grace period
        self._menu_bar_hide_timer.timeout.connect(self._hide_menu_bar_if_mouse_gone)

        # Polls the global cursor position rather than relying on mouse-move
        # events: Qt only delivers QEvent.MouseMove when the widget under the
        # cursor has mouse tracking enabled (or a button is held), and most
        # widgets in this window don't opt into that. Polling has no such
        # blind spot regardless of what widget is under the cursor.
        self._menu_bar_poll_timer = QTimer(self)
        self._menu_bar_poll_timer.setInterval(100)
        self._menu_bar_poll_timer.timeout.connect(self._check_mouse_for_menu_bar)

        # Store the known height of the menu bar so we can use it even when
        # the bar is hidden (sizeHint returns 0 when hidden).
        self._menu_bar_known_height = self.menuBar().sizeHint().height() or 25

        # Apply the saved auto-hide preference on startup
        self._apply_menu_bar_auto_hide(self._get_display_settings_auto_hide())

    # ------------------------------------------------------------------
    # Auto-hide helpers
    # ------------------------------------------------------------------

    def _icon_exists(self, name: str) -> bool:
        """Safely check if an icon file exists before loading it."""
        try:
            path = str(ASSETS_DIR / name)
            return os.path.exists(path)
        except (OSError, TypeError) as e:
            logger.debug(f"Icon existence check failed for {name}: {e}")
            return False

    def _get_display_settings_auto_hide(self) -> bool:
        """Read the current auto-hide preference from wherever DisplaySettings lives."""
        ds = self._resolve_display_settings()
        if ds is not None:
            return ds.get_menu_bar_auto_hide()
        return False

    def _resolve_display_settings(self):
        """Return the DisplaySettings instance, or None if unavailable."""
        app = QApplication.instance()
        if app is not None and hasattr(app, "display_settings"):
            return app.display_settings
        return None

    def _apply_menu_bar_auto_hide(self, enabled: bool):
        """
        Turn auto-hide on or off.
        When ON:  the menu bar is hidden and a polling timer watches the
                  global cursor position so we know when it's near the top
                  of the window, including the area where the hidden menu
                  bar used to be.
        When OFF: the menu bar is always visible (normal behaviour).
        """
        menu_bar = self.menuBar()

        if enabled:
            # Record the height now while the bar is still visible so we have
            # a reliable value to use when the bar is hidden.
            h = menu_bar.sizeHint().height()
            if h > 0:
                self._menu_bar_known_height = h

            menu_bar.hide()
            self._menu_bar_poll_timer.start()
        else:
            # Stop both timers and make sure the bar is visible
            self._menu_bar_hide_timer.stop()
            self._menu_bar_poll_timer.stop()
            menu_bar.show()

    def _hide_menu_bar_if_mouse_gone(self):
        """Called by the timer — hides the bar only if auto-hide is still on."""
        if self._get_display_settings_auto_hide():
            self.menuBar().hide()

    def _check_mouse_for_menu_bar(self):
        """
        Polls the global cursor position for menu bar auto-show on hover.

        Polling avoids relying on QEvent.MouseMove, which Qt only delivers to
        a widget when that widget has mouse tracking enabled (or a mouse
        button is held) — most widgets in this window don't opt into that,
        so an event-based approach misses most cursor movement.
        """
        if not self._get_display_settings_auto_hide():
            return

        # Only react while this window is the one actually on screen under
        # the cursor — otherwise another window overlapping the same screen
        # coordinates would spuriously pop the bar open.
        if not self.isActiveWindow():
            return

        menu_bar = self.menuBar()
        # Use the stored height — sizeHint() returns 0 when the bar is hidden.
        trigger_height = self._menu_bar_known_height + 5

        local_pos = self.mapFromGlobal(QCursor.pos())
        x, y = local_pos.x(), local_pos.y()
        inside_top_region = 0 <= x <= self.width() and 0 <= y <= trigger_height

        if inside_top_region:
            # Mouse is near the top — show the bar and cancel any pending hide.
            self._menu_bar_hide_timer.stop()
            if not menu_bar.isVisible():
                menu_bar.show()
        else:
            # Mouse moved away — start the grace-period timer.
            if (
                menu_bar.isVisible()
                and not menu_bar.activeAction()
                and not self._menu_bar_hide_timer.isActive()
            ):
                self._menu_bar_hide_timer.start()

    # ------------------------------------------------------------------
    # Audio settings
    # ------------------------------------------------------------------

    def show_equalizer_dialog(self):
        """Show the equalizer configuration dialog."""
        if not hasattr(self, "equalizer_dialog"):
            self.equalizer_dialog = EqualizerDialog(
                self.controller.mediaplayer.equalizer, app_config, self
            )
        self.equalizer_dialog.show()
        self.equalizer_dialog.raise_()
        self.equalizer_dialog.activateWindow()

    # ------------------------------------------------------------------
    # General Settings
    # ------------------------------------------------------------------

    def show_general_settings_dialog(self):
        """Open the General Settings (ConfigDialog) window.

        Also covers what used to be the separate Display Settings and
        Manage Audio Settings dialogs, so both a DisplaySettings instance
        and the live player are resolved and handed in when available.
        """
        from src.core.config_dialog import ConfigDialog

        display_settings = self._resolve_display_settings()

        player = getattr(self, "mediaplayer", None)
        if player is None and hasattr(self, "controller"):
            player = getattr(self.controller, "mediaplayer", None)

        dialog = ConfigDialog(app_config, display_settings, player, self)

        if display_settings is not None:
            display_settings.menu_bar_auto_hide_changed.connect(
                self._apply_menu_bar_auto_hide
            )

        dialog.exec_()

        if display_settings is not None:
            try:
                display_settings.menu_bar_auto_hide_changed.disconnect(
                    self._apply_menu_bar_auto_hide
                )
            except RuntimeError:
                logger.debug(
                    "menu_bar_auto_hide_changed signal was already disconnected"
                )

    # ------------------------------------------------------------------
    # Other dialogs
    # ------------------------------------------------------------------

    def show_statistics_dialog(self):
        if not hasattr(self, "statistics_dialog"):
            self.statistics_dialog = MusicStatsDialog(self.controller, self)
        self.statistics_dialog.show()
        self.statistics_dialog.raise_()
        self.statistics_dialog.activateWindow()

    def show_alias_management_dialog(self):
        if not hasattr(self, "alias_management_dialog"):
            self.alias_management_dialog = AliasManagementDialog(self.controller, self)
        self.alias_management_dialog.show()
        self.alias_management_dialog.raise_()
        self.alias_management_dialog.activateWindow()

    def show_explicit_recalc(self):
        """Backfill is_explicit for every track that has lyrics but has
        never had it determined (NULL) -- never overwrites a manual or
        previously-computed value. See ExplicitRecalcWorker."""
        if getattr(self, "_explicit_recalc_worker", None) is not None:
            return
        show_status_message(self, "Recalculating explicit flags…", duration=0)
        self._explicit_recalc_worker = ExplicitRecalcWorker(self.controller)
        self._explicit_recalc_worker.finished.connect(self._on_explicit_recalc_finished)
        self._explicit_recalc_worker.error.connect(self._on_explicit_recalc_error)
        self._explicit_recalc_worker.start()

    def _on_explicit_recalc_finished(self, scanned: int, flagged: int):
        show_status_message(
            self,
            f"Explicit flags recalculated: {scanned} track(s) scanned, "
            f"{flagged} flagged explicit",
        )
        self._explicit_recalc_worker.wait()
        self._explicit_recalc_worker = None

    def show_mood_autotag_dialog(self):
        if not hasattr(self, "mood_autotag_dialog"):
            self.mood_autotag_dialog = MoodAutoTagDialog(self.controller, self)
        self.mood_autotag_dialog.show()
        self.mood_autotag_dialog.raise_()
        self.mood_autotag_dialog.activateWindow()

    def _on_explicit_recalc_error(self, message: str):
        show_status_message(self, f"Explicit flag recalculation failed: {message}")
        self._explicit_recalc_worker.wait()
        self._explicit_recalc_worker = None

    def show_duplicate_finder(self):
        """Open the Duplicate Track Finder dialog."""
        if not hasattr(self, "duplicate_finder_dialog"):
            self.duplicate_finder_dialog = DuplicateFinderDialog(self.controller, self)
        self.duplicate_finder_dialog.show()
        self.duplicate_finder_dialog.raise_()
        self.duplicate_finder_dialog.activateWindow()

    def show_about_dialog(self):
        description = """TrackYak is a powerful application for tracking and managing your music library."""

        about_box = QMessageBox(self)
        about_box.setWindowTitle("About TrackYak")
        about_box.setIcon(QMessageBox.Information)
        about_box.setTextFormat(Qt.RichText)
        about_box.setText(
            f"<h2>TrackYak</h2>"
            f"<p>Version {get_version()}</p>"
            f"<p><b>Developed by Baby Yak Studios</b></p>"
            f"<hr>"
            f"<h3>Description:</h3>"
            f"<p>{description.replace(chr(10), '<br>')}</p>"
            f"<hr>"
            f"<h3>License:</h3>"
            f"<p><a href='file:///{os.path.abspath('license.md')}'>View Full License Text</a></p>"
        )
        about_box.setTextInteractionFlags(Qt.TextBrowserInteraction)
        about_box.setStandardButtons(QMessageBox.Ok)
        about_box.setModal(True)
        about_box.exec_()

    def toggle_queue_visibility(self, checked):
        """Toggle queue dock visibility."""
        self.set_queue_visible(checked)

    def show_audio_properties(self):
        dialog = AudioAnalysisDialog(self.controller)
        dialog.exec_()
        return dialog

    def show_import_dialog(self):
        """Display the ImportDialog when the 'Import Directory' action is triggered."""
        if not hasattr(self, "import_dialog"):
            self.import_dialog = ImportDialog(self.controller)

        try:
            self.import_dialog.isVisible()
        except RuntimeError:
            logger.debug("Import dialog was deleted; recreating")
            self.import_dialog = ImportDialog(self.controller)

        self.import_dialog.raise_()
        self.import_dialog.activateWindow()
        self.import_dialog.show()

    def show_file_manager(self):
        """Show the FileManager dialog when the 'Manage Library' action is triggered."""
        if not hasattr(self, "file_manager"):
            self.file_manager = FileManager(self.controller)
            self.file_manager.library_modified.connect(self._refresh_all_views)

        self.file_manager.show()

    def toggle_fullscreen(self):
        """Toggle between fullscreen and normal window mode (Wayland/X11 safe)."""
        if self.windowState() & Qt.WindowFullScreen:
            self.setWindowState(self.windowState() & ~Qt.WindowFullScreen)
        else:
            self.setWindowState(self.windowState() | Qt.WindowFullScreen)

        QApplication.processEvents()
        self.repaint()

    def open_miniplayer(self):
        """Show or hide the mini-player window as an independent window."""
        if hasattr(self, "_mini_player") and self._mini_player:
            try:
                self._mini_player.close()
                self._mini_player.deleteLater()
            except RuntimeError:
                logger.debug("Mini player window was already deleted")
            self._mini_player = None

        logger.debug("Opening mini player window")
        self._mini_player = MiniPlayerWindow(self.controller)
        self._mini_player.setParent(None)

        main_window_pos = self.pos()
        main_window_size = self.size()
        self._mini_player.move(
            main_window_pos.x() + main_window_size.width() - 350,
            main_window_pos.y() + 50,
        )

        player = self.controller.mediaplayer
        player.track_changed.connect(self._mini_player._on_track_changed)
        player.state_changed.connect(self._mini_player._on_player_state_changed)

        self._mini_player.show()
        self._mini_player.raise_()

    def show_missing_tracks(self):
        self._missing_tracks = MissingTracks(self.controller, parent=self)
