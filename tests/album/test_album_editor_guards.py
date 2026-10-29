"""AlbumEditor save/close guards, lazy tab refresh, suggestion loading and cover art checks."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QDialog, QLineEdit, QMessageBox, QTabWidget, QWidget
import pytest
import shiboken6

from src.album.edit.base_album_edit import AlbumEditor
from src.common.widgets.nullable_numeric_field import create_nullable_int_field


class _Bare(AlbumEditor):
    """AlbumEditor with only the QDialog base initialised; tests add the attributes they need."""

    def __init__(self):
        QDialog.__init__(self)
        self._cover_embed_worker = None


@pytest.fixture
def no_message_boxes(monkeypatch):
    calls = []
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, _n=name, **k: calls.append(_n)))
    return calls


# ---------------------------------------------------------------------------
# reject(): Cancel / Esc / window close
# ---------------------------------------------------------------------------


def test_reject_stays_open_when_user_keeps_unsaved_changes(qapp, monkeypatch):
    dialog = _Bare()
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
    with patch.object(AlbumEditor, "_has_unsaved_changes", return_value=True), patch.object(QDialog, "reject") as base_reject:
        dialog.reject()
    base_reject.assert_not_called()


def test_reject_blocked_while_cover_embed_runs(qapp, no_message_boxes):
    dialog = _Bare()
    dialog._cover_embed_worker = MagicMock(isRunning=MagicMock(return_value=True))
    with patch.object(AlbumEditor, "_has_unsaved_changes") as has_changes, patch.object(QDialog, "reject") as base_reject:
        dialog.reject()
    base_reject.assert_not_called()
    has_changes.assert_not_called()
    assert no_message_boxes == ["information"]


# ---------------------------------------------------------------------------
# save_changes(): validation and failed writes
# ---------------------------------------------------------------------------


def _save_editor(update_ok=True, name="Album", month=None):
    dialog = _Bare()
    dialog.album = SimpleNamespace(album_id=7, album_name="Album", release_month=None)
    name_edit = QLineEdit(name)
    dialog.field_widgets = {"album_name": name_edit, "release_month": create_nullable_int_field(1, 12, current_value=month)}
    dialog.controller = SimpleNamespace(update=MagicMock(update_entity=MagicMock(return_value=update_ok)))
    return dialog


def test_save_stays_open_when_update_fails(qapp, no_message_boxes):
    dialog = _save_editor(update_ok=False, name="Renamed")
    with patch.object(QDialog, "accept") as accept:
        dialog.save_changes()
    accept.assert_not_called()
    assert no_message_boxes == ["critical"]


def test_save_blocks_empty_album_name(qapp, no_message_boxes):
    dialog = _save_editor(name="   ")
    with patch.object(QDialog, "accept") as accept:
        dialog.save_changes()
    accept.assert_not_called()
    dialog.controller.update.update_entity.assert_not_called()
    assert no_message_boxes == ["warning"]


def test_month_outside_db_range_is_a_validation_error(qapp):
    dialog = _save_editor()
    dialog.field_widgets["release_month"].setText("0")  # "intermediate" for QIntValidator(1, 12)
    assert "between 1 and 12" in dialog._validation_error()


# ---------------------------------------------------------------------------
# refresh_view(): current tab now, other dependent tabs on first view
# ---------------------------------------------------------------------------


def _tab_editor():
    dialog = _Bare()
    dialog._stale_tabs = set()
    builds = []

    def _builder(title):
        def _build():
            builds.append(title)
            return QWidget()

        return _build

    titles = ("Overview", "Genres", "Album credit")
    dialog._tab_builders = lambda: {t: _builder(t) for t in titles}
    dialog.tabs = QTabWidget()
    for t in titles:
        dialog.tabs.addTab(QWidget(), t)
    dialog.tabs.currentChanged.connect(dialog._on_tab_changed)
    dialog._reload_album = lambda: None
    dialog._rebuild_link_buttons = lambda: None
    return dialog, builds


def test_refresh_view_rebuilds_current_tab_and_defers_the_rest(qapp):
    dialog, builds = _tab_editor()

    dialog.refresh_view()
    assert builds == ["Overview"]

    dialog.tabs.setCurrentIndex(1)
    assert builds == ["Overview", "Genres"]

    dialog.tabs.setCurrentIndex(1)
    dialog.tabs.setCurrentIndex(0)
    dialog.tabs.setCurrentIndex(1)
    assert builds == ["Overview", "Genres"]


def test_replaced_tab_is_deleted(qapp):
    dialog, _ = _tab_editor()
    old = dialog.tabs.widget(0)

    dialog._rebuild_tab_by_title("Overview")
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    assert not shiboken6.isValid(old)
    assert dialog.tabs.currentIndex() == 0


# ---------------------------------------------------------------------------
# Suggestions and album reload
# ---------------------------------------------------------------------------


def test_field_suggestions_scan_albums_once(qapp):
    dialog = _Bare()
    albums = [SimpleNamespace(album_language="Klingon", release_type=None, status="official", media_format="MiniDisc")]
    get_all = MagicMock(return_value=albums)
    dialog.controller = SimpleNamespace(get=SimpleNamespace(get_all_entities=get_all))

    result = dialog._fetch_field_suggestions()

    get_all.assert_called_once_with("Album")
    assert "Klingon" in result["album_language"]
    assert "MiniDisc" in result["media_format"]
    assert [s for s in result["status"] if s.lower() == "official"] == ["Official"]  # fallback casing wins


def test_reload_album_expires_the_identity_mapped_instance(qapp):
    dialog = _Bare()
    fresh = SimpleNamespace(album_id=3)
    session = MagicMock()
    dialog.controller = SimpleNamespace(get=SimpleNamespace(get_entity_object=lambda *a, **k: fresh, session=session))
    dialog.album = SimpleNamespace(album_id=3)
    dialog.helper = SimpleNamespace(album=None)
    dialog.tab_builder = SimpleNamespace(album=None)

    dialog._reload_album()

    session.expire.assert_called_once_with(fresh)
    assert dialog.album is dialog.helper.album is dialog.tab_builder.album is fresh


# ---------------------------------------------------------------------------
# Cover art: invalid files and clear confirmation
# ---------------------------------------------------------------------------


def test_pick_cover_rejects_unreadable_image(qapp, tmp_path, monkeypatch, no_message_boxes):
    bad = tmp_path / "cover.jpg"
    bad.write_bytes(b"not an image")
    dialog = _Bare()
    dialog._config = MagicMock(get_last_art_dir=MagicMock(return_value=str(tmp_path)))
    dialog._start_cover_embed = MagicMock()
    monkeypatch.setattr("src.album.edit.album_cover_art_mixin.QFileDialog.getOpenFileName", lambda *a, **k: (str(bad), ""))

    dialog._pick_cover("front")

    dialog._start_cover_embed.assert_not_called()
    assert no_message_boxes == ["warning"]


@pytest.mark.parametrize(("reply", "expected_calls"), [(QMessageBox.No, 0), (QMessageBox.Yes, 1)])
def test_clear_cover_needs_confirmation(qapp, monkeypatch, reply, expected_calls):
    dialog = _Bare()
    dialog._start_cover_embed = MagicMock()
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: reply))

    dialog._clear_cover("rear")

    assert dialog._start_cover_embed.call_count == expected_calls
