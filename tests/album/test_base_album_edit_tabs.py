"""
Regression tests for ArtworkTab and AdvancedTab in base_album_edit_tabs.py.

Motivating bugs:
- ArtworkTab.build() always created fresh, enabled pick/clear buttons, even
  if a background cover-embed worker was still running -- silently
  re-enabling controls mid-embed on any tab rebuild.
- AdvancedTab.build() formatted album_gain/album_peak with only a None
  check, unlike the structurally identical average_rating a few lines
  below, which was wrapped in try/except (TypeError, ValueError).
"""

from types import SimpleNamespace

from PySide6.QtWidgets import QLabel, QLineEdit

from src.album.edit.base_album_edit_tabs import AdvancedTab, ArtworkTab


class _StubEditor:
    def __init__(self, album=None, field_widgets=None, cover_embed_worker=None):
        self.album = album
        self.field_widgets = field_widgets or {}
        self._cover_embed_worker = cover_embed_worker
        self.set_enabled_calls = []

    def _pick_cover(self, cover_type):
        pass

    def _clear_cover(self, cover_type):
        pass

    def _load_artwork_previews(self):
        pass

    def _set_cover_controls_enabled(self, enabled):
        self.set_enabled_calls.append(enabled)


def _stat_tiles(tab):
    """Map each stat tile's caption (e.g. "ALBUM GAIN (DB)") to its value text."""
    tiles = {}
    for value_lbl in tab.findChildren(QLabel, "StatTileValue"):
        tile = value_lbl.parentWidget()
        caption_lbl = tile.findChild(QLabel, "StatTileLabel") if tile else None
        if caption_lbl is not None:
            tiles[caption_lbl.text()] = value_lbl.text()
    return tiles


def test_artwork_tab_keeps_controls_disabled_when_embed_worker_running(qapp):
    editor = _StubEditor(cover_embed_worker=object())

    ArtworkTab(editor).build()

    assert editor.set_enabled_calls == [False]


def test_artwork_tab_leaves_controls_enabled_when_no_embed_running(qapp):
    editor = _StubEditor(cover_embed_worker=None)

    ArtworkTab(editor).build()

    assert editor.set_enabled_calls == []


def test_advanced_tab_renders_dash_for_corrupt_gain_and_peak(qapp):
    album = SimpleNamespace(
        album_gain="not-a-number",
        album_peak=object(),
        tracks=[],
        total_duration=None,
        total_plays=None,
        average_rating=None,
    )
    editor = _StubEditor(album=album, field_widgets={})

    tab = AdvancedTab(editor).build()

    tiles = _stat_tiles(tab)
    # Non-numeric gain/peak fall back to their str() rather than crashing.
    assert tiles["ALBUM GAIN (DB)"] == "not-a-number"
    assert "ALBUM PEAK" in tiles


def test_advanced_tab_formats_numeric_gain_and_peak(qapp):
    album = SimpleNamespace(
        album_gain=-6.12345,
        album_peak=0.987654,
        tracks=[],
        total_duration=None,
        total_plays=None,
        average_rating=None,
    )
    editor = _StubEditor(album=album, field_widgets={})

    tab = AdvancedTab(editor).build()

    texts = [w.text() for w in tab.findChildren(QLabel)]
    assert "-6.12" in texts
    assert "0.9877" in texts


def test_advanced_tab_first_pass_widget_still_added(qapp):
    """The removed dead _row() helper must not affect the first_pass/second_pass
    widgets, which are added directly (not via _row)."""
    field = QLineEdit()
    album = SimpleNamespace(
        album_gain=None,
        album_peak=None,
        tracks=[],
        total_duration=None,
        total_plays=None,
        average_rating=None,
    )
    editor = _StubEditor(album=album, field_widgets={"first_pass": field})

    tab = AdvancedTab(editor).build()

    assert field in tab.findChildren(QLineEdit)
