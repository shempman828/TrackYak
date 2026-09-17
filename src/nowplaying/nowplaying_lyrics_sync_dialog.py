"""LyricSyncDialog — manual tap-to-sync tool launched from NowPlayingView.

Lets a user re-time a track's lyrics line-by-line: Return stamps the line
currently shown with the player's position (minus a reaction-time offset),
Backspace undoes the last stamp, Escape/Cancel discards the session, and
Save writes the result back to ``Track.lyrics`` once every line has a
stamp. See docs/specs/manual_lyric_sync.md.
"""

import contextlib

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
)

from src.foundation.config_setup import app_config
from src.foundation.logger_config import logger
from src.nowplaying.nowplaying_karaoke import _KaraokeLine
from src.nowplaying.nowplaying_lyrics_parser import build_lrc

# How many upcoming lines to preview below the current one.
_PREVIEW_ROWS = 3


class LyricSyncDialog(QDialog):
    """Tap-to-sync tool for one track's lyric lines.

    ``lines`` is the track's plain lyric text (already stripped of any
    existing timestamps by the caller) — one entry per lyric line, in order.
    """

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
        self._refresh()

    # ── UI ───────────────────────────────────────────────────────────────

    def _build_ui(self):
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

    # ── display ──────────────────────────────────────────────────────────

    def _on_state_changed(self, state: str):
        self._play_btn.setText("⏸" if state == "playing" else "▶")

    def _on_reaction_changed(self, value: int):
        app_config.set_manual_sync_reaction_ms(value)
        app_config.save()

    def _refresh(self):
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
        if self._idx == 0:
            return
        self._idx -= 1
        self._stamps[self._idx] = None
        self._refresh()

    def _save(self):
        if any(s is None for s in self._stamps):
            return
        lrc_text = build_lrc(list(zip(self._stamps, self._lines, strict=True)))
        if not self._controller.update.update_entities(
            "Track", [self._track.track_id], lyrics=lrc_text
        ):
            logger.error("LyricSyncDialog: failed to save synced lyrics")
            return
        self._track.lyrics = lrc_text
        self.saved.emit()
        self.accept()

    # ── keys ─────────────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self._tap()
        elif key == Qt.Key_Backspace:
            self._undo()
        elif key == Qt.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        with contextlib.suppress(RuntimeError, TypeError):
            self._player.state_changed.disconnect(self._on_state_changed)
        super().closeEvent(event)
