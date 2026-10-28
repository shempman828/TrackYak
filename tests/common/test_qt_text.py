"""Unit tests for src.common.widgets.qt_text.esc_amp.

Qt eats a lone '&' in the plain text of buttons, checkboxes, radio buttons,
group-box titles, menu items and tab labels as a mnemonic prefix. esc_amp
doubles it so names like "Simon & Garfunkel" / "R&B" / "Trinidad & Tobago"
render intact. This is the shared chokepoint every fixed call site routes
through.
"""

import pytest

from src.common.widgets.qt_text import esc_amp


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Simon & Garfunkel", "Simon && Garfunkel"),
        ("John Coltrane", "John Coltrane"),
        ("Earth, Wind & Fire & Friends", "Earth, Wind && Fire && Friends"),
        ("R&&B", "R&&&&B"),
        ("&AC/DC&", "&&AC/DC&&"),
        (None, ""),
        ("", ""),
        (42, "42"),
    ],
)
def test_esc_amp(value, expected):
    assert esc_amp(value) == expected
