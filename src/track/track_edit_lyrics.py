# ---------------------------------------------------------------------------
# LyricsTab
# ---------------------------------------------------------------------------
from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout

from src.core.censor import text_contains_explicit_words
from src.core.logger_config import logger
from src.core.status_utility import show_status_message
from src.db.db_mapping_tracks import TRACK_FIELDS
from src.lyrics.mood_autotag import auto_tag_lyrics_safe
from src.track.track_edit_basetab import _BaseTab
from src.track.track_edit_fieldform import (
    _coerce,
    _make_widget_for_field,
    _read_widget,
    _write_widget,
)


class LyricsTab(_BaseTab):
    def __init__(self, tracks: list, controller, parent=None):
        super().__init__(tracks, controller, parent)
        self._explicit_widget = None

        from src.lyrics.lyrics_search import LyricSearchThread

        self._lyric_thread = LyricSearchThread(parent=self)
        self._lyric_thread.lyrics_ready.connect(self._on_lyrics_ready)
        self._lyric_thread.lyrics_not_found.connect(self._on_lyrics_not_found)
        self._lyric_thread.error_occurred.connect(self._on_lyric_error)

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Search button + Explicit checkbox row
        btn_row = QHBoxLayout()
        self._search_btn = QPushButton("🔍  Search Lyrics Online")
        self._search_btn.clicked.connect(self._search_lyrics)
        btn_row.addWidget(self._search_btn)
        btn_row.addStretch()

        explicit_cfg = TRACK_FIELDS.get("is_explicit")
        if explicit_cfg:
            explicit_lbl = QLabel(f"{explicit_cfg.friendly or 'Explicit'}:")
            if explicit_cfg.tooltip:
                explicit_lbl.setToolTip(explicit_cfg.tooltip)
            self._explicit_widget = _make_widget_for_field(
                "is_explicit", explicit_cfg, self._mark_dirty
            )
            btn_row.addWidget(explicit_lbl)
            btn_row.addWidget(self._explicit_widget)

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
        self._lyric_thread.stop()
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

        if self._explicit_widget is not None:
            if self.is_multi:
                values = [getattr(t, "is_explicit", None) for t in tracks]
                unique = {str(v) for v in values}
                _write_widget(
                    self._explicit_widget, values[0] if len(unique) == 1 else None
                )
            else:
                _write_widget(
                    self._explicit_widget, getattr(self.track, "is_explicit", None)
                )

    def collect_changes(self) -> dict[str, Any]:
        changes = {}
        lyrics_dirty = "lyrics" in self._dirty
        if lyrics_dirty:
            final_lyrics = self._edit.toPlainText() or None
            changes["lyrics"] = final_lyrics
        else:
            final_lyrics = getattr(self.track, "lyrics", None)

        if self._explicit_widget is not None:
            if "is_explicit" in self._dirty:
                cfg = TRACK_FIELDS.get("is_explicit")
                new_val = _coerce(_read_widget(self._explicit_widget), cfg)
                if self.is_multi or self._has_changed("is_explicit", new_val):
                    changes["is_explicit"] = new_val
            elif (
                not self.is_multi
                and getattr(self.track, "is_explicit", None) is None
                and final_lyrics
            ):
                # Auto-fill only ever touches a never-determined (NULL)
                # value -- once is_explicit is anything else, manual or
                # auto-calculated, later saves leave it alone.
                changes["is_explicit"] = text_contains_explicit_words(final_lyrics)

        if lyrics_dirty and not self.is_multi and final_lyrics:
            moods_added, _places_added = self._run_mood_autotag(final_lyrics)
            if moods_added:
                show_status_message(self, f"Tagged mood(s): {', '.join(moods_added)}.")

        return changes

    def _run_mood_autotag(self, lyrics: str) -> tuple[list, list]:
        """Score `lyrics` and write any newly-matching mood/place
        associations for this track, additive-only. Never raises -- a
        matching failure must not block saving the rest of the dialog."""
        return auto_tag_lyrics_safe(self.controller, self.track.track_id, lyrics)

    def _search_lyrics(self):
        self._search_btn.setEnabled(False)
        show_status_message(self, "Searching for lyrics…", duration=0)
        self._lyric_thread.search(self.track)

    def _on_lyrics_ready(self, lyrics) -> None:
        formatted = self._format_lyrics(lyrics)
        self._edit.setPlainText(formatted)
        self._search_btn.setEnabled(True)
        message = "Lyrics found."
        if not self.is_multi and formatted:
            moods_added, _places_added = self._run_mood_autotag(formatted)
            if moods_added:
                message += f" Tagged mood(s): {', '.join(moods_added)}."
        show_status_message(self, message)

    def _on_lyrics_not_found(self) -> None:
        self._search_btn.setEnabled(True)
        show_status_message(self, "No lyrics found.")

    def _on_lyric_error(self, message: str) -> None:
        logger.error(f"Lyrics search error: {message}")
        self._search_btn.setEnabled(True)
        show_status_message(self, f"Lyrics search failed: {message}")

    def cleanup(self) -> None:
        self._lyric_thread.stop()

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
