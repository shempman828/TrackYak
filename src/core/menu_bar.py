"""Main-window menu bar mixin: menus, auto-hide behavior and the dialogs the menus open."""

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QCursor, QDesktopServices, QIcon, QKeySequence
from PySide6.QtWidgets import QApplication, QMessageBox

from src.analysis.analysis_dialog import AudioAnalysisDialog
from src.common.dialogs.alias_management_dialog import AliasManagementDialog
from src.core.startup_dialog import LICENSE_FILE
from src.foundation.asset_paths import ASSETS_DIR, icon
from src.foundation.config_setup import app_config
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message
from src.foundation.version import get_version
from src.importing.import_dialog import ImportDialog
from src.library.artwork.artwork_consistency_dialog import ArtworkConsistencyDialog
from src.library.duplicates.duplicate_finder import DuplicateFinderDialog
from src.library.missing_tracks import MissingTracks
from src.library.organize.organize_files_dialog import OrganizeFilesDialog
from src.lyrics.autotag.mood_autotag_dialog import MoodAutoTagDialog
from src.lyrics.autotag.place_song_about_review_dialog import PlaceSongAboutReviewDialog
from src.lyrics.explicit_recalc_worker import ExplicitRecalcWorker
from src.metadata.writers.metadata_writer_dialog import show_metadata_write_dialog
from src.player.equalizer.equalizer_dialog import EqualizerDialog
from src.player.ui.player_mini import MiniPlayerWindow
from src.statistics.statistics_dialog import MusicStatsDialog

_MENU_BAR_TRIGGER_SLACK = 5  # px below the menu bar that still counts as "near the top"


