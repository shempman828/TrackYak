# mini_player_integration.py

from PySide6.QtWidgets import QAction

from src.player_mini import MiniPlayerWindow


class MiniPlayerManager:
    """Manages the mini-player window and its integration with the main app."""

    def __init__(self, main_window, controller):
        self.main_window = main_window
        self.controller = controller
        self.mini_player = None

    def create_mini_player_action(self):
        """Create and return an action to toggle the mini-player."""
        action = QAction("Show Mini Player", self.main_window)
        action.setCheckable(True)
        action.triggered.connect(self.toggle_mini_player)

        # Add keyboard shortcut
        action.setShortcut("Ctrl+M")

        return action

    def toggle_mini_player(self, checked):
        """Show or hide the mini-player."""
        if checked:
            self.show_mini_player()
        else:
            self.hide_mini_player()

    def show_mini_player(self):
        """Create and show the mini-player window."""
        if not self.mini_player:
            self.mini_player = MiniPlayerWindow(self.controller, self.main_window)
            # Position it near the main window
            main_pos = self.main_window.pos()
            self.mini_player.move(main_pos.x() + 100, main_pos.y() + 100)

        self.mini_player.show()

    def hide_mini_player(self):
        """Hide the mini-player window."""
        if self.mini_player:
            self.mini_player.hide()
            # Don't delete, just hide for reuse

    def cleanup(self):
        """Clean up resources when closing."""
        if self.mini_player:
            self.mini_player.close()
            self.mini_player.deleteLater()
