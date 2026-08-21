"""Regression tests for the influence graph node-label bugs (Influences >
Global Network view): (1) labels unreadably small unless zoomed in very
far, (2) labels wider than their own node, and (3) node size tracking
name length instead of influence.

Bug 1 root cause (see scratch/graph_repro/repro.py for the full empirical
investigation against the real database): Cytoscape draws node labels in
graph space, not screen space, so on-screen label size is
font-size-in-graph-units * current zoom. The global network can be ~600
nodes across ~30 Louvain communities, so cy.fit() settles on a small zoom
(~0.09-0.12 measured against real data) just to fit everyone at once. The
label font-size range (previously 8-13 graph units) was sized as if that
fit-zoom were close to 1, so real labels rendered under 1.5px -- and even
zooming in a moderate, reasonable amount (enough to still see several
communities at once) only reached ~3px, well short of legible. Fixed by
scaling font size up (get_label_font_size's _FONT_SCALE).

Bug 2: scaling font size up alone made every label wider too (more graph
units needed to render the same text at a bigger font), but the node's
own box (get_node_size) wasn't recalibrated to match -- its pre-
_FONT_SCALE dimensions were sized for the old, much smaller font. Node
boxes auto-size to exactly contain their own word-wrapped label
(Cytoscape width/height: 'label' + text-wrap: 'wrap', in
_build_stylesheet) so a label can never overflow its own box; wrapping
only breaks at whitespace, never mid-word, and nothing is elided.

Bug 3: auto-sizing the box to fit *whatever text it's given* means a
low-influence node with a long name renders bigger than a high-influence
node with a short one -- node size now tracks name length more than
influence, defeating get_node_size's whole purpose (confirmed empirically
against the real DB, see scratch/graph_repro/repro.py). Two things fixed
this together: get_node_size's min/max were recalibrated to actually
match the new font scale (the old 25-160 range was narrower than a single
character at the new font size, so *every* box needed to grow just to
hold any text at all -- long names on low-influence nodes grew the most).
And graph.js's fitNodeLabel now tries the real name first, but swaps in a
short initials-style alias ("Christina Aguilera" -> "C. Aguilera") when
the real name would grow the box past a modest allowance over its
influence-based target -- so box size tracks influence for all but
genuinely long names, and nothing is ever elided. The real name is always
available via a hover tooltip (fullLabel data).

All three tests drive the REAL, unmodified graph.js / graph_page.html in
an offscreen QWebEngineView -- the first with a small, deterministic
synthetic graph (preset positions, no Louvain/fcose randomness) built
from real InfluenceGraphDataMixin.get_label_font_size values; the other
two through the real InfluenceGraphRenderMixin pipeline (_build_elements /
_build_stylesheet / _build_layout_options / _push_graph, including the
real fcose layout) against small synthetic graphs.
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

# How far a node's rendered box may exceed its influence-based minimum
# before graph.js's fitNodeLabel swaps in a shorter alias. Mirrors
# graph.js's SIZE_ALLOWANCE.
SIZE_ALLOWANCE = 1.25

# Offscreen QtWebEngine's canvas text measurement isn't perfectly
# reproducible run-to-run (font-substitution/rendering-backend jitter of
# roughly +/-25% has been observed against the exact same input), so
# assertions on absolute rendered size use a looser tolerance than
# SIZE_ALLOWANCE itself -- generous enough to absorb that jitter while
# still catching the actual bug this guards against (an unbounded, several-
# times-larger box for a long name, not a modest few-dozen-unit wobble).
SIZE_TEST_TOLERANCE = 2.0


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
            size = 175  # mid-range get_node_size() output
            name = f"Artist {idx}"
            elements.append(
                {
                    "data": {
                        "id": str(idx),
                        "label": name,
                        "fullLabel": name,
                        "parent": f"c{community}",
                        "minWidth": size,
                        "minHeight": size * 0.5,
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
            "width": "label",
            "height": "label",
            "padding": 10,
            "label": "data(label)",
            "font-size": "data(fontSize)",
            "font-family": "Helvetica Neue, Helvetica, Arial, sans-serif",
            "text-valign": "center",
            "text-halign": "center",
            "text-wrap": "wrap",
            "text-max-width": "data(minWidth)",
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


def _run_render_pipeline(qapp, harness_page, cases):
    """Drives host._push_graph() (the real InfluenceGraphRenderMixin
    pipeline, including the real fcose layout) for {node_id: (name, score)}
    cases, then returns {node_id: {label, fullLabel, minWidth, minHeight,
    boxW, boxH, fullW, fullH}} measured from the real Cytoscape instance
    graph.js creates."""
    from src.influences.influence_graph_legend import InfluenceGraphLegendMixin
    from src.influences.influence_graph_render import InfluenceGraphRenderMixin

    class _Host(InfluenceGraphDataMixin, InfluenceGraphRenderMixin, InfluenceGraphLegendMixin):
        def __init__(self):
            self.node_names = {}
            self.edges = []
            self.node_mass = {}
            self.community_id = {}
            self.community_names = {}
            self.influence_scores = {}
            self._page_ready = False
            self._pending_js = []

    web = harness_page
    host = _Host()
    host._web = web
    host._page_ready = True

    for node_id, (name, score) in cases.items():
        host.node_names[node_id] = name
        host.community_id[node_id] = 0
        host.influence_scores[node_id] = score

    host._push_graph()

    settle_deadline = time.time() + 5.0
    while time.time() < settle_deadline:
        qapp.processEvents()
        time.sleep(0.05)

    measure_code = """
    (function () {
      const cy = window.__cyHarness;
      if (!cy) return JSON.stringify({error: "no cy instance"});
      const out = {};
      cy.nodes('[parent]').forEach(function (node) {
        const boxOnly = node.boundingBox({ includeLabels: false });
        const withLabel = node.boundingBox({ includeLabels: true });
        out[node.id()] = {
          label: node.data('label'),
          fullLabel: node.data('fullLabel'),
          minWidth: node.data('minWidth'),
          minHeight: node.data('minHeight'),
          // boxW/boxH (boundingBox, includeLabels:false) and fullW/fullH
          // (includeLabels:true) both include the node's `padding` style
          // equally, so comparing them against each other (overflow check)
          // is apples-to-apples. coreW/coreH (node.width()/height()) is
          // the shape's own declared size *excluding* padding -- the same
          // units get_node_size's minWidth/minHeight are in -- so THAT is
          // what to compare against minWidth/minHeight, not boxW/boxH
          // (which would be off by ~2x padding).
          boxW: boxOnly.w, boxH: boxOnly.h,
          fullW: withLabel.w, fullH: withLabel.h,
          coreW: node.width(), coreH: node.height(),
        };
      });
      return JSON.stringify(out);
    })();
    """
    raw = _eval_js(qapp, web, measure_code, timeout_ms=10000)
    assert raw is not None, "measurement script timed out"
    result = json.loads(raw)
    assert set(result.keys()) == {str(k) for k in cases}, result.keys()
    return result


def test_global_graph_labels_never_exceed_their_own_box(qapp, harness_page):
    """Regression test for bug 2 (see module docstring): asserts every
    node's rendered box (bounding box excluding the label) exactly
    contains its label (bounding box including the label), with no
    ellipsis, while still honoring get_node_size's influence-based
    minimum -- across a mix of short/long names and low/high influence,
    including a single very long unbreakable "word" that can't be helped
    by wrapping or aliasing."""
    CASES = {
        0: ("The Rolling Stones and Their Many Long-Serving Backing Musicians", 0),
        1: ("U2", 48),
        2: ("Etta James", 20),
        3: ("Supercalifragilisticexpialidocious", 0),
        4: ("Cher", 0),
    }
    result = _run_render_pipeline(qapp, harness_page, CASES)

    for node_id, metrics in result.items():
        name, _score = CASES[int(node_id)]
        assert "…" not in metrics["label"] and "..." not in metrics["label"], (
            f"{name!r} was elided to {metrics['label']!r} -- names should only wrap "
            f"onto more lines or shorten to an alias, never be truncated."
        )
        assert metrics["fullW"] <= metrics["boxW"] + 0.5 and metrics["fullH"] <= metrics["boxH"] + 0.5, (
            f"{name!r}: label footprint {metrics['fullW']:.1f}x{metrics['fullH']:.1f} exceeds "
            f"its own box {metrics['boxW']:.1f}x{metrics['boxH']:.1f} -- the label overflows "
            f"its node instead of the box growing to contain it."
        )
        assert metrics["boxW"] >= metrics["minWidth"] - 0.5 and metrics["boxH"] >= metrics["minHeight"] - 0.5, (
            f"{name!r}: box {metrics['boxW']:.1f}x{metrics['boxH']:.1f} is smaller than its "
            f"influence-based minimum {metrics['minWidth']:.1f}x{metrics['minHeight']:.1f} -- "
            f"node size should still floor at get_node_size()."
        )


def test_global_graph_node_size_tracks_influence_not_name_length(qapp, harness_page):
    """Regression test for bug 3 (see module docstring): "influence graph
    nodes are hard to read because most artist names don't fit their
    node" was fixed by auto-sizing each box to its label -- which then
    let a minor artist's long name inflate their node past a major
    artist's short-named one, breaking the size-encodes-influence visual
    language. Asserts a low-influence node with a realistically long
    (but not absurd) name gets aliased and stays within a modest
    allowance of its influence-based target size, a low-influence node
    with a short name needs no alias at all, and a high-influence node's
    (larger) target size is unaffected by any of this.
    """
    CASES = {
        # Low influence, long real name -> should alias and stay near its
        # (small) influence target instead of ballooning.
        0: ("Christina Aguilera", 0),
        # Low influence, already-short name -> no aliasing needed.
        1: ("Cher", 0),
        # High influence, short name -> large target, unaffected either way.
        2: ("U2", 48),
    }
    result = _run_render_pipeline(qapp, harness_page, CASES)

    long_low = result["0"]
    short_low = result["1"]
    short_high = result["2"]

    assert long_low["label"] != long_low["fullLabel"], (
        f"{long_low['fullLabel']!r} (low influence) was not aliased -- expected a "
        f"shortened display label distinct from the full name."
    )
    assert long_low["coreW"] <= long_low["minWidth"] * SIZE_TEST_TOLERANCE, (
        f"Low-influence {long_low['fullLabel']!r} rendered {long_low['coreW']:.1f} wide against "
        f"an influence-based target of {long_low['minWidth']:.1f} -- its long name is still "
        f"inflating its box well past its influence tier, even after aliasing."
    )

    assert short_low["label"] == short_low["fullLabel"], (
        f"{short_low['fullLabel']!r} was aliased even though it already fits -- aliasing "
        f"should only kick in when the full name doesn't fit its influence-based box."
    )
    assert abs(short_low["coreW"] - short_low["minWidth"]) < 1 and abs(short_low["coreH"] - short_low["minHeight"]) < 1, (
        f"Low-influence, short-named {short_low['fullLabel']!r} should render at essentially "
        f"exactly its influence-based minimum box, not larger."
    )

    # The two low-influence nodes share the same get_node_size() score (0),
    # so their targets match -- the long name shouldn't make its box any
    # bigger than the short name's, once aliased.
    assert long_low["minWidth"] == short_low["minWidth"]
    assert long_low["coreW"] <= short_low["coreW"] * SIZE_TEST_TOLERANCE, (
        f"Low-influence {long_low['fullLabel']!r} ({long_low['coreW']:.1f} wide) is far larger "
        f"than equally-low-influence {short_low['fullLabel']!r} ({short_low['coreW']:.1f} wide) -- "
        f"node size is tracking name length more than influence."
    )

    # High influence should still mean a visibly bigger box than low
    # influence, regardless of name length -- the encoding still works.
    assert short_high["minWidth"] > long_low["minWidth"]
    assert short_high["coreW"] > long_low["coreW"]
