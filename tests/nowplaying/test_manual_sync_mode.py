"""Manual lyric sync: the SYNC button in NowPlayingView opens a tap-to-sync
dialog that re-times a track's lyrics line-by-line.

See docs/specs/manual_lyric_sync.md for the full spec these tests are mapped to.
"""

from types import SimpleNamespace

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog
import pytest

from src.foundation.config_setup import app_config
from src.nowplaying.nowplaying_lyrics_parser import parse_lyrics
from src.nowplaying.nowplaying_lyrics_sync_dialog import LyricSyncDialog
from src.nowplaying.nowplaying_view import NowPlayingView

_PLAIN_LYRICS = "line one\nline two\nline three"


class _FakeMediaPlayer(QObject):
    position_changed = Signal(int)
    state_changed = Signal(str)

    def __init__(self):
        super().__init__()
        self.position = 0
        self.toggle_calls = 0

    def toggle_play_pause(self):
        self.toggle_calls += 1


class _FakeUpdate:
    def __init__(self, result=True):
        self.calls = []
        self.result = result

    def update_entities(self, model_name, entity_ids, **kwargs):
        self.calls.append((model_name, entity_ids, kwargs))
        return self.result


@pytest.fixture
def player(qapp):
    return _FakeMediaPlayer()


@pytest.fixture
def update(qapp):
    return _FakeUpdate()


@pytest.fixture
def controller(player, update):
    return SimpleNamespace(mediaplayer=player, update=update)


@pytest.fixture
def track():
    return SimpleNamespace(track_id=1, lyrics=_PLAIN_LYRICS)


@pytest.fixture
def dialog(qapp, controller, track):
    dlg = LyricSyncDialog(controller, track, ["line one", "line two", "line three"])
    yield dlg
    dlg.deleteLater()


@pytest.fixture
def view(qapp, controller):
    v = NowPlayingView(controller)
    yield v
    v.deleteLater()


# ── AC1: SYNC button enablement ─────────────────────────────────────────────


def test_sync_button_disabled_with_no_lyrics(view):
    view._update_lyrics(SimpleNamespace(lyrics=None))
    assert view._manual_sync_btn.isEnabled() is False


def test_sync_button_enabled_once_lyrics_load(view):
    view._update_lyrics(SimpleNamespace(lyrics=_PLAIN_LYRICS))
    assert view._manual_sync_btn.isEnabled() is True


# ── AC2: opening shows line 1 and progress ──────────────────────────────────


def test_dialog_starts_on_first_line_with_progress(dialog):
    assert dialog._current_lbl.text() == "line one"
    assert dialog._progress_lbl.text() == "Line 1 of 3"
    assert dialog._save_btn.isEnabled() is False


# ── AC3 & AC4: tap stamps position minus reaction offset, advances ─────────


def test_tap_subtracts_reaction_offset_and_advances(dialog, player):
    dialog._reaction_spin.setValue(200)
    player.position = 5000

    dialog._tap()

    assert dialog._stamps[0] == 4800
    assert dialog._idx == 1
    assert dialog._current_lbl.text() == "line two"
    assert dialog._progress_lbl.text() == "Line 2 of 3"


def test_tap_stamp_never_precedes_the_previous_stamp(dialog, player):
    player.position = 1000
    dialog._reaction_spin.setValue(0)
    dialog._tap()  # stamps[0] = 1000

    player.position = 1000  # a second line arriving at the same raw position
    dialog._tap()

    assert dialog._stamps[1] == dialog._stamps[0] + 1


# ── AC5: Save stays disabled until every line is tapped ────────────────────


def test_save_enabled_only_after_every_line_tapped(dialog, player):
    for pos in (1000, 2000):
        player.position = pos
        dialog._tap()
    assert dialog._save_btn.isEnabled() is False

    player.position = 3000
    dialog._tap()
    assert dialog._idx == 3
    assert dialog._save_btn.isEnabled() is True


# ── AC6: Undo steps back, clears stamp, re-disables Save ───────────────────


def test_undo_clears_last_stamp_and_disables_save(dialog, player):
    for pos in (1000, 2000, 3000):
        player.position = pos
        dialog._tap()
    assert dialog._save_btn.isEnabled() is True

    dialog._undo()

    assert dialog._idx == 2
    assert dialog._stamps[2] is None
    assert dialog._save_btn.isEnabled() is False
    assert dialog._current_lbl.text() == "line three"


def test_undo_at_line_one_is_a_no_op(dialog):
    dialog._undo()
    assert dialog._idx == 0


# ── AC7: Escape / Cancel / close discard without saving ────────────────────


def test_escape_key_discards_session_without_saving(dialog, update):
    dialog._tap()
    QTest.keyClick(dialog, Qt.Key_Escape)
    assert dialog.result() == QDialog.Rejected
    assert update.calls == []


def test_cancel_button_discards_session_without_saving(dialog, update):
    dialog._tap()
    dialog._cancel_btn.click()
    assert dialog.result() == QDialog.Rejected
    assert update.calls == []


