# track_edit.py
"""
Track editing dialog — clean architecture.

Design rules:
  - ONE file. No external loaders/searchers modules.
  - Each tab is a self-contained QWidget subclass.
    It receives (tracks, controller) and owns its own load/save/search logic.
    It NEVER reaches back into the parent dialog.
  - TrackEditDialog accepts a single track OR a list of tracks.
    is_multi is a flag, not a separate class.
  - The dialog's Save button calls tab.collect_changes() on every tab and
    commits everything in one pass.
  - Relationship tabs (roles, genres, places, moods, awards, samples) write
    directly to the database on Add/Remove — no deferred save needed.
"""

from __future__ import annotations

from typing import Any, Dict, List, Union

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.db_mapping_tracks import TRACK_FIELDS
from src.logger_config import logger
from src.wikipedia_seach import search_wikipedia


# ---------------------------------------------------------------------------
# Helpers shared by all tabs
# ---------------------------------------------------------------------------


def _make_widget_for_field(field_name: str, field_config, on_change_cb):
    """
    Create and return the right editable widget for a TrackField.
    Connects the widget's change signal to on_change_cb(field_name).
    """
    if field_config.type == bool:  # noqa: E721
        w = QCheckBox()
        w.toggled.connect(lambda _checked, fn=field_name: on_change_cb(fn))
    elif field_config.type == int:  # noqa: E721
        w = QSpinBox()
        w.setRange(
            int(field_config.min) if field_config.min is not None else -2_147_483_648,
            int(field_config.max) if field_config.max is not None else 2_147_483_647,
        )
        w.valueChanged.connect(lambda _v, fn=field_name: on_change_cb(fn))
    elif field_config.type == float:  # noqa: E721
        w = QDoubleSpinBox()
        w.setDecimals(4)
        w.setRange(
            field_config.min if field_config.min is not None else -1e9,
            field_config.max if field_config.max is not None else 1e9,
        )
        w.valueChanged.connect(lambda _v, fn=field_name: on_change_cb(fn))
    elif field_config.longtext:
        w = QTextEdit()
        w.textChanged.connect(lambda fn=field_name: on_change_cb(fn))
    else:
        w = QLineEdit()
        if field_config.placeholder:
            w.setPlaceholderText(field_config.placeholder)
        if field_config.length:
            w.setMaxLength(field_config.length)
        w.textChanged.connect(lambda _t, fn=field_name: on_change_cb(fn))
    return w


def _read_widget(widget) -> Any:
    """Return the current value from any supported widget type."""
    if isinstance(widget, QCheckBox):
        return widget.isChecked()
    if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
        return widget.value()
    if isinstance(widget, QTextEdit):
        return widget.toPlainText()
    if isinstance(widget, QLineEdit):
        return widget.text()
    return None


def _write_widget(widget, value) -> None:
    """Write a value into any supported widget type without triggering signals."""
    if value is None:
        value_for_widget = None
    else:
        value_for_widget = value

    widget.blockSignals(True)
    try:
        if isinstance(widget, QCheckBox):
            widget.setChecked(
                bool(value_for_widget) if value_for_widget is not None else False
            )
        elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            widget.setValue(value_for_widget if value_for_widget is not None else 0)
        elif isinstance(widget, QTextEdit):
            widget.setPlainText(
                str(value_for_widget) if value_for_widget is not None else ""
            )
        elif isinstance(widget, QLineEdit):
            widget.setText(
                str(value_for_widget) if value_for_widget is not None else ""
            )
    finally:
        widget.blockSignals(False)


def _coerce(value, field_config) -> Any:
    """Convert a raw widget value to the correct Python type."""
    if value in (None, ""):
        return None
    try:
        if field_config.type == int:  # noqa: E721
            return int(value)
        if field_config.type == float:  # noqa: E721
            return float(value)
        if field_config.type == bool:  # noqa: E721
            return bool(value)
    except (ValueError, TypeError):
        return None
    return value


def _format_readonly(value, field_config) -> str:
    """Format a value for display in a readonly QLabel."""
    if value is None or value == "":
        return "—"
    if field_config and field_config.type == bool:  # noqa: E721
        return "Yes" if value else "No"
    text = str(value)
    if len(text) > 80:
        return text[:77] + "..."
    return text


# ---------------------------------------------------------------------------
# Base class for all tabs
# ---------------------------------------------------------------------------


class _BaseTab(QWidget):
    """
    Every tab subclass must implement:
      load(tracks)          — populate widgets from the track(s)
      collect_changes()     — return {field_name: new_value} for scalar fields
                              (relationship tabs return {} — they write directly)
    """

    def __init__(self, tracks: list, controller, parent=None):
        super().__init__(parent)
        # Always a list — even for single-track editing
        self.tracks = tracks
        self.controller = controller
        self.is_multi = len(tracks) > 1
        # Tracks which scalar fields the user has touched
        self._dirty: set = set()

    @property
    def track(self):
        """Convenience: the single track (only valid when is_multi is False)."""
        return self.tracks[0]

    def load(self, tracks: list) -> None:
        raise NotImplementedError

    def collect_changes(self) -> Dict[str, Any]:
        return {}

    def _mark_dirty(self, field_name: str) -> None:
        self._dirty.add(field_name)

    def _has_changed(self, field_name: str, new_value) -> bool:
        """Return True if new_value differs meaningfully from the original."""
        old = getattr(self.track, field_name, None)
        if old is None and new_value in (None, "", 0, 0.0, False):
            return False
        return str(old).strip() != str(new_value).strip()


# ---------------------------------------------------------------------------
# FieldFormTab — auto-builds a QFormLayout from TRACK_FIELDS for one category
# ---------------------------------------------------------------------------


