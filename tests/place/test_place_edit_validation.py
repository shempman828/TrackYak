"""Tests for PlaceEditDialog validation: parent cycles, same-named parents, coordinates, and preset parents."""

import pytest

from src.place.dialogs.place_edit import PlaceEditDialog, _parse_coordinate


class _FakePlace:
    def __init__(self, place_id, place_name, parent_id=None, lat=None, lon=None):
        self.place_id = place_id
        self.place_name = place_name
        self.place_type = "City"
        self.place_latitude = lat
        self.place_longitude = lon
        self.place_description = ""
        self.parent_id = parent_id
        self.MBID = None


class _StubGet:
    def __init__(self, places):
        self._places = places

    def get_all_entities(self, model_name, **_filters):
        return list(self._places)

    def get_entity_object(self, model_name, **filters):
        for place in self._places:
            if "place_id" in filters and place.place_id == filters["place_id"]:
                return place
            if "place_name" in filters and place.place_name == filters["place_name"]:
                return place
        return None


class _StubController:
    def __init__(self, places):
        self.get = _StubGet(places)


def _places():
    # 1 France > 2 Paris (FR) > 3 Bastille; 4 USA > 5 Paris (US) > 6 Venue
    return [
        _FakePlace(1, "France"),
        _FakePlace(2, "Paris", parent_id=1),
        _FakePlace(3, "Bastille", parent_id=2),
        _FakePlace(4, "USA"),
        _FakePlace(5, "Paris", parent_id=4),
        _FakePlace(6, "Venue", parent_id=5),
    ]


@pytest.fixture
def dialogs():
    opened = []
    yield opened
    for dialog in opened:
        dialog.close()


def _open(dialogs, controller, *args, **kwargs):
    dialog = PlaceEditDialog(controller, None, *args, **kwargs)
    dialogs.append(dialog)
    return dialog


def test_unchanged_prefilled_parent_keeps_its_id_when_another_place_has_the_same_name(qapp, dialogs):
    places = _places()
    venue = places[5]
    dialog = _open(dialogs, _StubController(places), venue)

    assert dialog.parent_edit.text() == "Paris"
    assert dialog.get_place_data()["parent_id"] == 5  # not the French Paris (id 2)


def test_parent_set_to_own_descendant_is_rejected(qapp, dialogs):
    places = _places()
    dialog = _open(dialogs, _StubController(places), places[1])  # Paris (FR)
    dialog.parent_edit.setText("Bastille")

    assert dialog.get_place_data() is None
    assert "inside itself" in dialog.parent_error.text()


def test_parent_set_to_itself_is_rejected_in_multi_edit(qapp, dialogs):
    places = _places()
    dialog = _open(dialogs, _StubController(places), [places[0], places[3]])  # France, USA
    dialog.parent_edit.setText("France")
    dialog._mark_dirty("parent_id")

    assert dialog.get_bulk_changes() is None


def test_new_child_dialog_prefills_the_parent(qapp, dialogs):
    places = _places()
    dialog = _open(dialogs, _StubController(places), preset_parent=places[4])  # Paris (US)
    dialog.name_edit.setText("Club")

    assert dialog.parent_edit.text() == "Paris"
    assert dialog.get_place_data()["parent_id"] == 5


def test_new_parent_dialog_prefills_old_parent_and_rejects_the_child_itself(qapp, dialogs):
    places = _places()
    bastille = places[2]
    dialog = _open(dialogs, _StubController(places), new_parent_of=bastille)
    dialog.name_edit.setText("Arrondissement")

    assert dialog.get_place_data()["parent_id"] == 2  # Bastille's old parent
    dialog.parent_edit.setText("Bastille")
    assert dialog.get_place_data() is None


@pytest.mark.parametrize(
    ("text", "limit", "expected"),
    [("", 90, (None, True)), ("45.5", 90, (45.5, True)), ("91", 90, (91.0, False)), ("-180", 180, (-180.0, True)), ("nan", 90, (None, False)), ("inf", 180, (None, False)), ("abc", 90, (None, False))],
)
def test_parse_coordinate(text, limit, expected):
    value, ok = _parse_coordinate(text, limit)
    assert ok is expected[1]
    if expected[1]:
        assert value == expected[0]


@pytest.mark.parametrize(("lat", "lon"), [("200", "10"), ("nan", "10"), ("10", ""), ("", "10")])
def test_invalid_or_half_coordinates_keep_the_dialog_open(qapp, dialogs, lat, lon):
    dialog = _open(dialogs, _StubController(_places()))
    dialog.name_edit.setText("Somewhere")
    dialog.lat_edit.setText(lat)
    dialog.lon_edit.setText(lon)
    accepted = []
    dialog.accepted.connect(lambda: accepted.append(True))

    dialog.validate_and_accept()

    assert accepted == []
    assert dialog.coords_error.text()


def test_valid_coordinates_accept_the_dialog(qapp, dialogs):
    dialog = _open(dialogs, _StubController(_places()))
    dialog.name_edit.setText("Somewhere")
    dialog.lat_edit.setText("36.16")
    dialog.lon_edit.setText("-86.78")
    accepted = []
    dialog.accepted.connect(lambda: accepted.append(True))

    dialog.validate_and_accept()

    assert accepted == [True]
