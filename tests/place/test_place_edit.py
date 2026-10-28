"""Place edit dialog: validation, MBID field, type completer, and bulk
(multi-place) edit.
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu
import pytest

from src.place import place_list
from src.place.dialogs import place_edit
from src.place.dialogs.place_edit import PlaceEditDialog, _parse_coordinate
from src.place.place_list import ListView

# ---------------------------------------------------------------------------
# Tests for PlaceEditDialog validation: parent cycles, same-named parents, coordinates, and preset parents.
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Tests for bug #243: the place edit dialog had no field for MBID even
# ---------------------------------------------------------------------------


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


class _StubGetMbid:
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


class _StubControllerMbid:
    def __init__(self, places=None):
        self.get = _StubGetMbid(places)


def test_mbid_field_prefilled_when_editing_existing_place(qapp):
    place = _StubPlace(1, "Chicago", mbid="abc-123")
    controller = _StubControllerMbid(places=[place])

    dialog = PlaceEditDialog(controller, place=place)
    try:
        assert dialog.mbid_edit.text() == "abc-123"
    finally:
        dialog.close()


def test_mbid_field_blank_when_place_has_no_mbid(qapp):
    place = _StubPlace(1, "Chicago", mbid=None)
    controller = _StubControllerMbid(places=[place])

    dialog = PlaceEditDialog(controller, place=place)
    try:
        assert dialog.mbid_edit.text() == ""
    finally:
        dialog.close()


def test_get_place_data_includes_entered_mbid(qapp):
    controller = _StubControllerMbid(places=[])

    dialog = PlaceEditDialog(controller)
    try:
        dialog.name_edit.setText("Chicago")
        dialog.mbid_edit.setText(" xyz-789 ")

        data = dialog.get_place_data()

        assert data["MBID"] == "xyz-789"
    finally:
        dialog.close()


def test_get_place_data_mbid_is_none_when_blank(qapp):
    controller = _StubControllerMbid(places=[])

    dialog = PlaceEditDialog(controller)
    try:
        dialog.name_edit.setText("Chicago")

        data = dialog.get_place_data()

        assert data["MBID"] is None
    finally:
        dialog.close()


# ---------------------------------------------------------------------------
# Tests for the place edit dialog's Type field autocompletion.
# ---------------------------------------------------------------------------


class _StubPlaceCompleter:
    def __init__(self, place_id, place_name, place_type, parent_id=None):
        self.place_id = place_id
        self.place_name = place_name
        self.place_type = place_type
        self.place_latitude = None
        self.place_longitude = None
        self.place_description = ""
        self.parent_id = parent_id
        self.MBID = None


class _StubGetCompleter:
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


class _StubControllerCompleter:
    def __init__(self, places=None):
        self.get = _StubGetCompleter(places)


def test_type_completer_lists_unique_title_cased_existing_types(qapp):
    places = [
        _StubPlaceCompleter(1, "Chicago", "city"),
        _StubPlaceCompleter(2, "Berlin", "CITY"),
        _StubPlaceCompleter(3, "Germany", "country"),
    ]
    controller = _StubControllerCompleter(places=places)

    dialog = PlaceEditDialog(controller)
    try:
        model = dialog.type_edit.completer().model()
        types = {model.data(model.index(i, 0)) for i in range(model.rowCount())}
        assert types == {"City", "Country"}
    finally:
        dialog.close()


def test_type_completer_excludes_blank_types(qapp):
    places = [
        _StubPlaceCompleter(1, "Chicago", "City"),
        _StubPlaceCompleter(2, "Nowhere", ""),
        _StubPlaceCompleter(3, "Nowhere2", None),
    ]
    controller = _StubControllerCompleter(places=places)

    dialog = PlaceEditDialog(controller)
    try:
        model = dialog.type_edit.completer().model()
        types = {model.data(model.index(i, 0)) for i in range(model.rowCount())}
        assert types == {"City"}
    finally:
        dialog.close()


def test_type_completer_matches_case_insensitive_substring(qapp):
    places = [_StubPlaceCompleter(1, "Chicago", "City")]
    controller = _StubControllerCompleter(places=places)

    dialog = PlaceEditDialog(controller)
    try:
        completer = dialog.type_edit.completer()
        assert completer.caseSensitivity() == Qt.CaseInsensitive
        assert completer.filterMode() == Qt.MatchContains
    finally:
        dialog.close()


def test_type_field_still_accepts_free_form_text_not_in_list(qapp):
    places = [_StubPlaceCompleter(1, "Chicago", "City")]
    controller = _StubControllerCompleter(places=places)

    dialog = PlaceEditDialog(controller)
    try:
        dialog.name_edit.setText("Atlantis")
        dialog.type_edit.setText("Mythical Continent")

        data = dialog.get_place_data()

        assert data["place_type"] == "Mythical Continent"
    finally:
        dialog.close()


# ---------------------------------------------------------------------------
# Tests for bulk-editing several places at once (docs/specs/place-list-multi-edit.md).
# ---------------------------------------------------------------------------


class _FakePlaceEdit:
    def __init__(self, place_id, place_name, place_type="City", parent_id=None, place_latitude=None, place_longitude=None, place_description="", mbid=None):
        self.place_id = place_id
        self.place_name = place_name
        self.place_type = place_type
        self.MBID = mbid
        self.parent_id = parent_id
        self.association_count = 0
        self.recursive_association_count = 0
        self.associations = []
        self.place_latitude = place_latitude
        self.place_longitude = place_longitude
        self.place_description = place_description


class _StubGetEdit:
    def __init__(self, places):
        self._places = places

    def get_all_entities(self, model_name, **filters):
        assert model_name == "Place"
        return list(self._places)

    def get_entity_object(self, model_name, **filters):
        assert model_name == "Place"
        place_id = filters.get("place_id")
        place_name = filters.get("place_name")
        for place in self._places:
            if place_id is not None and place.place_id == place_id:
                return place
            if place_name is not None and place.place_name == place_name:
                return place
        return None


class _StubUpdate:
    def __init__(self, result=True):
        self.result = result
        self.calls = []

    def update_entities(self, model_name, entity_ids, **kwargs):
        self.calls.append((model_name, list(entity_ids), dict(kwargs)))
        return self.result


class _StubControllerEdit:
    def __init__(self, places, update_result=True):
        self.get = _StubGetEdit(places)
        self.update = _StubUpdate(update_result)


def _make_list_view(places, update_result=True):
    controller = _StubControllerEdit(places, update_result=update_result)
    list_view = ListView(controller)
    list_view.load_places()
    return list_view, controller


def _select_all(list_view):
    list_view.tree_widget.selectAll()


def test_edit_action_not_shown_for_single_selection(qapp, monkeypatch):
    place = _FakePlaceEdit(1, "Chicago")
    list_view, _ = _make_list_view([place])
    built_menus = []
    monkeypatch.setattr(QMenu, "exec_", lambda self, *a, **k: built_menus.append(self))
    try:
        item = list_view.tree_widget.topLevelItem(0)
        position = list_view.tree_widget.visualItemRect(item).center()

        list_view.show_context_menu(position)

        labels = [a.text() for a in built_menus[0].actions()]
        assert not any(label.startswith("Edit ") and "Places" in label for label in labels)
        assert "Edit" in labels
    finally:
        list_view.close()


def test_edit_n_places_action_shown_for_multi_selection(qapp, monkeypatch):
    places = [_FakePlaceEdit(1, "Chicago"), _FakePlaceEdit(2, "Denver")]
    list_view, _ = _make_list_view(places)
    _select_all(list_view)
    built_menus = []
    monkeypatch.setattr(QMenu, "exec_", lambda self, *a, **k: built_menus.append(self))
    try:
        item = list_view.tree_widget.topLevelItem(0)
        position = list_view.tree_widget.visualItemRect(item).center()

        list_view.show_context_menu(position)

        labels = [a.text() for a in built_menus[0].actions()]
        assert "Edit 2 Places…" in labels
        assert "Delete 2 Places" in labels
    finally:
        list_view.close()


def test_multi_dialog_title_and_fields(qapp):
    places = [_FakePlaceEdit(1, "Chicago"), _FakePlaceEdit(2, "Denver")]
    controller = _StubControllerEdit(places)

    dialog = PlaceEditDialog(controller, place=places)
    try:
        assert dialog.is_multi is True
        assert dialog.windowTitle() == "Edit 2 Places"
        assert not hasattr(dialog, "name_edit")
        assert not hasattr(dialog, "mbid_edit")
        assert not hasattr(dialog, "region_edit")
        assert hasattr(dialog, "type_edit")
        assert hasattr(dialog, "lat_edit")
        assert hasattr(dialog, "lon_edit")
        assert hasattr(dialog, "desc_edit")
        assert hasattr(dialog, "parent_edit")
    finally:
        dialog.close()


def test_multi_dialog_prefills_shared_type(qapp):
    places = [_FakePlaceEdit(1, "Chicago", place_type="City"), _FakePlaceEdit(2, "Denver", place_type="City")]
    controller = _StubControllerEdit(places)

    dialog = PlaceEditDialog(controller, place=places)
    try:
        assert dialog.type_edit.text() == "City"
    finally:
        dialog.close()


def test_multi_dialog_leaves_mixed_type_blank(qapp):
    places = [_FakePlaceEdit(1, "Chicago", place_type="City"), _FakePlaceEdit(2, "Illinois", place_type="State")]
    controller = _StubControllerEdit(places)

    dialog = PlaceEditDialog(controller, place=places)
    try:
        assert dialog.type_edit.text() == ""
    finally:
        dialog.close()


def test_untouched_dialog_produces_no_changes(qapp):
    places = [_FakePlaceEdit(1, "Chicago", place_type="City"), _FakePlaceEdit(2, "Denver", place_type="City")]
    controller = _StubControllerEdit(places)

    dialog = PlaceEditDialog(controller, place=places)
    try:
        assert dialog.get_bulk_changes() == {}
    finally:
        dialog.close()


def test_edit_selected_places_no_op_when_nothing_changed(qapp, monkeypatch):
    places = [_FakePlaceEdit(1, "Chicago"), _FakePlaceEdit(2, "Denver")]
    list_view, controller = _make_list_view(places)
    _select_all(list_view)
    monkeypatch.setattr(PlaceEditDialog, "exec_", lambda self: PlaceEditDialog.Accepted)
    refreshed = []
    list_view.parent_view = type("PV", (), {"refresh_views": lambda self: refreshed.append(True)})()
    try:
        list_view.edit_selected_places()

        assert controller.update.calls == []
        assert refreshed == []
    finally:
        list_view.close()


def test_touching_only_type_field_updates_only_that_field(qapp):
    places = [_FakePlaceEdit(1, "Chicago", place_type="City"), _FakePlaceEdit(2, "Denver", place_type="City")]
    controller = _StubControllerEdit(places)

    dialog = PlaceEditDialog(controller, place=places)
    try:
        dialog.type_edit.setText("Metro")
        dialog._mark_dirty("place_type")  # textEdited requires real key events; simulate directly

        changes = dialog.get_bulk_changes()

        assert changes == {"place_type": "Metro"}
    finally:
        dialog.close()


def test_edit_selected_places_calls_update_entities_with_only_touched_fields(qapp, monkeypatch):
    places = [_FakePlaceEdit(1, "Chicago", place_type="City"), _FakePlaceEdit(2, "Denver", place_type="City")]
    list_view, controller = _make_list_view(places)
    _select_all(list_view)

    opened_dialogs = []

    def fake_exec(self):
        opened_dialogs.append(self)
        self.type_edit.setText("Metro")
        self._mark_dirty("place_type")
        return PlaceEditDialog.Accepted

    monkeypatch.setattr(PlaceEditDialog, "exec_", fake_exec)
    refreshed = []
    list_view.parent_view = type("PV", (), {"refresh_views": lambda self: refreshed.append(True)})()
    try:
        list_view.edit_selected_places()

        assert len(controller.update.calls) == 1
        model_name, entity_ids, kwargs = controller.update.calls[0]
        assert model_name == "Place"
        assert sorted(entity_ids) == [1, 2]
        assert kwargs == {"place_type": "Metro"}
        assert refreshed == [True]
    finally:
        list_view.close()


def test_multi_dialog_rejects_non_numeric_latitude(qapp):
    places = [_FakePlaceEdit(1, "Chicago"), _FakePlaceEdit(2, "Denver")]
    controller = _StubControllerEdit(places)

    dialog = PlaceEditDialog(controller, place=places)
    try:
        dialog.lat_edit.setText("not-a-number")
        dialog._mark_dirty("place_latitude")

        dialog.validate_and_accept()

        assert dialog.result() != PlaceEditDialog.Accepted
        assert "Invalid Coordinates" in dialog.coords_error.text()
        assert not dialog.coords_error.isHidden()
    finally:
        dialog.close()


def test_multi_dialog_rejects_unknown_parent_name(qapp):
    places = [_FakePlaceEdit(1, "Chicago"), _FakePlaceEdit(2, "Denver")]
    controller = _StubControllerEdit(places)

    dialog = PlaceEditDialog(controller, place=places)
    try:
        dialog.parent_edit.setText("Nowhere")
        dialog._mark_dirty("parent_id")

        changes = dialog.get_bulk_changes()

        assert changes is None
        assert "Invalid Parent" in dialog.parent_error.text()
        assert not dialog.parent_error.isHidden()
    finally:
        dialog.close()


def test_edit_selected_places_shows_error_when_update_fails(qapp, monkeypatch):
    places = [_FakePlaceEdit(1, "Chicago", place_type="City"), _FakePlaceEdit(2, "Denver", place_type="City")]
    list_view, _controller = _make_list_view(places, update_result=False)
    _select_all(list_view)

    def fake_exec(self):
        self.type_edit.setText("Metro")
        self._mark_dirty("place_type")
        return PlaceEditDialog.Accepted

    monkeypatch.setattr(PlaceEditDialog, "exec_", fake_exec)
    criticals = []
    monkeypatch.setattr(place_list.QMessageBox, "critical", lambda *a, **k: criticals.append(a))
    refreshed = []
    list_view.parent_view = type("PV", (), {"refresh_views": lambda self: refreshed.append(True)})()
    try:
        list_view.edit_selected_places()

        assert len(criticals) == 1
        assert refreshed == []
    finally:
        list_view.close()


def test_single_place_dialog_still_has_name_and_mbid_fields(qapp):
    place = _FakePlaceEdit(1, "Chicago", mbid="abc-123")
    controller = _StubControllerEdit([place])

    dialog = PlaceEditDialog(controller, place=place)
    try:
        assert dialog.is_multi is False
        assert dialog.windowTitle() == "Edit Place"
        assert dialog.name_edit.text() == "Chicago"
        assert dialog.mbid_edit.text() == "abc-123"
        data = dialog.get_place_data()
        assert data["place_name"] == "Chicago"
        assert data["MBID"] == "abc-123"
    finally:
        dialog.close()
