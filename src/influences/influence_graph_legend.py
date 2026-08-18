"""
influence_graph_legend.py

Community-color mapping, persisted cluster naming, and legend panel
wiring for InfluenceGraphView.
"""

import json

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDialog

from src.core.config_setup import app_config
from src.influences.cluster_name_dialog import ClusterNamesDialog
from src.influences.community_palette import generate_community_palette


class InfluenceGraphLegendMixin:
    """
    Expects the host class to provide: self._legend, self.legend_enabled,
    self.node_mass, self.node_names, self.community_id, self.community_names,
    self._community_anchor, self.height(), self._run_js(), and to be a
    QWidget subclass.
    """

    # Generated, not hand-picked: supports up to 50 communities with maximal
    # visual distinction (golden-angle hue spread — see
    # generate_community_palette). Communities beyond 50 wrap around and
    # reuse these hues at alternating lighter/darker strengths.
    _COMMUNITY_PALETTE = generate_community_palette(50)

    def get_community_color(self, community_index):
        """Map a Louvain community id to a stable, visually distinct color.

        Indexes into the generated 50-color palette. Communities beyond the
        base 50 reuse it at alternating lighter/darker strengths rather than
        falling back to arbitrary hues.
        """
        palette = self._COMMUNITY_PALETTE
        lap, idx = divmod(community_index, len(palette))
        color = QColor(palette[idx])
        if lap == 1:
            color = color.lighter(122)
        elif lap >= 2:
            factor = 115 + 12 * (lap - 1)
            color = color.darker(factor) if lap % 2 == 0 else color.lighter(factor)
        return color

    def _resolve_community_names(self):
        """Re-attach persisted cluster names after a Louvain recompute.

        Raw community indices are reassigned every time Louvain runs, so a
        name can't be pinned to an index. Instead each community is pinned to
        its highest-degree "anchor" artist (stable enough across re-runs on
        the same data), and names are persisted keyed by that artist's id.
        """
        anchors = {}
        for node_id, community_index in self.community_id.items():
            mass = self.node_mass.get(node_id, 0)
            current = anchors.get(community_index)
            if current is None or mass > self.node_mass.get(current, 0):
                anchors[community_index] = node_id
        self._community_anchor = anchors

        saved_names = app_config.get_influence_cluster_names()
        self.community_names = {
            community_index: saved_names[str(anchor_id)]
            for community_index, anchor_id in anchors.items()
            if str(anchor_id) in saved_names
        }

    def rename_communities(self, new_names_by_index):
        """Rename any number of clusters at once and persist each against
        its anchor artist (see ClusterNamesDialog, opened from the legend
        panel's "Rename…" button)."""
        saved_names = app_config.get_influence_cluster_names()
        for community_index, name in new_names_by_index.items():
            anchor_id = self._community_anchor.get(community_index)
            if anchor_id is None:
                continue
            name = name.strip()
            if name:
                self.community_names[community_index] = name
                saved_names[str(anchor_id)] = name
            else:
                self.community_names.pop(community_index, None)
                saved_names.pop(str(anchor_id), None)
            self._run_js(
                f"setLabel({json.dumps(f'c{community_index}')}, {json.dumps(name)})"
            )
        app_config.set_influence_cluster_names(saved_names)
        app_config.save()
        self._update_legend()

    def _open_rename_all_dialog(self):
        rows = self._legend_rows()
        if not rows:
            return
        dialog = ClusterNamesDialog(rows, parent=self)
        if dialog.exec() == QDialog.Accepted:
            self.rename_communities(dialog.cluster_names())

    def set_legend_visible(self, visible: bool):
        """Show/hide the cluster legend overlay, persisting the preference."""
        self.legend_enabled = visible
        app_config.set_influence_legend_visible(visible)
        app_config.save()
        self._update_legend()

    def _representative_artists(self, members_by_community, community_index, limit=5):
        """Return up to `limit` artist names for a community, ranked by mass
        (the same "most prominent node" weighting used for the anchor pick
        in _resolve_community_names) so the user has enough signal to tell
        clusters apart when renaming them.
        """
        members = sorted(
            members_by_community.get(community_index, []),
            key=lambda node_id: self.node_mass.get(node_id, 0),
            reverse=True,
        )
        return [self.node_names.get(node_id, "") for node_id in members[:limit]]

    def _legend_rows(self):
        counts = {}
        members_by_community = {}
        for node_id, community_index in self.community_id.items():
            counts[community_index] = counts.get(community_index, 0) + 1
            members_by_community.setdefault(community_index, []).append(node_id)
        sized = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
        return [
            (
                community_index,
                self.get_community_color(community_index),
                count,
                self.community_names.get(community_index, ""),
                self._representative_artists(members_by_community, community_index),
            )
            for community_index, count in sized
        ]

    def _update_legend(self):
        rows = self._legend_rows()
        if self.legend_enabled:
            self._legend.set_communities(rows)
        else:
            self._legend.hide()
        self._reposition_legend()

    def _reposition_legend(self):
        if self._legend.has_custom_position():
            self._legend.clamp_to_parent()
        else:
            margin = 14
            self._legend.move(margin, self.height() - self._legend.height() - margin)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_legend()
