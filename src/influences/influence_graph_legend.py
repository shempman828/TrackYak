"""
influence_graph_legend.py

Community-color mapping, persisted cluster naming, and legend panel
wiring for InfluenceGraphView.
"""

import json

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDialog

from src.core.config_setup import app_config
from src.influences import community_identity
from src.influences.cluster_name_dialog import ClusterNamesDialog
from src.influences.community_palette import generate_community_palette


class InfluenceGraphLegendMixin:
    """
    Expects the host class to provide: self._legend, self.legend_enabled,
    self.node_mass, self.node_names, self.community_id, self.community_names,
    self.community_levels, self.active_level, self.community_names_by_level,
    self.height(), self._run_js(), and to be a QWidget subclass.
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
        """Re-attach persisted cluster names after a Louvain recompute, for
        every eligible dendrogram level (not just the active one, so the
        rename dialog's level toggle has every level's names ready without
        triggering a recompute).

        Communities are matched to persisted names by membership-set
        overlap (Jaccard), not by index -- see community_identity.py.
        """
        legacy_raw = app_config.config.get("influences", "cluster_names", fallback="")
        if legacy_raw:
            try:
                legacy_names = json.loads(legacy_raw)
            except json.JSONDecodeError:
                legacy_names = {}
            if legacy_names:
                community_identity.migrate_legacy_anchor_names(
                    legacy_names, self.community_levels
                )
            app_config.config.remove_option("influences", "cluster_names")
            app_config.save()

        self.community_names_by_level = {}
        for level, community_id in enumerate(self.community_levels):
            members_by_community = {}
            for node_id, community_index in community_id.items():
                members_by_community.setdefault(community_index, set()).add(node_id)
            self.community_names_by_level[level] = (
                community_identity.match_and_resolve_names(level, members_by_community)
            )
        self.community_names = self.community_names_by_level.get(self.active_level, {})

    def rename_communities(self, new_names_by_level):
        """Rename communities across one or more levels at once, persisting
        each against its current membership snapshot (see
        ClusterNamesDialog, opened from the legend panel's "Rename…"
        button)."""
        for level, new_names in new_names_by_level.items():
            community_id = self.community_levels[level]
            members_by_community = {}
            for node_id, community_index in community_id.items():
                members_by_community.setdefault(community_index, set()).add(node_id)

            level_names = self.community_names_by_level.setdefault(level, {})
            for community_index, name in new_names.items():
                name = name.strip()
                old_name = level_names.get(community_index)
                community_identity.persist_rename(
                    level,
                    name,
                    old_name,
                    members_by_community.get(community_index, set()),
                )
                if name:
                    level_names[community_index] = name
                else:
                    level_names.pop(community_index, None)
                if level == self.active_level:
                    self._run_js(
                        f"setLabel({json.dumps(f'c{community_index}')}, {json.dumps(name)})"
                    )

        self.community_names = self.community_names_by_level.get(self.active_level, {})
        self._update_legend()

    def _on_level_changed(self, level):
        """Switch which dendrogram level drives graph coloring and the
        legend, without recomputing Louvain."""
        if level == self.active_level or level >= len(self.community_levels):
            return
        self.active_level = level
        self.community_id = self.community_levels[level]
        self.community_names = self.community_names_by_level.get(level, {})
        self._update_legend()
        self._push_graph()

    def _open_rename_all_dialog(self):
        rows_by_level = {}
        for level in range(len(self.community_levels)):
            rows = self._legend_rows_for_level(level)
            if rows:
                rows_by_level[level] = rows
        if not rows_by_level:
            return
        dialog = ClusterNamesDialog(rows_by_level, self.active_level, parent=self)
        if dialog.exec() == QDialog.Accepted:
            self.rename_communities(dialog.cluster_names())

    def set_legend_visible(self, visible: bool):
        """Show/hide the cluster legend overlay, persisting the preference."""
        self.legend_enabled = visible
        app_config.set_influence_legend_visible(visible)
        app_config.save()
        self._update_legend()

    def _representative_artists(self, members_by_community, community_index, limit=5):
        """Return up to `limit` artist names for a community, ranked by
        degree-based mass, so the user has enough signal to tell clusters
        apart when renaming them.
        """
        members = sorted(
            members_by_community.get(community_index, []),
            key=lambda node_id: self.node_mass.get(node_id, 0),
            reverse=True,
        )
        return [self.node_names.get(node_id, "") for node_id in members[:limit]]

    def _legend_rows_for_level(self, level):
        community_id = self.community_levels[level]
        names = self.community_names_by_level.get(level, {})
        counts = {}
        members_by_community = {}
        for node_id, community_index in community_id.items():
            counts[community_index] = counts.get(community_index, 0) + 1
            members_by_community.setdefault(community_index, []).append(node_id)
        sized = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
        return [
            (
                community_index,
                self.get_community_color(community_index),
                count,
                names.get(community_index, ""),
                self._representative_artists(members_by_community, community_index),
            )
            for community_index, count in sized
        ]

    def _legend_rows(self):
        if self.active_level is None:
            return []
        return self._legend_rows_for_level(self.active_level)

    def _update_legend(self):
        rows = self._legend_rows()
        if self.legend_enabled:
            self._legend.set_level_count(len(self.community_levels), self.active_level)
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
