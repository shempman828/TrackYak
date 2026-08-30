"""Tests for scale_qss_pixel_values() (src/display/display_settings.py),
the regex pass that lets the Appearance settings UI-scale slider preview
font/padding/margin/sizing changes live against the loaded theme's QSS.

Scaling must always run against a theme's original, unscaled QSS text --
these tests also guard against a caller compounding the factor by scaling
an already-scaled stylesheet.
"""
from PySide6.QtWidgets import QLabel
from src.display.display_settings import DisplaySettings, scale_qss_pixel_values
import ast
from pathlib import Path
from src.display.display_settings import scale_qss_pixel_values

# ---- test_display_settings__self_base.py -------------------------------------
def test_scales_font_size_px():
    assert scale_qss_pixel_values("font-size: 10px;", 1.5) == "font-size: 15px;"

def test_scales_padding_and_margin():
    qss = "padding: 8px;\nmargin-left: 4px;"
    assert scale_qss_pixel_values(qss, 0.5) == "padding: 4px;\nmargin-left: 2px;"

def test_scales_width_height_and_border_radius():
    qss = "min-width: 20px;\nheight: 40px;\nborder-radius: 8px;"
    expected = "min-width: 30px;\nheight: 60px;\nborder-radius: 12px;"
    assert scale_qss_pixel_values(qss, 1.5) == expected

def test_scales_every_value_in_multi_value_shorthand():
    """Regression: padding/margin/border-radius shorthand can carry 2-4
    space-separated px values (e.g. "padding: 2px 8px;") -- every value
    must scale independently, not just the first.
    """
    assert scale_qss_pixel_values("padding: 2px 8px;", 2.0) == "padding: 4px 16px;"
    assert (
        scale_qss_pixel_values("border-radius: 8px 8px 0 0;", 1.5)
        == "border-radius: 12px 12px 0 0;"
    )

def test_scales_only_the_width_in_border_shorthand():
    qss = "border-bottom: 2px solid rgba(133, 153, 234, 0.4);"
    expected = "border-bottom: 3px solid rgba(133, 153, 234, 0.4);"
    assert scale_qss_pixel_values(qss, 1.5) == expected

def test_leaves_non_px_units_untouched():
    qss = "font-size: 1.05em; padding: 8%; border-radius: 4pt;"
    assert scale_qss_pixel_values(qss, 2.0) == qss

def test_leaves_properties_outside_allow_list_untouched():
    qss = "background-position: 10px 5px;"
    assert scale_qss_pixel_values(qss, 2.0) == qss

def test_scale_of_one_is_identity_modulo_formatting():
    qss = "font-size: 12px;"
    assert scale_qss_pixel_values(qss, 1.0) == "font-size: 12px;"

def test_negative_margin_scales_correctly():
    assert scale_qss_pixel_values("margin-top: -10px;", 0.5) == "margin-top: -5px;"

def test_fractional_result_is_not_truncated():
    assert scale_qss_pixel_values("font-size: 12px;", 1.1) == "font-size: 13.2px;"

def test_ui_scale_change_reapplies_from_original_not_from_already_scaled(tmp_path, qapp):
    """Regression for compounding: DisplaySettings must re-scale from the
    theme's original QSS on every ui_scale change, not from whatever is
    already applied -- otherwise scaling down after scaling up would not
    return to the original size.
    """
    (tmp_path / "test_theme.qss").write_text("QLabel { font-size: 10px; }")
    settings = DisplaySettings(app=qapp, config=None)
    settings.theme_dir = tmp_path

    settings.set_theme("test_theme")
    settings.set_ui_scale(1.5)
    assert "font-size: 15px;" in qapp.styleSheet()

    settings.set_ui_scale(1.0)
    assert "font-size: 10px;" in qapp.styleSheet()

def test_preview_ui_scale_in_only_restyles_given_roots(tmp_path, qapp):
    """Regression: the scale-slider debounce handler used to call
    set_ui_scale(), which restyles via QApplication.setStyleSheet() -- an
    O(total live widget count) call that re-polishes every widget in the
    app, including ones sitting hidden behind another tab. For a widget
    tree that grows with the library (e.g. the album grid), that made
    every settled slider position freeze the app for as long as it took to
    re-polish widgets nobody could even see.

    preview_ui_scale_in() must restyle only the given roots -- proving it
    doesn't touch (and doesn't pay for) anything outside that scope.
    """
    (tmp_path / "test_theme.qss").write_text("QLabel { font-size: 10px; }")
    settings = DisplaySettings(app=qapp, config=None)
    settings.theme_dir = tmp_path
    settings.set_theme("test_theme")

    in_scope = QLabel()
    out_of_scope = QLabel()

    settings.preview_ui_scale_in(1.5, [in_scope])

    assert "font-size: 15px;" in in_scope.styleSheet()
    assert out_of_scope.styleSheet() == ""
    # The app-wide stylesheet itself must be untouched by a scoped preview --
    # only set_ui_scale() commits that.
    assert "15px" not in qapp.styleSheet()

# ---- test_no_unscaled_inline_styles.py ---------------------------------------
# Guardrail: every inline widget.setStyleSheet() call in src/ that sets a
# scalable sizing property (font-size, padding, border-radius, etc.) must go
# through apply_scaled_style() instead of calling setStyleSheet() directly --
# otherwise it silently stops tracking the Appearance settings UI-scale
# slider, which is exactly the class of bug this test exists to catch before
# it ships again.
#
# Static/AST-based rather than a plain grep so f-strings and multi-line QSS
# blocks (f"""...""") are handled correctly: only the literal text
# segments of an f-string are inspected (interpolated {expr} parts are
# skipped), which is sufficient since every real px value in this codebase
# is a literal, never itself interpolated.
SRC_ROOT = Path(__file__).resolve().parents[2] / "src"

_EXEMPT_FILES = {SRC_ROOT / "display" / "display_settings.py"}

def _literal_text(node: ast.expr) -> str:
    """Concatenate the literal (non-interpolated) text of a string or
    f-string AST node. Returns "" for anything else (e.g. a plain
    variable), which means such calls can't be statically checked here.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value
            for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
    return ""

def _find_violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "setStyleSheet"):
            continue
        if not node.args:
            continue
        text = _literal_text(node.args[0])
        # A real violation is text that scale_qss_pixel_values would actually
        # change -- i.e. it contains a scalable property with a live px
        # value, not just the property name (e.g. "border: none;" mentions
        # "border" but has nothing for the slider to affect).
        if text and scale_qss_pixel_values(text, 1.0) != scale_qss_pixel_values(text, 2.0):
            violations.append(f"{path}:{node.lineno}")
    return violations

def test_no_raw_setstylesheet_with_scalable_sizing_outside_display_settings():
    violations = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        if path in _EXEMPT_FILES:
            continue
        violations.extend(_find_violations(path))

    assert not violations, (
        "Found raw widget.setStyleSheet() calls with a scalable sizing "
        "property (font-size/padding/margin/width/height/border-radius/etc.) "
        "that won't track the UI-scale slider. Use "
        "src.display.display_settings.apply_scaled_style(widget, qss) "
        "instead:\n" + "\n".join(violations)
    )
