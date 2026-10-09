"""Tests for map data handling: coordinate checks, fit zoom, type-filter signals, and the dialog size."""

from types import SimpleNamespace

from PySide6.QtCore import QSize

from src.place.map.place_map import _FIT_MAX_ZOOM, MapView
from src.place.map.place_map_filter import MultiSelectWidget
from src.place.place_assoc_details import AssociationDetailsDialog


def _raw(lat, lon):
    return SimpleNamespace(place_id=1, place_name="X", place_type="City", place_latitude=lat, place_longitude=lon, place_description=None)


def test_bad_coordinates_are_not_put_on_the_map():
    for lat, lon in [(float("nan"), 1.0), (95.0, 1.0), (1.0, 181.0), (1.0, None), ("abc", 1.0)]:
        data = MapView._create_place_data(MapView, _raw(lat, lon))
        assert data["lat"] is None
        assert data["lon"] is None


def test_good_coordinates_are_kept():
    data = MapView._create_place_data(MapView, _raw("36.16", -86.78))
    assert (data["lat"], data["lon"]) == (36.16, -86.78)


def test_fit_bounds_has_a_zoom_limit():
    js = MapView._create_bounds_js(None, [{"lat": 1.0, "lon": 2.0}])
    assert f"maxZoom: {_FIT_MAX_ZOOM}" in js


def test_select_all_and_none_emit_one_signal(qapp):
    widget = MultiSelectWidget()
    widget.set_items(["City", "Country", "Venue"], default_selected=False)
    emitted = []
    widget.selection_changed.connect(emitted.append)

    widget.select_all()
    widget.select_none()

    assert emitted == [["City", "Country", "Venue"], []]


def test_association_dialog_size_is_clamped_with_integers(qapp, monkeypatch):
    controller = SimpleNamespace(get=SimpleNamespace(get_all_entities=lambda *a, **k: []))
    place = SimpleNamespace(place_id=1, place_name="A & B", place_type=None)
    dialog = AssociationDetailsDialog(controller, place)
    try:
        monkeypatch.setattr(dialog, "size", lambda: QSize(100000, 100000))
        dialog.adjust_size()  # raised TypeError when the clamp produced floats
    finally:
        dialog.close()
