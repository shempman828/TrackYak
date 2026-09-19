"""Tests for the place edit dialog's Type field autocompletion.

Covers PlaceEditDialog (src/place/place_edit.py): the Type field should
suggest existing place types (case-insensitive, unique, title-cased, blanks
excluded) while still accepting free-form text.
"""

from PySide6.QtCore import Qt

from src.place.place_edit import PlaceEditDialog


class _StubPlace:
    def __init__(self, place_id, place_name, place_type, parent_id=None):
        self.place_id = place_id
        self.place_name = place_name
        self.place_type = place_type
        self.place_latitude = None
        self.place_longitude = None
        self.place_description = ""
        self.parent_id = parent_id
        self.MBID = None


class _StubGet:
    def __init__(self, places=None):
        self._places = places or []

    def get_all_entities(self, model_name, **kwargs):
        return self._places

    def get_entity_object(self, model_name, **filters):
        place_id = filters.get("place_id")
        for place in self._places:
            if place.place_id == place_id:
                return place
        return None


class _StubController:
    def __init__(self, places=None):
        self.get = _StubGet(places)


def test_type_completer_lists_unique_title_cased_existing_types(qapp):
    places = [
        _StubPlace(1, "Chicago", "city"),
        _StubPlace(2, "Berlin", "CITY"),
        _StubPlace(3, "Germany", "country"),
    ]
    controller = _StubController(places=places)

    dialog = PlaceEditDialog(controller)
    try:
        model = dialog.type_edit.completer().model()
        types = {model.data(model.index(i, 0)) for i in range(model.rowCount())}
        assert types == {"City", "Country"}
    finally:
        dialog.close()


def test_type_completer_excludes_blank_types(qapp):
    places = [
        _StubPlace(1, "Chicago", "City"),
        _StubPlace(2, "Nowhere", ""),
        _StubPlace(3, "Nowhere2", None),
    ]
    controller = _StubController(places=places)

    dialog = PlaceEditDialog(controller)
    try:
        model = dialog.type_edit.completer().model()
        types = {model.data(model.index(i, 0)) for i in range(model.rowCount())}
        assert types == {"City"}
    finally:
        dialog.close()


def test_type_completer_matches_case_insensitive_substring(qapp):
    places = [_StubPlace(1, "Chicago", "City")]
    controller = _StubController(places=places)

    dialog = PlaceEditDialog(controller)
    try:
        completer = dialog.type_edit.completer()
        assert completer.caseSensitivity() == Qt.CaseInsensitive
        assert completer.filterMode() == Qt.MatchContains
    finally:
        dialog.close()


def test_type_field_still_accepts_free_form_text_not_in_list(qapp):
    places = [_StubPlace(1, "Chicago", "City")]
    controller = _StubController(places=places)

    dialog = PlaceEditDialog(controller)
    try:
        dialog.name_edit.setText("Atlantis")
        dialog.type_edit.setText("Mythical Continent")

        data = dialog.get_place_data()

        assert data["place_type"] == "Mythical Continent"
    finally:
        dialog.close()
