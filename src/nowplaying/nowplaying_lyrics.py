"""
nowplaying_lyrics.py

Lyrics/karaoke sync engine for NowPlayingView: parsing, mode switching
(karaoke vs. full-text), position-driven active-line tracking, the
"lyrics coming soon" countdown, and the sync-offset slider.
"""

from src.foundation.censor import censor_text
from src.foundation.config_setup import app_config
from src.foundation.logger_config import logger
from src.nowplaying.nowplaying_lyrics_parser import active_index, parse_lyrics

# If the next lyric line starts more than this many ms in the future, show a
# countdown timer instead of a blank karaoke display.
_LYRIC_GAP_THRESHOLD_MS = 5_000


class NowPlayingLyricsMixin:
    """
    Expects the host class to provide: self._is_synced, self._show_all_lyrics,
    self._lyrics_lines, self._active_idx, self._last_position_ms,
    self._sync_offset_ms, self._saved_offset_tenths, self._offset_save_timer,
    self._countdown_timer, self._next_lyric_ms, self._karaoke_lbl,
    self._next_lyric_lbls, self._preview_capacity, self._karaoke_block,
    self._plain_area, self._plain_lbl, self._no_lyrics_lbl, self._countdown_lbl,
    self._offset_row, self._offset_lbl, self._offset_slider,
    self._toggle_mode_btn, self._sync_toggle_btn, self._recalc_preview_capacity(),
    self._set_active(), self._switch_tab(), self._PAGE_LYRICS,
    self._PAGE_CREDITS, and to be a QWidget subclass.
    """

    # ── lyrics mode toggle ─────────────────────────────────────────────────

    def _on_toggle_lyrics_mode(self):
        """Switch between karaoke (synced) and full plain text view."""
        self._show_all_lyrics = not self._show_all_lyrics
        if self._show_all_lyrics:
            self._toggle_mode_btn.setText("KARAOKE")
            self._set_active(self._toggle_mode_btn, True)
            # Show full plain text from the synced lines
            text = "\n".join(t for _, t in self._lyrics_lines)
            self._karaoke_lbl.setVisible(False)
            self._hide_next_lyric_lbls()
            self._karaoke_block.setVisible(False)
            self._countdown_lbl.setVisible(False)
            self._countdown_timer.stop()
            self._plain_lbl.setText(text)
            self._plain_area.setVisible(True)
            self._plain_area.verticalScrollBar().setValue(0)
        else:
            self._toggle_mode_btn.setText("SHOW ALL")
            self._set_active(self._toggle_mode_btn, False)
            self._plain_area.setVisible(False)
            self._karaoke_block.setVisible(True)
            self._karaoke_lbl.setVisible(True)
            # Re-trigger display at current position
            self._last_position_ms = -1

    # ── lyrics ────────────────────────────────────────────────────────────

    def _update_lyrics(self, track):
        raw = censor_text(getattr(track, "lyrics", None))

        # Reset state
        self._is_synced = False
        self._show_all_lyrics = False
        self._lyrics_lines = []
        self._active_idx = -1
        self._last_position_ms = -1
        self._countdown_timer.stop()
        self._next_lyric_ms = -1

        if not raw or not raw.strip():
            self._set_lyrics_mode_none()
            return

        is_synced, lines = parse_lyrics(raw)
        self._lyrics_lines = lines
        self._is_synced = is_synced

        if is_synced:
            self._set_lyrics_mode_karaoke()
            # Don't blindly show first line — let position sync handle it.
            # (Handles the case where lyrics start 5 min in.)
        else:
            self._set_lyrics_mode_plain("\n".join(t for _, t in lines))

    def _set_lyrics_mode_none(self):
        """No lyrics available — switch to Credits tab automatically."""
        self._is_synced = False
        self._lyrics_lines = []
        self._active_idx = -1
        self._countdown_timer.stop()
        self._karaoke_lbl.setVisible(False)
        self._karaoke_lbl.clear_line()
        self._hide_next_lyric_lbls()
        self._karaoke_block.setVisible(False)
        self._plain_area.setVisible(False)
        self._plain_lbl.setText("")
        self._no_lyrics_lbl.setVisible(False)
        self._countdown_lbl.setVisible(False)
        self._offset_row.setVisible(False)
        # Auto-switch to Credits
        self._switch_tab(self._PAGE_CREDITS)

    def _set_lyrics_mode_karaoke(self):
        self._plain_area.setVisible(False)
        self._no_lyrics_lbl.setVisible(False)
        self._countdown_lbl.setVisible(False)
        self._hide_next_lyric_lbls()
        self._karaoke_block.setVisible(True)
        self._karaoke_lbl.setVisible(True)
        # Restore saved slider value (already set in __init__, keep it)
        # Slider row stays hidden until user clicks the ⏱ toggle
        # Reset toggle button label
        self._toggle_mode_btn.setText("SHOW ALL")
        self._set_active(self._toggle_mode_btn, False)
        # Switch to lyrics tab
        self._switch_tab(self._PAGE_LYRICS)

    def _set_lyrics_mode_plain(self, text: str):
        self._karaoke_lbl.setVisible(False)
        self._karaoke_lbl.clear_line()
        self._hide_next_lyric_lbls()
        self._karaoke_block.setVisible(False)
        self._no_lyrics_lbl.setVisible(False)
        self._countdown_lbl.setVisible(False)
        self._offset_row.setVisible(False)
        self._plain_lbl.setText(text)
        self._plain_area.setVisible(True)
        self._plain_area.verticalScrollBar().setValue(0)
        # Switch to lyrics tab
        self._switch_tab(self._PAGE_LYRICS)

    # ── position sync ─────────────────────────────────────────────────────

    def _on_position_changed(self, position_ms: int):
        if not self._is_synced or not self._lyrics_lines:
            return
        # Skip if we're in "show all" mode — no karaoke tracking needed
        if self._show_all_lyrics:
            return
        if abs(position_ms - self._last_position_ms) < 150:
            return
        self._last_position_ms = position_ms

        effective_ms = position_ms + self._sync_offset_ms

        # Find which line is current and what the next line's timestamp is
        new_idx = active_index(self._lyrics_lines, effective_ms)

        # Check gap to next upcoming lyric
        next_ts = self._find_next_lyric_ts(effective_ms)
        gap_ms = next_ts - effective_ms if next_ts >= 0 else -1

        # If we haven't reached the first lyric yet and it's far away → countdown
        if new_idx == 0 and self._lyrics_lines[0][0] > effective_ms:
            gap_to_first = self._lyrics_lines[0][0] - effective_ms
            if gap_to_first >= _LYRIC_GAP_THRESHOLD_MS:
                self._start_countdown(self._lyrics_lines[0][0])
                return

        # If current line is showing but next is far away → countdown after showing
        if new_idx == self._active_idx and gap_ms >= _LYRIC_GAP_THRESHOLD_MS:
            self._start_countdown(next_ts)
            return

        # Normal lyric display
        if new_idx != self._active_idx:
            self._stop_countdown()
            self._active_idx = new_idx
            text = self._lyrics_lines[new_idx][1]
            if text.strip():
                self._karaoke_lbl.show_line(text)
            else:
                # Blank line — check if next lyric is far
                if gap_ms >= _LYRIC_GAP_THRESHOLD_MS and next_ts >= 0:
                    self._start_countdown(next_ts)

            # Update next-line preview
            self._update_next_lyric_lbl(new_idx)

    def _update_next_lyric_lbl(self, current_idx: int):
        """Fill the upcoming-lyric preview stack below the current karaoke line.

        Populates up to ``self._preview_capacity`` rows — fitted to the karaoke
        block's height by ``_recalc_preview_capacity`` — with the next non-empty
        lyric lines; any rows left over are cleared and hidden.
        """
        self._recalc_preview_capacity()
        cap = min(self._preview_capacity, len(self._next_lyric_lbls))
        upcoming: list[str] = []
        for i in range(current_idx + 1, len(self._lyrics_lines)):
            t = self._lyrics_lines[i][1].strip()
            if t:
                upcoming.append(t)
                if len(upcoming) == cap:
                    break
        for lbl, text in zip(self._next_lyric_lbls, upcoming, strict=False):
            lbl.setText(text)
            lbl.setVisible(True)
        for lbl in self._next_lyric_lbls[len(upcoming) :]:
            lbl.setText("")
            lbl.setVisible(False)

    def _hide_next_lyric_lbls(self):
        """Clear and hide every row of the upcoming-lyric preview stack."""
        for lbl in self._next_lyric_lbls:
            lbl.setText("")
            lbl.setVisible(False)

    def _on_toggle_sync_slider(self):
        """Show/hide the sync offset slider row."""
        visible = self._offset_row.isVisible()
        self._offset_row.setVisible(not visible)
        self._set_active(self._sync_toggle_btn, not visible)

    def _find_next_lyric_ts(self, effective_ms: int) -> int:
        """Return timestamp of the next lyric line after effective_ms, or -1."""
        for ts, text in self._lyrics_lines:
            if ts > effective_ms and text.strip():
                return ts
        return -1

    def _start_countdown(self, target_ms: int):
        """Show countdown to target_ms below the current lyric line.
        The karaoke label stays visible so the last line isn't clipped away —
        only the small countdown indicator is added beneath it."""
        self._next_lyric_ms = target_ms
        # Keep karaoke label showing — don't hide it
        self._karaoke_lbl.setVisible(True)
        self._countdown_lbl.setVisible(True)
        self._update_countdown()
        if not self._countdown_timer.isActive():
            self._countdown_timer.start()

    def _stop_countdown(self):
        self._countdown_timer.stop()
        self._countdown_lbl.setVisible(False)
        self._karaoke_lbl.setVisible(True)
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

    def _on_offset_changed(self, value: int):
        """Slider moved — update offset immediately, debounce the config save."""
        self._sync_offset_ms = value * 100
        secs = self._sync_offset_ms / 1000
        sign = "+" if secs >= 0 else "−"  # noqa: RUF001 (U+2212 minus glyph)
        self._offset_lbl.setText(f"Sync  {sign}{abs(secs):.1f}s")
        self._last_position_ms = -1
        # Restart debounce timer
        self._offset_save_timer.start()

    def _save_offset_to_config(self):
        """Persist the current offset value to config."""
        try:
            app_config.set_lyrics_sync_offset(self._offset_slider.value())
            app_config.save()
            logger.debug(f"Saved lyrics sync offset: {self._offset_slider.value()}")
        except RuntimeError as exc:
            logger.warning(f"Could not save lyrics sync offset: {exc}")
