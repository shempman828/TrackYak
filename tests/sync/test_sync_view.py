"""SyncView tests: Options page layout and persistence, selection toolbar, device linking, shutdown."""

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


def test_on_music_path_changed_persists_combo_text():
    view = _view_with_settings_tab()
    prof = SyncProfile(name="P", path="")
    view.current_profile = prof
    view.profiles = [prof]
    view.profile_store = Mock()
    view._refresh_current_card = Mock()
    view._refresh_header = Mock()

    view.music_path_edit.setCurrentText("SD card/Music")
    view._on_music_path_changed()

    assert prof.music_path == "SD card/Music"
    view.profile_store.save.assert_called_once_with([prof])

    # A no-op re-fire (e.g. editingFinished after textActivated) does not re-save.
    view.profile_store.save.reset_mock()
    view._on_music_path_changed()
    view.profile_store.save.assert_not_called()


# ---------------------------------------------------------------------------
# "Convert lossless files to MP3" — the option is gated on ffmpeg being
# present, exposes a bitrate picker, and round-trips through the profile.
# ---------------------------------------------------------------------------


def test_transcode_widgets_disabled_without_ffmpeg(monkeypatch):  # AC13
    import src.sync.sync_options_mixin as sv

    monkeypatch.setattr(sv, "ffmpeg_available", lambda: False)
    view = _view_with_settings_tab()

    assert view.transcode_mp3_check.isEnabled() is False
    assert view.transcode_mp3_check.toolTip()  # non-empty "install ffmpeg" hint
    assert view.bitrate_combo.isEnabled() is False


def test_transcode_checkbox_enabled_with_ffmpeg_combo_follows_checkbox(monkeypatch):  # AC13
    import src.sync.sync_options_mixin as sv

    monkeypatch.setattr(sv, "ffmpeg_available", lambda: True)
    view = _view_with_settings_tab()

    assert view.transcode_mp3_check.isEnabled() is True
    # Bitrate picker stays disabled until the checkbox is actually ticked.
    assert view.bitrate_combo.isEnabled() is False


def test_toggling_transcode_options_persists_to_profile(monkeypatch):  # AC14
    import src.sync.sync_options_mixin as sv

    monkeypatch.setattr(sv, "ffmpeg_available", lambda: True)
    view = _view_with_settings_tab()
    prof = SyncProfile(name="P", path="")
    view.current_profile = prof
    view.profiles = [prof]
    view.profile_store = Mock()

    view.transcode_mp3_check.setChecked(True)
    assert prof.transcode_to_mp3 is True
    assert view.bitrate_combo.isEnabled() is True

    view.bitrate_combo.setCurrentText("192")
    assert prof.transcode_bitrate == "192k"
    assert view.profile_store.save.called


def test_load_profile_reflects_transcode_settings_without_signals(monkeypatch):  # AC14
    import src.sync.sync_options_mixin as sv

    monkeypatch.setattr(sv, "ffmpeg_available", lambda: True)
    view = _view_with_settings_tab()
    view.detail_stack = Mock()
    view.profile_page = Mock()
    view._apply_profile_selection = Mock()
    view._refresh_header = Mock()
    view.profile_store = Mock()
    prof = SyncProfile(name="P", path="", transcode_to_mp3=True, transcode_bitrate="256k")
    view.current_profile = prof

    fired = []
    view.transcode_mp3_check.toggled.connect(lambda *_: fired.append("check"))
    view.bitrate_combo.currentTextChanged.connect(lambda *_: fired.append("combo"))

    view._load_profile_into_ui()

    assert view.transcode_mp3_check.isChecked() is True
    assert view.bitrate_combo.currentText() == "256"
    assert view.bitrate_combo.isEnabled() is True
    assert fired == []  # signals stayed blocked during the load


# ---------------------------------------------------------------------------
# Transcode cache size row: a "Max cache size" spinbox (global, persisted to
# app_config) plus the existing "Clear MP3 cache (NN MB)" button.
# ---------------------------------------------------------------------------


def test_cache_max_spinbox_seeds_from_config_and_persists_on_change(monkeypatch):  # AC11
    import src.sync.sync_options_mixin as sv

    fake_cfg = Mock()
    fake_cfg.get_transcode_cache_max_mb.return_value = 1024
    monkeypatch.setattr(sv, "app_config", fake_cfg)

    view = _view_with_settings_tab()
    assert view.cache_max_spin.value() == 1024
    assert view.cache_max_spin.specialValueText()  # 0 renders as "Unlimited"

    view.cache_max_spin.setValue(2048)
    fake_cfg.set_transcode_cache_max_mb.assert_called_with(2048)
    # The disk write is debounced until the value settles.
    assert not fake_cfg.save.called
    assert view._cache_save_timer.isActive()
    view._flush_cache_max()
    assert fake_cfg.save.called


