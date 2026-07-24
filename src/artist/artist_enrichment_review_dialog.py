"""
artist_enrichment_review_dialog.py

Review/checkbox dialog shown after a MusicBrainz artist match is picked,
for relational enrichment data (aliases, birthplace/deathplace, band
membership) that can't just be dropped into a blank form field the way
scalar enrichment is -- each of these is a DB row that needs find-or-skip
dedup against existing data before it's safe to write, so the user
confirms what gets imported rather than it happening silently.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.core.logger_config import logger
from src.musicbrainz.musicbrainz_client import MBAlias, MBArtistRelations, MBGroupRelation
from src.place.place_association_types import (
    fetch_association_types,
    find_or_create_association_type,
)


class ArtistEnrichmentReviewDialog(QDialog):
    """
    Shows what MusicBrainz found beyond the scalar fields already handled
    by tab.set_if_empty() -- aliases, birthplace/deathplace, and
    band-membership relations to OTHER artists that already exist locally
    -- and lets the user pick which to import. Applies the checked items
    directly on accept.

    `has_content` is False when nothing survived filtering (e.g. every
    alias is a duplicate, no local artist matches a band relation) -- the
    caller should skip exec() in that case rather than show an empty dialog.
    """

    def __init__(self, controller, artist, relations: MBArtistRelations, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.artist = artist
        self.relations = relations
        self._alias_checks: List[Tuple[QCheckBox, MBAlias]] = []
        self._birthplace: Optional[Tuple[QCheckBox, str]] = None
        self._deathplace: Optional[Tuple[QCheckBox, str]] = None
        self._member_checks: List[Tuple[QCheckBox, MBGroupRelation, object]] = []
        self.has_content = False

        self.setWindowTitle("Review MusicBrainz Details")
        self.setMinimumSize(480, 420)
        self._build_ui()

    # ------------------------------------------------------------------
    # Filtering / matching against existing local data (no DB writes)
    # ------------------------------------------------------------------

    def _usable_aliases(self) -> List[MBAlias]:
        own_name = (self.artist.artist_name or "").strip().lower()
        existing = {a.strip().lower() for a in (self.artist.aliases_list or [])}
        out = []
        for alias in self.relations.aliases:
            key = alias.name.strip().lower()
            if not key or key == own_name or key in existing:
                continue
            conflict = self.controller.get.resolve_entity_or_alias(
                "Artist", "artist_name", alias.name
            )
            if conflict and conflict.artist_id != self.artist.artist_id:
                continue  # belongs to a different artist -- skip silently
            out.append(alias)
        return out

    def _existing_place_association_types(self) -> set:
        try:
            assocs = (
                self.controller.get.get_all_entities(
                    "PlaceAssociation",
                    entity_id=self.artist.artist_id,
                    entity_type="Artist",
                )
                or []
            )
        except Exception:
            assocs = []
        return {a.association_type.type_name for a in assocs if a.association_type}

    def _matched_group_relations(self) -> List[Tuple[MBGroupRelation, object]]:
        """Resolve each relation's counterpart to a local Artist by MBID,
        falling back to an exact (case-insensitive) name match. Relations
        with no local counterpart are dropped -- this flow never creates
        the other side of a membership, only links to what's already
        there."""
        existing_pairs = set()
        for m in self.artist.group_memberships:
            existing_pairs.add((m.group_id, m.member_id))
        for m in self.artist.member_memberships:
            existing_pairs.add((m.group_id, m.member_id))

        out = []
        for rel in self.relations.group_relations:
            counterpart = self.controller.get.get_entity_object("Artist", MBID=rel.mbid)
            if counterpart is None:
                counterpart = self.controller.get.get_entity_object(
                    "Artist", artist_name=rel.name
                )
            if counterpart is None or counterpart.artist_id == self.artist.artist_id:
                continue
            pair = (
                (self.artist.artist_id, counterpart.artist_id)
                if self.relations.is_group
                else (counterpart.artist_id, self.artist.artist_id)
            )
            if pair in existing_pairs:
                continue
            out.append((rel, counterpart))
        return out

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "MusicBrainz found the following additional details. "
                "Check the ones you'd like to import."
            )
        )

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)

        aliases = self._usable_aliases()
        if aliases:
            self.has_content = True
            box = QGroupBox("Aliases")
            box_layout = QVBoxLayout(box)
            for alias in aliases:
                label = f"{alias.name} ({alias.type})" if alias.type else alias.name
                cb = QCheckBox(label)
                cb.setChecked(True)
                box_layout.addWidget(cb)
                self._alias_checks.append((cb, alias))
            inner_layout.addWidget(box)

        existing_types = self._existing_place_association_types()
        place_rows = []
        if self.relations.birthplace and "Birthplace" not in existing_types:
            place_rows.append(("Birthplace", self.relations.birthplace))
        if self.relations.deathplace and "Deathplace" not in existing_types:
            place_rows.append(("Deathplace", self.relations.deathplace))
        if place_rows:
            self.has_content = True
            box = QGroupBox("Places")
            box_layout = QVBoxLayout(box)
            for assoc_type, place_name in place_rows:
                cb = QCheckBox(f"{assoc_type}: {place_name}")
                cb.setChecked(True)
                box_layout.addWidget(cb)
                if assoc_type == "Birthplace":
                    self._birthplace = (cb, place_name)
                else:
                    self._deathplace = (cb, place_name)
            inner_layout.addWidget(box)

        matched = self._matched_group_relations()
        if matched:
            self.has_content = True
            title = "Members" if self.relations.is_group else "Group Membership"
            box = QGroupBox(title)
            box_layout = QVBoxLayout(box)
            for rel, counterpart in matched:
                span = ""
                if rel.begin_year or rel.end_year:
                    end = "present" if rel.is_current else (rel.end_year or "?")
                    span = f" ({rel.begin_year or '?'}–{end})"
                role_bit = f" — {rel.role}" if rel.role else ""
                cb = QCheckBox(f"{counterpart.artist_name}{role_bit}{span}")
                cb.setChecked(True)
                box_layout.addWidget(cb)
                self._member_checks.append((cb, rel, counterpart))
            inner_layout.addWidget(box)

        if not self.has_content:
            inner_layout.addWidget(QLabel("Nothing new to import."))

        inner_layout.addStretch()
        scroll.setWidget(inner)
        layout.addWidget(scroll, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    def _on_accept(self):
        for cb, alias in self._alias_checks:
            if not cb.isChecked():
                continue
            try:
                self.controller.add.add_entity(
                    "ArtistAlias",
                    artist_id=self.artist.artist_id,
                    alias_name=alias.name,
                    alias_type=alias.type or None,
                )
            except Exception as e:
                logger.warning(f"Could not import MB alias '{alias.name}': {e}")

        for entry, assoc_type in ((self._birthplace, "Birthplace"), (self._deathplace, "Deathplace")):
            if entry is None:
                continue
            cb, place_name = entry
            if cb.isChecked():
                self._apply_place(place_name, assoc_type)

        for cb, rel, counterpart in self._member_checks:
            if not cb.isChecked():
                continue
            try:
                if self.relations.is_group:
                    group_id, member_id = self.artist.artist_id, counterpart.artist_id
                else:
                    group_id, member_id = counterpart.artist_id, self.artist.artist_id
                self.controller.add.add_entity(
                    "GroupMembership",
                    group_id=group_id,
                    member_id=member_id,
                    role=rel.role,
                    active_start_year=rel.begin_year,
                    active_end_year=rel.end_year,
                    is_current=1 if rel.is_current else 0,
                )
            except Exception as e:
                logger.warning(
                    f"Could not import group membership with '{counterpart.artist_name}': {e}"
                )

        self.accept()

    def _apply_place(self, place_name: str, assoc_type: str):
        try:
            place = self.controller.get.get_entity_object("Place", place_name=place_name)
            if place is None:
                place = self.controller.add.add_entity("Place", place_name=place_name)
            known_types = fetch_association_types(self.controller)
            assoc_type_obj = find_or_create_association_type(
                self.controller, assoc_type, known_types
            )
            self.controller.add.add_entity(
                "PlaceAssociation",
                entity_id=self.artist.artist_id,
                entity_type="Artist",
                place_id=place.place_id,
                association_type_id=assoc_type_obj.association_type_id
                if assoc_type_obj
                else None,
            )
        except Exception as e:
            logger.warning(f"Could not import {assoc_type} '{place_name}': {e}")
