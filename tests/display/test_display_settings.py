"""Tests for scale_qss_pixel_values() (src/display/display_settings.py),
the regex pass that lets the Appearance settings UI-scale slider preview
font/padding/margin/sizing changes live against the loaded theme's QSS.

Scaling must always run against a theme's original, unscaled QSS text --
these tests also guard against a caller compounding the factor by scaling
an already-scaled stylesheet.
"""

from src.display.display_settings import DisplaySettings, scale_qss_pixel_values


def test_scales_font_size_px():
    assert scale_qss_pixel_values("font-size: 10px;", 1.5) == "font-size: 15px;"


def test_scales_padding_and_margin():
    qss = "padding: 8px;\nmargin-left: 4px;"
    assert scale_qss_pixel_values(qss, 0.5) == "padding: 4px;\nmargin-left: 2px;"


def test_scales_width_height_and_border_radius():
    qss = "min-width: 20px;\nheight: 40px;\nborder-radius: 8px;"
    expected = "min-width: 30px;\nheight: 60px;\nborder-radius: 12px;"
    assert scale_qss_pixel_values(qss, 1.5) == expected


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
