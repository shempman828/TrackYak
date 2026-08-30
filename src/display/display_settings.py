# display_settings.py
from pathlib import Path
import re
import weakref

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QWidget

from src.core.asset_paths import resolve_theme_assets
from src.core.logger_config import logger

# QSS properties whose px values should track the UI scale slider.
_SCALABLE_QSS_PROPS = (
    "font-size",
    "min-width",
    "max-width",
    "width",
    "min-height",
    "max-height",
    "height",
    "border-top-left-radius",
    "border-top-right-radius",
    "border-bottom-left-radius",
    "border-bottom-right-radius",
    "border-radius",
    "padding-top",
    "padding-right",
    "padding-bottom",
    "padding-left",
    "padding",
    "margin-top",
    "margin-right",
    "margin-bottom",
    "margin-left",
    "margin",
    "border-top",
    "border-bottom",
    "border-left",
    "border-right",
    "border",
    "outline-offset",
    "outline",
    "letter-spacing",
    "spacing",
)

_SCALABLE_QSS_PATTERN = re.compile(r"\b(" + "|".join(_SCALABLE_QSS_PROPS) + r")(\s*:\s*)([^;}]+)")
_PX_VALUE_PATTERN = re.compile(r"(-?\d+(?:\.\d+)?)px\b")


def scale_qss_pixel_values(qss: str, scale: float) -> str:
    """Scale px-valued sizing properties (font-size, padding, margins,
    widths/heights, border-radius, etc.) in a stylesheet by `scale`.

    Scales every px value in a property, not just the first -- shorthand
    like "padding: 2px 8px;" or "border-radius: 8px 8px 0 0;" needs each
    value scaled independently.

    Always pass the theme's original, unscaled QSS text — scaling an
    already-scaled stylesheet would compound the factor on every call.
    """

    def _replace_px(match: re.Match) -> str:
        scaled = float(match.group(1)) * scale
        text = f"{scaled:.2f}".rstrip("0").rstrip(".")
        return f"{text}px"

    def _replace_prop(match: re.Match) -> str:
        prop, sep, value = match.group(1), match.group(2), match.group(3)
        return f"{prop}{sep}{_PX_VALUE_PATTERN.sub(_replace_px, value)}"

    return _SCALABLE_QSS_PATTERN.sub(_replace_prop, qss)


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

        # Unscaled QSS text of the currently loaded theme, re-scaled and
        # re-applied whenever ui_scale changes. None if no theme is loaded.
        self._raw_qss: str | None = None

        # widget -> unscaled inline stylesheet, for widgets styled via
        # style_widget() instead of the theme QSS. Weak-keyed so a widget
        # is dropped automatically once nothing else references it.
        self._styled_widgets: weakref.WeakKeyDictionary[QWidget, str] = weakref.WeakKeyDictionary()

    # ---------------------------------------------------------
    # Theme handling
    # ---------------------------------------------------------

    def set_theme(self, theme_name: str):
        """Load and apply a QSS theme by name. Expects <theme_name>.qss in theme_dir."""
        qss_path = self.theme_dir / f"{theme_name}.qss"

        if not qss_path.exists():
            raise FileNotFoundError(f"Theme not found: {qss_path}")

        qss = qss_path.read_text(encoding="utf-8")

        self._raw_qss = resolve_theme_assets(qss)
        self._apply_stylesheet()
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
        self._apply_stylesheet()
        self._reapply_styled_widgets()
        self.display_changed.emit()

    def preview_ui_scale_in(self, scale: float, roots: list[QWidget]):
        """Live-preview a scale change against only the given widget
        subtrees, instead of the whole app.

        `set_ui_scale()` calls `QApplication.setStyleSheet()`, which
        re-polishes every live widget under the app -- including ones
        sitting hidden behind another tab. For a widget tree that grows
        with the library (e.g. the album grid, which never destroys
        off-screen cards), that re-polish is O(total widget count) and can
        take tens of seconds even though nothing outside `roots` is even
        visible. Setting the same scaled stylesheet directly on just the
        on-screen roots cascades to their descendants and looks identical,
        for a fraction of the cost.

        Does not persist and does not touch widgets outside `roots` --
        call set_ui_scale() once to commit for real (this also corrects
        every widget this preview skipped).
        """
        self.ui_scale = scale
        self._apply_font()
        if self._raw_qss is not None:
            scaled_qss = scale_qss_pixel_values(self._raw_qss, scale)
            for root in roots:
                root.setStyleSheet(scaled_qss)
        self._reapply_styled_widgets()

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

    def _apply_font(self):
        """Apply scaled font globally."""
        scaled_size = max(1, int(self.font_size * self.ui_scale))
        font = QFont(self.font_family)
        font.setPointSize(scaled_size)
        self.app.setFont(font)

    def _apply_stylesheet(self):
        """Re-scale the loaded theme's raw QSS by ui_scale and apply it.

        No-op if no theme is loaded (self._raw_qss is None).
        """
        if self._raw_qss is None:
            return
        self.app.setStyleSheet(scale_qss_pixel_values(self._raw_qss, self.ui_scale))

    # ---------------------------------------------------------
    # Per-widget inline styles
    # ---------------------------------------------------------

    def style_widget(self, widget: QWidget, qss: str):
        """Scale-aware replacement for widget.setStyleSheet() on one-off
        inline styles (e.g. a caption's "font-size: 10px;").

        The widget is tracked via a weak reference and automatically
        re-scaled and re-applied whenever ui_scale changes, so callers
        don't need to reconnect to display_changed themselves. Nothing
        to clean up when the widget is destroyed -- the weak reference
        drops on its own.
        """
        self._styled_widgets[widget] = qss
        widget.setStyleSheet(scale_qss_pixel_values(qss, self.ui_scale))

    def _reapply_styled_widgets(self):
        """Re-scale and re-apply every widget registered via style_widget()."""
        for widget, qss in list(self._styled_widgets.items()):
            try:
                widget.setStyleSheet(scale_qss_pixel_values(qss, self.ui_scale))
            except RuntimeError:
                # Underlying C++ widget was destroyed without the Python
                # wrapper being collected yet -- drop it and move on.
                del self._styled_widgets[widget]

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
            self.config.config.set("display", "menu_bar_auto_hide", str(enabled).lower())
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


def apply_scaled_style(widget: QWidget, qss: str) -> None:
    """Scale-aware replacement for widget.setStyleSheet() on one-off inline
    styles -- use this anywhere a widget gets a literal stylesheet string
    with sizing properties (font-size, padding, etc.) so it stays in sync
    with the UI scale slider.

    Falls back to a plain, unscaled setStyleSheet() if no app-wide
    DisplaySettings is attached (e.g. tests that build widgets without
    going through run.py's bootstrap) -- there's nothing to track or
    rescale against in that case.
    """
    display_settings = getattr(QApplication.instance(), "display_settings", None)
    if display_settings is None:
        widget.setStyleSheet(qss)
        return
    display_settings.style_widget(widget, qss)
