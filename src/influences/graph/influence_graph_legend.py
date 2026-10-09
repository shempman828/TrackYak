"""Community colors, persisted cluster names, and legend-panel wiring for InfluenceGraphView."""

import json

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDialog

from src.foundation.config_setup import app_config
from src.influences.graph import community_identity
from src.influences.graph.cluster_name_dialog import ClusterNamesDialog
from src.influences.graph.community_palette import generate_community_palette


class InfluenceGraphLegendMixin:
    """Legend and cluster naming; the host provides the legend panel and the community model."""

    _COMMUNITY_PALETTE = generate_community_palette(50)

    def get_community_color(self, community_index):
        """Return the color for a community index; indexes past the palette reuse it lighter/darker."""
        palette = self._COMMUNITY_PALETTE
        lap, idx = divmod(community_index, len(palette))
        color = QColor(palette[idx])
        if lap == 1:
            color = color.lighter(122)
        elif lap >= 2:
            factor = 115 + 12 * (lap - 1)
            color = color.darker(factor) if lap % 2 == 0 else color.lighter(factor)
        return color

    @staticmethod
    def _members_by_community(community_id):
        """Return {community_index: set(node_ids)} for one level's partition."""
        members = {}
        for node_id, community_index in community_id.items():
            members.setdefault(community_index, set()).add(node_id)
        return members

    def _resolve_community_names(self):
        """Re-attach persisted cluster names to every eligible level after a recompute."""
        legacy_raw = app_config.config.get("influences", "cluster_names", fallback="")
        if legacy_raw:
            try:
                legacy_names = json.loads(legacy_raw)
            except json.JSONDecodeError:
                legacy_names = {}
            if legacy_names:
                community_identity.migrate_legacy_anchor_names(legacy_names, self.community_levels)
            app_config.config.remove_option("influences", "cluster_names")
            app_config.save()

        communities_by_level = {level: self._members_by_community(community_id) for level, community_id in enumerate(self.community_levels)}
        self.community_names_by_level = community_identity.resolve_all_levels(communities_by_level) if communities_by_level else {}
        self.community_names = self.community_names_by_level.get(self.active_level, {})

    def rename_communities(self, new_names_by_level):
        """Persist {level: {community_index: name}} renames (blank clears) and relabel the active level."""
        renames_by_level = {}
        for level, new_names in new_names_by_level.items():
            if level >= len(self.community_levels):
                continue
            members_by_community = self._members_by_community(self.community_levels[level])
            level_names = self.community_names_by_level.setdefault(level, {})
            renames = []
            for community_index, raw_name in new_names.items():
                name = raw_name.strip()
                old_name = level_names.get(community_index)
                if name == (old_name or ""):
                    continue
                renames.append((name, old_name, members_by_community.get(community_index, set())))
                if name:
                    level_names[community_index] = name
                else:
                    level_names.pop(community_index, None)
                if level == self.active_level:
                    self._run_js(f"setLabel({json.dumps(f'c{community_index}')}, {json.dumps(name)})")
            renames_by_level[level] = renames

        community_identity.persist_renames(renames_by_level)
        self.community_names = self.community_names_by_level.get(self.active_level, {})
        self._update_legend()

    def _on_level_changed(self, level):
        """Recolor the graph by another dendrogram level without recomputing Louvain."""
        # The level list is replaced when a running recompute finishes.
        if getattr(self, "_graph_worker", None) is not None:
            return
        if level == self.active_level or level >= len(self.community_levels):
            return
        self.active_level = level
        self.community_id = self.community_levels[level]
        self.community_names = self.community_names_by_level.get(level, {})
        # fcose relayout stalls visibly on a large graph; layoutstop clears the scrim.
        self._run_js("showLoading()")
        self._update_legend()
        self._push_graph()

    def _open_rename_all_dialog(self):
        """Open the rename dialog for every eligible level and apply the result."""
        if getattr(self, "_graph_worker", None) is not None:
            return
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
        """Return up to `limit` member names of a community, most-connected first."""
        members = sorted(members_by_community.get(community_index, []), key=lambda node_id: self.node_mass.get(node_id, 0), reverse=True)
        return [self.node_names.get(node_id, "") for node_id in members[:limit]]

    def _legend_rows_for_level(self, level):
        """Return (index, color, count, name, representatives) rows for a level, largest first."""
        community_id = self.community_levels[level]
        names = self.community_names_by_level.get(level, {})
        counts = {}
        members_by_community = {}
        for node_id, community_index in community_id.items():
            counts[community_index] = counts.get(community_index, 0) + 1
            members_by_community.setdefault(community_index, []).append(node_id)
        sized = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
        return [
            (community_index, self.get_community_color(community_index), count, names.get(community_index, ""), self._representative_artists(members_by_community, community_index))
            for community_index, count in sized
        ]

    def _legend_rows(self):
        """Return legend rows for the active level, or [] before the first compute."""
        if self.active_level is None:
            return []
        return self._legend_rows_for_level(self.active_level)

    def _update_legend(self):
        """Refresh the legend panel's level buttons and rows, or hide it when disabled."""
        rows = self._legend_rows()
        if self.legend_enabled:
            level_sizes = [len(set(community_id.values())) for community_id in self.community_levels]
            self._legend.set_level_count(level_sizes, self.active_level)
            self._legend.set_communities(rows)
        else:
            self._legend.hide()
        self._reposition_legend()

    def _reposition_legend(self):
        """Keep a user-placed legend inside the view, else anchor it bottom-left."""
        if self._legend.has_custom_position():
            self._legend.clamp_to_parent()
        else:
            margin = 14
            self._legend.move(margin, self.height() - self._legend.height() - margin)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_legend()