def test_clear_cache_button_shows_size_and_disables_when_empty(monkeypatch):  # AC12
    import src.sync.sync_options_mixin as sv

    view = _view_with_settings_tab()
    fake_cache = Mock()
    monkeypatch.setattr(sv, "TranscodeCache", lambda: fake_cache)

    fake_cache.size_bytes.return_value = 0
    view._update_cache_button_label()
    assert "0 MB" in view.clear_cache_btn.text()
    assert view.clear_cache_btn.isEnabled() is False

    fake_cache.size_bytes.return_value = 5 * 1024 * 1024
    view._update_cache_button_label()
    assert "5 MB" in view.clear_cache_btn.text()
    assert view.clear_cache_btn.isEnabled() is True


def test_close_event_cancels_and_joins_sync_items_loader():  # perf-AC13
    from PySide6.QtGui import QCloseEvent

    view = _view_with_settings_tab()
    loader = Mock()
    loader.isRunning.return_value = True
    view._sync_items_loader = loader

    view.closeEvent(QCloseEvent())

    loader.request_cancel.assert_called_once()
    loader.wait.assert_called_once()


def test_option_change_refreshes_selection_summary(monkeypatch):  # AC7
    import src.sync.sync_options_mixin as sv

    monkeypatch.setattr(sv, "ffmpeg_available", lambda: True)
    view = _view_with_settings_tab()
    prof = SyncProfile(name="P", path="")
    view.current_profile = prof
    view.profiles = [prof]
    view.profile_store = Mock()
    view._update_selected_items = Mock()

    # Toggling the transcode checkbox / bitrate must re-render the summary,
    # since its size figure depends on both.
    view.transcode_mp3_check.setChecked(True)
    view.bitrate_combo.setCurrentText("192")

    assert view._update_selected_items.call_count >= 2


# ---------------------------------------------------------------------------
# "Files on the destination" -- one 3-way switch (Keep / Remove untracked /
# Wipe, then copy) over the profile's clear_before_sync + prune_untracked
# pair; round-trips through the profile and is restored on load silently.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("index", "clear", "prune"), [(0, False, False), (1, False, True), (2, True, False)])
def test_cleanup_mode_persists_to_profile(index, clear, prune):  # AC11
    view = _view_with_settings_tab()
    start = 1 if index != 1 else 0  # make sure the switch actually changes
    prof = SyncProfile(name="P", path="", clear_before_sync=not clear, prune_untracked=not prune)
    view.cleanup_mode.blockSignals(True)
    view.cleanup_mode.setCurrentIndex(start)
    view.cleanup_mode.blockSignals(False)
    view.current_profile = prof
    view.profiles = [prof]
    view.profile_store = Mock()

    view.cleanup_mode.setCurrentIndex(index)

    assert (prof.clear_before_sync, prof.prune_untracked) == (clear, prune)
    assert view.profile_store.save.called


@pytest.mark.parametrize(("clear", "prune", "expected"), [(False, False, 0), (False, True, 1), (True, False, 2), (True, True, 2)])
def test_load_profile_reflects_cleanup_mode_without_signals(monkeypatch, clear, prune, expected):  # AC11
    import src.sync.sync_options_mixin as sv

    monkeypatch.setattr(sv, "ffmpeg_available", lambda: True)
    view = _view_with_settings_tab()
    view.detail_stack = Mock()
    view.profile_page = Mock()
    view._apply_profile_selection = Mock()
    view._refresh_header = Mock()
    view.profile_store = Mock()
    view.current_profile = SyncProfile(name="P", path="", clear_before_sync=clear, prune_untracked=prune)

    fired = []
    view.cleanup_mode.currentIndexChanged.connect(lambda *_: fired.append("mode"))

    view._load_profile_into_ui()

    assert view.cleanup_mode.currentIndex() == expected
    assert fired == []
    view.profile_store.save.assert_not_called()


# ---------------------------------------------------------------------------
# Expand All / Collapse All for the playlist+mood selection tree.
# ---------------------------------------------------------------------------


def _selection_tab_view():
    view = SyncView.__new__(SyncView)
    QWidget.__init__(view)
    view._selection_tab = view._build_selection_tab()  # keep widgets alive
    return view


def _nest_tree(tree):
    from PySide6.QtWidgets import QTreeWidgetItem

    header = QTreeWidgetItem(tree, ["PLAYLISTS  (1)"])
    folder = QTreeWidgetItem(header, ["Folder"])
    QTreeWidgetItem(folder, ["Leaf"])
    return header, folder


