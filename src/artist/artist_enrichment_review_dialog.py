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

from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from src.artist.artist_fuzzy_match import (
    FuzzyMatchDialog,
    _blocking_keys,
    artist_name_similarity,
)
from src.core.logger_config import logger
from src.musicbrainz.musicbrainz_client import MBAlias, MBArtistRelations, MBGroupRelation
from src.place.place_association_types import (
    fetch_association_types,
    find_or_create_association_type,
)
from src.place.place_chain_resolver import resolve_place_chain

# Same threshold used by the "Find Duplicates" artist merge tool
# (artist_view.py) -- keeps the two fuzzy-match features in agreement about
# what counts as a likely duplicate.
ALIAS_MERGE_THRESHOLD = 0.85


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
        self._birthplace: Optional[Tuple[QCheckBox, str, List[Dict[str, Any]]]] = None
        self._birth_assoc_type = "Origin" if relations.is_group else "Birthplace"
        self._deathplace: Optional[Tuple[QCheckBox, str, List[Dict[str, Any]]]] = None
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

    def _find_alias_merge_candidates(
        self, aliases: List[MBAlias]
    ) -> Dict[str, Tuple[object, int]]:
        """Fuzzy-match each usable alias against every *other* local artist
        name, to catch near-duplicates (punctuation, spelling, spacing)
        that `_usable_aliases()`'s exact-match check lets through -- e.g. an
        alias of "Guns N´ Roses" wouldn't exact-match a local "Guns N Roses"
        but is almost certainly the same artist. Uses the same blocking
        strategy as ArtistFuzzyMatchWorker (`_blocking_keys`) so this stays
        cheap even with tens of thousands of local artists.

        Returns alias.name -> (matching Artist, score_pct), one best match
        per alias, for any alias that scored above ALIAS_MERGE_THRESHOLD.
        """
        if not aliases:
            return {}
        try:
            all_artists = self.controller.get.get_all_entities("Artist") or []
        except SQLAlchemyError as e:
            logger.warning(f"Could not load artists for alias merge check: {e}")
            return {}

        blocks = defaultdict(list)
        for artist in all_artists:
            if artist.artist_id == self.artist.artist_id:
                continue
            for key in _blocking_keys(artist.artist_name):
                blocks[key].append(artist)

        candidates: Dict[str, Tuple[object, int]] = {}
        for alias in aliases:
            candidate_ids_seen = set()
            best_artist, best_ratio = None, 0.0
            for key in _blocking_keys(alias.name):
                for other in blocks.get(key, []):
                    if other.artist_id in candidate_ids_seen:
                        continue
                    candidate_ids_seen.add(other.artist_id)
                    ratio = artist_name_similarity(alias.name, other.artist_name)
                    if ratio >= ALIAS_MERGE_THRESHOLD and ratio > best_ratio:
                        best_artist, best_ratio = other, ratio
            if best_artist is not None:
                candidates[alias.name] = (best_artist, round(best_ratio * 100))
        return candidates

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
        except SQLAlchemyError as e:
            logger.warning(f"Could not load existing place associations: {e}")
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
            merge_candidates = self._find_alias_merge_candidates(aliases)
            box = QGroupBox("Aliases")
            box_layout = QVBoxLayout(box)
            for alias in aliases:
                label = f"{alias.name} ({alias.type})" if alias.type else alias.name
                cb = QCheckBox(label)
                cb.setChecked(True)
                box_layout.addWidget(cb)
                self._alias_checks.append((cb, alias))

                candidate = merge_candidates.get(alias.name)
                if candidate is not None:
                    match_artist, score = candidate
                    warning = QLabel(
                        f"⚠ {score}% similar to existing artist "
                        f"'{match_artist.artist_name}' -- possible duplicate. "
                        "Consider merging instead of adding this alias."
                    )
                    warning.setWordWrap(True)
                    warning.setStyleSheet("color: #b06a00; margin-left: 20px;")
                    box_layout.addWidget(warning)

                    merge_btn = QPushButton(
                        f"Review possible merge with '{match_artist.artist_name}'…"
                    )
                    merge_btn.setStyleSheet("margin-left: 20px;")
                    merge_btn.clicked.connect(
                        lambda _checked=False, box=cb, a=match_artist, s=score: (
                            self._review_alias_merge(box, a, s)
                        )
                    )
                    box_layout.addWidget(merge_btn)
            inner_layout.addWidget(box)

        existing_types = self._existing_place_association_types()
        place_rows = []
        if self.relations.birthplace and self._birth_assoc_type not in existing_types:
            place_rows.append(
                (self._birth_assoc_type, self.relations.birthplace, self.relations.birthplace_chain)
            )
        if self.relations.deathplace and "Deathplace" not in existing_types:
            place_rows.append(
                ("Deathplace", self.relations.deathplace, self.relations.deathplace_chain)
            )
        if place_rows:
            self.has_content = True
            box = QGroupBox("Places")
            box_layout = QVBoxLayout(box)
            for assoc_type, place_name, chain in place_rows:
                cb = QCheckBox(f"{assoc_type}: {place_name}")
                cb.setChecked(True)
                box_layout.addWidget(cb)
                if assoc_type == self._birth_assoc_type:
                    self._birthplace = (cb, place_name, chain)
                else:
                    self._deathplace = (cb, place_name, chain)
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

    def _review_alias_merge(self, cb: QCheckBox, candidate_artist, score: int) -> None:
        """Open the existing single-pair merge dialog (same one "Find
        Duplicates" uses) scoped to just this alias's candidate, instead of
        re-implementing merge logic here.
        """
        dialog = FuzzyMatchDialog(
            [(self.artist, candidate_artist, score)], self.controller, self
        )
        if dialog.exec_() != QDialog.Accepted:
            return

        if (
            self.controller.get.get_entity_object(
                "Artist", artist_id=self.artist.artist_id
            )
            is None
        ):
            # self.artist was the one merged away -- nothing left to enrich.
            self.reject()
            return

        # The alias name is now covered by the merge's own alias
        # preservation, so don't also add it as a separate alias here.
        cb.setChecked(False)
        cb.setEnabled(False)

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
            except SQLAlchemyError as e:
                logger.warning(f"Could not import MB alias '{alias.name}': {e}")

        place_cache: Dict[str, Any] = {}
        for entry, assoc_type in ((self._birthplace, self._birth_assoc_type), (self._deathplace, "Deathplace")):
            if entry is None:
                continue
            cb, place_name, chain = entry
            if cb.isChecked():
                self._apply_place(place_name, chain, assoc_type, place_cache)

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
            except SQLAlchemyError as e:
                logger.warning(
                    f"Could not import group membership with '{counterpart.artist_name}': {e}"
                )

        self.accept()

    def _apply_place(
        self,
        place_name: str,
        chain: List[Dict[str, Any]],
        assoc_type: str,
        cache: Dict[str, Any],
    ):
        try:
            if chain:
                # Walk the full MusicBrainz area chain (city -> county ->
                # state -> country, etc) so the place is linked with its
                # complete parent hierarchy, not just a bare name.
                place = resolve_place_chain(self.controller, chain, cache)
            else:
                # MusicBrainz didn't return an area MBID (rare) -- fall
                # back to the old flat by-name find-or-create.
                place = self.controller.get.get_entity_object(
                    "Place", place_name=place_name
                )
                if place is None:
                    place = self.controller.add.add_entity(
                        "Place", place_name=place_name
                    )
            if place is None:
                return
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
        except SQLAlchemyError as e:
            logger.warning(f"Could not import {assoc_type} '{place_name}': {e}")
