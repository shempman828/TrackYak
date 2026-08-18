# ---------------------------------------------------------------------------
# GenresTab
# ---------------------------------------------------------------------------
from __future__ import annotations

from src.common.entity_completer_edit import find_or_create_by_name
from src.common.tag_association_tab import _BaseTrackAssociationTab


class GenresTab(_BaseTrackAssociationTab):
    model_name = "Genre"
    id_field = "genre_id"
    name_field = "genre_name"
    assoc_model = "TrackGenre"
    placeholder_text = "Search genres…"
    add_button_text = "Add Genre"

    def _load_track_items(self, track):
        # Genre exposes a direct ORM relationship, faster than the
        # get_entity_links + get_entity_object N+1 pattern the base uses.
        return [(g.genre_id, g.genre_name) for g in track.genres]

    def _invalidate_cache(self):
        # track.genres is a secondary= relationship -- once loaded, it's
        # cached on the Track instance and the session's expire_on_commit=
        # False means a plain commit() won't invalidate it, so a genre
        # added/removed here would keep showing the pre-edit list until
        # expired explicitly.
        session = self.controller.get.session
        for track in self.tracks:
            session.expire(track, ["genres"])

    def _find_or_create(self, name: str):
        return find_or_create_by_name(
            self.controller,
            self.model_name,
            self.name_field,
            name,
            self._known_entities(),
            extra_lookup=lambda: self.controller.get.resolve_entity_or_alias(
                "Genre", "genre_name", name
            ),
        )
