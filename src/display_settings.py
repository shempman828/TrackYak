# display_settings.py
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication


class DisplaySettings(QObject):
    """
    Centralized display settings manager.

    Responsibilities:
    - Load and apply QSS themes
    - Control global font and font size
    - Apply UI scaling
    - Control menu bar auto-hide behavior
    """

    display_changed = Signal()
    # Emitted specifically when auto-hide changes so the menu bar can respond immediately
    menu_bar_auto_hide_changed = Signal(bool)

    def __init__(self, app=None, config=None):
        super().__init__()

        # Get QApplication instance if not provided
        self.app = app or QApplication.instance()
        if self.app is None:
            raise RuntimeError("No QApplication instance available")

        self.config = config

        # If config is provided, load settings from it
        if config:
            self.ui_scale = float(config.get_ui_scale())
            self.font_family = config.get_font_family()
            self.font_size = int(config.get_font_size())
            self.theme_name = config.get_display_theme()
            self.theme_dir = Path(config.themes_dir)

            # Load menu bar auto-hide from config (defaults to False = always visible)
            self.menu_bar_auto_hide: bool = config.config.getboolean(
                "display", "menu_bar_auto_hide", fallback=False
            )
        else:
            # Default settings
            self.theme_dir = Path("themes")
            self.theme_name: str | None = None
            self.ui_scale: float = 1.0
            self.font_family: str = "Inter"
            self.font_size: int = 10
            self.menu_bar_auto_hide: bool = False

    # ---------------------------------------------------------
    # Theme handling
    # ---------------------------------------------------------

    def set_theme(self, theme_name: str):
        """Load and apply a QSS theme by name. Expects <theme_name>.qss in theme_dir."""
        qss_path = self.theme_dir / f"{theme_name}.qss"

        if not qss_path.exists():
            raise FileNotFoundError(f"Theme not found: {qss_path}")

        qss = qss_path.read_text(encoding="utf-8")

        self.app.setStyleSheet(qss)
        self.theme_name = theme_name

        if self.config:
            self.config.set_display_theme(theme_name)
            self.config.save()

        self.display_changed.emit()

    # ---------------------------------------------------------
    # UI scale
    # ---------------------------------------------------------

    def set_ui_scale(self, scale: float):
        """Set UI scale factor. Typical values: 0.9, 1.0, 1.1, 1.25"""
        self.ui_scale = scale

        if self.config:
            self.config.set_ui_scale(scale)
            self.config.save()

        self._apply_font()
        self.display_changed.emit()

    # ---------------------------------------------------------
    # Font handling
    # ---------------------------------------------------------

    def set_font_family(self, family: str):
        self.font_family = family

        if self.config:
            self.config.set_font_family(family)
            self.config.save()

        self._apply_font()
        self.display_changed.emit()

    def set_font_size(self, size: int):
        self.font_size = size

        if self.config:
            self.config.set_font_size(size)
            self.config.save()

        self._apply_font()
        self.display_changed.emit()

    def _apply_font(self):
        """Apply scaled font globally."""
        scaled_size = int(self.font_size * self.ui_scale)
        font = QFont(self.font_family)
        font.setPointSize(scaled_size)
        self.app.setFont(font)

    # ---------------------------------------------------------
    # Menu bar auto-hide
    # ---------------------------------------------------------

    def set_menu_bar_auto_hide(self, enabled: bool):
        """
        Enable or disable menu bar auto-hide.
        When enabled, the menu bar hides until the user moves the mouse over it.
        The setting is saved to config immediately.
        """
        self.menu_bar_auto_hide = enabled

        if self.config:
            self.config.config.set(
                "display", "menu_bar_auto_hide", str(enabled).lower()
            )
            self.config.save()

        # Notify the main window so it can activate/deactivate the behavior
        self.menu_bar_auto_hide_changed.emit(enabled)
        self.display_changed.emit()

    def get_menu_bar_auto_hide(self) -> bool:
        """Return whether menu bar auto-hide is currently enabled."""
        return self.menu_bar_auto_hide

    # ---------------------------------------------------------
    # Bulk apply (useful on startup)
    # ---------------------------------------------------------

    def apply_all(self):
        """Re-apply all current settings. Call once during app startup."""
        if self.theme_name:
            try:
                self.set_theme(self.theme_name)
            except FileNotFoundError:
                default_theme = "dark" if self.config else "default"
                if default_theme:
                    self.set_theme(default_theme)
        else:
            self._apply_font()

    # ---------------------------------------------------------
    # Getters
    # ---------------------------------------------------------

    def get_settings(self):
        """Get all current display settings as a dictionary."""
        return {
            "theme": self.theme_name,
            "ui_scale": self.ui_scale,
            "font_family": self.font_family,
            "font_size": self.font_size,
            "scaled_font_size": int(self.font_size * self.ui_scale),
            "menu_bar_auto_hide": self.menu_bar_auto_hide,
        }

    def get_available_themes(self):
        """Get list of available theme files."""
        theme_files = []
        if self.theme_dir.exists():
            for file in self.theme_dir.glob("*.qss"):
                theme_files.append(file.stem)
        return theme_files
