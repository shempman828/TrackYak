"""App-wide theme, font, UI-scale and display-option manager."""

import math
import re
import weakref

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QWidget

from src.foundation.asset_paths import THEMES_DIR, resolve_theme_assets
from src.foundation.logger_config import logger

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

MIN_UI_SCALE = 0.5
MAX_UI_SCALE = 3.0


def scale_qss_pixel_values(qss: str, scale: float) -> str:
    """Scale every px value of the sizing properties in a stylesheet by `scale`."""
    # Always pass the original, unscaled QSS: scaling scaled text compounds the factor.

    def _replace_px(match: re.Match) -> str:
        original = float(match.group(1))
        scaled = original * scale
        # Keep 1px+ values (hairline borders) at 1px or more, or Qt can draw them as 0.
        if abs(original) >= 1 and abs(scaled) < 1:
            scaled = math.copysign(1.0, original)
        text = f"{scaled:.2f}".rstrip("0").rstrip(".")
        return f"{text}px"

    def _replace_prop(match: re.Match) -> str:
        prop, sep, value = match.group(1), match.group(2), match.group(3)
        return f"{prop}{sep}{_PX_VALUE_PATTERN.sub(_replace_px, value)}"

    return _SCALABLE_QSS_PATTERN.sub(_replace_prop, qss)


class DisplaySettings(QObject):
    """Load and apply the QSS theme, app font, UI scale and display options."""

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
            self.ui_scale = _clamp_scale(float(config.get_ui_scale()))
            self.font_family = config.get_font_family()
            self.font_size = max(1, int(config.get_font_size()))
            # Normalize falsy values (None, "") to None so callers can rely on truthiness
            theme = config.get_display_theme()
            self.theme_name: str | None = theme if theme else None
            self.theme_dir = config.themes_dir
            self.menu_bar_auto_hide: bool = config.get_menu_bar_auto_hide()

            # Explicit-content display options
            self.blur_explicit_art: bool = config.get_blur_explicit_art()
            self.censor_explicit_words: bool = config.get_censor_explicit_words()
        else:
            # Default settings
            self.theme_dir = THEMES_DIR
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
        """Load, apply and persist the theme <theme_name>.qss from theme_dir."""
        self._load_theme(theme_name)

        if self.config:
            self.config.set_display_theme(theme_name)
            self.config.save()

        logger.info(f"Applied theme: {theme_name}")
        self.display_changed.emit()

    def _load_theme(self, theme_name: str):
        """Read and apply <theme_name>.qss without persisting; raises OSError or UnicodeDecodeError."""
        qss_path = self.theme_dir / f"{theme_name}.qss"
        if not qss_path.exists():
            raise FileNotFoundError(f"Theme not found: {qss_path}")

        self._raw_qss = resolve_theme_assets(qss_path.read_text(encoding="utf-8"))
        self._apply_stylesheet()
        self.theme_name = theme_name

    # ---------------------------------------------------------
    # UI scale
    # ---------------------------------------------------------

    def set_ui_scale(self, scale: float):
        """Set the UI scale factor (clamped to 0.5-3.0) and persist it."""
        scale = _clamp_scale(scale)
        self.ui_scale = scale

        if self.config:
            self.config.set_ui_scale(scale)
            self.config.save()

        self._apply_font()
        self._apply_stylesheet()
        self._reapply_styled_widgets()
        self.display_changed.emit()

    def preview_ui_scale_in(self, scale: float, roots: list[QWidget]):
        """Preview a scale change on only the given widget subtrees, without persisting."""
        # An app-wide setStyleSheet re-polishes every live widget (O(widgets), slow for big grids);
        # call set_ui_scale() to commit, which also corrects the widgets this preview skipped.
        scale = _clamp_scale(scale)
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
        """Set the font size (minimum 1 pt) and persist it."""
        size = max(1, int(size))
        self.font_size = size

        if self.config:
            self.config.set_font_size(size)
            self.config.save()

        self._apply_font()
        self.display_changed.emit()

    def _apply_font(self):
        """Apply the scaled font globally."""
        font = QFont(self.font_family)
        font.setPointSize(self._scaled_font_size())
        self.app.setFont(font)

    def _scaled_font_size(self) -> int:
        """Return the font size multiplied by ui_scale, truncated, minimum 1 pt."""
        return max(1, int(self.font_size * self.ui_scale))

    def _apply_stylesheet(self):
        """Scale the loaded theme's raw QSS by ui_scale and apply it app-wide (no-op without a theme)."""
        if self._raw_qss is None:
            return
        self.app.setStyleSheet(scale_qss_pixel_values(self._raw_qss, self.ui_scale))

    # ---------------------------------------------------------
    # Per-widget inline styles
    # ---------------------------------------------------------

    def style_widget(self, widget: QWidget, qss: str):
        """Apply an inline stylesheet that is re-scaled automatically when ui_scale changes."""
        # Weakly tracked: nothing to clean up when the widget is destroyed.
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
        """Enable or disable menu bar auto-hide and persist it."""
        self.menu_bar_auto_hide = enabled

        if self.config:
            self.config.set_menu_bar_auto_hide(enabled)
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
        """Re-apply all current settings once at app startup, without rewriting config."""
        if self.theme_name:
            try:
                self._load_theme(self.theme_name)
                logger.info(f"Applied theme: {self.theme_name}")
            except FileNotFoundError:
                # Stored theme is gone: clear the stale name so the next startup does not retry it.
                logger.warning(f"Stored theme {self.theme_name!r} not found; clearing")
                self.theme_name = None
                if self.config:
                    self.config.set_display_theme("")
                    self.config.save()
            except (OSError, UnicodeDecodeError) as e:
                # Keep the stored name: a read error (e.g. permissions) can be temporary.
                logger.error(f"Could not load theme {self.theme_name!r}: {e}")

        # Always apply the font: the QSS theme does not set the app font.
        self._apply_font()
        self.display_changed.emit()

    # ---------------------------------------------------------
    # Getters
    # ---------------------------------------------------------

    def get_settings(self):
        """Return all current display settings as a dict."""
        return {
            "theme": self.theme_name,
            "ui_scale": self.ui_scale,
            "font_family": self.font_family,
            "font_size": self.font_size,
            "scaled_font_size": self._scaled_font_size(),
            "menu_bar_auto_hide": self.menu_bar_auto_hide,
            "blur_explicit_art": self.blur_explicit_art,
            "censor_explicit_words": self.censor_explicit_words,
        }

    def get_available_themes(self) -> list[str]:
        """Return the sorted names (file stems) of the available .qss themes."""
        if not self.theme_dir.exists():
            return []
        return sorted(f.stem for f in self.theme_dir.glob("*.qss"))


def apply_scaled_style(widget: QWidget, qss: str) -> None:
    """Scale-aware replacement for widget.setStyleSheet() on inline styles with sizing properties."""
    display_settings = getattr(QApplication.instance(), "display_settings", None)
    # No app-wide DisplaySettings (e.g. tests without run.py bootstrap): apply unscaled.
    if display_settings is None:
        widget.setStyleSheet(qss)
        return
    display_settings.style_widget(widget, qss)


def _clamp_scale(scale: float) -> float:
    """Clamp a UI scale factor to [MIN_UI_SCALE, MAX_UI_SCALE]; non-finite values become 1.0."""
    if not math.isfinite(scale):
        return 1.0
    return min(MAX_UI_SCALE, max(MIN_UI_SCALE, scale))
