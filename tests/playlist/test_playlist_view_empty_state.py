"""Regression test: an empty playlist library used to render a blank tree
with no explanation. load_playlists() now shows a placeholder row instead.
"""

from types import SimpleNamespace

from PySide6.QtCore import Qt
import pytest

from src.playlist.playlist_view import PlaylistView


class _FakeController:
    def __init__(self):
        self.get = SimpleNamespace(
            get_all_entities=lambda entity, **kwargs: [],
            get_entity_object=lambda entity, **kwargs: None,
        )
        self.update = SimpleNamespace()
        self.add = SimpleNamespace()
        self.delete = SimpleNamespace()


@pytest.fixture
def view(qapp):
    v = PlaylistView(_FakeController())
    yield v
    v.deleteLater()


def test_empty_library_shows_placeholder_item(view):
    assert view.tree.topLevelItemCount() == 1
    item = view.tree.topLevelItem(0)
    assert "No playlists yet" in item.text(0)
    assert item.data(0, Qt.UserRole) is None  # not mistakable for a real playlist row