def test_selection_toolbar_has_expand_collapse_buttons():  # AC1
    view = _selection_tab_view()

    assert view.expand_all_btn.text() == "Expand All"
    assert view.collapse_all_btn.text() == "Collapse All"


def test_collapse_all_button_collapses_every_node():  # AC2
    view = _selection_tab_view()
    header, folder = _nest_tree(view.sync_tree)
    view.sync_tree.expandAll()
    assert header.isExpanded() and folder.isExpanded()

    view.collapse_all_btn.click()

    assert not header.isExpanded()
    assert not folder.isExpanded()


def test_expand_all_button_expands_every_node():  # AC2
    view = _selection_tab_view()
    header, folder = _nest_tree(view.sync_tree)
    view.sync_tree.collapseAll()
    assert not header.isExpanded()

    view.expand_all_btn.click()

    assert header.isExpanded()
    assert folder.isExpanded()


def test_expand_collapse_no_crash_on_empty_tree():  # AC3
    view = _selection_tab_view()

    view.collapse_all_btn.click()
    view.expand_all_btn.click()

    assert view.sync_tree.topLevelItemCount() == 0


# ---------------------------------------------------------------------------
# Linking a device enumerates MTP devices on a worker thread, never inline.
# ---------------------------------------------------------------------------


def test_link_device_scans_off_the_gui_thread(monkeypatch):
    import src.sync.sync_device_mixin as sv

    started = []

    class _Worker:
        def __init__(self, _mtp):
            self.ready = Mock()

        def isRunning(self):
            return False

        def start(self):
            started.append(self)

    monkeypatch.setattr(sv, "mtp_available", lambda: True)
    monkeypatch.setattr(sv, "MtpListWorker", _Worker)
    view = _view_with_settings_tab()
    view.mtp_manager = Mock()
    view._link_worker = None
    view.current_profile = None
    view.change_destination_btn = Mock()

    view._link_device()

    view.mtp_manager.list_devices.assert_not_called()
    assert len(started) == 1
    assert view.link_device_btn.text() == "Scanning…"
    assert not view.link_device_btn.isEnabled()


# ---------------------------------------------------------------------------
# Music path normalization, link target, shutdown, and polling while hidden.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("typed", "saved"), [("", "Music"), ("  /../Music/ ", "Music"), ("SD card//Music/", "SD card/Music")])
def test_music_path_is_normalized_before_saving(typed, saved):
    view = _view_with_settings_tab()
    prof = SyncProfile(name="P", path="", music_path="Old")
    view.current_profile = prof
    view.profiles = [prof]
    view.profile_store = Mock()
    view._refresh_current_card = Mock()
    view._refresh_header = Mock()

    view.music_path_edit.setCurrentText(typed)
    view._on_music_path_changed()

    assert prof.music_path == saved
    assert view.music_path_edit.currentText() == saved


def test_link_result_ignored_when_user_switched_profile():
    view = _view_with_settings_tab()
    first, second = SyncProfile(name="A", path=""), SyncProfile(name="B", path="")
    view.profiles = [first, second]
    view.profile_store = Mock()
    view.cards = []
    view.change_destination_btn = Mock()
    view._refresh_header = Mock()
    view._link_target = first
    view.current_profile = second

    from src.sync.mtp_manager import MtpDevice

    view._on_link_devices_listed([MtpDevice(uri="mtp://x/", name="Phone")])

    assert first.device_uri == "" and second.device_uri == ""
    view.profile_store.save.assert_not_called()


def test_shutdown_cancels_and_joins_every_running_worker():
    view = _view_with_settings_tab()
    workers = {}
    for name in ("sync_worker", "_sync_items_loader", "_mtp_list_worker", "_link_worker", "_detect_worker"):
        worker = Mock()
        worker.isRunning.return_value = True
        setattr(view, name, worker)
        workers[name] = worker

    view.shutdown()

    for worker in workers.values():
        worker.request_cancel.assert_called_once()
        worker.wait.assert_called_once()


def test_poll_runs_only_while_view_is_visible(monkeypatch):
    import src.sync.sync_device_mixin as dm

    monkeypatch.setattr(dm, "mtp_available", lambda: True)
    view = _view_with_settings_tab()
    from PySide6.QtCore import QTimer

    view._mtp_poll_timer = QTimer()
    view._refresh_mtp_devices = Mock()

    view._start_mtp_polling()
    assert view._mtp_poll_timer.isActive()
    view._refresh_mtp_devices.assert_called_once()

    view._stop_mtp_polling()
    assert not view._mtp_poll_timer.isActive()
