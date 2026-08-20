"""Regression test for the "influence graph nodes are hard to read unless
very zoomed in" bug (Influences > Global Network view).

Root cause (see scratch/graph_repro/repro.py for the full empirical
investigation against the real database): Cytoscape draws node labels in
graph space, not screen space, so on-screen label size is
font-size-in-graph-units * current zoom. The global network can be ~600
nodes across ~30 Louvain communities, so cy.fit() settles on a small zoom
(~0.09-0.12 measured against real data) just to fit everyone at once. The
label font-size range (previously 8-13 graph units) was sized as if that
fit-zoom were close to 1, so real labels rendered under 1.5px -- and even
zooming in a moderate, reasonable amount (enough to still see several
communities at once) only reached ~3px, well short of legible.

This test reproduces the essential shape of the bug -- many nodes spread
across a graph-space extent much larger than the viewport, forcing a small
fit-zoom -- with a small, deterministic synthetic graph (preset positions,
no Louvain/fcose randomness) instead of the full DB/algorithm pipeline, so
it stays fast and hermetic. It drives the REAL, unmodified graph.js /
graph_page.html in an offscreen QWebEngineView and uses the REAL
InfluenceGraphDataMixin.get_label_font_size/get_label_width to build each
node's style data -- only the graph data itself (positions, node count) is
synthetic.
"""

import json
import os
import time
from pathlib import Path

import pytest

from src.influences.influence_graph_data import InfluenceGraphDataMixin

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = REPO_ROOT / "src" / "influences" / "web"

# A moderate zoom-in from the natural full-graph fit -- not zoomed to a
# single node -- representing "zoomed in a reasonable amount, enough to
# still see a cross-section spanning multiple communities."
CROSS_SECTION_ZOOM_MULT = 3.0
MIN_READABLE_FONT_PX = 8.0


def _synthetic_elements(grid=10, spacing=500):
    """A 10x10 grid of nodes (100 total) across 10 fake communities, spread
    5000x5000 graph units apart -- large enough relative to a normal
    viewport to force the same "small fit-zoom" condition the real ~600
    node global graph produces, without needing real DB data or a slow
    randomized fcose layout."""
    elements = []
    for community in range(grid):
        elements.append({"data": {"id": f"c{community}", "label": f"Community {community}"}})
    idx = 0
    for row in range(grid):
        for col in range(grid):
            community = idx % grid
            size = 60  # mid-range get_node_size() output
            name = f"Artist {idx}"
            elements.append(
                {
                    "data": {
                        "id": str(idx),
                        "label": name,
                        "parent": f"c{community}",
                        "width": size,
                        "height": size * 0.5,
                        "labelWidth": InfluenceGraphDataMixin.get_label_width(size, name),
                        "fontSize": InfluenceGraphDataMixin.get_label_font_size(size),
                        "color": "#111111",
                        "gradientColors": "#ffffff #cccccc",
                        "borderColor": "#000000",
                    },
                    "position": {"x": col * spacing, "y": row * spacing},
                }
            )
            idx += 1
    return elements


_STYLESHEET = [
    {
        "selector": "node[parent]",
        "style": {
            "shape": "round-rectangle",
            "width": "data(width)",
            "height": "data(height)",
            "label": "data(label)",
            "font-size": "data(fontSize)",
            "font-family": "Helvetica Neue, Helvetica, Arial, sans-serif",
            "text-valign": "center",
            "text-halign": "center",
            "text-wrap": "ellipsis",
            "text-max-width": "data(labelWidth)",
        },
    },
    {"selector": "node:parent", "style": {"background-opacity": 0, "events": "no"}},
]
_LAYOUT = {"name": "preset", "fit": True, "padding": 40, "animate": False}


