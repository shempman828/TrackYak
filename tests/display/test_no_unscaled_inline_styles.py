"""Guardrail: every inline widget.setStyleSheet() call in src/ that sets a
scalable sizing property (font-size, padding, border-radius, etc.) must go
through apply_scaled_style() instead of calling setStyleSheet() directly --
otherwise it silently stops tracking the Appearance settings UI-scale
slider, which is exactly the class of bug this test exists to catch before
it ships again.

Static/AST-based rather than a plain grep so f-strings and multi-line QSS
blocks (f\"\"\"...\"\"\") are handled correctly: only the literal text
segments of an f-string are inspected (interpolated {expr} parts are
skipped), which is sufficient since every real px value in this codebase
is a literal, never itself interpolated.
"""

import ast
from pathlib import Path

from src.display.display_settings import scale_qss_pixel_values

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"

# display_settings.py is the trusted implementation -- it's the only file
# allowed to call widget.setStyleSheet()/app.setStyleSheet() directly.
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