def test_closing_the_window_discards_session_without_saving(dialog, update):
    dialog._tap()
    dialog.close()
    assert update.calls == []


# ── AC8 & AC9: Save writes a well-formed, round-trippable LRC block ────────


def test_save_writes_ordered_lrc_via_update_entities(dialog, player, update, track):
    for pos in (1000, 2000, 3000):
        player.position = pos
        dialog._tap()
    dialog._reaction_spin.setValue(0)

    dialog._save()

    assert len(update.calls) == 1
    model_name, entity_ids, kwargs = update.calls[0]
    assert model_name == "Track"
    assert entity_ids == [track.track_id]
    lrc_text = kwargs["lyrics"]

    is_synced, lines = parse_lyrics(lrc_text)
    assert is_synced is True
    assert [text for _, text in lines] == ["line one", "line two", "line three"]
    timestamps = [ts for ts, _ in lines]
    assert timestamps == sorted(timestamps)
    assert dialog.result() == QDialog.Accepted


def test_save_blocked_while_any_line_is_untapped(dialog, update):
    dialog._tap()
    dialog._save()
    assert update.calls == []


# ── AC10: saving refreshes the main view in place ───────────────────────────


def test_save_updates_view_lyrics_in_memory_and_rerenders(view, player, update):
    track = SimpleNamespace(track_id=7, lyrics=_PLAIN_LYRICS)
    view._update_lyrics(track)
    view.track = track

    view._on_open_sync_dialog()
    dlg = view._sync_dialog
    for pos in (1000, 2000, 3000):
        player.position = pos
        dlg._reaction_spin.setValue(0)
        dlg._tap()
    dlg._save()

    assert track.lyrics != _PLAIN_LYRICS
    assert view._is_synced is True
    assert view._sync_dialog is None  # cleared once the dialog finished


# ── AC11: mini transport drives the shared player ──────────────────────────


def test_play_pause_button_calls_shared_player_toggle(dialog, player):
    dialog._play_btn.click()
    assert player.toggle_calls == 1


def test_play_state_signal_updates_button_glyph(dialog, player):
    player.state_changed.emit("playing")
    assert dialog._play_btn.text() == "⏸"
    player.state_changed.emit("paused")
    assert dialog._play_btn.text() == "▶"


# ── AC12: reaction offset only affects future taps, and persists ───────────


def test_reaction_offset_change_does_not_alter_stamps_already_taken(dialog, player):
    dialog._reaction_spin.setValue(100)
    player.position = 5000
    dialog._tap()
    assert dialog._stamps[0] == 4900

    dialog._reaction_spin.setValue(300)
    player.position = 6000
    dialog._tap()

    assert dialog._stamps[0] == 4900
    assert dialog._stamps[1] == 5700


def test_reaction_offset_persists_via_app_config(dialog):
    original = app_config.get_manual_sync_reaction_ms()
    try:
        dialog._reaction_spin.setValue(350)
        assert app_config.get_manual_sync_reaction_ms() == 350
    finally:
        app_config.set_manual_sync_reaction_ms(original)
        app_config.save()


# ── AC13: track change closes any open dialog ───────────────────────────────


def test_track_change_closes_the_open_sync_dialog(view, update):
    track_a = SimpleNamespace(track_id=1, lyrics=_PLAIN_LYRICS)
    view._update_lyrics(track_a)
    view.track = track_a
    view._on_open_sync_dialog()
    assert view._sync_dialog is not None

    track_b = SimpleNamespace(
        track_id=2,
        lyrics="other lyrics",
        track_name="b",
        primary_artist_names="artist",
        artists=[],
        album=None,
        bpm=None,
        key=None,
        mode=None,
        primary_time_signature=None,
        play_count=None,
        genres=None,
        is_instrumental=False,
    )
    view.updateUI(track_b)

    assert view._sync_dialog is None
    assert update.calls == []  # no stamps were ever saved for the abandoned session


# ── AC14: Return/Backspace/Escape do nothing on the main view ──────────────


def test_sync_keys_have_no_effect_on_the_main_view_when_dialog_closed(view):
    view._update_lyrics(SimpleNamespace(lyrics=_PLAIN_LYRICS))
    view.show()
    for key in (Qt.Key_Return, Qt.Key_Backspace, Qt.Key_Escape):
        QTest.keyClick(view, key)
    assert view._sync_dialog is None
    view.hide()


# ── AC15: dialog reads raw lyrics, not the censored display copy ───────────


def test_sync_dialog_uses_raw_lyrics_not_the_display_copy(view):
    track = SimpleNamespace(track_id=3, lyrics="shit happened\nline two")
    view._update_lyrics(track)
    view.track = track
    # Simulate what a censored display copy would look like, to prove the
    # dialog doesn't source its lines from this.
    view._lyrics_lines = [(0, "s*** happened"), (0, "line two")]

    view._on_open_sync_dialog()

    assert view._sync_dialog._lines == ["shit happened", "line two"]