@pytest.fixture
def harness_page(qapp, tmp_path):
    os.environ.setdefault(
        "QTWEBENGINE_CHROMIUM_FLAGS",
        "--disable-gpu --disable-software-rasterizer --disable-gpu-compositing",
    )
    from PySide6.QtCore import QEventLoop, QTimer, QUrl
    from PySide6.QtWebEngineWidgets import QWebEngineView

    web_uri = WEB_DIR.as_uri()
    harness_html = tmp_path / "harness.html"
    harness_html.write_text(
        f"""<!doctype html>
<html><head><meta charset="utf-8">
<style>
  html, body {{ margin: 0; padding: 0; width: 100%; height: 100%; overflow: hidden; }}
  #cy {{ width: 100%; height: 100%; display: block; }}
</style>
</head><body>
<div id="cy"></div>
<div id="node-tooltip" style="display:none"></div>
<script src="{web_uri}/vendor/cytoscape.min.js"></script>
<script src="{web_uri}/vendor/layout-base.js"></script>
<script src="{web_uri}/vendor/cose-base.js"></script>
<script src="{web_uri}/vendor/cytoscape-fcose.js"></script>
<script>cytoscape.use(cytoscapeFcose);</script>
<script>
  // Test-only instrumentation: capture the Cytoscape instance the real,
  // unmodified graph.js creates (it keeps `cy` as a closure-private
  // variable). Does not alter graph.js's behavior.
  (function () {{
    const orig = window.cytoscape;
    function wrapped(...args) {{
      const inst = orig.apply(this, args);
      window.__cyHarness = inst;
      return inst;
    }}
    window.cytoscape = wrapped;
  }})();
</script>
<script src="{web_uri}/graph.js"></script>
</body></html>"""
    )

    web = QWebEngineView()
    web.resize(1200, 800)
    web.show()

    load_loop = QEventLoop()
    web.loadFinished.connect(lambda ok: load_loop.quit())
    web.load(QUrl.fromLocalFile(str(harness_html)))
    QTimer.singleShot(10000, load_loop.quit)
    load_loop.exec()

    # loadFinished fires once the HTML/scripts have executed, but the
    # offscreen page hasn't necessarily computed CSS layout for #cy yet --
    # constructing the Cytoscape instance before that happens reads a
    # 0-sized (or stale) container and produces a degenerate fit (observed:
    # cy.fit() silently settling on exactly zoom=1 instead of a real
    # computed value). Give the renderer a moment to lay out before
    # anything calls loadGraph().
    settle_deadline = time.time() + 0.5
    while time.time() < settle_deadline:
        qapp.processEvents()
        time.sleep(0.02)

    yield web


def _eval_js(qapp, web, code, timeout_ms=5000):
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    box = {}

    def cb(value):
        box["value"] = value
        loop.quit()

    web.page().runJavaScript(code, cb)
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    return box.get("value")


def test_global_graph_labels_readable_at_moderate_zoom(qapp, harness_page):
    web = harness_page
    elements = _synthetic_elements()

    load_code = (
        f"loadGraph({json.dumps(elements)}, {json.dumps(_STYLESHEET)}, "
        f"{json.dumps(_LAYOUT)}, {json.dumps('#000000')})"
    )
    _eval_js(qapp, web, load_code)

    deadline = time.time() + 3.0
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.02)

    measure_code = f"""
    (function () {{
      const cy = window.__cyHarness;
      if (!cy) return JSON.stringify({{error: "no cy instance"}});
      const baseZoom = cy.zoom();
      const bb = cy.nodes('[parent]').boundingBox();
      const center = {{x: (bb.x1 + bb.x2) / 2, y: (bb.y1 + bb.y2) / 2}};
      cy.zoom({{level: baseZoom * {CROSS_SECTION_ZOOM_MULT}, position: center}});
      let sum = 0, n = 0;
      cy.nodes('[parent]').forEach(function (node) {{
        sum += node.data('fontSize') * cy.zoom();
        n++;
      }});
      return JSON.stringify({{avgFontPx: n ? sum / n : 0, zoom: cy.zoom(), baseZoom: baseZoom}});
    }})();
    """
    raw = _eval_js(qapp, web, measure_code, timeout_ms=10000)
    assert raw is not None, "measurement script timed out"
    result = json.loads(raw)
    assert "error" not in result, result.get("error")

    assert result["avgFontPx"] >= MIN_READABLE_FONT_PX, (
        f"Average label font at a moderate {CROSS_SECTION_ZOOM_MULT}x zoom-in from the "
        f"full-graph fit (base zoom={result['baseZoom']:.4f}) was "
        f"{result['avgFontPx']:.2f}px -- below the {MIN_READABLE_FONT_PX}px readability "
        f"floor. Labels should be legible at a moderate zoom that still shows a "
        f"multi-community cross-section, not only when zoomed in far enough to see "
        f"just part of one community."
    )