class FieldFormTab(_BaseTab):
    """
    Generic tab that renders all TRACK_FIELDS belonging to `category`.
    Editable fields → appropriate input widget.
    Read-only fields → styled QLabel.
    """

    def __init__(self, category: str, tracks: list, controller, parent=None):
        super().__init__(tracks, controller, parent)
        self.category = category
        self._widgets: Dict[str, QWidget] = {}  # editable widgets
        self._labels: Dict[str, QLabel] = {}  # readonly labels
        self._build_ui()

    def _build_ui(self):
        layout = QFormLayout(self)
        layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        fields = {
            name: cfg
            for name, cfg in TRACK_FIELDS.items()
            if cfg.category == self.category
        }

        if self.is_multi:
            note = QLabel("⚠  Changes will apply to all selected tracks.")
            note.setStyleSheet("color: #888; font-style: italic;")
            layout.addRow(note)

        for field_name, cfg in fields.items():
            # Build the label
            label_text = cfg.friendly or field_name
            lbl = QLabel(f"{label_text}:")
            if cfg.tooltip:
                lbl.setToolTip(cfg.tooltip)

            if not cfg.editable:
                # Read-only display label
                val_lbl = QLabel("—")
                val_lbl.setWordWrap(True)
                val_lbl.setStyleSheet("color: #666; font-style: italic;")
                val_lbl.setFocusPolicy(Qt.NoFocus)
                val_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
                self._labels[field_name] = val_lbl
                layout.addRow(lbl, val_lbl)
            else:
                # Skip fields marked multiple=False in multi-track mode
                if self.is_multi and not cfg.multiple:
                    continue
                w = _make_widget_for_field(field_name, cfg, self._mark_dirty)
                self._widgets[field_name] = w
                layout.addRow(lbl, w)

    # ── _BaseTab interface ───────────────────────────────────────────────

    def load(self, tracks: list) -> None:
        self.tracks = tracks
        self._dirty.clear()

        if self.is_multi:
            # Show value only when all tracks agree; blank otherwise
            for field_name, w in self._widgets.items():
                values = [getattr(t, field_name, None) for t in tracks]
                unique = set(str(v) for v in values)
                _write_widget(w, values[0] if len(unique) == 1 else None)
        else:
            for field_name, w in self._widgets.items():
                _write_widget(w, getattr(self.track, field_name, None))
            for field_name, lbl in self._labels.items():
                cfg = TRACK_FIELDS.get(field_name)
                lbl.setText(
                    _format_readonly(getattr(self.track, field_name, None), cfg)
                )

    def collect_changes(self) -> Dict[str, Any]:
        changes = {}
        for field_name in self._dirty:
            w = self._widgets.get(field_name)
            if w is None:
                continue
            cfg = TRACK_FIELDS.get(field_name)
            if cfg is None:
                continue
            raw = _read_widget(w)
            new_val = _coerce(raw, cfg)
            if self.is_multi or self._has_changed(field_name, new_val):
                changes[field_name] = new_val
        return changes


# ---------------------------------------------------------------------------
# LyricsTab
# ---------------------------------------------------------------------------


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
            self._edit.setPlainText("")
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


# ---------------------------------------------------------------------------
# IdentificationTab — like FieldFormTab("Identification") but adds Wikipedia
# ---------------------------------------------------------------------------


class IdentificationTab(FieldFormTab):
    def __init__(self, tracks: list, controller, parent=None):
        super().__init__("Identification", tracks, controller, parent)
        self._inject_wikipedia_button()

    def _inject_wikipedia_button(self):
        """Replace the plain Wikipedia link field with one that has a search button."""
        wiki_widget = self._widgets.get("track_wikipedia_link")
        if wiki_widget is None:
            return

        # Find the row in the form layout and replace the field widget
        form = self.layout()
        for i in range(form.rowCount()):
            label_item = form.itemAt(i, QFormLayout.LabelRole)  # noqa: F841
            field_item = form.itemAt(i, QFormLayout.FieldRole)
            if field_item and field_item.widget() is wiki_widget:
                # Build a row widget: [line edit] [search button]
                container = QWidget()
                row = QHBoxLayout(container)
                row.setContentsMargins(0, 0, 0, 0)
                row.addWidget(wiki_widget)
                btn = QPushButton("Search Wikipedia")
                btn.clicked.connect(self._search_wikipedia)
                row.addWidget(btn)
                form.removeRow(i)
                label_item_widget = QLabel("Wikipedia Link:")
                form.insertRow(i, label_item_widget, container)
                break

    def _search_wikipedia(self):
        try:
            query = self.track.track_name if not self.is_multi else ""
            title, summary, _full, link, _images = search_wikipedia(query, self)
            if not link:
                return
            wiki_w = self._widgets.get("track_wikipedia_link")
            if wiki_w:
                wiki_w.setText(link)
                self._mark_dirty("track_wikipedia_link")
            # Optionally pre-fill description if it is empty
            desc_w = self._widgets.get("track_description")
            if desc_w and summary and not _read_widget(desc_w).strip():
                desc_text = summary[:500] + ("..." if len(summary) > 500 else "")
                _write_widget(desc_w, desc_text)
                self._mark_dirty("track_description")
        except Exception as e:
            logger.error(f"Wikipedia search error: {e}")


# ---------------------------------------------------------------------------
# RolesTab — artist / role relationships
# ---------------------------------------------------------------------------


