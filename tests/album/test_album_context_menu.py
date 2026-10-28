"""AlbumContextMenuMixin: album creation, queue actions, track lookup, and
the batched Delete Empty Albums action.
"""

from types import SimpleNamespace

from PySide6.QtWidgets import QDialog, QWidget
import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from src.album.album_delete_dialog import DeleteEmptyAlbumsDialog
import src.album.view.album_context_menu as album_context_menu_module
from src.album.view.album_context_menu import AlbumContextMenuMixin
from src.db.db_helpers.delete import DeleteDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.album import Album
from src.db.db_tables.base import Base

# ---------------------------------------------------------------------------
# Regression tests for AlbumContextMenuMixin.
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Regression test: _delete_empty_albums() used to delete empty albums one at
# ---------------------------------------------------------------------------


class _DbController:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.delete = DeleteDB(session)


class _AlbumDeleteHost(QWidget, AlbumContextMenuMixin):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.load_calls = 0

    def load_albums(self):
        self.load_calls += 1

    @staticmethod
    def _get_track_count(album):
        return len(album.tracks) if album.tracks else 0


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


@pytest.fixture
def controller(session):
    return _DbController(session)


def _make_empty_albums(session, count):
    albums = [Album(album_name=f"Empty Album {i}") for i in range(count)]
    session.add_all(albums)
    session.commit()
    return albums


def test_delete_empty_albums_batches_delete_into_one_commit(qapp, controller, monkeypatch):
    albums = _make_empty_albums(controller.get.session, 5)
    host = _AlbumDeleteHost(controller)

    monkeypatch.setattr(DeleteEmptyAlbumsDialog, "exec_", lambda self: QDialog.Accepted)

    calls = []
    real_delete_entity = controller.delete.delete_entity

    def spying_delete_entity(*args, **kwargs):
        calls.append((args, kwargs))
        return real_delete_entity(*args, **kwargs)

    monkeypatch.setattr(controller.delete, "delete_entity", spying_delete_entity)

    host._delete_empty_albums()

    assert len(calls) == 1, "delete_entity must be called once for the whole batch, not per album"
    _args, kwargs = calls[0]
    assert kwargs.get("entity_ids") == [a.album_id for a in albums]
    assert "entity_id" not in kwargs

    remaining = controller.get.get_all_entities("Album")
    assert remaining == []
    assert host.load_calls == 1
