"""
album_musicbrainz_review_dialog.py

Review/checkbox dialog shown after a MusicBrainz canonical release has been
fetched in full (see musicbrainz_client.fetch_release_detail). Modeled on
src/artist/artist_enrichment_review_dialog.py's checkbox-per-item /
apply-on-accept pattern, scaled up to per-track groups: album credits, track
credits, and recording locations are relational data that needs
find-or-create/dedup against existing local data before it's safe to write,
so the user confirms what gets imported rather than it happening silently.

Nothing is written until the user actually clicks OK: fill-blank scalars
(track number/side/barcode, disc assignment) for auto-matched tracks are
computed at construction time but only applied in `_on_accept()`, right
alongside the judgment-call items (manual track matches, credits, recording
locations, album aliases) -- so hitting Cancel here leaves the database
untouched, not just the checkbox-gated portion of it.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHeaderView,
    QLabel,
    QMessageBox,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from src.common.entity_completer_edit import find_or_create_by_name
from src.common.match_confidence import confidence_color, confidence_label
from src.core.logger_config import logger
from src.musicbrainz.musicbrainz_client import MBAlias, MBReleaseDetail, MBReleaseTrack
from src.place.place_association_types import (
    fetch_association_types,
    find_or_create_association_type,
)
from src.place.place_chain_resolver import resolve_place_chain

_SKIP = "— Skip —"

# Floor for the position-match title sanity check in _match_tracks: a real
# discrepancy (different song at the same position -- wrong regional
# tracklist, mis-tagged file, etc.) should be well below this, while
# formatting noise (remaster tags, case, punctuation) should clear it.
_POSITION_MATCH_TITLE_FLOOR = 0.4


def _format_mb_track_label(mbt: MBReleaseTrack) -> str:
    side = f"Side {mbt.side}, " if mbt.side else ""
    return f"Disc {mbt.disc_number}, {side}Track {mbt.track_number or '?'}: {mbt.title}"


class AlbumMusicBrainzReviewDialog(QDialog):
    def __init__(
        self,
        controller,
        album,
        detail: MBReleaseDetail,
        aliases: list[MBAlias],
        parent=None,
    ):
        super().__init__(parent)
        self.controller = controller
        self.album = album
        self.detail = detail
        self.aliases = aliases
        self.has_content = False

        self._disc_by_number: dict[int, Any] = {}
        self._matched: dict[int, Any] = {}  # id(MBReleaseTrack) -> Track
        self._manual_combos: list[tuple[QComboBox, MBReleaseTrack]] = []
        self._alias_checks: list[tuple[QCheckBox, MBAlias]] = []
        self._album_credit_checks: list[tuple[QCheckBox, Any]] = []
        self._credit_checks: list[tuple[QCheckBox, MBReleaseTrack, Any]] = []
        self._location_checks: list[tuple[QCheckBox, str, list[MBReleaseTrack]]] = []
        self._failed_writes: list[str] = []

        self.setWindowTitle("Review MusicBrainz Album Details")
        self.setMinimumSize(560, 540)

        self._match_tracks()
        self._match_summary = (
            f"Matched {len(self._matched)} of {len(detail.tracks)} track(s)."
        )
        self._build_ui()

    # ------------------------------------------------------------------
    # Track matching (no DB writes) -- exact (disc_number, absolute
    # position) match confirmed by title agreement, then a title-similarity
    # guess for a single-medium release, then a manual QComboBox for
    # anything left.
    # ------------------------------------------------------------------

    @staticmethod
    def _title_similarity(a: str | None, b: str | None) -> float:
        if not a or not b:
            return 0.0
        return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()

    @classmethod
    def _position_match_confirmed(cls, mbt: MBReleaseTrack, local) -> bool:
        """Position alone (disc_number, absolute_position) isn't sufficient
        confirmation that two tracks are the same recording -- it's just
        where each happens to sit in its own list. If the local track has
        no name yet there's nothing to contradict the position, so trust
        it. Otherwise require the titles to reasonably agree; a real title
        conflict at the same position is a discrepancy that needs manual
        review, not something to silently paper over."""
        if not local.track_name:
            return True
        return (
            cls._title_similarity(mbt.title, local.track_name)
            >= _POSITION_MATCH_TITLE_FLOOR
        )

    @staticmethod
    def _discs_compatible(local_disc_num: int | None, mb_disc_num: int | None) -> bool:
        """A missing disc number on either side isn't a real conflict --
        only two present, differing disc numbers mean the track actually
        belongs to a different medium."""
        return (
            local_disc_num is None
            or mb_disc_num is None
            or local_disc_num == mb_disc_num
        )

    def _match_tracks(self):
        local_tracks = list(self.album.tracks or [])
        local_by_number: dict[int, list] = {}
        for t in local_tracks:
            if t.track_number is not None:
                local_by_number.setdefault(t.track_number, []).append(t)

        used_ids = set()
        matched: dict[int, Any] = {}
        for mbt in self.detail.tracks:
            # Local track numbering is absolute (sequential across a
            # vinyl disc's sides), while mbt.track_number is deliberately
            # side-relative (e.g. "B1" -> 1) for display/apply purposes.
            # Matching must use the medium-sequential absolute_position
            # instead, or vinyl releases would never auto-match and would
            # always fall through to manual review.
            key_number = mbt.absolute_position
            if key_number is None:
                key_number = mbt.track_number
            candidates = local_by_number.get(key_number, [])
            # Prefer a candidate whose disc number actually agrees with
            # mbt's over one that's merely compatible (e.g. untagged, disc
            # number None) so a real same-position match on another disc
            # doesn't get shadowed by an untagged track.
            candidates = sorted(
                candidates,
                key=lambda c: (c.disc.disc_number if c.disc else None)
                != mbt.disc_number,
            )
            local = None
            for cand in candidates:
                if cand.track_id in used_ids:
                    continue
                cand_disc_num = cand.disc.disc_number if cand.disc else None
                if not self._discs_compatible(cand_disc_num, mbt.disc_number):
                    continue
                local = cand
                break
            if local is not None and self._position_match_confirmed(mbt, local):
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
        guesses: dict[int, Any] = {}
        guess_scores: dict[int, float] = {}
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
                    abs(
                        (mbt.absolute_position or mbt.track_number or 0)
                        - (local.track_number or 0)
                    ),
                    mbt,
                    local,
                )
                for mbt in remaining_mb
                for local in remaining_local
            ]
            candidates.sort(key=lambda c: (-c[0], c[1]))
            used_mb_ids = set()
            used_local_ids = set()
            for score, _dist, mbt, local in candidates:
                if id(mbt) in used_mb_ids or local.track_id in used_local_ids:
                    continue
                guesses[id(mbt)] = local
                guess_scores[id(mbt)] = score
                used_mb_ids.add(id(mbt))
                used_local_ids.add(local.track_id)

        self._matched = matched
        self._remaining_mb = remaining_mb
        self._remaining_local_options = remaining_local
        self._guesses = guesses
        self._guess_scores = guess_scores

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
    # Fill-blank scalars for auto-matched tracks (disc assignment, track
    # number/side/barcode). Computed against self._matched at construction
    # time, but the actual writes are deferred to _on_accept() below --
    # nothing here touches the database until the user clicks OK.
    # ------------------------------------------------------------------

    def apply_immediate_scalars(self):
        self._plan_discs()
        updates = []
        for mbt in self.detail.tracks:
            track = self._matched.get(id(mbt))
            if track is None:
                continue
            update = self._track_scalar_update(track, mbt)
            if update is not None:
                updates.append(update)
        self._batch_update_tracks(updates)
        self._report_failed_writes()

    def _plan_discs(self):
        existing_by_number = {d.disc_number: d for d in (self.album.discs or [])}
        rows_by_number: dict[int, dict] = {}
        for mbt in self.detail.tracks:
            num = mbt.disc_number
            if num in self._disc_by_number or num in rows_by_number:
                continue
            disc = existing_by_number.get(num)
            if disc is not None:
                self._disc_by_number[num] = disc
                continue
            rows_by_number[num] = {
                "album_id": self.album.album_id,
                "disc_number": num,
                "disc_title": mbt.disc_title,
            }

        if not rows_by_number:
            return
        discs, failed = self.controller.add.add_entities_with_fallback(
            "Disc", list(rows_by_number.values())
        )
        for disc in discs:
            self._disc_by_number[disc.disc_number] = disc
        for row in failed:
            self._failed_writes.append(f"Disc {row['disc_number']}")

    def _track_scalar_update(
        self, track, mbt: MBReleaseTrack, *, force: bool = False
    ) -> dict | None:
        """force=True is for manual matches: the user just told us this MB
        track *is* this local track even though their track_number/side
        disagreed (that disagreement is exactly why it needed a manual
        match), so MB's values should win rather than only filling blanks.

        Returns the update dict (including track_id) for
        update_entities_bulk_with_fallback, or None if there's nothing to
        change -- this is pure computation, no DB write, so callers can
        gather every track's update and apply them all in a single batch."""
        kwargs = {}
        if mbt.track_number is not None and (force or track.track_number is None):
            kwargs["track_number"] = mbt.track_number
        if mbt.side and (force or not track.side):
            kwargs["side"] = mbt.side
        # Manual match means the user just confirmed these are the same
        # recording, whatever their titles look like -- no fuzzy gate
        # needed, that confirmation is the gate. Take MB's title as the
        # corrected one whenever it isn't already what's stored locally,
        # e.g. a locally-truncated "Good Riddance" becoming "Good
        # Riddance (Time of Your Life)".
        if (
            force
            and mbt.title
            and mbt.title.strip().lower() != (track.track_name or "").strip().lower()
        ):
            kwargs["track_name"] = mbt.title
        if not track.track_barcode and self.detail.barcode:
            kwargs["track_barcode"] = self.detail.barcode
        if track.disc_id is None:
            disc = self._disc_by_number.get(mbt.disc_number)
            if disc is not None:
                kwargs["disc_id"] = disc.disc_id
        if not kwargs:
            return None
        kwargs["track_id"] = track.track_id
        return kwargs

    def _batch_update_tracks(self, updates: list[dict]):
        if not updates:
            return
        _, failed = self.controller.update.update_entities_bulk_with_fallback(
            "Track", updates
        )
        for row in failed:
            self._failed_writes.append(f"Track {row['track_id']} scalar update")

    def _report_failed_writes(self):
        if not self._failed_writes:
            return
        QMessageBox.warning(
            self,
            "Some MusicBrainz Data Could Not Be Saved",
            "The following item(s) could not be saved and were skipped:\n\n"
            + "\n".join(f"• {item}" for item in self._failed_writes),
        )
        self._failed_writes.clear()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _usable_aliases(self) -> list[MBAlias]:
        existing = {
            (a.alias_name or "").strip().lower()
            for a in (self.album.album_aliases or [])
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

            table = QTableWidget(len(self._remaining_mb), 3)
            table.setHorizontalHeaderLabels(
                ["MusicBrainz Track", "Match to Local Track", "Confidence"]
            )
            table.verticalHeader().setVisible(False)
            table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            table.setSelectionMode(QAbstractItemView.NoSelection)
            header = table.horizontalHeader()
            header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.Stretch)
            header.setSectionResizeMode(2, QHeaderView.ResizeToContents)

            for row, mbt in enumerate(self._remaining_mb):
                mb_item = QTableWidgetItem(_format_mb_track_label(mbt))
                table.setItem(row, 0, mb_item)

                combo = QComboBox()
                combo.addItem(_SKIP, None)
                for local in self._remaining_local_options:
                    local_side = f", side {local.side}" if local.side else ""
                    combo.addItem(
                        f"{local.track_name} (currently track {local.track_number or '?'}{local_side})",
                        local,
                    )
                guess = self._guesses.get(id(mbt))
                score = self._guess_scores.get(id(mbt))
                if guess is not None:
                    idx = combo.findData(guess)
                    if idx >= 0:
                        combo.setCurrentIndex(idx)
                table.setCellWidget(row, 1, combo)
                self._manual_combos.append((combo, mbt))

                if guess is not None:
                    conf_text = confidence_label(score)
                    conf_color = confidence_color(score)
                else:
                    conf_text = "No suggestion"
                    conf_color = confidence_color(0.0)
                conf_item = QTableWidgetItem(conf_text)
                conf_item.setForeground(conf_color)
                conf_item.setTextAlignment(Qt.AlignCenter)
                table.setItem(row, 2, conf_item)

            table.resizeRowsToContents()
            table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            row_height_total = sum(
                table.rowHeight(r) for r in range(table.rowCount())
            )
            table.setFixedHeight(
                header.height() + row_height_total + 2 * table.frameWidth()
            )
            box_layout.addWidget(table)
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

        if self.detail.credits:
            self.has_content = True
            box = QGroupBox("Album Credits")
            box_layout = QVBoxLayout(box)
            for credit in self.detail.credits:
                cb = QCheckBox(f"{credit.artist_name} — {credit.role_name}")
                cb.setChecked(True)
                box_layout.addWidget(cb)
                self._album_credit_checks.append((cb, credit))
            inner_layout.addWidget(box)

        for mbt in self.detail.tracks:
            if not mbt.credits:
                continue
            self.has_content = True
            box = QGroupBox(_format_mb_track_label(mbt))
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
                chain_label = ", ".join(
                    node["name"] for node in chain if node.get("name")
                )
                track_titles = ", ".join(mbt.title for mbt in tracks_here)
                cb = QCheckBox(f"{chain_label}\n  → {track_titles}")
                cb.setChecked(True)
                box_layout.addWidget(cb)
                self._location_checks.append((cb, place_mbid, tracks_here))
            inner_layout.addWidget(box)

        if not self.has_content:
            inner_layout.addWidget(QLabel("Nothing further to review."))

        inner_layout.addStretch(1)
        scroll.setWidget(inner)
        layout.addWidget(scroll, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._autosize(inner)

    def _autosize(self, content: QWidget) -> None:
        """Grow the dialog to fit the review content, within reason.

        QScrollArea's own sizeHint() doesn't grow with its child widget, so
        left alone the dialog stays pinned to setMinimumSize() no matter how
        wide the credit/alias/track rows actually are. Match the sizing
        convention used by publisher_fuzzy_match._autosize: measure the
        scrolled widget directly, clamp to a fraction of the screen so a
        long review can't blow past it, with the configured minimum as a
        floor.
        """
        hint = content.sizeHint()

        screen = self.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry()

        # Extra room for the scrollbar plus the summary label/buttons/margins
        # that sit outside the scrolled content itself.
        width = hint.width() + 60
        height = hint.height() + 120

        width = max(self.minimumWidth(), min(width, int(available.width() * 0.9)))
        height = max(self.minimumHeight(), min(height, int(available.height() * 0.9)))

        self.resize(width, height)

    # ------------------------------------------------------------------
    # Apply -- everything the user gets to review, plus the fill-blank
    # scalars for auto-matched tracks, all happen here, only once OK has
    # actually been clicked. Nothing above this point writes to the
    # database.
    # ------------------------------------------------------------------

    def _on_accept(self):
        # Auto-matched tracks' disc assignment + scalar fill first, since
        # manual matches below need self._disc_by_number (populated here
        # via _plan_discs) for their own disc assignment.
        self.apply_immediate_scalars()

        # Manual matches decided just now still need their own scalar fill
        # + disc assignment, since apply_immediate_scalars() only covered
        # tracks that were already auto-matched at construction time.
        manual_updates = []
        for combo, mbt in self._manual_combos:
            track = combo.currentData()
            if track is not None:
                update = self._track_scalar_update(track, mbt, force=True)
                if update is not None:
                    manual_updates.append(update)
        self._batch_update_tracks(manual_updates)

        alias_rows = [
            {
                "album_id": self.album.album_id,
                "alias_name": alias.name,
                "alias_type": alias.type or None,
            }
            for cb, alias in self._alias_checks
            if cb.isChecked()
        ]
        _, failed = self.controller.add.add_entities_with_fallback(
            "AlbumAlias", alias_rows
        )
        for row in failed:
            self._failed_writes.append(f"Album alias '{row['alias_name']}'")

        known_roles = self.controller.get.get_all_entities("Role") or []

        album_credit_rows = []
        next_sort_order_by_role: dict[int, int] = {}
        planned_album_pairs: set[tuple[int, int]] = set()
        for cb, credit in self._album_credit_checks:
            if not cb.isChecked():
                continue
            row = self._plan_album_credit(
                credit, known_roles, next_sort_order_by_role, planned_album_pairs
            )
            if row is not None:
                album_credit_rows.append(row)
                planned_album_pairs.add((row["artist_id"], row["role_id"]))
        _, failed = self.controller.add.add_entities_with_fallback(
            "AlbumRoleAssociation", album_credit_rows
        )
        for row in failed:
            self._failed_writes.append(
                f"Album credit (artist {row['artist_id']}, role {row['role_id']})"
            )

        track_credit_rows = []
        planned_by_track: dict[int, set] = {}
        for cb, mbt, credit in self._credit_checks:
            if not cb.isChecked():
                continue
            track = self._resolved_track(mbt)
            if track is None:
                continue
            row = self._plan_track_credit(track, credit, known_roles, planned_by_track)
            if row is not None:
                track_credit_rows.append(row)
        _, failed = self.controller.add.add_entities_with_fallback(
            "TrackArtistRole", track_credit_rows
        )
        for row in failed:
            self._failed_writes.append(
                f"Track credit (track {row['track_id']}, artist {row['artist_id']})"
            )

        known_place_types = fetch_association_types(self.controller)
        place_cache: dict[str, Any] = {}
        place_rows = []
        for cb, place_mbid, mb_tracks in self._location_checks:
            if not cb.isChecked():
                continue
            place_rows.extend(
                self._plan_location_rows(
                    place_mbid, mb_tracks, place_cache, known_place_types
                )
            )
        _, failed = self.controller.add.add_entities_with_fallback(
            "PlaceAssociation", place_rows
        )
        for row in failed:
            self._failed_writes.append(
                f"Recording location for track {row['entity_id']}"
            )

        self._report_failed_writes()
        self.accept()

    def _resolve_artist(self, credit) -> Any | None:
        # MBID, then as-credited name (+ known local alias), then the
        # artist's canonical MB name -- distinct from the as-credited name
        # when a release prints a variant credit (e.g. "H. Arlen" for
        # canonical "Harold Arlen") -- before giving up and creating a new
        # Artist. Checking the canonical name here is what lets a credit
        # under a variant spelling resolve to the artist's existing local
        # row instead of spawning a duplicate that then needs manual
        # fuzzy-match dedupe.
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

        if credit.canonical_name and credit.canonical_name != credit.artist_name:
            artist = self.controller.get.resolve_entity_or_alias(
                "Artist", "artist_name", credit.canonical_name
            )
            if artist is not None:
                if not artist.MBID and credit.artist_mbid:
                    self.controller.update.update_entity(
                        "Artist", artist.artist_id, MBID=credit.artist_mbid
                    )
                self.controller.add.add_entity(
                    "ArtistAlias",
                    artist_id=artist.artist_id,
                    alias_name=credit.artist_name,
                    alias_type=None,
                )
                return artist

        return self.controller.add.add_entity(
            "Artist", artist_name=credit.artist_name, MBID=credit.artist_mbid
        )

    def _plan_track_credit(
        self,
        track,
        credit,
        known_roles: list[Any],
        planned_by_track: dict[int, set],
    ) -> dict | None:
        """Resolve (and, if genuinely new, create) the artist/role for this
        credit, but leave the actual TrackArtistRole junction row for the
        caller to batch-insert alongside every other checked credit."""
        try:
            artist = self._resolve_artist(credit)
            if artist is None:
                return None
            role = find_or_create_by_name(
                self.controller, "Role", "role_name", credit.role_name, known_roles
            )
            if role is None:
                return None
            if role not in known_roles:
                known_roles.append(role)

            already = any(
                ar.artist_id == artist.artist_id and ar.role_id == role.role_id
                for ar in (track.artist_roles or [])
            )
            planned = planned_by_track.setdefault(track.track_id, set())
            if already or (artist.artist_id, role.role_id) in planned:
                return None
            planned.add((artist.artist_id, role.role_id))

            return {
                "track_id": track.track_id,
                "artist_id": artist.artist_id,
                "role_id": role.role_id,
            }
        except SQLAlchemyError as e:
            logger.warning(
                f"Could not import credit '{credit.artist_name} — "
                f"{credit.role_name}' on track {track.track_id}: {e}"
            )
            return None

    def _plan_album_credit(
        self,
        credit,
        known_roles: list[Any],
        next_sort_order_by_role: dict[int, int],
        planned_pairs: set[tuple[int, int]],
    ) -> dict | None:
        """Same idea as `_plan_track_credit`, for album-level credits. The
        sort_order that AlbumRoleAssociation rows for the same role share is
        normally derived by re-reading `self.album.album_roles` after each
        commit; since nothing is committed until the whole batch goes in,
        `next_sort_order_by_role` tracks the same running count in memory."""
        try:
            artist = self._resolve_artist(credit)
            if artist is None:
                return None
            role = find_or_create_by_name(
                self.controller, "Role", "role_name", credit.role_name, known_roles
            )
            if role is None:
                return None
            if role not in known_roles:
                known_roles.append(role)

            siblings = [
                ra
                for ra in (self.album.album_roles or [])
                if ra.role_id == role.role_id
            ]
            if any(ra.artist_id == artist.artist_id for ra in siblings):
                return None
            if (artist.artist_id, role.role_id) in planned_pairs:
                return None
            if role.role_id not in next_sort_order_by_role:
                next_sort_order_by_role[role.role_id] = (
                    max(ra.sort_order for ra in siblings) + 1 if siblings else 0
                )
            sort_order = next_sort_order_by_role[role.role_id]
            next_sort_order_by_role[role.role_id] += 1

            return {
                "album_id": self.album.album_id,
                "artist_id": artist.artist_id,
                "role_id": role.role_id,
                "sort_order": sort_order,
            }
        except SQLAlchemyError as e:
            logger.warning(
                f"Could not import album credit '{credit.artist_name} — "
                f"{credit.role_name}': {e}"
            )
            return None

    def _plan_location_rows(
        self,
        place_mbid: str,
        mb_tracks: list[MBReleaseTrack],
        place_cache: dict[str, Any],
        known_place_types: list[Any],
    ) -> list[dict]:
        chain = self.detail.place_chains.get(place_mbid)
        if not chain:
            return []
        try:
            studio = resolve_place_chain(self.controller, chain, place_cache)
            if studio is None:
                return []
            assoc_type = find_or_create_association_type(
                self.controller, "Recording Location", known_place_types
            )
            rows = []
            for mbt in mb_tracks:
                track = self._resolved_track(mbt)
                if track is None:
                    continue
                already = any(
                    p.place_id == studio.place_id for p in (track.places or [])
                )
                if already:
                    continue
                rows.append(
                    {
                        "entity_id": track.track_id,
                        "entity_type": "Track",
                        "place_id": studio.place_id,
                        "association_type_id": (
                            assoc_type.association_type_id if assoc_type else None
                        ),
                    }
                )
            return rows
        except SQLAlchemyError as e:
            logger.warning(f"Could not import recording location: {e}")
            return []
