"""Tests for the tiered-community legend/rename-dialog UI: level toggling
recolors without recomputing, renames round-trip through
community_identity.json, and the rename dialog's level toggle preserves
in-progress edits across levels.

See docs/specs/tiered_community_naming.md, acceptance criteria 3-4, 8-9.
"""

import configparser
import json

import pytest

from src.influences.cluster_name_dialog import ClusterNamesDialog
from src.influences.influence_graph_legend import InfluenceGraphLegendMixin


class _Host(InfluenceGraphLegendMixin):
    """Minimal stand-in for InfluenceGraphView: real legend-mixin logic,
    stubbed Qt/Cytoscape side effects so tests don't need a real
    QWebEngineView or LegendPanel."""

    def __init__(self, community_levels, active_level=None):
        self.community_levels = community_levels
        self.active_level = active_level
        self.community_id = community_levels[active_level] if active_level is not None else {}
        self.community_names = {}
        self.community_names_by_level = {}
        self.node_mass = {}
        self.node_names = {}
        self.legend_enabled = False
        self.push_graph_calls = 0
        self.js_calls = []

    def _run_js(self, code):
        self.js_calls.append(code)

    def _push_graph(self):
        self.push_graph_calls += 1

    def _update_legend(self):
        pass

    def height(self):
        return 0


@pytest.fixture
def isolated_app_config(monkeypatch, tmp_path):
    """Point community naming's legacy-migration check at a throwaway ini
    instead of the real config.ini, so these tests never touch the user's
    actual config file."""
    import src.influences.influence_graph_legend as legend_module

    fake_config = configparser.ConfigParser()
    monkeypatch.setattr(legend_module.app_config, "config", fake_config)
    monkeypatch.setattr(legend_module.app_config, "save", lambda: None)
    return fake_config


@pytest.fixture
def isolated_identity_path(monkeypatch, tmp_path):
    """Redirect community_identity's default persistence path so tests
    never touch the real community_identity.json."""
    import src.influences.community_identity as identity_module

    path = tmp_path / "community_identity.json"
    monkeypatch.setattr(identity_module, "_default_path", lambda: path)
    return path


def _fine_and_coarse_levels():
    # Level 0 (finest): 2 communities. Level 1 (coarsest): merged into 1
    # -- filtered out by tolerance rules in real use, but fine for testing
    # the toggle mechanics directly against two genuinely different levels.
    level0 = {1: 0, 2: 0, 3: 1, 4: 1}
    level1 = {1: 0, 2: 0, 3: 0, 4: 1}
    return [level0, level1]


def test_level_change_recolors_without_recompute(isolated_app_config, isolated_identity_path):
    levels = _fine_and_coarse_levels()
    host = _Host(levels, active_level=0)

    host._on_level_changed(1)

    assert host.active_level == 1
    assert host.community_id == levels[1]
    assert host.push_graph_calls == 1


def test_legend_rows_before_first_compute_is_empty_not_a_crash(
    isolated_app_config, isolated_identity_path
):
    # active_level is still None here -- e.g. legend toggled before the
    # first background compute finishes.
    host = _Host([], active_level=None)
    assert host._legend_rows() == []


def test_level_change_to_same_level_is_noop(isolated_app_config, isolated_identity_path):
    levels = _fine_and_coarse_levels()
    host = _Host(levels, active_level=0)

    host._on_level_changed(0)

    assert host.push_graph_calls == 0


def test_rename_persists_and_survives_new_session(isolated_app_config, isolated_identity_path):
    levels = _fine_and_coarse_levels()
    host = _Host(levels, active_level=0)
    host._resolve_community_names()

    host.rename_communities({0: {0: "Bebop", 1: "Arena Rock"}})
    assert host.community_names == {0: "Bebop", 1: "Arena Rock"}

    # Simulate a fresh recompute in a new session: same underlying
    # membership, brand-new host/dicts.
    new_levels = [dict(levels[0]), dict(levels[1])]
    new_host = _Host(new_levels, active_level=0)
    new_host._resolve_community_names()

    assert new_host.community_names == {0: "Bebop", 1: "Arena Rock"}


def test_open_rename_dialog_rows_span_every_eligible_level(
    isolated_app_config, isolated_identity_path
):
    levels = _fine_and_coarse_levels()
    host = _Host(levels, active_level=0)
    host._resolve_community_names()

    rows_by_level = {}
    for level in range(len(host.community_levels)):
        rows = host._legend_rows_for_level(level)
        if rows:
            rows_by_level[level] = rows

    assert set(rows_by_level.keys()) == {0, 1}
    assert {row[0] for row in rows_by_level[0]} == {0, 1}
    assert {row[0] for row in rows_by_level[1]} == {0, 1}


def test_dialog_preserves_edits_across_level_toggle(qapp):
    rows_by_level = {
        0: [(0, _fake_color(), 2, "", []), (1, _fake_color(), 2, "", [])],
        1: [(0, _fake_color(), 3, "", []), (1, _fake_color(), 1, "", [])],
    }
    dialog = ClusterNamesDialog(rows_by_level, active_level=0)

    dialog._edits[0][0].setText("Bebop")
    dialog._show_level(1)
    dialog._edits[1][0].setText("Rock")
    dialog._show_level(0)

    assert dialog._edits[0][0].text() == "Bebop"

    names = dialog.cluster_names()
    assert names[0][0] == "Bebop"
    assert names[1][0] == "Rock"


def test_dialog_multi_level_rename_persists_both(
    qapp, isolated_app_config, isolated_identity_path
):
    levels = _fine_and_coarse_levels()
    host = _Host(levels, active_level=0)
    host._resolve_community_names()

    rows_by_level = {}
    for level in range(len(host.community_levels)):
        rows = host._legend_rows_for_level(level)
        if rows:
            rows_by_level[level] = rows

    dialog = ClusterNamesDialog(rows_by_level, host.active_level)
    dialog._edits[0][0].setText("Bebop")
    dialog._show_level(1)
    dialog._edits[1][0].setText("Rock")

    host.rename_communities(dialog.cluster_names())

    data = json.loads(isolated_identity_path.read_text())
    assert data["0"]["Bebop"] == sorted(
        nid for nid, idx in levels[0].items() if idx == 0
    )
    assert data["1"]["Rock"] == sorted(
        nid for nid, idx in levels[1].items() if idx == 0
    )


def _fake_color():
    from PySide6.QtGui import QColor

    return QColor("#336699")
