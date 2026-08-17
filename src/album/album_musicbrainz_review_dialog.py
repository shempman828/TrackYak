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

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
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
    QProgressBar,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from src.common.cancellable_worker import CancellableWorker
from src.common.entity_completer_edit import find_or_create_by_name
from src.common.match_confidence import confidence_color, confidence_label
from src.core.logger_config import logger
from src.musicbrainz.musicbrainz_client import (
    MBAlias,
    MBLabelInfo,
    MBReleaseDetail,
    MBReleaseTrack,
)
from src.place.place_association_types import (
    fetch_association_types,
    find_or_create_association_type,
)
from src.place.place_chain_resolver import resolve_place_chain
from src.publisher.publisher_musicbrainz_import import import_album_labels

_SKIP = "— Skip —"

# _match_tracks' auto-match pass scores every (MB track, local track) pair
# it's willing to consider and blind-matches the best one -- no user review
# -- once its combined score clears this floor. Title similarity is the
# dominant signal (see _TITLE_WEIGHT/_POSITION_WEIGHT below); position only
# nudges the score, so this floor is calibrated against title_sim at an
# *exact* position match (score = _TITLE_WEIGHT * title_sim + _POSITION_WEIGHT,
# since exact-position agreement is 1.0) -- i.e. even with perfect position
# agreement, the title still has to clear roughly title_sim >= 0.53 to
# blind-match, and considerably higher the further the position is off.
_TITLE_WEIGHT = 0.75
_POSITION_WEIGHT = 0.25
_AUTO_MATCH_SCORE_FLOOR = 0.65


def _format_mb_track_label(mbt: MBReleaseTrack) -> str:
    side = f"Side {mbt.side}, " if mbt.side else ""
    return f"Disc {mbt.disc_number}, {side}Track {mbt.track_number or '?'}: {mbt.title}"


# ---------------------------------------------------------------------------
# Write-phase helpers -- module-level (not dialog methods) so both the
# has_content=False synchronous path (AlbumMusicBrainzReviewDialog.
# apply_immediate_scalars, called directly on the UI thread when there's
# nothing to review) and _ReviewAcceptWorker (the has_content=True path,
# backgrounded because resolving/writing many credits synchronously froze
# the UI long enough to trigger the OS "not responding" prompt) share one
# implementation. Every function here takes `controller` explicitly rather
# than reading `self.controller`, so it works the same whether it's called
# from the main thread or from _ReviewAcceptWorker's background thread.
# ---------------------------------------------------------------------------


def _plan_discs(controller, album, detail: MBReleaseDetail):
    """Returns (disc_by_number, failed_writes)."""
    existing_by_number = {d.disc_number: d for d in (album.discs or [])}
    disc_by_number: dict[int, Any] = {}
    rows_by_number: dict[int, dict] = {}
    for mbt in detail.tracks:
        num = mbt.disc_number
        if num in disc_by_number or num in rows_by_number:
            continue
        disc = existing_by_number.get(num)
        if disc is not None:
            disc_by_number[num] = disc
            continue
        rows_by_number[num] = {
            "album_id": album.album_id,
            "disc_number": num,
            "disc_title": mbt.disc_title,
        }

    failed_writes: list[str] = []
    if rows_by_number:
        discs, failed = controller.add.add_entities_with_fallback(
            "Disc", list(rows_by_number.values())
        )
        for disc in discs:
            disc_by_number[disc.disc_number] = disc
        for row in failed:
            failed_writes.append(f"Disc {row['disc_number']}")
    return disc_by_number, failed_writes


