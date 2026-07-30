"""Tests for bug #243: the place edit dialog had no field for MBID even
though Place already stores it (populated automatically by MusicBrainz
place-chain resolution). Users had no way to view or manually set/correct
a place's MBID.

Covers PlaceEditDialog (src/place/place_edit.py): the MBID field must be
prefilled when editing an existing place and must round-trip through
get_place_data().
"""

from src.place.place_edit import PlaceEditDialog


class _StubPlace:
    def __init__(self, place_id, place_name, mbid=None, parent_id=None):
        self.place_id = place_id
        self.place_name = place_name
        self.place_type = "City"
        self.place_latitude = None
        self.place_longitude = None
        self.place_description = ""
        self.parent_id = parent_id
        self.MBID = mbid


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


def test_mbid_field_prefilled_when_editing_existing_place(qapp):
    place = _StubPlace(1, "Chicago", mbid="abc-123")
    controller = _StubController(places=[place])

    dialog = PlaceEditDialog(controller, place=place)
    try:
        assert dialog.mbid_edit.text() == "abc-123"
    finally:
        dialog.close()


def test_mbid_field_blank_when_place_has_no_mbid(qapp):
    place = _StubPlace(1, "Chicago", mbid=None)
    controller = _StubController(places=[place])

    dialog = PlaceEditDialog(controller, place=place)
    try:
        assert dialog.mbid_edit.text() == ""
    finally:
        dialog.close()


def test_get_place_data_includes_entered_mbid(qapp):
    controller = _StubController(places=[])

    dialog = PlaceEditDialog(controller)
    try:
        dialog.name_edit.setText("Chicago")
        dialog.mbid_edit.setText(" xyz-789 ")

        data = dialog.get_place_data()

        assert data["MBID"] == "xyz-789"
    finally:
        dialog.close()


def test_get_place_data_mbid_is_none_when_blank(qapp):
    controller = _StubController(places=[])

    dialog = PlaceEditDialog(controller)
    try:
        dialog.name_edit.setText("Chicago")

        data = dialog.get_place_data()

        assert data["MBID"] is None
    finally:
        dialog.close()
