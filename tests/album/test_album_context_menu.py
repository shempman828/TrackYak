"""
Regression tests for AlbumContextMenuMixin.

Motivating bugs:
- `_create_new_album` wrapped album creation and the optional artist-link
  step in one try/except, so a link failure after a successful album
  creation reported total failure and skipped load_albums(), hiding the
  newly created album.
- The "Add to Queue" action interpolated the raw album name into QAction
  text without esc_amp(), unlike the merge/delete actions, so a "&" in a
  name was eaten as a Qt mnemonic.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy.exc import SQLAlchemyError

import src.album.album_context_menu as album_context_menu_module
from src.album.album_context_menu import AlbumContextMenuMixin


class _StubDialog:
    """Stands in for NewAlbumDialog.exec_()/property surface."""

    def __init__(self, album_name="New Album", release_year=None, artist_name="", is_compilation=0):
        self.album_name = album_name
        self.release_year = release_year
        self.artist_name = artist_name
        self.is_compilation = is_compilation

    def exec_(self):
        from PySide6.QtWidgets import QDialog

        return QDialog.Accepted


class _Host(AlbumContextMenuMixin):
    def __init__(self, controller):
        self.controller = controller
        self.load_albums_calls = 0
        self.shown_album = None

    def load_albums(self):
        self.load_albums_calls += 1

    def _show_album_details(self, album):
        self.shown_album = album

    def _get_track_count(self, album):
        return 0


class _FakeAdd:
    def __init__(self, album, fail_on=frozenset()):
        self._album = album
        self._fail_on = fail_on
        self.calls = []

    def add_entity(self, model_name, **kwargs):
        self.calls.append((model_name, kwargs))
        if model_name in self._fail_on:
            raise SQLAlchemyError(f"boom on {model_name}")
        if model_name == "Album":
            return self._album
        return SimpleNamespace(artist_id=1)


class _FakeGet:
    def get_entity_object(self, model_name, **kwargs):
        return None  # force the "create a new artist" branch


class _Controller:
    def __init__(self, add, get=None):
        self.add = add
        self.get = get or _FakeGet()


@pytest.fixture(autouse=True)
def _stub_new_album_dialog(monkeypatch):
    """Replace the real Qt dialog with a stub so tests don't need a modal loop."""

    def _factory(controller, parent, **kw):
        return monkeypatch._dialog

    monkeypatch.setattr(album_context_menu_module, "NewAlbumDialog", _factory)
    yield


def test_artist_link_failure_does_not_hide_the_created_album(monkeypatch, qapp):
    album = SimpleNamespace(album_id=1, album_name="New Album")
    add = _FakeAdd(album, fail_on={"AlbumRoleAssociation"})
    host = _Host(_Controller(add))

    monkeypatch._dialog = _StubDialog(artist_name="Some Artist")
    monkeypatch.setattr(
        album_context_menu_module, "QMessageBox", SimpleNamespace(critical=lambda *a, **k: None)
    )

    host._create_new_album()

    # The album was created and the caller still gets to see it, even
    # though linking the artist failed.
    assert host.load_albums_calls == 1
    assert host.shown_album is album


def test_album_creation_failure_does_not_call_load_albums(monkeypatch, qapp):
    add = _FakeAdd(album=None, fail_on={"Album"})
    host = _Host(_Controller(add))

    monkeypatch._dialog = _StubDialog(artist_name="")
    monkeypatch.setattr(
        album_context_menu_module, "QMessageBox", SimpleNamespace(critical=lambda *a, **k: None)
    )

    host._create_new_album()

    assert host.load_albums_calls == 0
    assert host.shown_album is None


def test_queue_action_escapes_ampersand_in_album_name(qapp):
    from src.common.widgets.qt_text import esc_amp

    assert esc_amp("Simon & Garfunkel") == "Simon && Garfunkel"


def test_get_album_tracks_filters_server_side_by_album_id(qapp):
    class _FakeGetTracks:
        def __init__(self):
            self.calls = []

        def get_all_entities(self, model_name, **kwargs):
            self.calls.append((model_name, kwargs))
            return ["track-for-this-album"]

    get = _FakeGetTracks()
    host = _Host(_Controller(add=None, get=get))
    album = SimpleNamespace(album_id=42)

    result = host._get_album_tracks(album)

    assert result == ["track-for-this-album"]
    assert get.calls == [("Track", {"album_id": 42})]
