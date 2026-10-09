"""Manual tap-to-sync dialog for lyrics (see docs/specs/manual_lyric_sync.md)."""

import contextlib

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSizePolicy, QSpinBox, QVBoxLayout

from src.foundation.config_setup import app_config
from src.foundation.logger_config import logger
from src.nowplaying.lyrics.nowplaying_karaoke import _KaraokeLine
from src.nowplaying.lyrics.nowplaying_lyrics_parser import build_lrc

# How many upcoming lines to preview below the current one.
_PREVIEW_ROWS = 3


class LyricSyncDialog(QDialog):
    """Tap-to-sync tool that stamps each of one track's lyric lines with the player position."""

    # ``lines`` is plain text, one entry per lyric line; the caller strips any old timestamps.

    saved = Signal()

    def __init__(self, controller, track, lines: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sync Lyrics")
        self._controller = controller
        self._track = track
        self._lines = list(lines)
        self._stamps: list[int | None] = [None] * len(self._lines)
        self._idx = 0
        self._player = controller.mediaplayer

        self._build_ui()
        self._player.state_changed.connect(self._on_state_changed)
        self._player_connected = True
        self._refresh()

    # ── UI ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        """Build the progress line, line preview, transport row, and buttons."""
        layout = QVBoxLayout(self)

        self._progress_lbl = QLabel()
        self._progress_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._progress_lbl)

        self._current_lbl = _KaraokeLine()
        layout.addWidget(self._current_lbl)

        self._preview_lbls: list[QLabel] = []
        for i in range(_PREVIEW_ROWS):
            role = "nextLyric" if i == 0 else "nextLyric2" if i == 1 else "nextLyric3"
            lbl = QLabel("")
            lbl.setProperty("npRole", role)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setWordWrap(True)
            layout.addWidget(lbl)
            self._preview_lbls.append(lbl)

        transport_row = QHBoxLayout()
        self._play_btn = QPushButton("▶")
        self._play_btn.setCursor(Qt.PointingHandCursor)
        self._play_btn.clicked.connect(self._player.toggle_play_pause)
        transport_row.addWidget(self._play_btn)

        transport_row.addWidget(QLabel("Reaction:"))
        self._reaction_spin = QSpinBox()
        self._reaction_spin.setRange(0, 500)
        self._reaction_spin.setSingleStep(10)
        self._reaction_spin.setSuffix(" ms")
        self._reaction_spin.setValue(app_config.get_manual_sync_reaction_ms())
        self._reaction_spin.valueChanged.connect(self._on_reaction_changed)
        transport_row.addWidget(self._reaction_spin)
        transport_row.addStretch(1)
        layout.addLayout(transport_row)

        btn_row = QHBoxLayout()
        self._undo_btn = QPushButton("Undo")
        self._undo_btn.clicked.connect(self._undo)
        btn_row.addWidget(self._undo_btn)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._cancel_btn)

        btn_row.addStretch(1)

        self._save_btn = QPushButton("Save")
        self._save_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._save_btn.clicked.connect(self._save)
        btn_row.addWidget(self._save_btn)
        layout.addLayout(btn_row)

        hint = QLabel("Enter: stamp line  ·  Backspace: undo  ·  Esc: cancel")
        hint.setAlignment(Qt.AlignCenter)
        hint.setProperty("npRole", "syncHint")
        layout.addWidget(hint)

        # An autoDefault button eats Enter (it clicks itself), so Enter could
        # never reach keyPressEvent's tap while a button has focus.
        for btn in (self._play_btn, self._undo_btn, self._cancel_btn, self._save_btn):
            btn.setAutoDefault(False)
            btn.setDefault(False)

    # ── display ──────────────────────────────────────────────────────────

    def _on_state_changed(self, state: str):
        """Show pause while playing, play otherwise."""
        self._play_btn.setText("⏸" if state == "playing" else "▶")

    def _on_reaction_changed(self, value: int):
        """Persist the reaction-time offset."""
        app_config.set_manual_sync_reaction_ms(value)
        app_config.save()

    def _refresh(self):
        """Show the current line, the preview, and the button states."""
        total = len(self._lines)
        self._progress_lbl.setText(f"Line {min(self._idx + 1, total)} of {total}")
        self._current_lbl.show_line(self._lines[self._idx] if self._idx < total else "")

        upcoming = self._lines[self._idx + 1 :]
        for lbl, text in zip(self._preview_lbls, upcoming, strict=False):
            lbl.setText(text)
        for lbl in self._preview_lbls[len(upcoming) :]:
            lbl.setText("")

        self._undo_btn.setEnabled(self._idx > 0)
        self._save_btn.setEnabled(self._idx >= total)

    # ── tap engine ───────────────────────────────────────────────────────

    def _tap(self):
        """Stamp the current line with the player position minus the reaction offset."""
        if self._idx >= len(self._lines):
            return
        raw_ms = self._player.position
        ts = raw_ms - self._reaction_spin.value()
        prev_ts = self._stamps[self._idx - 1] if self._idx > 0 else None
        if prev_ts is not None:
            ts = max(ts, prev_ts + 1)
        self._stamps[self._idx] = max(0, ts)
        self._idx += 1
        self._refresh()

    def _undo(self):
        """Remove the last stamp."""
        if self._idx == 0:
            return
        self._idx -= 1
        self._stamps[self._idx] = None
        self._refresh()

    def _save(self):
        """Write the stamped lines to the track as LRC and close."""
        if any(s is None for s in self._stamps):
            return
        lrc_text = build_lrc(list(zip(self._stamps, self._lines, strict=True)))
        if not self._controller.update.update_entities("Track", [self._track.track_id], lyrics=lrc_text):
            logger.error("LyricSyncDialog: failed to save synced lyrics")
            # Keep the dialog open so the taps are not lost; the user can retry.
            QMessageBox.warning(self, "Sync Lyrics", "The synced lyrics could not be saved. Try again, or cancel to discard.")
            return
        self._track.lyrics = lrc_text
        self.saved.emit()
        self.accept()

    # ── keys ─────────────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        """Enter stamps, Backspace undoes, Esc cancels."""
        key = event.key()
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self._tap()
        elif key == Qt.Key_Backspace:
            self._undo()
        elif key == Qt.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)

    def done(self, result: int):
        """Disconnect from the player on accept/reject, which never run closeEvent."""
        self._disconnect_player()
        super().done(result)

    def closeEvent(self, event):
        """Disconnect from the player when the window closes (also covers a never-shown dialog)."""
        self._disconnect_player()
        super().closeEvent(event)

    def _disconnect_player(self):
        """Stop listening to the player; safe to call more than once."""
        if not self._player_connected:
            return
        self._player_connected = False
        with contextlib.suppress(RuntimeError, TypeError):
            self._player.state_changed.disconnect(self._on_state_changed)
