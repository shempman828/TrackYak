# display_settings.py
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from src.core.asset_paths import resolve_theme_assets
from src.core.logger_config import logger


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
            # Normalize falsy values (None, "") to None so callers can rely on truthiness
            theme = config.get_display_theme()
            self.theme_name: str | None = theme if theme else None
            self.theme_dir = Path(config.themes_dir)

            # Load menu bar auto-hide from config (defaults to False = always visible)
            self.menu_bar_auto_hide: bool = config.config.getboolean(
                "display", "menu_bar_auto_hide", fallback=False
            )

            # Explicit-content display options
            self.blur_explicit_art: bool = config.get_blur_explicit_art()
            self.censor_explicit_words: bool = config.get_censor_explicit_words()
        else:
            # Default settings
            self.theme_dir = Path("themes")
            self.theme_name: str | None = None
            self.ui_scale: float = 1.0
            self.font_family: str = "Inter"
            self.font_size: int = 10
            self.menu_bar_auto_hide: bool = False
            self.blur_explicit_art: bool = False
            self.censor_explicit_words: bool = False

        # ----- preview state -----
        # Snapshot of committed values, populated when a preview is started.
        # None means no preview is in progress.
        self._pre_preview: dict | None = None

    # ---------------------------------------------------------
    # Theme handling
    # ---------------------------------------------------------

    def set_theme(self, theme_name: str):
        """Load and apply a QSS theme by name. Expects <theme_name>.qss in theme_dir."""
        qss_path = self.theme_dir / f"{theme_name}.qss"

        if not qss_path.exists():
            raise FileNotFoundError(f"Theme not found: {qss_path}")

        qss = qss_path.read_text(encoding="utf-8")

        self.app.setStyleSheet(resolve_theme_assets(qss))
        self.theme_name = theme_name

        if self.config:
            self.config.set_display_theme(theme_name)
            self.config.save()

        logger.info(f"Applied theme: {theme_name}")
        self.display_changed.emit()

    # ---------------------------------------------------------
    # UI scale
    # ---------------------------------------------------------

    def set_ui_scale(self, scale: float):
        """Set UI scale factor and persist immediately. Typical values: 0.9, 1.0, 1.1, 1.25"""
        self.ui_scale = scale

        if self.config:
            self.config.set_ui_scale(scale)
            self.config.save()

        self._apply_font()
        self.display_changed.emit()

    def preview_ui_scale(self, scale: float):
        """Apply a scale change live without persisting it.

        Call commit_preview() to save or revert_preview() to undo.
        Multiple preview_* calls in sequence are all reverted together by
        a single revert_preview().
        """
        self._snapshot_for_preview()
        self.ui_scale = scale
        self._apply_font()
        self.display_changed.emit()

    # ---------------------------------------------------------
    # Font handling
    # ---------------------------------------------------------

    def set_font_family(self, family: str):
        """Set font family and persist immediately."""
        self.font_family = family

        if self.config:
            self.config.set_font_family(family)
            self.config.save()

        self._apply_font()
        self.display_changed.emit()

    def set_font_size(self, size: int):
        """Set font size and persist immediately."""
        self.font_size = size

        if self.config:
            self.config.set_font_size(size)
            self.config.save()

        self._apply_font()
        self.display_changed.emit()

    def preview_font_family(self, family: str):
        """Apply a font-family change live without persisting it.

        Call commit_preview() to save or revert_preview() to undo.
        """
        self._snapshot_for_preview()
        self.font_family = family
        self._apply_font()
        self.display_changed.emit()

    def preview_font_size(self, size: int):
        """Apply a font-size change live without persisting it.

        Call commit_preview() to save or revert_preview() to undo.
        """
        self._snapshot_for_preview()
        self.font_size = size
        self._apply_font()
        self.display_changed.emit()

    def _apply_font(self):
        """Apply scaled font globally."""
        scaled_size = max(1, int(self.font_size * self.ui_scale))
        font = QFont(self.font_family)
        font.setPointSize(scaled_size)
        self.app.setFont(font)

    # ---------------------------------------------------------
    # Preview commit / revert
    # ---------------------------------------------------------

    def _snapshot_for_preview(self):
        """Record current committed values the first time a preview begins."""
        if self._pre_preview is None:
            self._pre_preview = {
                "ui_scale": self.ui_scale,
                "font_family": self.font_family,
                "font_size": self.font_size,
            }

    def commit_preview(self):
        """Persist whatever font/scale values are currently live to config.

        No-op if no preview is in progress.
        """
        if self._pre_preview is None:
            return

        self._pre_preview = None

        if self.config:
            self.config.set_ui_scale(self.ui_scale)
            self.config.set_font_family(self.font_family)
            self.config.set_font_size(self.font_size)
            self.config.save()

    def revert_preview(self):
        """Restore font/scale to their last committed values and re-apply.

        No-op if no preview is in progress.
        """
        if self._pre_preview is None:
            return

        self.ui_scale = self._pre_preview["ui_scale"]
        self.font_family = self._pre_preview["font_family"]
        self.font_size = self._pre_preview["font_size"]
        self._pre_preview = None

        self._apply_font()
        self.display_changed.emit()

    @property
    def preview_pending(self) -> bool:
        """True while a preview is in progress (i.e. commit_preview / revert_preview expected)."""
        return self._pre_preview is not None

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

        logger.debug(f"Menu bar auto-hide set to {enabled}")
        # Notify the main window so it can activate/deactivate the behavior
        self.menu_bar_auto_hide_changed.emit(enabled)
        self.display_changed.emit()

    def get_menu_bar_auto_hide(self) -> bool:
        """Return whether menu bar auto-hide is currently enabled."""
        return self.menu_bar_auto_hide

    # ---------------------------------------------------------
    # Explicit-content display options
    # ---------------------------------------------------------

    def set_blur_explicit_art(self, enabled: bool):
        """Enable or disable blurring of album art marked explicit."""
        self.blur_explicit_art = enabled

        if self.config:
            self.config.set_blur_explicit_art(enabled)
            self.config.save()

        self.display_changed.emit()

    def get_blur_explicit_art(self) -> bool:
        """Return whether explicit album art should be shown blurred."""
        return self.blur_explicit_art

    def set_censor_explicit_words(self, enabled: bool):
        """Enable or disable censoring of explicit words throughout the app."""
        self.censor_explicit_words = enabled

        if self.config:
            self.config.set_censor_explicit_words(enabled)
            self.config.save()

        self.display_changed.emit()

    def get_censor_explicit_words(self) -> bool:
        """Return whether explicit words should be censored throughout the app."""
        return self.censor_explicit_words

    # ---------------------------------------------------------
    # Bulk apply (useful on startup)
    # ---------------------------------------------------------

    def apply_all(self):
        """Re-apply all current settings. Call once during app startup."""
        if self.theme_name:
            try:
                self.set_theme(self.theme_name)
            except FileNotFoundError:
                # Stored theme is gone — fall back gracefully and clear the
                # stale name so we don't retry it on the next startup.
                logger.warning(f"Stored theme {self.theme_name!r} not found; clearing")
                self.theme_name = None
                if self.config:
                    self.config.set_display_theme("")
                    self.config.save()

        # Always apply the font: the QSS theme does not set the app font.
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
            "scaled_font_size": max(1, int(self.font_size * self.ui_scale)),
            "menu_bar_auto_hide": self.menu_bar_auto_hide,
            "blur_explicit_art": self.blur_explicit_art,
            "censor_explicit_words": self.censor_explicit_words,
        }

    def get_available_themes(self) -> list[str]:
        """Get sorted list of available theme names (stems of *.qss files)."""
        if not self.theme_dir.exists():
            return []
        return sorted(f.stem for f in self.theme_dir.glob("*.qss"))
