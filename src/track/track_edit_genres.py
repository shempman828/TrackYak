# ---------------------------------------------------------------------------
# GenresTab
# ---------------------------------------------------------------------------
from __future__ import annotations

from PySide6.QtCore import QStringListModel, Qt
from PySide6.QtWidgets import (
    QCompleter,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
)

from src.core.logger_config import logger
from src.track.track_edit_basetab import _BaseTab


def _fetch_all_genres(controller):
    """Fetch all genres for completer indexing."""
    try:
        return controller.get.get_all_entities("Genre") or []
    except Exception as e:
        logger.warning(f"Could not fetch genres for completer: {e}")
        return []


def _find_or_create_genre(controller, name, known_genres):
    """
    Look up a genre by name (case-insensitive) among known genres; create
    one only if none is found. Matching against the already-loaded list
    (rather than a fresh DB query) guarantees an existing genre always wins
    over creating a same-named duplicate, regardless of case.
    """
    lowered = name.strip().lower()
    for genre in known_genres:
        if (genre.genre_name or "").strip().lower() == lowered:
            return genre
    genre = controller.get.resolve_entity_or_alias("Genre", "genre_name", name)
    if genre:
        return genre
    return controller.add.add_entity("Genre", genre_name=name)


class _GenreNameEdit(QLineEdit):
    """
    QLineEdit with a QCompleter over known genres. When the user picks a
    completion, we remember the matched genre_id directly so add-time skips
    the name-lookup roundtrip entirely (and can't collide with a same-named
    genre). Any manual edit after a match clears the lock — typing a new
    name always means "maybe create new."
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("Search genres…")
        self._display_to_id = {}
        self._matched_id = None
        self.textEdited.connect(self._on_manual_edit)

    def set_index(self, display_to_id: dict):
        """Rebuild the completer's backing model."""
        self._display_to_id = dict(display_to_id)
        model = QStringListModel(sorted(self._display_to_id.keys()), self)
        completer = QCompleter(model, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        completer.activated.connect(self._on_completion_picked)
        self.setCompleter(completer)

    def _on_completion_picked(self, text):
        self._matched_id = self._display_to_id.get(text)

    def _on_manual_edit(self, _text):
        self._matched_id = None

    def matched_genre_id(self):
        return self._matched_id

    def reset(self):
        self.clear()
        self._matched_id = None


class GenresTab(_BaseTab):
    def __init__(self, tracks: list, controller, parent=None):
        super().__init__(tracks, controller, parent)
        self._build_ui()
        self._refresh_completer_index()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        self._search = _GenreNameEdit()
        self._search.textChanged.connect(self._on_search_text_changed)
        self._search.returnPressed.connect(self._add)
        search_row.addWidget(self._search)

        self._add_btn = QPushButton("Add Genre")
        self._add_btn.setEnabled(False)
        self._add_btn.clicked.connect(self._add)
        search_row.addWidget(self._add_btn)
        layout.addLayout(search_row)

        self._list = QListWidget()
        layout.addWidget(self._list)

    def _on_search_text_changed(self, text: str):
        self._add_btn.setEnabled(bool(text.strip()))

    def _refresh_completer_index(self):
        self._known_genres = _fetch_all_genres(self.controller)
        index = {g.genre_name: g.genre_id for g in self._known_genres if g.genre_name}
        self._search.set_index(index)

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

    def _add(self):
        genre_name = self._search.text().strip()
        if not genre_name:
            return

        matched_id = self._search.matched_genre_id()
        try:
            if matched_id is not None:
                genre = self.controller.get.get_entity_object(
                    "Genre", genre_id=matched_id
                )
            else:
                genre = _find_or_create_genre(
                    self.controller, genre_name, self._known_genres
                )
        except Exception as e:
            logger.error(f"Failed to find/create genre: {e}")
            return
        if not genre:
            return

        rows = [
            {"track_id": track.track_id, "genre_id": genre.genre_id}
            for track in self.tracks
        ]
        try:
            self.controller.add.add_entities("TrackGenre", rows)
        except Exception as e:
            logger.error(f"Failed to add genre to tracks: {e}")
        self._search.reset()
        self._refresh_completer_index()
        self.load(self.tracks)

    def _remove_selected(self):
        item = self._list.currentItem()
        if not item:
            return
        genre_id = item.data(Qt.UserRole)
        track_ids = [track.track_id for track in self.tracks]
        try:
            self.controller.delete.delete_entity(
                "TrackGenre", track_id=track_ids, genre_id=genre_id
            )
        except Exception as e:
            logger.error(f"Failed to remove genre from tracks: {e}")
        self.load(self.tracks)

    def contextMenuEvent(self, event):
        if self._list.currentItem():
            menu = QMenu(self)
            menu.addAction("Remove", self._remove_selected)
            menu.exec(event.globalPos())
