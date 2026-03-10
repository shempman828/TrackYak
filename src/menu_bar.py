import os

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence
from PySide6.QtWidgets import QApplication, QMessageBox

from src.analysis_dialog import AudioAnalysisDialog
from src.asset_paths import icon
from src.config_setup import app_config
from src.display_dialog import DisplaySettingsDialog
from src.duplicate_finder import DuplicateFinderDialog
from src.equalizer_dialog import EqualizerDialog
from src.file_manager_dialog import FileManager
from src.import_dialog import ImportDialog
from src.player_mini import MiniPlayerWindow
from src.player_settings import show_audio_settings_dialog
from src.statistics_dialog import MusicStatsDialog


class MenuBar:
    def _init_menu_bar(self):
        """Create the main menu bar without navigation-specific actions."""
        menu_bar = self.menuBar()

        # File menu
        file_menu = menu_bar.addMenu("File")

        import_action = QAction("Import Directory", self)
        import_action.setIcon(QIcon(icon("import.svg")))
        import_action.triggered.connect(self.show_import_dialog)
        file_menu.addAction(import_action)

        file_manager_action = QAction("Manage Library", self)
        file_manager_action.setIcon(QIcon(icon("manage_library.svg")))
        file_manager_action.triggered.connect(self.show_file_manager)
        file_menu.addAction(file_manager_action)

        statistics_action = QAction("View Library Statistics", self)
        statistics_action.setIcon(QIcon(icon("statistics.svg")))
        statistics_action.triggered.connect(self.show_statistics_dialog)
        file_menu.addAction(statistics_action)

        duplicate_action = QAction("Find Duplicate Tracks", self)
        duplicate_action.setToolTip("Scan library for possible duplicate tracks")
        duplicate_action.triggered.connect(self.show_duplicate_finder)
        file_menu.addAction(duplicate_action)

        file_menu.addSeparator()

        # General Settings — opens the full ConfigDialog
        general_settings_action = QAction("General Settings", self)
        general_settings_action.setIcon(
            QIcon(icon("settings.svg"))
            if self._icon_exists("settings.svg")
            else QIcon()
        )
        general_settings_action.triggered.connect(self.show_general_settings_dialog)
        file_menu.addAction(general_settings_action)

        file_menu.addSeparator()

        exit_action = QAction("Exit", self)
        exit_action.setShortcut(QKeySequence("Ctrl+Q"))
        exit_action.setIcon(QIcon(icon("exit.svg")))
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # Audio Menu
        audio_menu = menu_bar.addMenu("Audio")

        audio_settings_action = QAction("Manage Audio Settings", self)
        audio_settings_action.setIcon(QIcon(icon("audio_settings.svg")))
        audio_settings_action.triggered.connect(self.show_audio_settings_dialog)
        audio_menu.addAction(audio_settings_action)

        equalizer_action = QAction("Equalizer Settings", self)
        equalizer_action.setIcon(QIcon(icon("equalizer.svg")))
        equalizer_action.triggered.connect(self.show_equalizer_dialog)
        audio_menu.addAction(equalizer_action)

        audio_props_action = QAction("Audio File Analysis", self)
        audio_props_action.setIcon(QIcon(icon("audio_analysis.svg")))
        audio_props_action.triggered.connect(self.show_audio_properties)
        audio_menu.addAction(audio_props_action)

        # View menu
        self.view_menu = menu_bar.addMenu("View")

        display_settings_action = QAction("Display Settings", self)
        display_settings_action.setIcon(QIcon(icon("display_settings.svg")))
        display_settings_action.triggered.connect(self.show_display_settings_dialog)
        self.view_menu.addAction(display_settings_action)

        self.toggle_queue_action = QAction("Show Queue", self)
        self.toggle_queue_action.setCheckable(True)
        self.toggle_queue_action.setChecked(False)
        self.toggle_queue_action.setShortcut(QKeySequence("Shift+Q"))
        self.toggle_queue_action.setShortcutContext(Qt.ApplicationShortcut)
        self.toggle_queue_action.triggered.connect(self.toggle_queue_visibility)
        self.toggle_queue_action.setIcon(QIcon(icon("toggle_queue.svg")))
        self.view_menu.addAction(self.toggle_queue_action)

        fullscreen_action = QAction("Full Screen", self)
        fullscreen_action.setShortcut(QKeySequence("F11"))
        fullscreen_action.setIcon(QIcon(icon("fullscreen.svg")))
        fullscreen_action.triggered.connect(self.toggle_fullscreen)
        self.view_menu.addAction(fullscreen_action)

        miniplayer_action = QAction("Mini Player", self)
        miniplayer_action.setShortcut(QKeySequence("Ctrl+M"))
        miniplayer_action.triggered.connect(self.open_miniplayer)
        self.view_menu.addAction(miniplayer_action)

        # Help menu
        help_menu = menu_bar.addMenu("Help")

        about_action = QAction("About", self)
        about_action.triggered.connect(self.show_about_dialog)
        help_menu.addAction(about_action)

        support_action = QAction("Support this Project", self)
        help_menu.addAction(support_action)

        wikipedia_url = "https://wikimediafoundation.org/give/?rdfrom=%2F%2Fdonate.wikimedia.org%2Fw%2Findex.php%3Ftitle%3DWays_to_Give%26redirect%3Dno#ways-to-give"
        wikipedia_action = QAction("Support Wikipedia", self)
        wikipedia_action.triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl(wikipedia_url))
        )
        help_menu.addAction(wikipedia_action)

        # --- Menu bar auto-hide setup ---
        # A timer is used to add a small delay before hiding so the bar doesn't
        # flicker when the user moves between menus.
        self._menu_bar_hide_timer = QTimer(self)
        self._menu_bar_hide_timer.setSingleShot(True)
        self._menu_bar_hide_timer.setInterval(300)  # 300 ms grace period
        self._menu_bar_hide_timer.timeout.connect(self._hide_menu_bar_if_mouse_gone)

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
            path = icon(name)
            return bool(path) and os.path.exists(path)
        except Exception:
            return False

    def _get_display_settings_auto_hide(self) -> bool:
        """Read the current auto-hide preference from wherever DisplaySettings lives."""
        ds = self._resolve_display_settings()
        if ds is not None:
            return ds.get_menu_bar_auto_hide()
        return False

    def _resolve_display_settings(self):
        """Return the DisplaySettings instance, or None if unavailable."""
        if hasattr(self, "display_settings"):
            return self.display_settings
        if hasattr(self, "controller") and hasattr(self.controller, "display_settings"):
            return self.controller.display_settings
        if hasattr(app_config, "display_settings"):
            return app_config.display_settings
        return None

    def _apply_menu_bar_auto_hide(self, enabled: bool):
        """
        Turn auto-hide on or off.
        When ON:  the menu bar is hidden and a QApplication-level event filter
                  is installed so we catch mouse moves anywhere in the window,
                  including the area where the hidden menu bar used to be.
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

            # Install a QApplication-level event filter.
            # This is the key fix: a plain mouseMoveEvent on the main window
            # is NOT delivered when the mouse is over the hidden menu bar area,
            # but an app-level filter sees every mouse move in the process.
            QApplication.instance().installEventFilter(self)
        else:
            # Stop the hide timer and make sure the bar is visible
            self._menu_bar_hide_timer.stop()
            menu_bar.show()
            QApplication.instance().removeEventFilter(self)

    def _hide_menu_bar_if_mouse_gone(self):
        """Called by the timer — hides the bar only if auto-hide is still on."""
        if self._get_display_settings_auto_hide():
            self.menuBar().hide()

    def eventFilter(self, watched, event):
        """
        App-level event filter used for menu bar auto-show on hover.

        We listen for MouseMove events on any widget that belongs to this
        window. When the mouse is near the top of the window the bar appears;
        moving away starts the hide timer.

        This replaces the old mouseMoveEvent override because mouseMoveEvent
        is NOT reliably delivered over the region where a hidden menu bar
        used to be — the app-level filter has no such blind spot.
        """
        from PySide6.QtCore import QEvent

        if event.type() == QEvent.MouseMove and self._get_display_settings_auto_hide():
            # Only act on events that belong to widgets inside this window.
            if isinstance(watched, QApplication.__class__):
                pass  # should not happen, but guard anyway
            widget = watched if hasattr(watched, "window") else None
            if widget is not None and widget.window() is not self:
                return super().eventFilter(watched, event)

            menu_bar = self.menuBar()
            # Use the stored height — sizeHint() returns 0 when the bar is hidden.
            trigger_height = self._menu_bar_known_height + 5

            # Map the mouse position to this window's coordinate system.
            try:
                global_pos = event.globalPosition().toPoint()
            except AttributeError:
                global_pos = event.globalPos()
            local_pos = self.mapFromGlobal(global_pos)
            y = local_pos.y()

            if y <= trigger_height:
                # Mouse is near the top — show the bar and cancel any pending hide.
                self._menu_bar_hide_timer.stop()
                if not menu_bar.isVisible():
                    menu_bar.show()
            else:
                # Mouse moved away — start the grace-period timer.
                if menu_bar.isVisible() and not menu_bar.activeAction():
                    self._menu_bar_hide_timer.start()

        return super().eventFilter(watched, event)

    # ------------------------------------------------------------------
    # Audio settings
    # ------------------------------------------------------------------

    def show_audio_settings_dialog(self):
        """Open the audio settings dialog. Uses controller.mediaplayer if available."""
        # Support both self.mediaplayer and self.controller.mediaplayer
        player = getattr(self, "mediaplayer", None)
        if player is None and hasattr(self, "controller"):
            player = getattr(self.controller, "mediaplayer", None)

        if player is None:
            self.statusBar().showMessage("Audio player not available", 3000)
            return

        settings_applied = show_audio_settings_dialog(player, self)

        if settings_applied:
            self.statusBar().showMessage("Audio settings updated", 3000)
        else:
            self.statusBar().showMessage("Audio settings unchanged", 3000)

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
        """Open the General Settings (ConfigDialog) window."""
        from src.config_dialog import ConfigDialog

        dialog = ConfigDialog(app_config, self)
        dialog.exec_()

    # ------------------------------------------------------------------
    # Other dialogs
    # ------------------------------------------------------------------

    def show_statistics_dialog(self):
        if not hasattr(self, "statistics_dialog"):
            self.statistics_dialog = MusicStatsDialog(self.controller, self)
        self.statistics_dialog.show()
        self.statistics_dialog.raise_()
        self.statistics_dialog.activateWindow()

    def show_duplicate_finder(self):
        """Open the Duplicate Track Finder dialog."""
        dialog = DuplicateFinderDialog(self.controller)
        dialog.exec_()

    def show_about_dialog(self):
        description = """TrackYak is a powerful application for tracking and managing your music library."""

        about_box = QMessageBox(self)
        about_box.setWindowTitle("About TrackYak")
        about_box.setIcon(QMessageBox.Information)
        about_box.setTextFormat(Qt.RichText)
        about_box.setText(
            f"<h2>TrackYak</h2>"
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
        if not hasattr(self, "queue_dock"):
            return

        self.queue_dock.setVisible(checked)
        if checked:
            self.addDockWidget(Qt.RightDockWidgetArea, self.queue_dock)
            self.queue_dock.raise_()
        self.toggle_queue_action.setChecked(checked)

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
                pass
            self._mini_player = None

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

    def show_display_settings_dialog(self):
        """Show the display settings dialog."""
        display_settings = self._resolve_display_settings()

        if display_settings is None:
            from src.display_settings import DisplaySettings

            display_settings = DisplaySettings()

        dialog = DisplaySettingsDialog(display_settings, self)

        # When auto-hide changes inside the dialog, apply it immediately
        display_settings.menu_bar_auto_hide_changed.connect(
            self._apply_menu_bar_auto_hide
        )

        dialog.exec_()

        # Disconnect after dialog closes to avoid duplicate connections next time
        try:
            display_settings.menu_bar_auto_hide_changed.disconnect(
                self._apply_menu_bar_auto_hide
            )
        except RuntimeError:
            pass  # Signal was already disconnected
