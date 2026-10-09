"""MoodsTab: tag the edited track(s) with moods."""

from __future__ import annotations

from src.track.edit.tag_association_tab import _BaseTrackAssociationTab


class MoodsTab(_BaseTrackAssociationTab):
    """Add and remove moods."""

    model_name = "Mood"
    id_field = "mood_id"
    name_field = "mood_name"
    assoc_model = "MoodTrackAssociation"
    relationship = "moods"
    placeholder_text = "Search moods…"
    add_button_text = "Add Mood"
