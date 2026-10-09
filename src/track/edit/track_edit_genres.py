"""GenresTab: tag the edited track(s) with genres."""

from __future__ import annotations

from src.common.widgets.entity_completer_edit import find_or_create_by_name
from src.track.edit.tag_association_tab import _BaseTrackAssociationTab


class GenresTab(_BaseTrackAssociationTab):
    """Add and remove genres; a split-alias name expands into its target genres."""

    model_name = "Genre"
    id_field = "genre_id"
    name_field = "genre_name"
    assoc_model = "TrackGenre"
    relationship = "genres"
    placeholder_text = "Search genres…"
    add_button_text = "Add Genre"

    def _find_or_create(self, name: str):
        """Return the split-alias targets of `name`, else the genre by name or alias, else a new genre."""
        # See docs/specs/split_and_merge_aliases.md.
        split_targets = self.controller.get.resolve_split_alias("Genre", name)
        if split_targets:
            return split_targets

        return find_or_create_by_name(
            self.controller, self.model_name, self.name_field, name, self._known_entities(), extra_lookup=lambda: self.controller.get.resolve_entity_or_alias("Genre", "genre_name", name)
        )