def _track_scalar_update(
    track, mbt: MBReleaseTrack, disc_by_number: dict, barcode: str | None, *, force: bool = False
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
    # mbt.side present means the local album is being reorganized into
    # vinyl sides -- a locally flat/absolute track_number (e.g. 9) is
    # *expected* to disagree with MB's side-relative one (e.g. B1 -> 1)
    # even on a correct match (matching itself used absolute_position,
    # not track_number, to find this local row). That disagreement
    # isn't a reason for caution the way it is for a same-scheme
    # mismatch, so treat it like a manual match and let MB's numbering
    # win rather than only filling blanks.
    renumbering_by_side = mbt.side is not None
    if mbt.track_number is not None and (
        force or renumbering_by_side or track.track_number is None
    ):
        kwargs["track_number"] = mbt.track_number
    if mbt.side and (force or renumbering_by_side or not track.side):
        kwargs["side"] = mbt.side
    # A manual match means the user just confirmed these are the same
    # recording, whatever their titles look like -- no fuzzy gate
    # needed, that confirmation is the gate. An auto-match already
    # passed _position_match_confirmed's title-similarity floor (or the
    # local track had no name at all), so it's equally trustworthy here.
    # Either way, take MB's title as the corrected one whenever it isn't
    # already what's stored locally -- covers both small discrepancies
    # (e.g. "Layin'" vs "Laying") and a locally-truncated "Good
    # Riddance" becoming "Good Riddance (Time of Your Life)".
    if (
        mbt.title
        and mbt.title.strip().lower() != (track.track_name or "").strip().lower()
    ):
        kwargs["track_name"] = mbt.title
    if not track.track_barcode and barcode:
        kwargs["track_barcode"] = barcode
    if track.disc_id is None:
        disc = disc_by_number.get(mbt.disc_number)
        if disc is not None:
            kwargs["disc_id"] = disc.disc_id
    if not kwargs:
        return None
    kwargs["track_id"] = track.track_id
    return kwargs


def _batch_update_tracks(controller, updates: list[dict]) -> list[str]:
    if not updates:
        return []
    _, failed = controller.update.update_entities_bulk_with_fallback("Track", updates)
    return [f"Track {row['track_id']} scalar update" for row in failed]


def _resolve_artist(controller, credit) -> Any | None:
    # MBID, then as-credited name (+ known local alias), then the
    # artist's canonical MB name -- distinct from the as-credited name
    # when a release prints a variant credit (e.g. "H. Arlen" for
    # canonical "Harold Arlen") -- before giving up and creating a new
    # Artist. Checking the canonical name here is what lets a credit
    # under a variant spelling resolve to the artist's existing local
    # row instead of spawning a duplicate that then needs manual
    # fuzzy-match dedupe.
    if credit.artist_mbid:
        artist = controller.get.get_entity_object("Artist", MBID=credit.artist_mbid)
        if artist is not None:
            return artist

    artist = controller.get.resolve_entity_or_alias(
        "Artist", "artist_name", credit.artist_name
    )
    if artist is not None:
        return artist

    if credit.canonical_name and credit.canonical_name != credit.artist_name:
        artist = controller.get.resolve_entity_or_alias(
            "Artist", "artist_name", credit.canonical_name
        )
        if artist is not None:
            if not artist.MBID and credit.artist_mbid:
                controller.update.update_entity(
                    "Artist", artist.artist_id, MBID=credit.artist_mbid
                )
            controller.add.add_entity(
                "ArtistAlias",
                artist_id=artist.artist_id,
                alias_name=credit.artist_name,
                alias_type=None,
            )
            return artist

    return controller.add.add_entity(
        "Artist", artist_name=credit.artist_name, MBID=credit.artist_mbid
    )


def _plan_track_credit(
    controller,
    track,
    credit,
    known_roles: list[Any],
    planned_by_track: dict[int, set],
) -> dict | None:
    """Resolve (and, if genuinely new, create) the artist/role for this
    credit, but leave the actual TrackArtistRole junction row for the
    caller to batch-insert alongside every other checked credit."""
    try:
        artist = _resolve_artist(controller, credit)
        if artist is None:
            return None
        role = find_or_create_by_name(
            controller, "Role", "role_name", credit.role_name, known_roles
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
    controller,
    album,
    credit,
    known_roles: list[Any],
    next_sort_order_by_role: dict[int, int],
    planned_pairs: set[tuple[int, int]],
) -> dict | None:
    """Same idea as `_plan_track_credit`, for album-level credits. The
    sort_order that AlbumRoleAssociation rows for the same role share is
    normally derived by re-reading `album.album_roles` after each
    commit; since nothing is committed until the whole batch goes in,
    `next_sort_order_by_role` tracks the same running count in memory."""
    try:
        artist = _resolve_artist(controller, credit)
        if artist is None:
            return None
        role = find_or_create_by_name(
            controller, "Role", "role_name", credit.role_name, known_roles
        )
        if role is None:
            return None
        if role not in known_roles:
            known_roles.append(role)

        siblings = [
            ra for ra in (album.album_roles or []) if ra.role_id == role.role_id
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
            "album_id": album.album_id,
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
    controller,
    detail: MBReleaseDetail,
    resolve_track,
    place_mbid: str,
    mb_tracks: list[MBReleaseTrack],
    place_cache: dict[str, Any],
    known_place_types: list[Any],
) -> list[dict]:
    """`resolve_track(mbt) -> Track | None` is injected by the caller,
    since resolving a mbt to a local Track means either reading the
    dialog's widget state (main thread) or re-fetching by ID (worker
    thread) -- this function doesn't need to know which."""
    chain = detail.place_chains.get(place_mbid)
    if not chain:
        return []
    try:
        studio = resolve_place_chain(controller, chain, place_cache)
        if studio is None:
            return []
        assoc_type = find_or_create_association_type(
            controller, "Recording Location", known_place_types
        )
        rows = []
        for mbt in mb_tracks:
            track = resolve_track(mbt)
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


class _ReviewAcceptWorker(CancellableWorker):
    """Runs AlbumMusicBrainzReviewDialog's write phase (credit/role/
    publisher resolution and every DB write) off the UI thread -- with
    enough credits on a release, doing this synchronously in _on_accept()
    froze the whole app long enough to trigger the OS's "not responding"
    prompt.

    All Qt widget state (checked boxes, manual-match combo selections)
    must be read on the UI thread *before* this worker starts -- see
    AlbumMusicBrainzReviewDialog._on_accept() -- since QWidget access from
    a background thread isn't safe. Album/Track rows are re-fetched by ID
    here rather than reusing the dialog's already-loaded ORM objects,
    since those are bound to the main thread's scoped_session and
    touching an unloaded relationship on them from this thread would mean
    two threads sharing one SQLAlchemy Session. `controller`'s
    scoped_session hands this thread its own Session the first time it's
    touched here, same as every other CancellableWorker in this codebase
    that writes to the DB.

    Signals:
        progress(current, total)
        finished(failed_writes) - list[str] of human-readable failures,
            same shape _report_failed_writes() already expects.
        error(message) - only for something unexpected enough to abort
            the whole run; per-item failures go into finished's list
            instead.
    """

    progress = Signal(int, int)
    finished = Signal(list)
    error = Signal(str)

    def __init__(
        self,
        controller,
        album_id: int,
        detail: MBReleaseDetail,
        matched_track_ids: dict[int, int],
        manual_track_ids: dict[int, int | None],
        checked_aliases: list,
        checked_labels: list,
        checked_album_credits: list,
        checked_track_credits: list,
        checked_locations: list,
        parent=None,
    ):
        super().__init__(parent)
        self._controller = controller
        self._album_id = album_id
        self._detail = detail
        self._matched_track_ids = matched_track_ids
        self._manual_track_ids = manual_track_ids
        self._checked_aliases = checked_aliases
        self._checked_labels = checked_labels
        self._checked_album_credits = checked_album_credits
        self._checked_track_credits = checked_track_credits
        self._checked_locations = checked_locations

    def run(self):
        try:
            self._run()
        except Exception as e:
            # Intentional broad boundary catch: this is a QThread's run()
            # body -- an unhandled exception here would kill the thread
            # silently instead of surfacing to the UI.
            logger.error(f"MusicBrainz review accept failed: {e}", exc_info=True)
            self.error.emit(str(e))
        finally:
            self._release_db_session()

    def _run(self):
        controller = self._controller
        album = controller.get.get_entity_object("Album", album_id=self._album_id)
        if album is None:
            self.error.emit("This album no longer exists.")
            return

        failed_writes: list[str] = []
        mbt_by_id = {id(mbt): mbt for mbt in self._detail.tracks}
        track_cache: dict[int, Any] = {}

        def get_track(track_id):
            if track_id not in track_cache:
                track_cache[track_id] = controller.get.get_entity_object(
                    "Track", track_id=track_id
                )
            return track_cache[track_id]

        def resolve_track(mbt):
            tid = self._matched_track_ids.get(id(mbt))
            if tid is None:
                tid = self._manual_track_ids.get(id(mbt))
            return get_track(tid) if tid is not None else None

        total = max(
            1,
            len(self._matched_track_ids)
            + len(self._manual_track_ids)
            + len(self._checked_aliases)
            + len(self._checked_labels)
            + len(self._checked_album_credits)
            + len(self._checked_track_credits)
            + len(self._checked_locations),
        )
        done = 0

        def tick():
            nonlocal done
            done += 1
            self.progress.emit(done, total)

        disc_by_number, disc_failed = _plan_discs(controller, album, self._detail)
        failed_writes.extend(disc_failed)

        updates = []
        for mbt_id, track_id in self._matched_track_ids.items():
            if self.is_cancelled:
                return
            track = get_track(track_id)
            if track is not None:
                update = _track_scalar_update(
                    track, mbt_by_id[mbt_id], disc_by_number, self._detail.barcode
                )
                if update is not None:
                    updates.append(update)
            tick()
        failed_writes.extend(_batch_update_tracks(controller, updates))

        manual_updates = []
        for mbt_id, track_id in self._manual_track_ids.items():
            if self.is_cancelled:
                return
            if track_id is not None:
                track = get_track(track_id)
                if track is not None:
                    update = _track_scalar_update(
                        track,
                        mbt_by_id[mbt_id],
                        disc_by_number,
                        self._detail.barcode,
                        force=True,
                    )
                    if update is not None:
                        manual_updates.append(update)
            tick()
        failed_writes.extend(_batch_update_tracks(controller, manual_updates))

        if self._checked_aliases:
            if self.is_cancelled:
                return
            alias_rows = [
                {
                    "album_id": album.album_id,
                    "alias_name": alias.name,
                    "alias_type": alias.type or None,
                }
                for alias in self._checked_aliases
            ]
            _, failed = controller.add.add_entities_with_fallback(
                "AlbumAlias", alias_rows
            )
            failed_writes.extend(f"Album alias '{row['alias_name']}'" for row in failed)
            for _ in self._checked_aliases:
                tick()

        known_roles = controller.get.get_all_entities("Role") or []
        place_cache: dict[str, Any] = {}

        if self._checked_labels:
            if self.is_cancelled:
                return
            failed_writes.extend(
                import_album_labels(controller, album, self._checked_labels, place_cache)
            )
            for _ in self._checked_labels:
                tick()

        if self._checked_album_credits:
            album_credit_rows = []
            next_sort_order_by_role: dict[int, int] = {}
            planned_album_pairs: set = set()
            for credit in self._checked_album_credits:
                if self.is_cancelled:
                    return
                row = _plan_album_credit(
                    controller,
                    album,
                    credit,
                    known_roles,
                    next_sort_order_by_role,
                    planned_album_pairs,
                )
                if row is not None:
                    album_credit_rows.append(row)
                    planned_album_pairs.add((row["artist_id"], row["role_id"]))
                tick()
            _, failed = controller.add.add_entities_with_fallback(
                "AlbumRoleAssociation", album_credit_rows
            )
            failed_writes.extend(
                f"Album credit (artist {row['artist_id']}, role {row['role_id']})"
                for row in failed
            )

        if self._checked_track_credits:
            track_credit_rows = []
            planned_by_track: dict[int, set] = {}
            for mbt_id, credit in self._checked_track_credits:
                if self.is_cancelled:
                    return
                tid = self._matched_track_ids.get(mbt_id)
                if tid is None:
                    tid = self._manual_track_ids.get(mbt_id)
                track = get_track(tid) if tid is not None else None
                if track is not None:
                    row = _plan_track_credit(
                        controller, track, credit, known_roles, planned_by_track
                    )
                    if row is not None:
                        track_credit_rows.append(row)
                tick()
            _, failed = controller.add.add_entities_with_fallback(
                "TrackArtistRole", track_credit_rows
            )
            failed_writes.extend(
                f"Track credit (track {row['track_id']}, artist {row['artist_id']})"
                for row in failed
            )

        if self._checked_locations:
            known_place_types = fetch_association_types(controller)
            place_rows = []
            for place_mbid, mb_tracks in self._checked_locations:
                if self.is_cancelled:
                    return
                place_rows.extend(
                    _plan_location_rows(
                        controller,
                        self._detail,
                        resolve_track,
                        place_mbid,
                        mb_tracks,
                        place_cache,
                        known_place_types,
                    )
                )
                tick()
            _, failed = controller.add.add_entities_with_fallback(
                "PlaceAssociation", place_rows
            )
            failed_writes.extend(
                f"Recording location for track {row['entity_id']}" for row in failed
            )

        if not self.is_cancelled:
            self.finished.emit(failed_writes)


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

        self._matched: dict[int, Any] = {}  # id(MBReleaseTrack) -> Track
        self._manual_combos: list[tuple[QComboBox, MBReleaseTrack]] = []
        self._alias_checks: list[tuple[QCheckBox, MBAlias]] = []
        self._album_credit_checks: list[tuple[QCheckBox, Any]] = []
        self._label_checks: list[tuple[QCheckBox, MBLabelInfo]] = []
        self._credit_checks: list[tuple[QCheckBox, MBReleaseTrack, Any]] = []
        self._location_checks: list[tuple[QCheckBox, str, list[MBReleaseTrack]]] = []
        self._failed_writes: list[str] = []
        self._accept_worker: _ReviewAcceptWorker | None = None

        self.setWindowTitle("Review MusicBrainz Album Details")
        self.setMinimumSize(560, 540)

        self._match_tracks()
        self._match_summary = (
            f"Matched {len(self._matched)} of {len(detail.tracks)} track(s)."
        )
        self._build_ui()

    # ------------------------------------------------------------------
    # Track matching (no DB writes) -- title-dominant combined score across
    # every disc-compatible (MB track, local track) pair, blind-matching
    # the best-scoring pairs first; then a title-similarity guess for a
    # single-medium release covers whatever's left; then a manual
    # QComboBox for anything still unresolved.
    # ------------------------------------------------------------------

    @staticmethod
    def _title_similarity(a: str | None, b: str | None) -> float:
        if not a or not b:
            return 0.0
        return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()

    @staticmethod
    def _position_agreement(mb_pos: int | None, local_pos: int | None) -> float:
        """1.0 for an exact position match, decaying with distance. Neither
        side having a usable position is a lack of signal, not disagreement,
        so it scores neutrally rather than dragging the combined score down."""
        if mb_pos is None or local_pos is None:
            return 0.5
        return 1.0 / (1.0 + abs(mb_pos - local_pos))

    @classmethod
    def _combined_score(cls, mbt: MBReleaseTrack, local) -> float:
        """Track name is the primary matching signal -- position is a minor
        factor, not a filter, so a same-numbered-but-wrong-song candidate
        doesn't beat the actual best title match sitting at another
        position. A local track with no name yet has nothing to compare
        textually, so position is the only signal available for it."""
        mb_pos = mbt.absolute_position if mbt.absolute_position is not None else mbt.track_number
        pos_agreement = cls._position_agreement(mb_pos, local.track_number)
        if not local.track_name:
            return pos_agreement
        title_sim = cls._title_similarity(mbt.title, local.track_name)
        return _TITLE_WEIGHT * title_sim + _POSITION_WEIGHT * pos_agreement

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

        # Score every disc-compatible pair (title-dominant, position as a
        # minor factor -- see _combined_score) and blind-match the
        # best-scoring pairs first, so a much better title match elsewhere
        # in the tracklist always wins over a same-numbered-but-wrong-song
        # candidate instead of that candidate being the only one considered.
        pairs = []
        for mbt in self.detail.tracks:
            for local in local_tracks:
                cand_disc_num = local.disc.disc_number if local.disc else None
                if not self._discs_compatible(cand_disc_num, mbt.disc_number):
                    continue
                pairs.append((self._combined_score(mbt, local), mbt, local))
        pairs.sort(key=lambda p: -p[0])

        used_ids = set()
        matched: dict[int, Any] = {}
        for score, mbt, local in pairs:
            if score < _AUTO_MATCH_SCORE_FLOOR:
                break
            if id(mbt) in matched or local.track_id in used_ids:
                continue
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
        # However many unmatched local tracks are left over, at most that
        # many of the unmatched MB tracks can possibly be real local tracks
        # that just failed to auto-match -- the rest are guaranteed to be
        # tracks MusicBrainz lists that don't exist in the user's album at
        # all (e.g. all 8 local tracks matched cleanly but MB's release has
        # 9), which is an error, not an ordinary "needs manual review" case.
        self._guaranteed_missing = max(0, len(remaining_mb) - len(remaining_local))

    def _on_manual_combo_changed(self, _index: int):
        self._refresh_manual_combo_options(changed_combo=self.sender())

    def _refresh_manual_combo_options(self, changed_combo=None):
        """Once a local track is picked in one row's combo, remove it from
        every other row's option list -- otherwise the same local track can
        be manually assigned to more than one MusicBrainz track. Any other
        row that already held that same track (e.g. from its own auto-guess)
        is bumped back to "Skip" -- the row the user just edited wins the
        claim, since it's the one they're actively acting on.

        Compares local-track options by identity (`is`), not `==`/hashing --
        local tracks here can be plain SQLAlchemy rows (default identity
        hash) or test doubles like SimpleNamespace (unhashable, and `==`
        would compare field values rather than "same row")."""
        if changed_combo is not None:
            claimed = changed_combo.currentData()
            if claimed is not None:
                for combo, _mbt in self._manual_combos:
                    if combo is changed_combo:
                        continue
                    if combo.currentData() is claimed:
                        combo.blockSignals(True)
                        combo.setCurrentIndex(0)  # back to Skip -- just claimed elsewhere
                        combo.blockSignals(False)

        selections = [combo.currentData() for combo, _mbt in self._manual_combos]
        for row, (combo, _mbt) in enumerate(self._manual_combos):
            current = selections[row]
            taken_elsewhere = [
                data
                for i, data in enumerate(selections)
                if i != row and data is not None
            ]
            available = [
                local
                for local in self._remaining_local_options
                if local is current or not any(local is t for t in taken_elsewhere)
            ]
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(_SKIP, None)
            for local in available:
                local_side = f", side {local.side}" if local.side else ""
                combo.addItem(
                    f"{local.track_name} (currently track {local.track_number or '?'}{local_side})",
                    local,
                )
            idx = combo.findData(current) if current is not None else 0
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)

    # ------------------------------------------------------------------
    # Fill-blank scalars for auto-matched tracks (disc assignment, track
    # number/side/barcode). Computed against self._matched at construction
    # time, but the actual writes are deferred to _on_accept() below --
    # nothing here touches the database until the user clicks OK.
    # ------------------------------------------------------------------

    def apply_immediate_scalars(self):
        disc_by_number, failed = _plan_discs(self.controller, self.album, self.detail)
        updates = []
        for mbt in self.detail.tracks:
            track = self._matched.get(id(mbt))
            if track is None:
                continue
            update = _track_scalar_update(
                track, mbt, disc_by_number, self.detail.barcode
            )
            if update is not None:
                updates.append(update)
        failed += _batch_update_tracks(self.controller, updates)
        self._failed_writes.extend(failed)
        self._report_failed_writes()

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

    def _usable_labels(self) -> list[MBLabelInfo]:
        existing_mbids = {
            p.MBID for p in (self.album.publishers or []) if p.MBID
        }
        existing_names = {
            (p.publisher_name or "").strip().lower()
            for p in (self.album.publishers or [])
        }
        out = []
        for label in self.detail.labels:
            if label.mbid in existing_mbids:
                continue
            if label.name.strip().lower() in existing_names:
                continue
            out.append(label)
        return out

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(self._match_summary))

        if self._guaranteed_missing:
            warning = QLabel(
                f"⚠ Error: MusicBrainz lists {self._guaranteed_missing} more "
                f"track(s) than your album has. At least {self._guaranteed_missing} "
                "track(s) below don't exist in your library and can't be "
                "matched to anything -- your album may be missing tracks."
            )
            warning.setWordWrap(True)
            warning.setStyleSheet("color: darkred; font-weight: bold;")
            layout.addWidget(warning)

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
                # There is no candidate left at all -- not merely a case that
                # failed to auto-match -- once every unmatched local track has
                # already been claimed. That's the case the row must call out
                # as an error rather than a routine manual pick.
                no_local_candidates = not self._remaining_local_options

                mb_item = QTableWidgetItem(_format_mb_track_label(mbt))
                if no_local_candidates:
                    mb_item.setBackground(QColor(255, 224, 224))
                    bold_font = QFont()
                    bold_font.setBold(True)
                    mb_item.setFont(bold_font)
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

                if no_local_candidates:
                    conf_text = "Not in your album — ERROR"
                    conf_color = QColor(Qt.red)
                elif guess is not None:
                    conf_text = confidence_label(score)
                    conf_color = confidence_color(score)
                else:
                    conf_text = "No suggestion"
                    conf_color = confidence_color(0.0)
                conf_item = QTableWidgetItem(conf_text)
                conf_item.setForeground(conf_color)
                conf_item.setTextAlignment(Qt.AlignCenter)
                if no_local_candidates:
                    conf_item.setBackground(QColor(255, 224, 224))
                    bold_font = QFont()
                    bold_font.setBold(True)
                    conf_item.setFont(bold_font)
                table.setItem(row, 2, conf_item)

            # Connect after every row's combo exists (and its initial guess
            # is set) so a mid-loop selection doesn't fire a refresh against
            # a still-partially-built self._manual_combos.
            for combo, _mbt in self._manual_combos:
                combo.currentIndexChanged.connect(self._on_manual_combo_changed)

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

        labels = self._usable_labels()
        if labels:
            self.has_content = True
            box = QGroupBox("Publisher(s)")
            box_layout = QVBoxLayout(box)
            for label in labels:
                text = label.name
                if label.founders:
                    founder_names = ", ".join(f.name for f in label.founders)
                    text += f" — founded by {founder_names}"
                cb = QCheckBox(text)
                cb.setChecked(True)
                box_layout.addWidget(cb)
                self._label_checks.append((cb, label))
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
        self._scroll = scroll

        self.progress_status_label = QLabel()
        self.progress_status_label.setVisible(False)
        layout.addWidget(self.progress_status_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._on_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

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
        """Read every bit of Qt widget state that the write phase needs
        (checked boxes, manual-match combo selections) right here on the UI
        thread, then hand it all to _ReviewAcceptWorker as plain data --
        with enough credits on a release, running the resolve/write loop
        synchronously froze the whole app long enough to trigger the OS
        "not responding" prompt. Nothing below this point may touch a
        QWidget from the worker; see _ReviewAcceptWorker's docstring."""
        matched_track_ids = {
            mbt_id: track.track_id for mbt_id, track in self._matched.items()
        }
        manual_track_ids: dict[int, int | None] = {}
        for combo, mbt in self._manual_combos:
            track = combo.currentData()
            manual_track_ids[id(mbt)] = track.track_id if track is not None else None

        checked_aliases = [alias for cb, alias in self._alias_checks if cb.isChecked()]
        checked_labels = [label for cb, label in self._label_checks if cb.isChecked()]
        checked_album_credits = [
            credit for cb, credit in self._album_credit_checks if cb.isChecked()
        ]
        checked_track_credits = [
            (id(mbt), credit) for cb, mbt, credit in self._credit_checks if cb.isChecked()
        ]
        checked_locations = [
            (place_mbid, mb_tracks)
            for cb, place_mbid, mb_tracks in self._location_checks
            if cb.isChecked()
        ]

        self._set_busy(True)
        self._accept_worker = _ReviewAcceptWorker(
            self.controller,
            self.album.album_id,
            self.detail,
            matched_track_ids,
            manual_track_ids,
            checked_aliases,
            checked_labels,
            checked_album_credits,
            checked_track_credits,
            checked_locations,
            parent=self,
        )
        self._accept_worker.progress.connect(self._on_accept_progress)
        self._accept_worker.finished.connect(self._on_accept_finished)
        self._accept_worker.error.connect(self._on_accept_error)
        self._accept_worker.start()

    def _set_busy(self, busy: bool):
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(not busy)
        self.buttons.button(QDialogButtonBox.Cancel).setEnabled(not busy)
        self._scroll.setEnabled(not busy)
        self.progress_bar.setVisible(busy)
        self.progress_status_label.setVisible(busy)
        if busy:
            self.progress_bar.setRange(0, 0)  # indeterminate until first progress signal
            self.progress_status_label.setText("Applying MusicBrainz data…")

    def _on_accept_progress(self, current: int, total: int):
        if self.progress_bar.maximum() == 0:
            self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(current)
        self.progress_status_label.setText(
            f"Applying MusicBrainz data… ({current} of {total})"
        )

    def _on_accept_finished(self, failed_writes: list[str]):
        self._set_busy(False)
        self._failed_writes.extend(failed_writes)
        self._report_failed_writes()
        self.accept()

    def _on_accept_error(self, message: str):
        self._set_busy(False)
        QMessageBox.critical(
            self,
            "MusicBrainz Import Failed",
            f"Could not finish importing MusicBrainz data:\n\n{message}",
        )

    def _detach_accept_worker(self):
        """Best-effort stop for a write still in flight when the dialog is
        closed out from under it (Cancel/Esc/X while busy -- the buttons are
        disabled during the write, but Esc still reaches reject()). Mirrors
        _detach_running_worker in musicbrainz_match_dialog.py: request
        cancellation, then disconnect so a signal arriving after this dialog
        is gone can't call back into dead widgets, and reparent so a still-
        running QThread destroyed alongside its parent doesn't crash Qt."""
        worker = self._accept_worker
        if worker is None or not worker.isRunning():
            return
        worker.request_cancel()
        try:
            worker.finished.disconnect()
        except RuntimeError:
            pass
        try:
            worker.error.disconnect()
        except RuntimeError:
            pass
        try:
            worker.progress.disconnect()
        except RuntimeError:
            pass
        worker.setParent(None)
        worker.finished.connect(worker.deleteLater)
        worker.error.connect(worker.deleteLater)

    def reject(self):
        self._detach_accept_worker()
        super().reject()

    def closeEvent(self, event):
        self._detach_accept_worker()
        super().closeEvent(event)
