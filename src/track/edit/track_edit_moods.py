# ---------------------------------------------------------------------------
# MoodsTab
# ---------------------------------------------------------------------------
from __future__ import annotations

from src.common.tag_association_tab import _BaseTrackAssociationTab


class MoodsTab(_BaseTrackAssociationTab):
    model_name = "Mood"
    id_field = "mood_id"
    name_field = "mood_name"
    assoc_model = "MoodTrackAssociation"
    placeholder_text = "Search moods…"
    add_button_text = "Add Mood"
