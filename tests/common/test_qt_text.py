"""Unit tests for src.common.qt_text.esc_amp.

Qt eats a lone '&' in the plain text of buttons, checkboxes, radio buttons,
group-box titles, menu items and tab labels as a mnemonic prefix. esc_amp
doubles it so names like "Simon & Garfunkel" / "R&B" / "Trinidad & Tobago"
render intact. This is the shared chokepoint every fixed call site routes
through.
"""

from src.common.qt_text import esc_amp


def test_single_ampersand_is_doubled():
    assert esc_amp("Simon & Garfunkel") == "Simon && Garfunkel"


def test_no_ampersand_is_unchanged():
    assert esc_amp("John Coltrane") == "John Coltrane"


def test_multiple_ampersands_all_doubled():
    assert esc_amp("Earth, Wind & Fire & Friends") == "Earth, Wind && Fire && Friends"


def test_adjacent_ampersands_are_each_doubled():
    # "R&&B" as input -> every '&' independently doubled.
    assert esc_amp("R&&B") == "R&&&&B"


def test_none_becomes_empty_string():
    assert esc_amp(None) == ""


def test_empty_string_stays_empty():
    assert esc_amp("") == ""


def test_non_string_is_coerced():
    assert esc_amp(42) == "42"


def test_leading_and_trailing_ampersand():
    assert esc_amp("&AC/DC&") == "&&AC/DC&&"