class RolesTab(_BaseTab):
    def __init__(self, tracks: list, controller, parent=None):
        super().__init__(tracks, controller, parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # ── Search row ────────────────────────────────────────────────────
        search_row = QHBoxLayout()

        self._artist_search = QLineEdit()
        self._artist_search.setPlaceholderText("Search artists… (min 2 chars)")
        self._artist_search.textChanged.connect(self._on_artist_search)
        search_row.addWidget(self._artist_search)

        self._artist_combo = QComboBox()
        self._artist_combo.setVisible(False)
        self._artist_combo.currentIndexChanged.connect(self._on_artist_selected)
        search_row.addWidget(self._artist_combo)

        self._role_edit = QLineEdit()
        self._role_edit.setPlaceholderText("Role (e.g. Performer, Composer…)")
        self._role_edit.textChanged.connect(self._update_add_btn)
        search_row.addWidget(self._role_edit)

        self._add_btn = QPushButton("Add Role")
        self._add_btn.setEnabled(False)
        self._add_btn.clicked.connect(self._add_role)
        search_row.addWidget(self._add_btn)
        layout.addLayout(search_row)

        # ── Current roles table ───────────────────────────────────────────
        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Artist", "Role", ""])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeToContents
        )
        layout.addWidget(self._table)

    # ── Loading ───────────────────────────────────────────────────────────

    def load(self, tracks: list) -> None:
        self.tracks = tracks
        self._table.setRowCount(0)
        if self.is_multi:
            self._load_common_roles()
        else:
            for role_assoc in self.track.artist_roles:
                self._add_table_row(
                    artist_name=role_assoc.artist.artist_name
                    if role_assoc.artist
                    else "?",
                    role_name=role_assoc.role.role_name if role_assoc.role else "?",
                    artist_id=role_assoc.artist.artist_id
                    if role_assoc.artist
                    else None,
                    role_id=role_assoc.role.role_id if role_assoc.role else None,
                )

    def _load_common_roles(self):
        """Show only roles shared by every track in the selection."""
        all_sets = []
        for t in self.tracks:
            s = set()
            for ra in t.artist_roles:
                if ra.artist and ra.role:
                    s.add(
                        (
                            ra.artist.artist_id,
                            ra.role.role_id,
                            ra.artist.artist_name,
                            ra.role.role_name,
                        )
                    )
            all_sets.append(s)
        common = all_sets[0]
        for s in all_sets[1:]:
            common &= s
        for artist_id, role_id, artist_name, role_name in common:
            self._add_table_row(artist_name, role_name, artist_id, role_id)

    def _add_table_row(self, artist_name, role_name, artist_id, role_id):
        row = self._table.rowCount()
        self._table.insertRow(row)
        artist_item = QTableWidgetItem(artist_name)
        artist_item.setData(Qt.UserRole, artist_id)
        self._table.setItem(row, 0, artist_item)
        role_item = QTableWidgetItem(role_name)
        role_item.setData(Qt.UserRole, role_id)
        self._table.setItem(row, 1, role_item)
        btn = QPushButton("Remove")
        btn.clicked.connect(lambda _checked, r=row: self._remove_role(r))
        self._table.setCellWidget(row, 2, btn)

    # ── Search ────────────────────────────────────────────────────────────

    def _on_artist_search(self, text: str):
        text = text.strip()
        self._artist_combo.blockSignals(True)
        self._artist_combo.clear()
        if len(text) >= 2:
            results = self.controller.get.get_entity_object("Artist", artist_name=text)
            self._artist_combo.addItem(f"Create new: '{text}'", "new")
            if results is not None:
                items = results if isinstance(results, list) else [results]
                for a in items:
                    self._artist_combo.addItem(a.artist_name, a.artist_id)
            self._artist_combo.setVisible(self._artist_combo.count() > 1)
        else:
            self._artist_combo.setVisible(False)
        self._artist_combo.blockSignals(False)
        self._update_add_btn()

    def _on_artist_selected(self, index: int):
        if index > 0:
            self._artist_search.blockSignals(True)
            self._artist_search.setText(self._artist_combo.currentText())
            self._artist_search.blockSignals(False)
        self._update_add_btn()

    def _update_add_btn(self):
        artist_ok = len(self._artist_search.text().strip()) >= 2
        role_ok = len(self._role_edit.text().strip()) >= 2
        self._add_btn.setEnabled(artist_ok and role_ok)

    # ── Add / Remove ──────────────────────────────────────────────────────

    def _add_role(self):
        artist_name = self._artist_search.text().strip()
        role_name = self._role_edit.text().strip()
        if not artist_name or not role_name:
            return

        # Resolve or create artist
        combo_data = (
            self._artist_combo.currentData() if self._artist_combo.isVisible() else None
        )
        if combo_data and combo_data != "new":
            artist = self.controller.get.get_entity_object(
                "Artist", artist_id=combo_data
            )
        else:
            existing = self.controller.get.get_entity_object(
                "Artist", artist_name=artist_name
            )
            if existing:
                artist = existing if not isinstance(existing, list) else existing[0]
            else:
                artist = self.controller.add.add_entity(
                    "Artist", artist_name=artist_name
                )

        # Resolve or create role
        existing_role = self.controller.get.get_entity_object(
            "Role", role_name=role_name
        )
        if existing_role:
            role = (
                existing_role
                if not isinstance(existing_role, list)
                else existing_role[0]
            )
        else:
            role = self.controller.add.add_entity("Role", role_name=role_name)

        if not artist or not role:
            QMessageBox.warning(self, "Error", "Could not resolve artist or role.")
            return

        for track in self.tracks:
            try:
                self.controller.add.add_entity(
                    "TrackArtistRole",
                    track_id=track.track_id,
                    artist_id=artist.artist_id,
                    role_id=role.role_id,
                )
            except Exception as e:
                logger.error(f"Failed to add role to track {track.track_id}: {e}")

        self._artist_search.clear()
        self._role_edit.clear()
        self._artist_combo.setVisible(False)
        self.load(self.tracks)

    def _remove_role(self, row: int):
        artist_item = self._table.item(row, 0)
        role_item = self._table.item(row, 1)
        if not artist_item or not role_item:
            return
        artist_id = artist_item.data(Qt.UserRole)
        role_id = role_item.data(Qt.UserRole)
        for track in self.tracks:
            try:
                self.controller.delete.delete_entity(
                    "TrackArtistRole",
                    track_id=track.track_id,
                    artist_id=artist_id,
                    role_id=role_id,
                )
            except Exception as e:
                logger.error(f"Failed to remove role from track {track.track_id}: {e}")
        self.load(self.tracks)


