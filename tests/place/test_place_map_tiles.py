"""Guard the Places-map basemap against regressing to a keyed / gated tile
provider.

The map has to render without any API key or registration. CARTO's
token-free ``basemaps.cartocdn.com`` endpoint was retired (it started
stamping "API KEY REQUIRED" over every tile), so both the on-disk HTML
template and the in-code fallback template must point at a keyless
provider instead.
"""

from pathlib import Path

from src.place.place_map import MapView

TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "assets" / "place_map_template.html"

# Providers that require an API key, a token, or have started gating the
# anonymous endpoint we used to rely on.
FORBIDDEN_TILE_HOSTS = (
    "basemaps.cartocdn.com",
    "cartocdn.com",
    "api.mapbox.com",
    "tiles.stadiamaps.com",
    "api.maptiler.com",
    "tile.thunderforest.com",
)

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