class MenuBar:
    """Mixin for GUI that builds the menu bar and owns the menu actions' dialogs."""

    def add_action(self, menu, text, icon_name=None, slot=None, shortcut=None, *, tooltip=None, checkable=False, checked=False, shortcut_context=None):
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

        self.add_action(file_menu, "Import Directory", "import.svg", self.show_import_dialog)
        self.add_action(file_menu, "View Library Statistics", "statistics.svg", self.show_statistics_dialog)

        file_menu.addSeparator()

        self.add_action(file_menu, "General Settings", "settings.svg", self.show_general_settings_dialog)

        file_menu.addSeparator()

        self.add_action(file_menu, "Exit", "exit.svg", self.close, shortcut="Ctrl+Q")

        # Audio Menu
        audio_menu = menu_bar.addMenu("Audio")

        self.add_action(audio_menu, "Equalizer Settings", "equalizer.svg", self.show_equalizer_dialog)
        self.add_action(audio_menu, "Audio File Analysis", "audio_analysis.svg", self.show_audio_properties)

        # Tools menu
        tools_menu = menu_bar.addMenu("Tools")

        self.add_action(
            tools_menu, "Organize Files…", icon_name="manage_library.svg", slot=self.show_organize_files, tooltip="Reorganize library files into a consistent AlbumArtist/Album/Track folder structure"
        )
        self.add_action(tools_menu, "Write Metadata…", icon_name="write.svg", slot=self.show_metadata_writer, tooltip="Push database metadata edits back into the audio files' embedded tags")

        tools_menu.addSeparator()

        self.add_action(tools_menu, "Manage Aliases…", slot=self.show_alias_management_dialog, tooltip="View and edit merge/split aliases and skipped genres")
        self.add_action(
            tools_menu, "Recalculate Explicit Flags…", slot=self.show_explicit_recalc, tooltip="Scan every track with lyrics but no Explicit setting yet, and flag it against assets/explicit_words.txt"
        )
        self.add_action(
            tools_menu, "Mood Tagging…", slot=self.show_mood_autotag_dialog, tooltip="Auto-tag tracks with moods and known places from their lyrics, and review lyrics words not yet assigned to a mood"
        )
        self.add_action(
            tools_menu,
            "Review Song-About Places…",
            slot=self.show_place_song_about_review_dialog,
            tooltip="Approve, change, or reject places lyric-detected in tracks' lyrics before they're linked -- your choice is remembered per place",
        )
        self.add_action(
            tools_menu, "Artwork Conflicts…", slot=self.show_artwork_consistency_dialog, tooltip="Scan for albums whose tracks disagree on embedded artwork and re-embed one version into every track"
        )

        tools_menu.addSeparator()

        self.add_action(tools_menu, "Find Duplicate Tracks", slot=self.show_duplicate_finder, tooltip="Scan library for possible duplicate tracks")
        self.add_action(tools_menu, "Find Missing Tracks", slot=self.show_missing_tracks, tooltip="Scan library for missing tracks")

        # View menu
        self.view_menu = menu_bar.addMenu("View")

        self.toggle_queue_action = self.add_action(
            self.view_menu, "Show Queue", "toggle_queue.svg", self.toggle_queue_visibility, shortcut="Shift+Q", checkable=True, checked=False, shortcut_context=Qt.ApplicationShortcut
        )

        self.add_action(self.view_menu, "Full Screen", "fullscreen.svg", self.toggle_fullscreen, shortcut="F11")
        self.add_action(self.view_menu, "Mini Player", slot=self.open_miniplayer, shortcut="Ctrl+M")

        self.view_menu.addSeparator()
        self.add_action(self.view_menu, "Reset Layout", slot=self._reset_ui_layout, tooltip="Restore the navigation, queue, and player panels to their default positions")

        # Help menu
        help_menu = menu_bar.addMenu("Help")

        self.add_action(help_menu, "About", slot=self.show_about_dialog)
        self.add_action(help_menu, "Support this Project")  # placeholder: no URL yet, see bugs.md

        wikipedia_url = "https://wikimediafoundation.org/give/?rdfrom=%2F%2Fdonate.wikimedia.org%2Fw%2Findex.php%3Ftitle%3DWays_to_Give%26redirect%3Dno#ways-to-give"
        self.add_action(help_menu, "Support Wikipedia", slot=lambda: QDesktopServices.openUrl(QUrl(wikipedia_url)))

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

    def _icon_exists(self, name: str) -> bool:
        """Safely check if an icon file exists before loading it."""
        try:
            return Path(ASSETS_DIR / name).exists()
        except (OSError, TypeError) as e:
            logger.debug(f"Icon existence check failed for {name}: {e}")
            return False

    # ------------------------------------------------------------------
    # Auto-hide helpers
    # ------------------------------------------------------------------

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
        """Turn menu bar auto-hide on (hide the bar and poll the cursor) or off (always show it)."""
        menu_bar = self.menuBar()

        if enabled:
            # Record the height while the bar is still visible; sizeHint() is 0 once it's hidden.
            h = menu_bar.sizeHint().height()
            if h > 0:
                self._menu_bar_known_height = h

            menu_bar.hide()
            self._menu_bar_poll_timer.start()
        else:
            self._menu_bar_hide_timer.stop()
            self._menu_bar_poll_timer.stop()
            menu_bar.show()

    def _cursor_in_menu_bar_region(self) -> bool:
        """Return True if the cursor is over the top strip where the menu bar sits."""
        # Use the stored height — sizeHint() returns 0 when the bar is hidden.
        trigger_height = self._menu_bar_known_height + _MENU_BAR_TRIGGER_SLACK
        local_pos = self.mapFromGlobal(QCursor.pos())
        return 0 <= local_pos.x() <= self.width() and 0 <= local_pos.y() <= trigger_height

    def _hide_menu_bar_if_mouse_gone(self):
        """Hide the bar after the grace period, unless auto-hide is off, a menu is open, or the cursor came back."""
        if not self._get_display_settings_auto_hide():
            return
        menu_bar = self.menuBar()
        if menu_bar.activeAction() is not None or self._cursor_in_menu_bar_region():
            return
        menu_bar.hide()

    def _check_mouse_for_menu_bar(self):
        """Show the auto-hidden menu bar while the cursor is near the top of the window."""
        # Polling (not QEvent.MouseMove) because most widgets here don't enable mouse tracking.
        if not self._get_display_settings_auto_hide():
            return

        # Only react while this window is the one actually on screen under
        # the cursor — otherwise another window overlapping the same screen
        # coordinates would spuriously pop the bar open.
        if not self.isActiveWindow():
            return

        menu_bar = self.menuBar()
        if self._cursor_in_menu_bar_region():
            self._menu_bar_hide_timer.stop()
            if not menu_bar.isVisible():
                menu_bar.show()
        elif menu_bar.isVisible() and not menu_bar.activeAction() and not self._menu_bar_hide_timer.isActive():
            self._menu_bar_hide_timer.start()

    # ------------------------------------------------------------------
    # Dialog helpers
    # ------------------------------------------------------------------

    def _show_singleton_dialog(self, attr: str, factory):
        """Create the dialog stored at self.<attr> on first use (or after Qt deleted it), then show and focus it."""
        dialog = getattr(self, attr, None)
        if dialog is not None:
            try:
                dialog.isVisible()
            except RuntimeError:
                logger.debug(f"{attr} was deleted; recreating")
                dialog = None
        if dialog is None:
            dialog = factory()
            setattr(self, attr, dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        return dialog

    # ------------------------------------------------------------------
    # Audio settings
    # ------------------------------------------------------------------

    def show_equalizer_dialog(self):
        """Show the equalizer configuration dialog."""
        self._show_singleton_dialog("equalizer_dialog", lambda: EqualizerDialog(self.controller.mediaplayer.equalizer, app_config, self))

    # ------------------------------------------------------------------
    # General Settings
    # ------------------------------------------------------------------

    def show_general_settings_dialog(self):
        """Open the modal General Settings dialog with the live DisplaySettings and player."""
        from src.core.config_dialog import ConfigDialog

        display_settings = self._resolve_display_settings()

        player = getattr(self, "mediaplayer", None)
        if player is None and hasattr(self, "controller"):
            player = getattr(self.controller, "mediaplayer", None)

        dialog = ConfigDialog(app_config, display_settings, player, self)

        if display_settings is not None:
            display_settings.menu_bar_auto_hide_changed.connect(self._apply_menu_bar_auto_hide)

        dialog.exec_()

        if display_settings is not None:
            try:
                display_settings.menu_bar_auto_hide_changed.disconnect(self._apply_menu_bar_auto_hide)
            except RuntimeError:
                logger.debug("menu_bar_auto_hide_changed signal was already disconnected")

    # ------------------------------------------------------------------
    # Other dialogs
    # ------------------------------------------------------------------

    def show_statistics_dialog(self):
        """Show the library statistics dialog."""
        self._show_singleton_dialog("statistics_dialog", lambda: MusicStatsDialog(self.controller, self))

    def show_alias_management_dialog(self):
        """Show the alias management dialog."""
        self._show_singleton_dialog("alias_management_dialog", lambda: AliasManagementDialog(self.controller, self))

    def show_explicit_recalc(self):
        """Start a background backfill of is_explicit for tracks with lyrics and no value yet."""
        # Only NULL values are filled; manual or earlier values are never overwritten (see ExplicitRecalcWorker).
        if getattr(self, "_explicit_recalc_worker", None) is not None:
            return
        show_status_message(self, "Recalculating explicit flags…", duration=0)
        self._explicit_recalc_worker = ExplicitRecalcWorker(self.controller)
        self._explicit_recalc_worker.finished.connect(self._on_explicit_recalc_finished)
        self._explicit_recalc_worker.error.connect(self._on_explicit_recalc_error)
        self._explicit_recalc_worker.start()

    def _on_explicit_recalc_finished(self, scanned: int, flagged: int):
        """Report the recalculation result and release the worker."""
        show_status_message(self, f"Explicit flags recalculated: {scanned} track(s) scanned, {flagged} flagged explicit")
        self._stop_explicit_recalc_worker()

    def _on_explicit_recalc_error(self, message: str):
        """Report a recalculation failure and release the worker."""
        show_status_message(self, f"Explicit flag recalculation failed: {message}")
        self._stop_explicit_recalc_worker()

    def _stop_explicit_recalc_worker(self):
        """Cancel and join the explicit-flag worker, if one exists."""
        worker = getattr(self, "_explicit_recalc_worker", None)
        if worker is None:
            return
        worker.request_cancel()
        worker.wait()
        self._explicit_recalc_worker = None

    def show_mood_autotag_dialog(self):
        """Show the mood auto-tagging dialog."""
        self._show_singleton_dialog("mood_autotag_dialog", lambda: MoodAutoTagDialog(self.controller, self))

    def show_place_song_about_review_dialog(self):
        """Refresh and show the song-about places review dialog."""
        self._show_singleton_dialog("place_song_about_review_dialog", lambda: PlaceSongAboutReviewDialog(self.controller, self)).refresh()

    def show_artwork_consistency_dialog(self):
        """Show the artwork conflicts dialog."""
        self._show_singleton_dialog("artwork_consistency_dialog", lambda: ArtworkConsistencyDialog(self.controller, self))

    def show_duplicate_finder(self):
        """Open the Duplicate Track Finder dialog."""
        self._show_singleton_dialog("duplicate_finder_dialog", lambda: DuplicateFinderDialog(self.controller, self))

    def show_about_dialog(self):
        """Show the About box with version and a link to the license file."""
        description = "TrackYak is a powerful application for tracking and managing your music library."
        license_url = QUrl.fromLocalFile(str(LICENSE_FILE)).toString()

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
            f"<p>{description}</p>"
            f"<hr>"
            f"<h3>License:</h3>"
            f"<p><a href='{license_url}'>View Full License Text</a></p>"
        )
        about_box.setTextInteractionFlags(Qt.TextBrowserInteraction)
        about_box.setStandardButtons(QMessageBox.Ok)
        about_box.setModal(True)
        about_box.exec_()

    def toggle_queue_visibility(self, checked):
        """Toggle queue dock visibility."""
        self.set_queue_visible(checked)

    def show_audio_properties(self):
        """Open the modal audio file analysis dialog."""
        AudioAnalysisDialog(self.controller, parent=self).exec_()

    def show_import_dialog(self):
        """Display the ImportDialog when the 'Import Directory' action is triggered."""
        self._show_singleton_dialog("import_dialog", lambda: ImportDialog(self.controller))

    def show_organize_files(self):
        """Show the Organize Files dialog when the Tools action is triggered."""

        def build():
            dialog = OrganizeFilesDialog(self.controller)
            # Parent it to the main window but keep it a separate top-level window.
            dialog.setParent(self, dialog.windowFlags())
            dialog.library_modified.connect(self._refresh_all_views)
            return dialog

        self._show_singleton_dialog("organize_files_dialog", build)

    def show_metadata_writer(self):
        """Open the metadata write dialog when the 'Write Metadata' action is triggered."""
        show_metadata_write_dialog(self.controller, self)

    def toggle_fullscreen(self):
        """Toggle between fullscreen and normal window mode (Wayland/X11 safe)."""
        if self.windowState() & Qt.WindowFullScreen:
            self.setWindowState(self.windowState() & ~Qt.WindowFullScreen)
        else:
            self.setWindowState(self.windowState() | Qt.WindowFullScreen)

        QApplication.processEvents()
        self.repaint()

    def _close_miniplayer(self) -> bool:
        """Close the mini player; return True if it was open and visible."""
        mini = getattr(self, "_mini_player", None)
        if mini is None:
            return False
        was_visible = False
        try:
            was_visible = mini.isVisible()
            mini.close()
            mini.deleteLater()
        except RuntimeError:
            logger.debug("Mini player window was already deleted")
        self._mini_player = None
        return was_visible

    def open_miniplayer(self):
        """Toggle the mini player: close it if it is showing, else open it as an independent window."""
        if self._close_miniplayer():
            return

        logger.debug("Opening mini player window")
        self._mini_player = MiniPlayerWindow(self.controller)
        self._mini_player.setParent(None)

        main_window_pos = self.pos()
        main_window_size = self.size()
        self._mini_player.move(main_window_pos.x() + main_window_size.width() - 350, main_window_pos.y() + 50)

        player = self.controller.mediaplayer
        player.track_changed.connect(self._mini_player._on_track_changed)
        player.state_changed.connect(self._mini_player._on_player_state_changed)

        self._mini_player.show()
        self._mini_player.raise_()

    def show_missing_tracks(self):
        """Scan for tracks whose files are missing and show them."""
        self._missing_tracks = MissingTracks(self.controller, parent=self)