# ---------------------------------------------------------------------------
# GenresTab
# ---------------------------------------------------------------------------


class GenresTab(_BaseTab):
    def __init__(self, tracks: list, controller, parent=None):
        super().__init__(tracks, controller, parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search genres… (min 2 chars)")
        self._search.textChanged.connect(self._on_search)
        search_row.addWidget(self._search)

        self._combo = QComboBox()
        self._combo.setVisible(False)
        self._combo.currentIndexChanged.connect(self._on_selected)
        search_row.addWidget(self._combo)

        self._add_btn = QPushButton("Add Genre")
        self._add_btn.setEnabled(False)
        self._add_btn.clicked.connect(self._add)
        search_row.addWidget(self._add_btn)
        layout.addLayout(search_row)

        self._list = QListWidget()
        layout.addWidget(self._list)

    def load(self, tracks: list) -> None:
        self.tracks = tracks
        self._list.clear()
        if self.is_multi:
            genres = self._common_genres()
        else:
            genres = [(g.genre_id, g.genre_name) for g in self.track.genres]
        for gid, gname in genres:
            item = QListWidgetItem(gname)
            item.setData(Qt.UserRole, gid)
            self._list.addItem(item)

    def _common_genres(self):
        all_sets = []
        for t in self.tracks:
            all_sets.append({(g.genre_id, g.genre_name) for g in t.genres})
        common = all_sets[0]
        for s in all_sets[1:]:
            common &= s
        return list(common)

    def _on_search(self, text: str):
        text = text.strip()
        self._combo.blockSignals(True)
        self._combo.clear()
        if len(text) >= 2:
            results = self.controller.get.get_entity_object("Genre", genre_name=text)
            self._combo.addItem(f"Create new: '{text}'", "new")
            if results is not None:
                items = results if isinstance(results, list) else [results]
                for g in items:
                    self._combo.addItem(g.genre_name, g.genre_id)
            self._combo.setVisible(self._combo.count() > 1)
        else:
            self._combo.setVisible(False)
        self._combo.blockSignals(False)
        self._add_btn.setEnabled(len(text) >= 2)

    def _on_selected(self, index: int):
        if index > 0:
            self._search.blockSignals(True)
            self._search.setText(self._combo.currentText())
            self._search.blockSignals(False)

    def _add(self):
        genre_name = self._search.text().strip()
        if not genre_name:
            return
        combo_data = self._combo.currentData() if self._combo.isVisible() else None
        if combo_data and combo_data != "new":
            genre = self.controller.get.get_entity_object("Genre", genre_id=combo_data)
        else:
            existing = self.controller.get.get_entity_object(
                "Genre", genre_name=genre_name
            )
            if existing:
                genre = existing if not isinstance(existing, list) else existing[0]
            else:
                genre = self.controller.add.add_entity("Genre", genre_name=genre_name)
        if not genre:
            return
        for track in self.tracks:
            try:
                self.controller.add.add_entity(
                    "TrackGenre", track_id=track.track_id, genre_id=genre.genre_id
                )
            except Exception as e:
                logger.error(f"Failed to add genre to track {track.track_id}: {e}")
        self._search.clear()
        self._combo.setVisible(False)
        self.load(self.tracks)

    def _remove_selected(self):
        item = self._list.currentItem()
        if not item:
            return
        genre_id = item.data(Qt.UserRole)
        for track in self.tracks:
            try:
                self.controller.delete.delete_entity(
                    "TrackGenre", track_id=track.track_id, genre_id=genre_id
                )
            except Exception as e:
                logger.error(f"Failed to remove genre from track {track.track_id}: {e}")
        self.load(self.tracks)

    def contextMenuEvent(self, event):
        if self._list.currentItem():
            menu = QMenu(self)
            menu.addAction("Remove", self._remove_selected)
            menu.exec(event.globalPos())


# ---------------------------------------------------------------------------
# PlacesTab
# ---------------------------------------------------------------------------


class PlacesTab(_BaseTab):
    def __init__(self, tracks: list, controller, parent=None):
        super().__init__(tracks, controller, parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search places… (min 2 chars)")
        self._search.textChanged.connect(self._on_search)
        search_row.addWidget(self._search)

        self._combo = QComboBox()
        self._combo.setVisible(False)
        self._combo.currentIndexChanged.connect(self._on_selected)
        search_row.addWidget(self._combo)

        self._type_edit = QLineEdit()
        self._type_edit.setPlaceholderText("Type (Recorded, Composed, etc.)")
        search_row.addWidget(self._type_edit)

        self._add_btn = QPushButton("Add Place")
        self._add_btn.setEnabled(False)
        self._add_btn.clicked.connect(self._add)
        search_row.addWidget(self._add_btn)
        layout.addLayout(search_row)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Place", "Type", ""])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeToContents
        )
        layout.addWidget(self._table)

    def load(self, tracks: list) -> None:
        self.tracks = tracks
        self._table.setRowCount(0)
        if self.is_multi:
            rows = self._common_places()
        else:
            assocs = self.controller.get.get_entity_links(
                "PlaceAssociation", entity_id=self.track.track_id, entity_type="Track"
            )
            rows = []
            for a in assocs:
                place = self.controller.get.get_entity_object(
                    "Place", place_id=a.place_id
                )
                if place:
                    rows.append(
                        (place.place_id, place.place_name, a.association_type or "")
                    )
        for place_id, place_name, assoc_type in rows:
            self._add_row(place_id, place_name, assoc_type)

    def _common_places(self):
        all_sets = []
        for t in self.tracks:
            s = set()
            assocs = self.controller.get.get_entity_links(
                "PlaceAssociation", entity_id=t.track_id, entity_type="Track"
            )
            for a in assocs:
                place = self.controller.get.get_entity_object(
                    "Place", place_id=a.place_id
                )
                if place:
                    s.add((place.place_id, place.place_name, a.association_type or ""))
            all_sets.append(s)
        common = all_sets[0]
        for s in all_sets[1:]:
            common &= s
        return list(common)

    def _add_row(self, place_id, place_name, assoc_type):
        row = self._table.rowCount()
        self._table.insertRow(row)
        pi = QTableWidgetItem(place_name)
        pi.setData(Qt.UserRole, place_id)
        self._table.setItem(row, 0, pi)
        self._table.setItem(row, 1, QTableWidgetItem(assoc_type))
        btn = QPushButton("Remove")
        btn.clicked.connect(lambda _c, r=row: self._remove_row(r))
        self._table.setCellWidget(row, 2, btn)

    def _on_search(self, text: str):
        text = text.strip()
        self._combo.blockSignals(True)
        self._combo.clear()
        if len(text) >= 2:
            results = self.controller.get.get_entity_object("Place", place_name=text)
            self._combo.addItem(f"Create new: '{text}'", "new")
            if results is not None:
                items = results if isinstance(results, list) else [results]
                for p in items:
                    self._combo.addItem(p.place_name, p.place_id)
            self._combo.setVisible(self._combo.count() > 1)
        else:
            self._combo.setVisible(False)
        self._combo.blockSignals(False)
        self._add_btn.setEnabled(len(text) >= 2)

    def _on_selected(self, index: int):
        if index > 0:
            self._search.blockSignals(True)
            self._search.setText(self._combo.currentText())
            self._search.blockSignals(False)

    def _add(self):
        place_name = self._search.text().strip()
        assoc_type = self._type_edit.text().strip() or None
        if not place_name:
            return
        combo_data = self._combo.currentData() if self._combo.isVisible() else None
        if combo_data and combo_data != "new":
            place = self.controller.get.get_entity_object("Place", place_id=combo_data)
        else:
            existing = self.controller.get.get_entity_object(
                "Place", place_name=place_name
            )
            if existing:
                place = existing if not isinstance(existing, list) else existing[0]
            else:
                place = self.controller.add.add_entity("Place", place_name=place_name)
        if not place:
            return
        for track in self.tracks:
            try:
                self.controller.add.add_entity(
                    "PlaceAssociation",
                    entity_id=track.track_id,
                    entity_type="Track",
                    place_id=place.place_id,
                    association_type=assoc_type,
                )
            except Exception as e:
                logger.error(f"Failed to add place to track {track.track_id}: {e}")
        self._search.clear()
        self._type_edit.clear()
        self._combo.setVisible(False)
        self.load(self.tracks)

    def _remove_row(self, row: int):
        place_item = self._table.item(row, 0)
        if not place_item:
            return
        place_id = place_item.data(Qt.UserRole)
        for track in self.tracks:
            try:
                self.controller.delete.delete_entity(
                    "PlaceAssociation",
                    entity_id=track.track_id,
                    entity_type="Track",
                    place_id=place_id,
                )
            except Exception as e:
                logger.error(f"Failed to remove place from track {track.track_id}: {e}")
        self.load(self.tracks)


# ---------------------------------------------------------------------------
# MoodsTab
# ---------------------------------------------------------------------------


class MoodsTab(_BaseTab):
    def __init__(self, tracks: list, controller, parent=None):
        super().__init__(tracks, controller, parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search moods… (min 2 chars)")
        self._search.textChanged.connect(self._on_search)
        search_row.addWidget(self._search)

        self._combo = QComboBox()
        self._combo.setVisible(False)
        self._combo.currentIndexChanged.connect(self._on_selected)
        search_row.addWidget(self._combo)

        self._add_btn = QPushButton("Add Mood")
        self._add_btn.setEnabled(False)
        self._add_btn.clicked.connect(self._add)
        search_row.addWidget(self._add_btn)
        layout.addLayout(search_row)

        self._list = QListWidget()
        layout.addWidget(self._list)

    def load(self, tracks: list) -> None:
        self.tracks = tracks
        self._list.clear()
        if self.is_multi:
            moods = self._common_moods()
        else:
            assocs = self.controller.get.get_entity_links(
                "MoodTrackAssociation", track_id=self.track.track_id
            )
            moods = []
            for a in assocs:
                mood = self.controller.get.get_entity_object("Mood", mood_id=a.mood_id)
                if mood:
                    moods.append((mood.mood_id, mood.mood_name))
        for mood_id, mood_name in moods:
            item = QListWidgetItem(mood_name)
            item.setData(Qt.UserRole, mood_id)
            self._list.addItem(item)

    def _common_moods(self):
        all_sets = []
        for t in self.tracks:
            s = set()
            assocs = self.controller.get.get_entity_links(
                "MoodTrackAssociation", track_id=t.track_id
            )
            for a in assocs:
                mood = self.controller.get.get_entity_object("Mood", mood_id=a.mood_id)
                if mood:
                    s.add((mood.mood_id, mood.mood_name))
            all_sets.append(s)
        common = all_sets[0]
        for s in all_sets[1:]:
            common &= s
        return list(common)

    def _on_search(self, text: str):
        text = text.strip()
        self._combo.blockSignals(True)
        self._combo.clear()
        if len(text) >= 2:
            results = self.controller.get.get_entity_object("Mood", mood_name=text)
            self._combo.addItem(f"Create new: '{text}'", "new")
            if results is not None:
                items = results if isinstance(results, list) else [results]
                for m in items:
                    self._combo.addItem(m.mood_name, m.mood_id)
            self._combo.setVisible(self._combo.count() > 1)
        else:
            self._combo.setVisible(False)
        self._combo.blockSignals(False)
        self._add_btn.setEnabled(len(text) >= 2)

    def _on_selected(self, index: int):
        if index > 0:
            self._search.blockSignals(True)
            self._search.setText(self._combo.currentText())
            self._search.blockSignals(False)

    def _add(self):
        mood_name = self._search.text().strip()
        if not mood_name:
            return
        combo_data = self._combo.currentData() if self._combo.isVisible() else None
        if combo_data and combo_data != "new":
            mood = self.controller.get.get_entity_object("Mood", mood_id=combo_data)
        else:
            existing = self.controller.get.get_entity_object(
                "Mood", mood_name=mood_name
            )
            if existing:
                mood = existing if not isinstance(existing, list) else existing[0]
            else:
                mood = self.controller.add.add_entity("Mood", mood_name=mood_name)
        if not mood:
            return
        for track in self.tracks:
            try:
                self.controller.add.add_entity_link(
                    "MoodTrackAssociation",
                    track_id=track.track_id,
                    mood_id=mood.mood_id,
                )
            except Exception as e:
                logger.error(f"Failed to add mood to track {track.track_id}: {e}")
        self._search.clear()
        self._combo.setVisible(False)
        self.load(self.tracks)

    def _remove_selected(self):
        item = self._list.currentItem()
        if not item:
            return
        mood_id = item.data(Qt.UserRole)
        for track in self.tracks:
            try:
                self.controller.delete.delete_entity(
                    "MoodTrackAssociation", track_id=track.track_id, mood_id=mood_id
                )
            except Exception as e:
                logger.error(f"Failed to remove mood from track {track.track_id}: {e}")
        self.load(self.tracks)

    def contextMenuEvent(self, event):
        if self._list.currentItem():
            menu = QMenu(self)
            menu.addAction("Remove", self._remove_selected)
            menu.exec(event.globalPos())


# ---------------------------------------------------------------------------
# AwardsTab
# ---------------------------------------------------------------------------


class AwardsTab(_BaseTab):
    def __init__(self, tracks: list, controller, parent=None):
        super().__init__(tracks, controller, parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search awards… (min 2 chars)")
        self._search.textChanged.connect(self._on_search)
        search_row.addWidget(self._search)

        self._combo = QComboBox()
        self._combo.setVisible(False)
        self._combo.currentIndexChanged.connect(self._on_selected)
        search_row.addWidget(self._combo)

        self._cat_edit = QLineEdit()
        self._cat_edit.setPlaceholderText("Category (optional)")
        search_row.addWidget(self._cat_edit)

        self._year_spin = QSpinBox()
        self._year_spin.setRange(0, 2200)
        self._year_spin.setSpecialValueText("Year")
        search_row.addWidget(self._year_spin)

        self._add_btn = QPushButton("Add Award")
        self._add_btn.setEnabled(False)
        self._add_btn.clicked.connect(self._add)
        search_row.addWidget(self._add_btn)
        layout.addLayout(search_row)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Award", "Category", "Year", ""])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeToContents
        )
        layout.addWidget(self._table)

    def load(self, tracks: list) -> None:
        self.tracks = tracks
        self._table.setRowCount(0)
        if self.is_multi:
            rows = self._common_awards()
        else:
            assocs = self.controller.get.get_entity_links(
                "AwardAssociation", entity_id=self.track.track_id, entity_type="Track"
            )
            rows = []
            for a in assocs:
                award = self.controller.get.get_entity_object(
                    "Award", award_id=a.award_id
                )
                if award:
                    rows.append(
                        (award.award_id, award.award_name, a.category or "", a.year)
                    )
        for award_id, award_name, category, year in rows:
            self._add_row(award_id, award_name, category, year)

    def _common_awards(self):
        all_sets = []
        for t in self.tracks:
            s = set()
            assocs = self.controller.get.get_entity_links(
                "AwardAssociation", entity_id=t.track_id, entity_type="Track"
            )
            for a in assocs:
                award = self.controller.get.get_entity_object(
                    "Award", award_id=a.award_id
                )
                if award:
                    s.add(
                        (
                            award.award_id,
                            award.award_name,
                            a.category or "",
                            a.year or 0,
                        )
                    )
            all_sets.append(s)
        common = all_sets[0]
        for s in all_sets[1:]:
            common &= s
        return [
            (aid, aname, cat, yr if yr != 0 else None) for aid, aname, cat, yr in common
        ]

    def _add_row(self, award_id, award_name, category, year):
        row = self._table.rowCount()
        self._table.insertRow(row)
        ai = QTableWidgetItem(award_name)
        ai.setData(Qt.UserRole, award_id)
        self._table.setItem(row, 0, ai)
        self._table.setItem(row, 1, QTableWidgetItem(category))
        self._table.setItem(row, 2, QTableWidgetItem(str(year) if year else ""))
        btn = QPushButton("Remove")
        btn.clicked.connect(lambda _c, r=row: self._remove_row(r))
        self._table.setCellWidget(row, 3, btn)

    def _on_search(self, text: str):
        text = text.strip()
        self._combo.blockSignals(True)
        self._combo.clear()
        if len(text) >= 2:
            results = self.controller.get.get_entity_object("Award", award_name=text)
            self._combo.addItem(f"Create new: '{text}'", "new")
            if results is not None:
                items = results if isinstance(results, list) else [results]
                for a in items:
                    self._combo.addItem(a.award_name, a.award_id)
            self._combo.setVisible(self._combo.count() > 1)
        else:
            self._combo.setVisible(False)
        self._combo.blockSignals(False)
        self._add_btn.setEnabled(len(text) >= 2)

    def _on_selected(self, index: int):
        if index > 0:
            self._search.blockSignals(True)
            self._search.setText(self._combo.currentText())
            self._search.blockSignals(False)

    def _add(self):
        award_name = self._search.text().strip()
        category = self._cat_edit.text().strip() or None
        year = self._year_spin.value() or None
        if not award_name:
            return
        combo_data = self._combo.currentData() if self._combo.isVisible() else None
        if combo_data and combo_data != "new":
            award = self.controller.get.get_entity_object("Award", award_id=combo_data)
        else:
            existing = self.controller.get.get_entity_object(
                "Award", award_name=award_name
            )
            if existing:
                award = existing if not isinstance(existing, list) else existing[0]
            else:
                award = self.controller.add.add_entity("Award", award_name=award_name)
        if not award:
            return
        for track in self.tracks:
            try:
                self.controller.add.add_entity(
                    "AwardAssociation",
                    entity_id=track.track_id,
                    entity_type="Track",
                    award_id=award.award_id,
                    category=category,
                    year=year,
                )
            except Exception as e:
                logger.error(f"Failed to add award to track {track.track_id}: {e}")
        self._search.clear()
        self._cat_edit.clear()
        self._year_spin.setValue(0)
        self._combo.setVisible(False)
        self.load(self.tracks)

    def _remove_row(self, row: int):
        award_item = self._table.item(row, 0)
        if not award_item:
            return
        award_id = award_item.data(Qt.UserRole)
        for track in self.tracks:
            try:
                self.controller.delete.delete_entity(
                    "AwardAssociation",
                    entity_id=track.track_id,
                    entity_type="Track",
                    award_id=award_id,
                )
            except Exception as e:
                logger.error(f"Failed to remove award from track {track.track_id}: {e}")
        self.load(self.tracks)


# ---------------------------------------------------------------------------
# SamplesTab
# ---------------------------------------------------------------------------


class SamplesTab(_BaseTab):
    def __init__(self, tracks: list, controller, parent=None):
        super().__init__(tracks, controller, parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Search / add used samples
        search_row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search tracks to sample… (min 2 chars)")
        self._search.textChanged.connect(self._on_search)
        search_row.addWidget(self._search)

        self._combo = QComboBox()
        self._combo.setVisible(False)
        self._combo.currentIndexChanged.connect(self._on_selected)
        search_row.addWidget(self._combo)

        self._add_btn = QPushButton("Add Sample")
        self._add_btn.setEnabled(False)
        self._add_btn.clicked.connect(self._add)
        search_row.addWidget(self._add_btn)
        layout.addLayout(search_row)

        # Samples used list
        layout.addWidget(QLabel("Samples Used (tracks this track samples):"))
        self._used_list = QListWidget()
        self._used_list.itemDoubleClicked.connect(self._open_sampled)
        self._used_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._used_list.customContextMenuRequested.connect(
            lambda pos: self._list_context_menu(
                self._used_list, pos, self._remove_sample
            )
        )
        layout.addWidget(self._used_list)

        # Sampled-by list (read-only)
        layout.addWidget(QLabel("Sampled By (tracks that sample this track):"))
        self._by_list = QListWidget()
        self._by_list.itemDoubleClicked.connect(self._open_sampler)
        layout.addWidget(self._by_list)

        layout.addWidget(QLabel("Double-click any track to open its editor."))

    def load(self, tracks: list) -> None:
        self.tracks = tracks
        self._used_list.clear()
        self._by_list.clear()

        # Samples tab is single-track only in a meaningful way
        if self.is_multi:
            self._used_list.addItem("(Select a single track to manage samples)")
            self._add_btn.setEnabled(False)
            return

        for sample in self.track.samples_used:
            st = sample.sampled
            if st:
                text = st.track_name + (f"  [{st.album_name}]" if st.album_name else "")
                item = QListWidgetItem(text)
                item.setData(Qt.UserRole, st.track_id)
                self._used_list.addItem(item)

        for sample in self.track.sampled_by_tracks:
            st = sample.sampled_by
            if st:
                text = st.track_name + (f"  [{st.album_name}]" if st.album_name else "")
                item = QListWidgetItem(text)
                item.setData(Qt.UserRole, st.track_id)
                self._by_list.addItem(item)

    def _on_search(self, text: str):
        text = text.strip()
        self._combo.blockSignals(True)
        self._combo.clear()
        if len(text) >= 2:
            results = self.controller.get.get_entity_object("Track", track_name=text)
            if results is not None:
                items = results if isinstance(results, list) else [results]
                for t in items:
                    self._combo.addItem(t.track_name, t.track_id)
            self._combo.setVisible(self._combo.count() > 0)
        else:
            self._combo.setVisible(False)
        self._combo.blockSignals(False)
        self._add_btn.setEnabled(len(text) >= 2 and self._combo.count() > 0)

    def _on_selected(self, index: int):
        if index >= 0:
            self._search.blockSignals(True)
            self._search.setText(self._combo.currentText())
            self._search.blockSignals(False)

    def _add(self):
        if self._combo.currentData() is None:
            return
        sampled_id = self._combo.currentData()
        try:
            self.controller.add.add_entity(
                "TrackSample",
                sampler_id=self.track.track_id,
                sampled_id=sampled_id,
            )
        except Exception as e:
            logger.error(f"Failed to add sample: {e}")
        self._search.clear()
        self._combo.setVisible(False)
        self.load(self.tracks)

    def _remove_sample(self):
        item = self._used_list.currentItem()
        if not item:
            return
        sampled_id = item.data(Qt.UserRole)
        try:
            self.controller.delete.delete_entity(
                "TrackSample",
                sampler_id=self.track.track_id,
                sampled_id=sampled_id,
            )
        except Exception as e:
            logger.error(f"Failed to remove sample: {e}")
        self.load(self.tracks)

    def _open_sampled(self, item):
        self._open_track(item.data(Qt.UserRole))

    def _open_sampler(self, item):
        self._open_track(item.data(Qt.UserRole))

    def _open_track(self, track_id):
        track = self.controller.get.get_entity_object("Track", track_id=track_id)
        if track:
            dlg = TrackEditDialog(track, self.controller, self)
            dlg.exec()

    @staticmethod
    def _list_context_menu(list_widget, pos, remove_cb):
        if list_widget.currentItem():
            menu = QMenu(list_widget)
            menu.addAction("Remove", remove_cb)
            menu.exec(list_widget.mapToGlobal(pos))


# ---------------------------------------------------------------------------
# TrackEditDialog — the main dialog
# ---------------------------------------------------------------------------


class TrackEditDialog(QDialog):
    """
    Edit one track — or bulk-edit many at once.

    Usage:
        # Single track
        dlg = TrackEditDialog(track, controller, parent)
        # Multiple tracks
        dlg = TrackEditDialog([t1, t2, t3], controller, parent)
    """

    field_modified = Signal()

    def __init__(
        self,
        track_or_tracks: Union[Any, List],
        controller,
        parent=None,
    ):
        super().__init__(parent)

        # Normalise to a list
        if isinstance(track_or_tracks, list):
            self.tracks = track_or_tracks
        else:
            self.tracks = [track_or_tracks]

        self.controller = controller
        self.is_multi = len(self.tracks) > 1

        # Convenience property
        self.track = self.tracks[0]

        title = (
            f"Edit {len(self.tracks)} Tracks"
            if self.is_multi
            else f"Edit Track: {self.track.track_name}"
        )
        self.setWindowTitle(title)
        self.setMinimumSize(900, 650)

        self._build_ui()
        self._load_all()

    # ── UI Construction ───────────────────────────────────────────────────

    def _build_ui(self):
        root = QHBoxLayout(self)

        # Left sidebar — tab navigation list
        self._sidebar = QListWidget()
        self._sidebar.setFixedWidth(155)
        self._sidebar.currentRowChanged.connect(self._on_nav)
        root.addWidget(self._sidebar)

        # Right side: stacked tab widgets + button box
        right = QVBoxLayout()
        self._stack = QStackedWidget()
        right.addWidget(self._stack)

        btn_box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self._on_save)
        btn_box.rejected.connect(self.reject)
        right.addWidget(btn_box)

        right_widget = QWidget()
        right_widget.setLayout(right)
        root.addWidget(right_widget, stretch=1)

        # Build all tabs
        self._tabs: List[_BaseTab] = []
        self._add_tab("Basic Info", FieldFormTab("Basic", self.tracks, self.controller))
        self._add_tab("Lyrics", LyricsTab(self.tracks, self.controller))
        self._add_tab("Dates", FieldFormTab("Date", self.tracks, self.controller))
        self._add_tab(
            "Classical", FieldFormTab("Classical", self.tracks, self.controller)
        )
        self._add_tab(
            "Properties", FieldFormTab("Properties", self.tracks, self.controller)
        )
        self._add_tab("Identification", IdentificationTab(self.tracks, self.controller))
        self._add_tab("User Data", FieldFormTab("User", self.tracks, self.controller))
        self._add_tab("Artists & Roles", RolesTab(self.tracks, self.controller))
        self._add_tab("Genres", GenresTab(self.tracks, self.controller))
        self._add_tab("Places", PlacesTab(self.tracks, self.controller))
        self._add_tab("Moods", MoodsTab(self.tracks, self.controller))
        self._add_tab("Awards", AwardsTab(self.tracks, self.controller))
        self._add_tab("Aliases", FieldFormTab("Alias", self.tracks, self.controller))
        self._add_tab("Samples", SamplesTab(self.tracks, self.controller))
        self._add_tab(
            "Advanced", FieldFormTab("Advanced", self.tracks, self.controller)
        )

        # Keyboard shortcuts Ctrl+1 … Ctrl+9 for first 9 tabs
        for i in range(min(9, len(self._tabs))):
            sc = QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self)
            sc.activated.connect(lambda idx=i: self._sidebar.setCurrentRow(idx))

        self._sidebar.setCurrentRow(0)

    def _add_tab(self, label: str, tab: _BaseTab):
        self._sidebar.addItem(label)
        self._stack.addWidget(tab)
        self._tabs.append(tab)

    def _on_nav(self, row: int):
        self._stack.setCurrentIndex(row)

    # ── Data ──────────────────────────────────────────────────────────────

    def _load_all(self):
        for tab in self._tabs:
            try:
                tab.load(self.tracks)
            except Exception as e:
                logger.error(
                    f"Error loading tab {type(tab).__name__}: {e}", exc_info=True
                )

    # ── Save ──────────────────────────────────────────────────────────────

    def _on_save(self):
        try:
            # Collect scalar field changes from all tabs
            all_changes: Dict[str, Any] = {}
            for tab in self._tabs:
                try:
                    changes = tab.collect_changes()
                    all_changes.update(changes)
                except Exception as e:
                    logger.error(
                        f"Error collecting changes from {type(tab).__name__}: {e}"
                    )

            if all_changes:
                for track in self.tracks:
                    self.controller.update.update_entity(
                        "Track", track.track_id, **all_changes
                    )
                logger.info(
                    f"Saved {len(self.tracks)} track(s), "
                    f"fields: {list(all_changes.keys())}"
                )

            self.field_modified.emit()
            self.accept()

        except Exception as e:
            logger.error(f"Error saving track(s): {e}", exc_info=True)
            QMessageBox.critical(self, "Save Error", f"Failed to save:\n{e}")


# ---------------------------------------------------------------------------
# Backwards-compatibility alias so existing callers don't need changes
# ---------------------------------------------------------------------------

# Any code that imported MultiTrackEditDialog can now pass a list to
# TrackEditDialog instead. We keep the name around to avoid import errors.
MultiTrackEditDialog = TrackEditDialog
