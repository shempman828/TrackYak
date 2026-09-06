"""Regression: the Sync view's Settings tab must scroll, not crunch.

The Settings tab stacks four fixed-height QGroupBoxes in a QVBoxLayout. With
no scroll area, a short or narrow detail pane squeezed those groups below
their sizeHint -- clipped titles, overlapping rows, unusable inputs. The tab
is now wrapped in a resizable QScrollArea, so a small viewport scrolls the
full-height content instead of compressing it.
"""

from PySide6.QtWidgets import QScrollArea, QWidget
import pytest

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
