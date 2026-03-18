# ---------------------------------------------------------------------------
# LyricsTab
# ---------------------------------------------------------------------------
from __future__ import annotations

from typing import Any, Dict

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from src.db_mapping_tracks import TRACK_FIELDS
from src.logger_config import logger
from src.track_edit_basetab import _BaseTab


class LyricsTab(_BaseTab):
    def __init__(self, tracks: list, controller, parent=None):
        super().__init__(tracks, controller, parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Search button row
        btn_row = QHBoxLayout()
        self._search_btn = QPushButton("🔍  Search Lyrics Online")
        self._search_btn.clicked.connect(self._search_lyrics)
        btn_row.addWidget(self._search_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        lbl = QLabel("Lyrics:")
        cfg = TRACK_FIELDS.get("lyrics")
        if cfg and cfg.tooltip:
            lbl.setToolTip(cfg.tooltip)
        layout.addWidget(lbl)

        self._edit = QTextEdit()
        self._edit.textChanged.connect(lambda: self._mark_dirty("lyrics"))
        layout.addWidget(self._edit)

    def load(self, tracks: list) -> None:
        self.tracks = tracks
        self._dirty.clear()
        if self.is_multi:
            # Block signals so setting the placeholder empty text does NOT
            # mark lyrics as dirty — we never want to overwrite all tracks
            # with null just because we cleared the box on load.
            self._edit.blockSignals(True)
            self._edit.setPlainText("")
            self._edit.blockSignals(False)
            self._search_btn.setEnabled(False)
        else:
            val = getattr(self.track, "lyrics", None) or ""
            self._edit.blockSignals(True)
            self._edit.setPlainText(val)
            self._edit.blockSignals(False)
            self._search_btn.setEnabled(True)

    def collect_changes(self) -> Dict[str, Any]:
        if "lyrics" not in self._dirty:
            return {}
        return {"lyrics": self._edit.toPlainText() or None}

    def _search_lyrics(self):
        try:
            from src.lyrics_search import search_lyrics_for_track

            lyrics = search_lyrics_for_track(self.track)
            if lyrics:
                formatted = self._format_lyrics(lyrics)
                self._edit.setPlainText(formatted)
            else:
                QMessageBox.information(self, "Lyrics Search", "No lyrics found.")
        except Exception as e:
            logger.error(f"Lyrics search error: {e}")
            QMessageBox.warning(self, "Lyrics Search", f"Search failed:\n{e}")

    @staticmethod
    def _format_lyrics(lyrics_obj) -> str:
        """
        Convert lyrics to a plain string.
        Handles three cases:
          - already a str → return as-is
          - object with a .lyrics dict attribute → format as [timestamp] line
          - bare dict → format as [timestamp] line
        """
        if isinstance(lyrics_obj, str):
            return lyrics_obj

        # Unwrap object wrapper if present
        lyrics_dict = getattr(lyrics_obj, "lyrics", lyrics_obj)

        if isinstance(lyrics_dict, dict):
            lines = []
            for ts in sorted(lyrics_dict.keys()):
                line = lyrics_dict[ts]
                if str(line).strip() == "♪":
                    lines.append("")
                else:
                    lines.append(f"[{ts}] {line}")
            return "\n".join(lines)

        # Fallback: just stringify whatever we got
        return str(lyrics_obj)
