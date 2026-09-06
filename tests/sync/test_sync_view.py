"""Regression: the Sync view's Settings tab must scroll, not crunch.

The Settings tab stacks four fixed-height QGroupBoxes in a QVBoxLayout. With
no scroll area, a short or narrow detail pane squeezed those groups below
their sizeHint -- clipped titles, overlapping rows, unusable inputs. The tab
is now wrapped in a resizable QScrollArea, so a small viewport scrolls the
full-height content instead of compressing it.
"""

from unittest.mock import Mock

from PySide6.QtWidgets import QComboBox, QScrollArea, QWidget
import pytest

from src.sync.sync_profile import SyncProfile
from src.sync.sync_view import SyncView

pytestmark = pytest.mark.usefixtures("qapp")


def _settings_tab():
    view = SyncView.__new__(SyncView)
    QWidget.__init__(view)
    return view._build_settings_tab()


def test_settings_tab_is_scrollable():
    tab = _settings_tab()

    assert isinstance(tab, QScrollArea)
    assert tab.widgetResizable() is True
    assert tab.widget() is not None
    # The four group boxes give the content a substantial natural height.
    assert tab.widget().sizeHint().height() > 300


def test_short_viewport_scrolls_instead_of_crunching(qapp):
    tab = _settings_tab()
    content = tab.widget()
    natural_height = content.sizeHint().height()

    tab.resize(500, 120)
    tab.show()
    qapp.processEvents()

    # Content keeps (about) its full height and overflows the viewport, so the
    # vertical scrollbar engages rather than the group boxes being squashed.
    assert content.height() >= natural_height - 4
    assert content.height() > tab.viewport().height()
    assert tab.verticalScrollBar().maximum() > 0

    tab.hide()


# ---------------------------------------------------------------------------
# "Music folder on device" — a dropdown of common MTP locations plus a
# free-text fallback, so setting the on-device destination is as easy as the
# fallback folder's Browse button (previously a bare, hand-typed line edit).
# ---------------------------------------------------------------------------


def _view_with_settings_tab():
    view = SyncView.__new__(SyncView)
    QWidget.__init__(view)
    # Keep the built tab referenced; its widgets are otherwise GC'd immediately.
    view._settings_tab = view._build_settings_tab()
    return view


def test_music_folder_field_offers_presets_and_custom_entry():
    view = _view_with_settings_tab()
    combo = view.music_path_edit

    assert isinstance(combo, QComboBox)
    # Editable, so any custom relative path is still possible (the fallback).
    assert combo.isEditable()
    # Typed custom values must not pollute the curated preset list.
    assert combo.insertPolicy() == QComboBox.NoInsert
    items = [combo.itemText(i) for i in range(combo.count())]
    assert "Music" in items
    assert len(items) >= 2


def test_on_music_path_changed_persists_combo_text():
    view = _view_with_settings_tab()
    prof = SyncProfile(name="P", path="")
    view.current_profile = prof
    view.profiles = [prof]
    view.profile_store = Mock()

    view.music_path_edit.setCurrentText("SD card/Music")
    view._on_music_path_changed()

    assert prof.music_path == "SD card/Music"
    view.profile_store.save.assert_called_once_with([prof])

    # A no-op re-fire (e.g. editingFinished after textActivated) does not re-save.
    view.profile_store.save.reset_mock()
    view._on_music_path_changed()
    view.profile_store.save.assert_not_called()
