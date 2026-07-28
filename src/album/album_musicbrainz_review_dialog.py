"""
album_musicbrainz_review_dialog.py

Review/checkbox dialog shown after a MusicBrainz canonical release has been
fetched in full (see musicbrainz_client.fetch_release_detail). Modeled on
src/artist/artist_enrichment_review_dialog.py's checkbox-per-item /
apply-on-accept pattern, scaled up to per-track groups: track credits and
recording locations are relational data that needs find-or-create/dedup
against existing local data before it's safe to write, so the user confirms
what gets imported rather than it happening silently.

Unambiguous fill-blank scalars (track number/side/barcode, disc assignment)
for tracks that auto-matched cleanly are applied immediately on construction,
the same "no confirmation needed" rule the rest of the app already uses for
scalar enrichment -- only the judgment-call items (manual track matches,
credits, recording locations, album aliases) go behind the checkbox review.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.common.entity_completer_edit import find_or_create_by_name
from src.core.logger_config import logger
from src.musicbrainz.musicbrainz_client import MBAlias, MBReleaseDetail, MBReleaseTrack
from src.place.place_association_types import (
    fetch_association_types,
    find_or_create_association_type,
)

_SKIP = "— Skip —"


class AlbumMusicBrainzReviewDialog(QDialog):
    def __init__(
        self,
        controller,
        album,
        detail: MBReleaseDetail,
        aliases: List[MBAlias],
        parent=None,
    ):
        super().__init__(parent)
        self.controller = controller
        self.album = album
        self.detail = detail
        self.aliases = aliases
        self.has_content = False

        self._disc_by_number: Dict[int, Any] = {}
        self._matched: Dict[int, Any] = {}  # id(MBReleaseTrack) -> Track
        self._manual_combos: List[Tuple[QComboBox, MBReleaseTrack]] = []
        self._alias_checks: List[Tuple[QCheckBox, MBAlias]] = []
        self._credit_checks: List[Tuple[QCheckBox, MBReleaseTrack, Any]] = []
        self._location_checks: List[Tuple[QCheckBox, str, List[MBReleaseTrack]]] = []

        self.setWindowTitle("Review MusicBrainz Album Details")
        self.setMinimumSize(560, 540)

        self._match_tracks()
        self._match_summary = (
            f"Matched {len(self._matched)} of {len(detail.tracks)} track(s)."
        )
        self._build_ui()

    # ------------------------------------------------------------------
    # Track matching (no DB writes) -- exact (disc_number, track_number)
    # match, then a title-similarity guess for a single-medium release,
    # then a manual QComboBox for anything left.
    # ------------------------------------------------------------------

    @staticmethod
    def _title_similarity(a: Optional[str], b: Optional[str]) -> float:
        if not a or not b:
            return 0.0
        return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()

    def _match_tracks(self):
        local_tracks = list(self.album.tracks or [])
        local_by_key = {}
        for t in local_tracks:
            disc_num = t.disc.disc_number if t.disc else None
            if disc_num is not None and t.track_number is not None:
                local_by_key[(disc_num, t.track_number)] = t

        used_ids = set()
        matched: Dict[int, Any] = {}
        for mbt in self.detail.tracks:
            local = local_by_key.get((mbt.disc_number, mbt.track_number))
            if local is not None and local.track_id not in used_ids:
                matched[id(mbt)] = local
                used_ids.add(local.track_id)

        remaining_mb = [mbt for mbt in self.detail.tracks if id(mbt) not in matched]
        remaining_local = sorted(
            (t for t in local_tracks if t.track_id not in used_ids),
            key=lambda t: (t.track_number is None, t.track_number or 0, t.track_id),
        )

        # Only safe to guess pairing automatically for a single medium -- a
        # multi-disc release with untagged local tracks has no reliable
        # signal for which disc a given local track belongs to.
        single_medium = len({mbt.disc_number for mbt in self.detail.tracks}) <= 1
        guesses: Dict[int, Any] = {}
        if single_medium and remaining_mb and remaining_local:
            # Greedily pair by title similarity first -- highest-scoring
            # pairs win -- so a missing/extra track in the middle of the
            # list doesn't shift every later track's guess out of position
            # the way a plain positional zip() would. Position is only a
            # tiebreaker, for when titles give no signal (e.g. blank
            # local track names).
            candidates = [
                (
                    self._title_similarity(mbt.title, local.track_name),
                    abs((mbt.track_number or 0) - (local.track_number or 0)),
                    mbt,
                    local,
                )
                for mbt in remaining_mb
                for local in remaining_local
            ]
            candidates.sort(key=lambda c: (-c[0], c[1]))
            used_mb_ids = set()
            used_local_ids = set()
            for _score, _dist, mbt, local in candidates:
                if id(mbt) in used_mb_ids or local.track_id in used_local_ids:
                    continue
                guesses[id(mbt)] = local
                used_mb_ids.add(id(mbt))
                used_local_ids.add(local.track_id)

        self._matched = matched
        self._remaining_mb = remaining_mb
        self._remaining_local_options = remaining_local
        self._guesses = guesses

    def _resolved_track(self, mbt: MBReleaseTrack):
        """The local Track this MB track ultimately maps to -- auto-matched,
        or whatever the user picked in its manual-match combo, if any."""
        track = self._matched.get(id(mbt))
        if track is not None:
            return track
        for combo, combo_mbt in self._manual_combos:
            if combo_mbt is mbt:
                data = combo.currentData()
                return data
        return None

    # ------------------------------------------------------------------
    # Immediate apply: unambiguous fill-blank scalars for auto-matched
    # tracks. No confirmation needed -- same rule the rest of the app uses
    # for scalar enrichment -- so this runs right away rather than being
    # gated behind the checkbox review below.
    # ------------------------------------------------------------------

    def apply_immediate_scalars(self):
        self._plan_discs()
        for mbt in self.detail.tracks:
            track = self._matched.get(id(mbt))
            if track is None:
                continue
            self._apply_track_scalars(track, mbt)

    def _plan_discs(self):
        existing_by_number = {d.disc_number: d for d in (self.album.discs or [])}
        for mbt in self.detail.tracks:
            num = mbt.disc_number
            if num in self._disc_by_number:
                continue
            disc = existing_by_number.get(num)
            if disc is None:
                try:
                    disc = self.controller.add.add_entity(
                        "Disc",
                        album_id=self.album.album_id,
                        disc_number=num,
                        disc_title=mbt.disc_title,
                    )
                except Exception as e:
                    logger.warning(f"Could not create Disc {num}: {e}")
                    continue
            self._disc_by_number[num] = disc

    def _apply_track_scalars(self, track, mbt: MBReleaseTrack):
        kwargs = {}
        if track.track_number is None and mbt.track_number is not None:
            kwargs["track_number"] = mbt.track_number
        if not track.side and mbt.side:
            kwargs["side"] = mbt.side
        if not track.track_barcode and self.detail.barcode:
            kwargs["track_barcode"] = self.detail.barcode
        if track.disc_id is None:
            disc = self._disc_by_number.get(mbt.disc_number)
            if disc is not None:
                kwargs["disc_id"] = disc.disc_id
        if kwargs:
            try:
                self.controller.update.update_entity("Track", track.track_id, **kwargs)
            except Exception as e:
                logger.warning(f"Could not update track {track.track_id}: {e}")

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _usable_aliases(self) -> List[MBAlias]:
        existing = {
            (a.alias_name or "").strip().lower() for a in (self.album.album_aliases or [])
        }
        own_name = (self.album.album_name or "").strip().lower()
        out = []
        for alias in self.aliases:
            key = alias.name.strip().lower()
            if not key or key == own_name or key in existing:
                continue
            out.append(alias)
        return out

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(self._match_summary))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)

        if self._remaining_mb:
            self.has_content = True
            box = QGroupBox("Unmatched Tracks — pick a local track or skip")
            box_layout = QVBoxLayout(box)
            for mbt in self._remaining_mb:
                row_label = QLabel(
                    f"Disc {mbt.disc_number}, Track {mbt.track_number or '?'}: {mbt.title}"
                )
                box_layout.addWidget(row_label)
                combo = QComboBox()
                combo.addItem(_SKIP, None)
                for local in self._remaining_local_options:
                    combo.addItem(
                        f"{local.track_name} (currently track {local.track_number or '?'})",
                        local,
                    )
                guess = self._guesses.get(id(mbt))
                if guess is not None:
                    idx = combo.findData(guess)
                    if idx >= 0:
                        combo.setCurrentIndex(idx)
                box_layout.addWidget(combo)
                self._manual_combos.append((combo, mbt))
            inner_layout.addWidget(box)

        aliases = self._usable_aliases()
        if aliases:
            self.has_content = True
            box = QGroupBox("Album Aliases")
            box_layout = QVBoxLayout(box)
            for alias in aliases:
                label = f"{alias.name} ({alias.type})" if alias.type else alias.name
                cb = QCheckBox(label)
                cb.setChecked(True)
                box_layout.addWidget(cb)
                self._alias_checks.append((cb, alias))
            inner_layout.addWidget(box)

        for mbt in self.detail.tracks:
            if not mbt.credits:
                continue
            self.has_content = True
            box = QGroupBox(
                f"Disc {mbt.disc_number}, Track {mbt.track_number or '?'}: {mbt.title}"
            )
            box_layout = QVBoxLayout(box)
            for credit in mbt.credits:
                cb = QCheckBox(f"{credit.artist_name} — {credit.role_name}")
                cb.setChecked(True)
                box_layout.addWidget(cb)
                self._credit_checks.append((cb, mbt, credit))
            inner_layout.addWidget(box)

        if self.detail.place_chains:
            self.has_content = True
            box = QGroupBox("Recording Locations")
            box_layout = QVBoxLayout(box)
            for place_mbid, chain in self.detail.place_chains.items():
                tracks_here = [
                    mbt
                    for mbt in self.detail.tracks
                    if mbt.location_place_mbid == place_mbid
                ]
                if not tracks_here:
                    continue
                chain_label = ", ".join(node["name"] for node in chain if node.get("name"))
                track_titles = ", ".join(mbt.title for mbt in tracks_here)
                cb = QCheckBox(f"{chain_label}\n  → {track_titles}")
                cb.setChecked(True)
                box_layout.addWidget(cb)
                self._location_checks.append((cb, place_mbid, tracks_here))
            inner_layout.addWidget(box)

        if not self.has_content:
            inner_layout.addWidget(QLabel("Nothing further to review."))

        inner_layout.addStretch()
        scroll.setWidget(inner)
        layout.addWidget(scroll, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    # Apply (only the checkbox-gated relational data -- immediate scalars
    # already happened in apply_immediate_scalars())
    # ------------------------------------------------------------------

    def _on_accept(self):
        # Manual matches decided just now still need their own scalar fill
        # + disc assignment, since apply_immediate_scalars() only covered
        # tracks that were already auto-matched at construction time.
        for combo, mbt in self._manual_combos:
            track = combo.currentData()
            if track is not None:
                self._apply_track_scalars(track, mbt)

        for cb, alias in self._alias_checks:
            if not cb.isChecked():
                continue
            try:
                self.controller.add.add_entity(
                    "AlbumAlias",
                    album_id=self.album.album_id,
                    alias_name=alias.name,
                    alias_type=alias.type or None,
                )
            except Exception as e:
                logger.warning(f"Could not import album alias '{alias.name}': {e}")

        known_roles = self.controller.get.get_all_entities("Role") or []
        for cb, mbt, credit in self._credit_checks:
            if not cb.isChecked():
                continue
            track = self._resolved_track(mbt)
            if track is None:
                continue
            self._apply_credit(track, credit, known_roles)

        known_place_types = fetch_association_types(self.controller)
        place_cache: Dict[str, Any] = {}
        for cb, place_mbid, mb_tracks in self._location_checks:
            if not cb.isChecked():
                continue
            self._apply_location(place_mbid, mb_tracks, place_cache, known_place_types)

        self.accept()

    def _resolve_artist(self, credit) -> Optional[Any]:
        if credit.artist_mbid:
            artist = self.controller.get.get_entity_object(
                "Artist", MBID=credit.artist_mbid
            )
            if artist is not None:
                return artist
        artist = self.controller.get.resolve_entity_or_alias(
            "Artist", "artist_name", credit.artist_name
        )
        if artist is not None:
            return artist
        return self.controller.add.add_entity(
            "Artist", artist_name=credit.artist_name, MBID=credit.artist_mbid
        )

    def _apply_credit(self, track, credit, known_roles: List[Any]):
        try:
            artist = self._resolve_artist(credit)
            if artist is None:
                return
            role = find_or_create_by_name(
                self.controller, "Role", "role_name", credit.role_name, known_roles
            )
            if role is None:
                return
            if role not in known_roles:
                known_roles.append(role)

            already = any(
                ar.artist_id == artist.artist_id and ar.role_id == role.role_id
                for ar in (track.artist_roles or [])
            )
            if already:
                return
            self.controller.add.add_entity(
                "TrackArtistRole",
                track_id=track.track_id,
                artist_id=artist.artist_id,
                role_id=role.role_id,
            )
        except Exception as e:
            logger.warning(
                f"Could not import credit '{credit.artist_name} — "
                f"{credit.role_name}' on track {track.track_id}: {e}"
            )

    def _resolve_place_chain(
        self, chain: List[Dict[str, Any]], cache: Dict[str, Any]
    ) -> Optional[Any]:
        """Find-or-create every level of a place chain, outermost first, so
        each level's parent already exists before the next is resolved.
        Matches by MBID globally, then by name scoped to the already-
        resolved parent's children only -- never a bare global name search,
        so e.g. two same-named places under different parents (a "Paris"
        in Tennessee vs. a "Paris" in France) resolve to distinct rows.
        """
        parent = None
        for node in reversed(chain):
            mbid = node["mbid"]
            place = cache.get(mbid)
            if place is None:
                place = self.controller.get.get_entity_object("Place", MBID=mbid)
            if place is None:
                siblings = (
                    self.controller.get.get_all_entities(
                        "Place", parent_id=parent.place_id if parent else None
                    )
                    or []
                )
                name_key = (node.get("name") or "").strip().lower()
                place = next(
                    (
                        p
                        for p in siblings
                        if (p.place_name or "").strip().lower() == name_key
                    ),
                    None,
                )
            if place is None:
                place = self.controller.add.add_entity(
                    "Place",
                    place_name=node.get("name") or "",
                    place_type=node.get("type"),
                    MBID=mbid,
                    parent_id=parent.place_id if parent else None,
                    place_latitude=node.get("latitude"),
                    place_longitude=node.get("longitude"),
                )
            if place is None:
                return None
            cache[mbid] = place
            parent = place
        return parent

    def _apply_location(
        self,
        place_mbid: str,
        mb_tracks: List[MBReleaseTrack],
        place_cache: Dict[str, Any],
        known_place_types: List[Any],
    ):
        chain = self.detail.place_chains.get(place_mbid)
        if not chain:
            return
        try:
            studio = self._resolve_place_chain(chain, place_cache)
            if studio is None:
                return
            assoc_type = find_or_create_association_type(
                self.controller, "Recording Location", known_place_types
            )
            for mbt in mb_tracks:
                track = self._resolved_track(mbt)
                if track is None:
                    continue
                already = any(
                    p.place_id == studio.place_id for p in (track.places or [])
                )
                if already:
                    continue
                self.controller.add.add_entity(
                    "PlaceAssociation",
                    entity_id=track.track_id,
                    entity_type="Track",
                    place_id=studio.place_id,
                    association_type_id=(
                        assoc_type.association_type_id if assoc_type else None
                    ),
                )
        except Exception as e:
            logger.warning(f"Could not import recording location: {e}")
