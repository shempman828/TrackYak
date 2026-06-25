# ---------------------------------------------------------------------------
# IdentificationTab — like FieldFormTab("Identification") but adds Wikipedia
# ---------------------------------------------------------------------------
from __future__ import annotations

from src.track_edit_fieldform import FieldFormTab


class IdentificationTab(FieldFormTab):
    def __init__(self, tracks: list, controller, parent=None):
        super().__init__("Identification", tracks, controller, parent)
