"""Tests for the "Find Duplicate Places…" context-menu action wiring
(docs/specs/place_duplicate_detection.md, acceptance criteria 1-3).
"""

from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMenu

from src.place.place_list import ListView


class _FakePlace:
    def __init__(self, place_id, place_name, place_type="City", parent_id=None):
        self.place_id = place_id
        self.place_name = place_name
        self.place_type = place_type
        self.MBID = None
        self.parent_id = parent_id
        self.association_count = 0
        self.recursive_association_count = 0
        self.place_latitude = None
        self.place_longitude = None
        self.place_description = ""


class _StubGet:
    def __init__(self, places):
        self._places = places

    def get_all_entities(self, model_name, **filters):
        assert model_name == "Place"
        return list(self._places)


class _StubController:
    def __init__(self, places):
        self.get = _StubGet(places)


def _make_list_view(qapp, places=None):
    places = places if places is not None else [_FakePlace(1, "USA", place_type="Country")]
    controller = _StubController(places)
    list_view = ListView(controller)
    list_view.load_places()
    return list_view


# ---- acceptance criterion 1: action always shown -----------------------


def test_find_duplicates_action_shown_when_no_item_under_cursor(qapp, monkeypatch):
    list_view = _make_list_view(qapp, places=[])
    built_menus = []
    monkeypatch.setattr(QMenu, "exec_", lambda self, *a, **k: built_menus.append(self))
    try:
        list_view.show_context_menu(list_view.tree_widget.rect().center())

        assert len(built_menus) == 1
        labels = [a.text() for a in built_menus[0].actions()]
        assert "🔎 Find Duplicate Places…" in labels
    finally:
        list_view.close()


def test_find_duplicates_action_shown_alongside_item_actions(qapp, monkeypatch):
    place = _FakePlace(1, "USA", place_type="Country")
    list_view = _make_list_view(qapp, places=[place])
    built_menus = []
    monkeypatch.setattr(QMenu, "exec_", lambda self, *a, **k: built_menus.append(self))
    try:
        item = list_view.tree_widget.topLevelItem(0)
        position = list_view.tree_widget.visualItemRect(item).center()

        list_view.show_context_menu(position)

        assert len(built_menus) == 1
        labels = [a.text() for a in built_menus[0].actions()]
        assert "🔎 Find Duplicate Places…" in labels
        assert "Merge" in labels
    finally:
        list_view.close()


# ---- acceptance criteria 2 and 3: empty-result status messages ---------


def test_find_fuzzy_matches_with_no_places_shows_status_message(qapp):
    list_view = ListView(_StubController([]))
    try:
        with patch("src.place.place_list.show_status_message") as mock_status:
            list_view.find_fuzzy_matches()

        mock_status.assert_called_once()
        assert "No places found" in mock_status.call_args[0][1]
    finally:
        list_view.close()


def test_find_fuzzy_matches_with_no_similar_pairs_shows_status_message(qapp):
    places = [_FakePlace(1, "Tokyo", place_type="City"), _FakePlace(2, "Berlin", place_type="City")]
    list_view = _make_list_view(qapp, places=places)
    try:
        with patch("src.place.place_list.show_status_message") as mock_status:
            list_view.find_fuzzy_matches()
            list_view._fuzzy_worker.wait(5000)
            QApplication.processEvents()

        mock_status.assert_called_once()
        assert "No similar place names found" in mock_status.call_args[0][1]
    finally:
        list_view.close()
