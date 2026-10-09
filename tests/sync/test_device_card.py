"""DeviceCard keyboard access and the shared size formatter."""

from unittest.mock import Mock

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
import pytest

from src.sync.device_card import DeviceCard, format_file_size
from src.sync.sync_profile import SyncProfile

pytestmark = pytest.mark.usefixtures("qapp")


@pytest.mark.parametrize("key", [Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space])
def test_card_activates_from_keyboard(key):
    on_click = Mock()
    card = DeviceCard(SyncProfile(name="Phone", path="/x"), on_click)

    card.keyPressEvent(QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier))

    on_click.assert_called_once_with(card)


def test_card_is_focusable_and_named_for_screen_readers():
    card = DeviceCard(SyncProfile(name="Phone", path="/x"), Mock())

    assert card.focusPolicy() == Qt.StrongFocus
    assert card.accessibleName() == "Sync profile Phone"


@pytest.mark.parametrize(("size", "text"), [(0, "0 B"), (None, "0 B"), (512, "512 B"), (1536, "1.50 KB"), (5 * 1024**3, "5.00 GB")])
def test_format_file_size(size, text):
    assert format_file_size(size) == text
