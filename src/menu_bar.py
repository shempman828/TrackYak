import os

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence
from PySide6.QtWidgets import QApplication, QMessageBox

from analysis_dialog import AudioAnalysisDialog
from asset_paths import icon
from config_setup import app_config
from display_dialog import DisplaySettingsDialog
from equalizer_dialog import EqualizerDialog
from file_manager_dialog import FileManager
from import_dialog import ImportDialog
from player_mini import MiniPlayerWindow
from player_settings import show_audio_settings_dialog
from statistics_dialog import MusicStatsDialog


class MenuBar:
    def _init_menu_bar(self):
        """Create the main menu bar without navigation-specific actions."""
        menu_bar = self.menuBar()

        # File menu
        file_menu = menu_bar.addMenu("File")
        # Import Directory Action
        import_action = QAction("Import Directory", self)
        import_action.setIcon(QIcon(icon("import.svg")))
        import_action.triggered.connect(self.show_import_dialog)
        file_menu.addAction(import_action)

        # File Manager Action
        file_manager_action = QAction("Manage Library", self)
        file_manager_action.setIcon(QIcon(icon("manage_library.svg")))
        file_manager_action.triggered.connect(self.show_file_manager)
        file_menu.addAction(file_manager_action)

        # Library Statistics
        statistics_action = QAction("View Library Statistics", self)
        statistics_action.setIcon(QIcon(icon("statistics.svg")))
        statistics_action.triggered.connect(self.show_statistics_dialog)
        file_menu.addAction(statistics_action)

        file_menu.addSeparator()
        file_menu.addSeparator()

        exit_action = QAction("Exit", self)
        exit_action.setShortcut(QKeySequence("Ctrl+Q"))
        exit_action.setIcon(QIcon(icon("exit.svg")))
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # Audio Menu
        audio_menu = menu_bar.addMenu("Audio")
        # Audio Settings Action
        audio_settings_action = QAction("Manage Audio Settings", self)
        audio_settings_action.setIcon(QIcon(icon("audio_settings.svg")))
        audio_settings_action.triggered.connect(self.show_audio_settings_dialog)
        audio_menu.addAction(audio_settings_action)

        # equalizer
        equalizer_action = QAction("Equalizer Settings", self)
        equalizer_action.setIcon(QIcon(icon("equalizer.svg")))
        equalizer_action.triggered.connect(self.show_equalizer_dialog)
        audio_menu.addAction(equalizer_action)

        # Audio Properties Action
        audio_props_action = QAction("Audio File Analysis", self)
        audio_props_action.setIcon(QIcon(icon("audio_analysis.svg")))
        audio_props_action.triggered.connect(self.show_audio_properties)
        audio_menu.addAction(audio_props_action)

        # View menu
        self.view_menu = menu_bar.addMenu("View")

        # Display Settings
        display_settings_action = QAction("Display Settings", self)
        display_settings_action.setIcon(QIcon(icon("display_settings.svg")))
        display_settings_action.triggered.connect(self.show_display_settings_dialog)
        self.view_menu.addAction(display_settings_action)

        # Add queue visibility toggle
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

        # miniplayer toggle
        miniplayer_action = QAction("Mini Player", self)
        miniplayer_action.setShortcut(QKeySequence("Ctrl+M"))
        miniplayer_action.triggered.connect(self.open_miniplayer)
        self.view_menu.addAction(miniplayer_action)

        # Help menu
        help_menu = menu_bar.addMenu("Help")

        # About
        about_action = QAction("About", self)
        about_action.triggered.connect(self.show_about_dialog)
        help_menu.addAction(about_action)

        # support project
        support_action = QAction("Support this Project", self)
        help_menu.addAction(support_action)

        # support Wikipedia
        wikipedia_url = "https://wikimediafoundation.org/give/?rdfrom=%2F%2Fdonate.wikimedia.org%2Fw%2Findex.php%3Ftitle%3DWays_to_Give%26redirect%3Dno#ways-to-give"
        wikipedia_action = QAction("Support Wikipedia", self)
        wikipedia_action.triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl(wikipedia_url))
        )
        help_menu.addAction(wikipedia_action)

    def show_audio_settings_dialog(self):
        # The dialog will handle all the settings automatically
        settings_applied = show_audio_settings_dialog(self.mediaplayer, self)

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

    def show_statistics_dialog(self):
        if not hasattr(self, "statistics_dialog"):
            self.statistics_dialog = MusicStatsDialog(self.controller, self)
        self.statistics_dialog.show()
        self.statistics_dialog.raise_()
        self.statistics_dialog.activateWindow()

    def show_about_dialog(self):
        # Program description
        description = """TrackYak is a powerful application for tracking and managing your music library."""

        # Create the about message box
        about_box = QMessageBox(self)
        about_box.setWindowTitle("About TrackYak")
        about_box.setIcon(QMessageBox.Information)

        # Set the main text with program title and developer
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

        # Make links clickable
        about_box.setTextInteractionFlags(Qt.TextBrowserInteraction)

        # Set standard OK button
        about_box.setStandardButtons(QMessageBox.Ok)

        # Optional: Set the dialog to be modal
        about_box.setModal(True)

        # Show the dialog
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
        # Check if we need to create a new dialog
        if not hasattr(self, "import_dialog"):
            self.import_dialog = ImportDialog(self.controller)

        # Check if the C++ object was deleted (happens with WA_DeleteOnClose)
        try:
            # This will raise RuntimeError if the C++ object was deleted
            self.import_dialog.isVisible()
        except RuntimeError:
            # Recreate the dialog
            self.import_dialog = ImportDialog(self.controller)

        # Bring the dialog to the front
        self.import_dialog.raise_()
        self.import_dialog.activateWindow()
        self.import_dialog.show()

    def show_file_manager(self):
        """Show the FileManager dialog when the 'Manage Library' action is triggered"""
        if not hasattr(self, "file_manager"):
            # Lazy initialization of the FileManager
            self.file_manager = FileManager(self.controller)
            # Connect the library modified signal
            self.file_manager.library_modified.connect(self._refresh_all_views)

        self.file_manager.show()

    def toggle_fullscreen(self):
        """Toggle between fullscreen and normal window mode (Wayland/X11 safe)."""
        # Use setWindowState instead of showFullScreen/showNormal (works better on Wayland)
        if self.windowState() & Qt.WindowFullScreen:
            self.setWindowState(self.windowState() & ~Qt.WindowFullScreen)
        else:
            self.setWindowState(self.windowState() | Qt.WindowFullScreen)

        # Ensure Qt processes the state change immediately
        QApplication.processEvents()

        # Force a repaint in case the compositor leaves a blank surface
        self.repaint()

    def open_miniplayer(self):
        """Show or hide the mini-player window as an independent window."""
        # Always create a new instance - simplest approach
        if hasattr(self, "_mini_player") and self._mini_player:
            # Try to close existing one
            try:
                self._mini_player.close()
                self._mini_player.deleteLater()
            except RuntimeError:
                pass
            self._mini_player = None

        # Create new instance
        self._mini_player = MiniPlayerWindow(self.controller)

        # Force it to be a top-level window
        self._mini_player.setParent(None)  # THIS IS KEY!

        # Position it
        main_window_pos = self.pos()
        main_window_size = self.size()
        self._mini_player.move(
            main_window_pos.x() + main_window_size.width() - 350,
            main_window_pos.y() + 50,
        )

        # Connect signals
        player = self.controller.mediaplayer
        player.track_changed.connect(self._mini_player._on_track_changed)
        player.state_changed.connect(self._mini_player._on_player_state_changed)

        # Show it
        self._mini_player.show()
        self._mini_player.raise_()

    def show_display_settings_dialog(self):
        """Show the display settings dialog."""
        # Check if display_settings is available (depends on your app structure)
        # Assuming it's available as self.display or through app_config
        display_settings = None

        # Try different possible locations for display settings
        if hasattr(self, "display_settings"):
            display_settings = self.display_settings
        elif hasattr(self, "controller") and hasattr(
            self.controller, "display_settings"
        ):
            display_settings = self.controller.display_settings
        elif hasattr(app_config, "display_settings"):
            display_settings = app_config.display_settings

        if display_settings is None:
            # Create a DisplaySettings instance if needed
            from display_settings import DisplaySettings

            display_settings = DisplaySettings()

        # Create and show the dialog
        dialog = DisplaySettingsDialog(display_settings, self)
        dialog.exec_()
