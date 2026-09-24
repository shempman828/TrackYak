"""
nowplaying_lyrics.py

Lyrics/karaoke sync engine for NowPlayingView: parsing, feeding the lyric
column, follow vs. browse-all mode, position-driven active-line tracking,
the "lyrics coming soon" countdown, and the sync-offset stepper.
"""

from src.foundation.censor import censor_text
from src.foundation.config_setup import app_config
from src.foundation.logger_config import logger
from src.nowplaying.nowplaying_lyrics_parser import active_index, parse_lyrics
from src.nowplaying.nowplaying_lyrics_sync_dialog import LyricSyncDialog

# If the next lyric line starts more than this many ms in the future, show a
# countdown under the lyric column.
_LYRIC_GAP_THRESHOLD_MS = 5_000

# Plain (unsynced) lyrics are paced across this share of the track, skipping
# a rough intro and outro where nobody sings.
_PACE_START = 0.05
_PACE_END = 0.95

# The sync offset is stored in tenths of a second and limited to ±5 s.
_OFFSET_LIMIT_TENTHS = 50


class NowPlayingLyricsMixin:
    """
    Expects the host class to provide: self._is_synced, self._show_all_lyrics,
    self._lyrics_lines, self._active_idx, self._last_position_ms,
    self._sync_offset_ms, self._offset_tenths, self._offset_save_timer,
    self._countdown_timer, self._next_lyric_ms, self._lyric_column,
    self._countdown_lbl, self._lyrics_toolbar, self._offset_group,
    self._offset_value_btn, self._toggle_mode_btn, self._manual_sync_btn,
    self._sync_dialog, self._set_active(), self._switch_tab(), self._player_duration(),
    self._PAGE_LYRICS, self._PAGE_CREDITS, self.controller, self.track, and to
    be a QWidget subclass.
    """

    # ── follow / browse-all toggle ─────────────────────────────────────────

    def _on_toggle_lyrics_mode(self):
        """ALL LINES button: switch between following the song and browsing."""
        self._lyric_column.set_following(not self._lyric_column.is_following())

    def _on_follow_changed(self, following: bool):
        """The column started or stopped following (button or wheel scroll)."""
        self._show_all_lyrics = not following and bool(self._lyrics_lines)
        self._set_active(self._toggle_mode_btn, self._show_all_lyrics)
        if following:
            # Re-sync on the next position tick instead of waiting for a line change.
            self._last_position_ms = -1

    # ── lyrics ────────────────────────────────────────────────────────────

    def _update_lyrics(self, track):
        # Instrumental tracks have no lyrics by definition — disable the LYRICS
        # tab entirely and pin the panel to CREDITS. Re-enable for every other
        # track so the state tracks the current track, not history.
        instrumental = bool(getattr(track, "is_instrumental", None))
        self._tab_lyrics.setEnabled(not instrumental)

        raw = None if instrumental else censor_text(getattr(track, "lyrics", None))

        # Reset state
        self._is_synced = False
        self._show_all_lyrics = False
        self._lyrics_lines = []
        self._active_idx = -1
        self._last_position_ms = -1
        self._stop_countdown()
        self._manual_sync_btn.setEnabled(False)

        if not raw or not raw.strip():
            self._set_lyrics_mode_none()
            return

        is_synced, lines = parse_lyrics(raw)
        self._lyrics_lines = lines
        self._is_synced = is_synced
        self._manual_sync_btn.setEnabled(bool(lines))

        if is_synced:
            self._set_lyrics_mode_karaoke()
            # Don't highlight the first line yet — position sync handles it.
            # (Handles the case where lyrics start 5 min in.)
        else:
            self._set_lyrics_mode_plain()

    def _set_lyrics_mode_none(self):
        """No lyrics available — switch to Credits tab automatically."""
        self._is_synced = False
        self._lyrics_lines = []
        self._active_idx = -1
        self._stop_countdown()
        self._lyric_column.clear()
        self._lyrics_toolbar.setVisible(False)
        self._switch_tab(self._PAGE_CREDITS)

    def _set_lyrics_mode_karaoke(self):
        self._lyric_column.set_lines([t for _, t in self._lyrics_lines], synced=True)
        self._set_active(self._toggle_mode_btn, False)
        self._toggle_mode_btn.setVisible(True)
        self._offset_group.setVisible(True)
        self._lyrics_toolbar.setVisible(True)
        self._switch_tab(self._PAGE_LYRICS)

    def _set_lyrics_mode_plain(self):
        self._lyric_column.set_lines([t for _, t in self._lyrics_lines], synced=False)
        # Unsynced text is paced by song progress; it has no timing to offset.
        self._set_active(self._toggle_mode_btn, False)
        self._toggle_mode_btn.setVisible(True)
        self._offset_group.setVisible(False)
        self._lyrics_toolbar.setVisible(True)
        self._switch_tab(self._PAGE_LYRICS)

    # ── position sync ─────────────────────────────────────────────────────

    def _on_position_changed(self, position_ms: int):
        if not self._lyrics_lines:
            return
        if not self._is_synced:
            self._pace_plain_lyrics(position_ms)
            return
        if abs(position_ms - self._last_position_ms) < 150:
            return
        self._last_position_ms = position_ms

        effective_ms = position_ms + self._sync_offset_ms

        # Before the first line nothing is highlighted; the first line waits
        # on the anchor as the upcoming one.
        before_first = self._lyrics_lines[0][0] > effective_ms
        new_idx = -1 if before_first else active_index(self._lyrics_lines, effective_ms)

        if new_idx != self._active_idx:
            self._active_idx = new_idx
            self._lyric_column.set_active(new_idx)

        next_ts = self._find_next_lyric_ts(effective_ms)
        gap_ms = next_ts - effective_ms if next_ts >= 0 else -1
        if gap_ms >= _LYRIC_GAP_THRESHOLD_MS:
            self._start_countdown(next_ts)
        else:
            self._stop_countdown()

    def _pace_plain_lyrics(self, position_ms: int):
        """Scroll unsynced lyrics by song progress, mapped over _PACE_START.._PACE_END."""
        duration = self._player_duration()
        if duration <= 0:
            return
        fraction = (position_ms / duration - _PACE_START) / (_PACE_END - _PACE_START)
        self._lyric_column.set_progress(fraction)

    def _find_next_lyric_ts(self, effective_ms: int) -> int:
        """Return timestamp of the next lyric line after effective_ms, or -1."""
        for ts, text in self._lyrics_lines:
            if ts > effective_ms and text.strip():
                return ts
        return -1

    def _start_countdown(self, target_ms: int):
        """Show the countdown to target_ms under the lyric column."""
        self._next_lyric_ms = target_ms
        self._countdown_lbl.setVisible(True)
        self._update_countdown()
        if not self._countdown_timer.isActive():
            self._countdown_timer.start()

    def _stop_countdown(self):
        self._countdown_timer.stop()
        self._countdown_lbl.setVisible(False)
        self._next_lyric_ms = -1

    def _update_countdown(self):
        """Refresh the countdown label text."""
        if self._next_lyric_ms < 0:
            self._countdown_timer.stop()
            return
        remaining_ms = self._next_lyric_ms - (self._last_position_ms + self._sync_offset_ms)
        if remaining_ms <= 0:
            self._stop_countdown()
            return
        secs = remaining_ms / 1000
        if secs >= 60:
            m, s = int(secs) // 60, int(secs) % 60
            txt = f"♪  in {m}:{s:02d}"
        else:
            txt = f"♪  in {secs:.0f}s"
        self._countdown_lbl.setText(txt)

    # ── sync offset stepper ──────────────────────────────────────────────

    def _on_offset_changed(self, value: int):
        """Set the offset (tenths of a second), then debounce the config save."""
        value = max(-_OFFSET_LIMIT_TENTHS, min(_OFFSET_LIMIT_TENTHS, int(value)))
        self._offset_tenths = value
        self._sync_offset_ms = value * 100
        self._refresh_offset_btn()
        self._last_position_ms = -1
        self._offset_save_timer.start()

    def _nudge_offset(self, step: int):
        self._on_offset_changed(self._offset_tenths + step)

    def _reset_offset(self):
        self._on_offset_changed(0)

    def _refresh_offset_btn(self):
        """Show the offset on the value button; highlight it when it is not 0."""
        secs = self._offset_tenths / 10
        if self._offset_tenths == 0:
            text = "⏱ 0.0s"
        else:
            sign = "+" if secs > 0 else "−"  # noqa: RUF001 (U+2212 minus glyph)
            text = f"⏱ {sign}{abs(secs):.1f}s"
        self._offset_value_btn.setText(text)
        self._set_active(self._offset_value_btn, self._offset_tenths != 0)

    def _save_offset_to_config(self):
        """Persist the current offset value to config."""
        try:
            app_config.set_lyrics_sync_offset(self._offset_tenths)
            app_config.save()
            logger.debug(f"Saved lyrics sync offset: {self._offset_tenths}")
        except RuntimeError as exc:
            logger.warning(f"Could not save lyrics sync offset: {exc}")

    # ── manual sync dialog ───────────────────────────────────────────────

    def _on_open_sync_dialog(self):
        """Launch the tap-to-sync dialog for the current track's raw lyrics.

        Reads from the track's raw ``lyrics`` field, not ``self._lyrics_lines``
        — the latter is built from the censor-filtered display copy, and
        syncing off it would permanently bake censored placeholders into the
        saved lyrics for tracks with explicit content.
        """
        if not self.track or not self._lyrics_lines:
            return
        raw = getattr(self.track, "lyrics", None) or ""
        _, lines = parse_lyrics(raw)
        plain_lines = [text for _, text in lines]
        if not plain_lines:
            return

        dlg = LyricSyncDialog(self.controller, self.track, plain_lines, self)
        dlg.saved.connect(lambda: self._update_lyrics(self.track))
        dlg.finished.connect(lambda _=None: setattr(self, "_sync_dialog", None))
        self._sync_dialog = dlg
        dlg.show()

    def _close_sync_dialog(self):
        """Discard any in-progress manual-sync session (e.g. on track change)."""
        dlg = getattr(self, "_sync_dialog", None)
        if dlg is not None:
            dlg.close()
