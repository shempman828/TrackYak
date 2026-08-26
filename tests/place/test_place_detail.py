"""Regression test: opening PlaceDetailView used to crash with
AttributeError: 'PlaceDetailView' object has no attribute 'view_on_map'.

The "View on Map" button was wired to a handler that was never
implemented (confirmed via git history), so it was removed rather than
stubbed out. This test guards against a re-add of the button without an
implementation.
"""

from PySide6.QtWidgets import QPushButton

from src.place.place_detail import PlaceDetailView


class _StubPlace:
    def __init__(self, place_id, place_name, parent_id=None):
        self.place_id = place_id
        self.place_name = place_name
        self.place_type = "City"
        self.place_latitude = None
        self.place_longitude = None
        self.place_description = ""
        self.parent_id = parent_id


class _StubGet:
    def __init__(self, places=None):
        self._places = places or []

    def get_entity_object(self, model_name, **filters):
        place_id = filters.get("place_id")
        for place in self._places:
            if place.place_id == place_id:
                return place
        return None


class _StubController:
    def __init__(self, places=None):
        self.get = _StubGet(places)


def test_place_detail_view_opens_without_error(qapp):
    place = _StubPlace(20, "Chicago")
    controller = _StubController(places=[place])

    dialog = PlaceDetailView(controller, place)
    try:
        button_labels = {
            button.text() for button in dialog.findChildren(QPushButton)
        }
        assert "View on Map" not in button_labels
        assert not hasattr(dialog, "view_on_map")
    finally:
        dialog.close()
