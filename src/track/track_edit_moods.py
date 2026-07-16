# ---------------------------------------------------------------------------
# MoodsTab
# ---------------------------------------------------------------------------
from __future__ import annotations


from PySide6.QtCore import QStringListModel, Qt
from PySide6.QtWidgets import (
    QCompleter,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QMenu,
    QListWidgetItem,
)

from src.core.logger_config import logger
from src.track.track_edit_basetab import _BaseTab


def _fetch_all_moods(controller):
    """Fetch all moods for completer indexing."""
    try:
        return controller.get.get_all_entities("Mood") or []
    except Exception as e:
        logger.warning(f"Could not fetch moods for completer: {e}")
        return []


def _find_or_create_mood(controller, name, known_moods):
    """
    Look up a mood by name (case-insensitive) among known moods; create one
    only if none is found. Matching against the already-loaded list (rather
    than a fresh DB query) guarantees an existing mood always wins over
    creating a same-named duplicate, regardless of case.
    """
    lowered = name.strip().lower()
    for mood in known_moods:
        if (mood.mood_name or "").strip().lower() == lowered:
            return mood
    return controller.add.add_entity("Mood", mood_name=name)


class _MoodNameEdit(QLineEdit):
    """
    QLineEdit with a QCompleter over known moods. When the user picks a
    completion, we remember the matched mood_id directly so add-time skips
    the name-lookup roundtrip entirely (and can't collide with a same-named
    mood). Any manual edit after a match clears the lock — typing a new
    name always means "maybe create new."
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("Search moods…")
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

    def matched_mood_id(self):
        return self._matched_id

    def reset(self):
        self.clear()
        self._matched_id = None


class MoodsTab(_BaseTab):
    def __init__(self, tracks: list, controller, parent=None):
        super().__init__(tracks, controller, parent)
        self._build_ui()
        self._refresh_completer_index()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        self._search = _MoodNameEdit()
        self._search.textChanged.connect(self._on_search_text_changed)
        self._search.returnPressed.connect(self._add)
        search_row.addWidget(self._search)

        self._add_btn = QPushButton("Add Mood")
        self._add_btn.setEnabled(False)
        self._add_btn.clicked.connect(self._add)
        search_row.addWidget(self._add_btn)
        layout.addLayout(search_row)

        self._list = QListWidget()
        layout.addWidget(self._list)

    def _on_search_text_changed(self, text: str):
        self._add_btn.setEnabled(bool(text.strip()))

    def _refresh_completer_index(self):
        self._known_moods = _fetch_all_moods(self.controller)
        index = {m.mood_name: m.mood_id for m in self._known_moods if m.mood_name}
        self._search.set_index(index)

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

    def _add(self):
        mood_name = self._search.text().strip()
        if not mood_name:
            return

        matched_id = self._search.matched_mood_id()
        try:
            if matched_id is not None:
                mood = self.controller.get.get_entity_object("Mood", mood_id=matched_id)
            else:
                mood = _find_or_create_mood(self.controller, mood_name, self._known_moods)
        except Exception as e:
            logger.error(f"Failed to find/create mood: {e}")
            return
        if not mood:
            return

        rows = [
            {"track_id": track.track_id, "mood_id": mood.mood_id}
            for track in self.tracks
        ]
        try:
            self.controller.add.add_entities("MoodTrackAssociation", rows)
        except Exception as e:
            logger.error(f"Failed to add mood to tracks: {e}")
        self._search.reset()
        self._refresh_completer_index()
        self.load(self.tracks)

    def _remove_selected(self):
        item = self._list.currentItem()
        if not item:
            return
        mood_id = item.data(Qt.UserRole)
        track_ids = [track.track_id for track in self.tracks]
        try:
            self.controller.delete.delete_entity(
                "MoodTrackAssociation", track_id=track_ids, mood_id=mood_id
            )
        except Exception as e:
            logger.error(f"Failed to remove mood from tracks: {e}")
        self.load(self.tracks)

    def contextMenuEvent(self, event):
        if self._list.currentItem():
            menu = QMenu(self)
            menu.addAction("Remove", self._remove_selected)
            menu.exec(event.globalPos())
