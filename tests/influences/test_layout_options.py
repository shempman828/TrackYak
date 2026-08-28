"""Regression test: the influence graph must snap straight to its computed
fcose layout, with no animation that mimics "watching it calculate."

There is no ongoing computation to visualize once the worker thread has
produced the graph and fcose has solved the node positions, so the layout
options must not re-introduce an animated settle (previously a deliberately
stretched 2200ms tween from a random scatter, standing in for an even
older per-tick Python physics simulation).
"""

from src.influences.influence_graph_render import InfluenceGraphRenderMixin


def test_layout_options_do_not_animate():
    opts = InfluenceGraphRenderMixin._build_layout_options(
        InfluenceGraphRenderMixin.__new__(InfluenceGraphRenderMixin)
    )

    assert opts["animate"] is False
    assert "animationDuration" not in opts
    assert "animationEasing" not in opts
