"""Regression test: the "Merge Publishers" dialog fed raw publisher names
straight into QRadioButton text, so Qt consumed the '&' as a mnemonic prefix
and a name like "Sony & ATV" rendered as "Sony  ATV". _display_name now
escapes '&' (after eliding), and still exposes the raw name via tooltip.
"""
from types import SimpleNamespace

from src.publisher.publisher_fuzzy_match import PublisherFuzzyMatchDialog, _MAX_NAME_CHARS

_display_name = PublisherFuzzyMatchDialog._display_name


def _publisher(publisher_id, name):
    return SimpleNamespace(publisher_id=publisher_id, publisher_name=name, MBID=None)


def test_display_name_doubles_ampersands():
    assert _display_name("Sony & ATV") == "Sony && ATV"


def test_display_name_handles_none():
    assert _display_name(None) == ""


def test_display_name_escapes_after_eliding():
    name = "A & " + "x" * _MAX_NAME_CHARS
    out = _display_name(name)
    # Elided on the real length, then the surviving '&' is doubled.
    assert out.endswith("…")
    assert "&&" in out
    assert len(out.replace("&&", "&")) <= _MAX_NAME_CHARS


def test_merge_dialog_radio_text_keeps_ampersand(qapp):
    a = _publisher(1, "Sony & ATV")
    b = _publisher(2, "Sony and ATV")
    dialog = PublisherFuzzyMatchDialog([(a, b, 95)], controller=None)
    try:
        radio_a = dialog.match_widgets[0][1]
        assert radio_a.text() == "Sony && ATV"
        assert radio_a.toolTip() == "Sony & ATV"
    finally:
        dialog.deleteLater()
