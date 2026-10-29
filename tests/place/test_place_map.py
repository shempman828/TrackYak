"""Place map: marker data and tile handling."""

from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QSize

from src.place.map.place_map import _FIT_MAX_ZOOM, MapView
from src.place.map.place_map_filter import MultiSelectWidget
from src.place.place_assoc_details import AssociationDetailsDialog

# ---------------------------------------------------------------------------
# Tests for map data handling: coordinate checks, fit zoom, type-filter signals, and the dialog size.
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Guard the Places-map basemap against regressing to a keyed / gated tile
# ---------------------------------------------------------------------------


TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "assets" / "place_map_template.html"


# Providers that require an API key, a token, or have started gating the
# anonymous endpoint we used to rely on.
FORBIDDEN_TILE_HOSTS = ("basemaps.cartocdn.com", "cartocdn.com", "api.mapbox.com", "tiles.stadiamaps.com", "api.maptiler.com", "tile.thunderforest.com")


# The keyless provider we switched to.
REQUIRED_TILE_HOST = "server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray"


def test_html_template_uses_keyless_basemap():
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    for host in FORBIDDEN_TILE_HOSTS:
        assert host not in template, f"{host} needs an API key / is gated"
    assert REQUIRED_TILE_HOST in template


def test_code_fallback_template_uses_keyless_basemap():
    # _get_fallback_template is only reached when the asset file is missing,
    # so it has to carry the same keyless provider.
    fallback = MapView._get_fallback_template(None)
    for host in FORBIDDEN_TILE_HOSTS:
        assert host not in fallback, f"{host} needs an API key / is gated"
    assert REQUIRED_TILE_HOST in fallback


def test_both_templates_upscale_past_native_zoom():
    # Esri Dark Gray Canvas has no native tiles past z16; without
    # maxNativeZoom Leaflet requests blank tiles when the user zooms in.
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    fallback = MapView._get_fallback_template(None)
    assert "maxNativeZoom" in template
    assert "maxNativeZoom" in fallback


def test_template_tints_only_the_basemap():
    # A filter on #map would also shift the marker colors away from the legend.
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    map_rule = template.split("#map {", 1)[1].split("}", 1)[0]
    assert "filter" not in map_rule
    assert ".leaflet-tile-pane" in template


def test_template_pins_every_cdn_script_and_stylesheet():
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    for tag in template.split("<")[1:]:
        if "https://unpkg.com" in tag and (tag.startswith(("script", "link"))):
            assert 'integrity="sha256-' in tag, tag


def test_template_shows_a_message_when_leaflet_does_not_load():
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    assert "typeof L === 'undefined'" in template
    assert "map-load-error" in template


def test_missing_leaflet_makes_the_next_refresh_reload_the_page():
    view = SimpleNamespace(_map_initialized=True)
    MapView._on_leaflet_check(view, True)
    assert view._map_initialized is False

    view = SimpleNamespace(_map_initialized=True)
    MapView._on_leaflet_check(view, False)
    assert view._map_initialized is True
