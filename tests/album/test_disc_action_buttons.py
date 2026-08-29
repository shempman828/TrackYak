"""Regression tests for the album Disc tab's Edit/Remove disc buttons.

Bug: "Edit disc" and "Remove disc" never became active. The buttons start
disabled and were only re-enabled inside DiscTabView.refresh_view(),
which __init__ never calls -- it calls load_data() directly. So opening an
album that already had discs left both buttons greyed out until the user
manually hit Refresh or added a disc.

Also covers the newly implemented DiscTabView.edit_disc().
"""

from PySide6.QtWidgets import QDialog

from src.album.disc_tab import view as disc_view
from src.album.disc_tab.view import DiscTabView


class _StubAlbum:
    def __init__(self, album_id=1, album_name="Test Album"):
        self.album_id = album_id
        self.album_name = album_name


class _StubDisc:
    def __init__(self, disc_id=10, disc_number=1, disc_title=None):
        self.disc_id = disc_id
        self.disc_number = disc_number
        self.disc_title = disc_title


class _StubGet:
    def __init__(self, discs):
        self._discs = discs

    def get_all_entities(self, model_name, **kwargs):
        if model_name == "Disc":
            return list(self._discs)
        return []


class _RecordingUpdate:
    def __init__(self):
        self.calls = []

    def update_entity(self, model_name, entity_id, **kwargs):
        self.calls.append((model_name, entity_id, kwargs))
        return True


class _StubController:
    def __init__(self, discs=()):
        self.get = _StubGet(discs)
        self.update = _RecordingUpdate()


def test_buttons_enabled_on_open_when_album_has_discs(qapp):
    view = DiscTabView(_StubAlbum(), _StubController(discs=[_StubDisc()]))

    assert view.edit_disc_btn.isEnabled()
    assert view.remove_disc_btn.isEnabled()


def test_buttons_disabled_on_open_when_album_has_no_discs(qapp):
    view = DiscTabView(_StubAlbum(), _StubController(discs=[]))

    assert not view.edit_disc_btn.isEnabled()
    assert not view.remove_disc_btn.isEnabled()


def test_edit_disc_writes_title_via_controller(qapp, monkeypatch):
    disc = _StubDisc(disc_id=42, disc_number=1, disc_title="Old")
    controller = _StubController(discs=[disc])
    view = DiscTabView(_StubAlbum(), controller)

    class _FakeDialog:
        def __init__(self, *args, **kwargs):
            pass

        def exec_(self):
            return QDialog.Accepted

        def get_disc_data(self):
            return {"disc_title": "New Title"}

    monkeypatch.setattr(disc_view, "DiscEditDialog", _FakeDialog)

    view.edit_disc()

    assert controller.update.calls == [("Disc", 42, {"disc_title": "New Title"})]
