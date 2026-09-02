"""
album_musicbrainz_review_import.py

Write-phase helpers for AlbumMusicBrainzReviewDialog -- module-level
functions (not dialog methods) so both the has_content=False synchronous
path (AlbumMusicBrainzReviewDialog.apply_immediate_scalars, called directly
on the UI thread when there's nothing to review) and _ReviewAcceptWorker
(the has_content=True path, backgrounded because resolving/writing many
credits synchronously froze the UI long enough to trigger the OS "not
responding" prompt) share one implementation. Every function here takes
`controller` explicitly rather than reading `self.controller`, so it works
the same whether it's called from the main thread or from
_ReviewAcceptWorker's background thread.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from sqlalchemy.exc import SQLAlchemyError

from src.award.award_series_import import import_awards_for_entity
from src.common.cancellable_worker import CancellableWorker
from src.common.entity_completer_edit import find_or_create_by_name
from src.foundation.config_setup import app_config
from src.foundation.logger_config import logger
from src.musicbrainz.musicbrainz_release import MBReleaseDetail, MBReleaseTrack
from src.place.place_association_types import (
    fetch_association_types,
    find_or_create_association_type,
)
from src.place.place_chain_resolver import resolve_place_chain
from src.publisher.publisher_musicbrainz_import import import_album_labels


def _format_mb_track_label(mbt: MBReleaseTrack) -> str:
    side = f"Side {mbt.side}, " if mbt.side else ""
    return f"Disc {mbt.disc_number}, {side}Track {mbt.track_number or '?'}: {mbt.title}"


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
    if mbt.title and mbt.title.strip().lower() != (track.track_name or "").strip().lower():
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


def _resolve_artists(controller, credit) -> list[Any]:
    """Every Artist this credit's artist name resolves to. A name that was
    previously split into 2+ artists (see SplitDB._record_split_alias)
    resolves to that same ordered list instead of the single find-or-
    create path recreating/reusing one combined Artist -- see
    docs/specs/split_and_merge_aliases.md."""
    split_targets = controller.get.resolve_split_alias("Artist", credit.artist_name)
    if split_targets:
        return split_targets

    artist = _resolve_artist(controller, credit)
    return [artist] if artist is not None else []


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

    artist = controller.get.resolve_entity_or_alias("Artist", "artist_name", credit.artist_name)
    if artist is not None and not artist.MBID:
        if credit.artist_mbid:
            controller.update.update_entity("Artist", artist.artist_id, MBID=credit.artist_mbid)
            artist.MBID = credit.artist_mbid
            import_awards_for_entity(
                controller.get.session, "Artist", artist.artist_id, credit.artist_mbid
            )
        return artist
    # A name match whose row already carries a (necessarily different --
    # the MBID lookup above would have caught an equal one) MBID is a
    # distinct real-world artist, not this credit -- ignore it rather
    # than merging two different people, and fall through below.

    if credit.canonical_name and credit.canonical_name != credit.artist_name:
        artist = controller.get.resolve_entity_or_alias(
            "Artist", "artist_name", credit.canonical_name
        )
        if artist is not None and not artist.MBID:
            if credit.artist_mbid:
                controller.update.update_entity("Artist", artist.artist_id, MBID=credit.artist_mbid)
                artist.MBID = credit.artist_mbid
                import_awards_for_entity(
                    controller.get.session, "Artist", artist.artist_id, credit.artist_mbid
                )
            controller.add.add_entity(
                "ArtistAlias",
                artist_id=artist.artist_id,
                alias_name=credit.artist_name,
                alias_type=None,
            )
            return artist

    new_artist = controller.add.add_entity(
        "Artist", artist_name=credit.artist_name, MBID=credit.artist_mbid
    )
    if credit.artist_mbid:
        import_awards_for_entity(
            controller.get.session, "Artist", new_artist.artist_id, credit.artist_mbid
        )
    return new_artist


def _resolve_roles_for_credit(controller, role_name: str, known_roles: list[Any]) -> list[Any]:
    """Every Role this credit's role name resolves to. A name that was
    previously split into 2+ roles (see SplitDB._record_split_alias)
    resolves to that same ordered list instead of find_or_create_by_name
    recreating/reusing one combined Role -- see
    docs/specs/split_and_merge_aliases.md. A role name on the parse-ignore
    list (docs/specs/role_parse_ignore_list.md) resolves to nothing, so
    both callers skip the credit -- same as the file-tag import path."""
    if role_name.strip().lower() in {r.lower() for r in app_config.get_excluded_roles()}:
        return []

    split_targets = controller.get.resolve_split_alias("Role", role_name)
    if split_targets:
        for role in split_targets:
            if role not in known_roles:
                known_roles.append(role)
        return split_targets

    role = find_or_create_by_name(controller, "Role", "role_name", role_name, known_roles)
    if role is None:
        return []
    if role not in known_roles:
        known_roles.append(role)
    return [role]


def _plan_track_credit(
    controller, track, credit, known_roles: list[Any], planned_by_track: dict[int, set]
) -> list[dict]:
    """Resolve (and, if genuinely new, create) the artist(s)/role(s) for
    this credit, but leave the actual TrackArtistRole junction rows for
    the caller to batch-insert alongside every other checked credit. A
    credit ordinarily resolves to exactly one artist and one role -- it
    resolves to more only when a split-alias rule matches (see
    _resolve_artists/_resolve_roles_for_credit), in which case one row is
    planned per (artist, role) pair."""
    try:
        artists = _resolve_artists(controller, credit)
        if not artists:
            return []
        roles = _resolve_roles_for_credit(controller, credit.role_name, known_roles)
        if not roles:
            return []

        existing = {(ar.artist_id, ar.role_id) for ar in (track.artist_roles or [])}
        planned = planned_by_track.setdefault(track.track_id, set())
        rows = []
        for artist in artists:
            for role in roles:
                pair = (artist.artist_id, role.role_id)
                if pair in existing or pair in planned:
                    continue
                planned.add(pair)
                rows.append(
                    {
                        "track_id": track.track_id,
                        "artist_id": artist.artist_id,
                        "role_id": role.role_id,
                    }
                )
        return rows
    except SQLAlchemyError as e:
        logger.warning(
            f"Could not import credit '{credit.artist_name} — "
            f"{credit.role_name}' on track {track.track_id}: {e}"
        )
        return []


def _plan_album_credit(
    controller,
    album,
    credit,
    known_roles: list[Any],
    next_sort_order_by_role: dict[int, int],
    planned_pairs: set[tuple[int, int]],
) -> list[dict]:
    """Same idea as `_plan_track_credit`, for album-level credits. The
    sort_order that AlbumRoleAssociation rows for the same role share is
    normally derived by re-reading `album.album_roles` after each
    commit; since nothing is committed until the whole batch goes in,
    `next_sort_order_by_role` tracks the same running count in memory.
    Unlike `_plan_track_credit`, this mutates `planned_pairs` itself
    (rather than leaving that to the caller) since a single call can now
    plan several rows that must not collide with each other."""
    try:
        artists = _resolve_artists(controller, credit)
        if not artists:
            return []
        roles = _resolve_roles_for_credit(controller, credit.role_name, known_roles)
        if not roles:
            return []

        rows = []
        for role in roles:
            siblings = [ra for ra in (album.album_roles or []) if ra.role_id == role.role_id]
            existing_artist_ids = {ra.artist_id for ra in siblings}
            if role.role_id not in next_sort_order_by_role:
                next_sort_order_by_role[role.role_id] = (
                    max(ra.sort_order for ra in siblings) + 1 if siblings else 0
                )
            for artist in artists:
                if artist.artist_id in existing_artist_ids:
                    continue
                pair = (artist.artist_id, role.role_id)
                if pair in planned_pairs:
                    continue
                planned_pairs.add(pair)
                sort_order = next_sort_order_by_role[role.role_id]
                next_sort_order_by_role[role.role_id] += 1
                rows.append(
                    {
                        "album_id": album.album_id,
                        "artist_id": artist.artist_id,
                        "role_id": role.role_id,
                        "sort_order": sort_order,
                    }
                )
        return rows
    except SQLAlchemyError as e:
        logger.warning(
            f"Could not import album credit '{credit.artist_name} — {credit.role_name}': {e}"
        )
        return []


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
            already = any(p.place_id == studio.place_id for p in (track.places or []))
            if already:
                continue
            rows.append(
                {
                    "entity_id": track.track_id,
                    "entity_type": "Track",
                    "place_id": studio.place_id,
                    "association_type_id": (assoc_type.association_type_id if assoc_type else None),
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
                track_cache[track_id] = controller.get.get_entity_object("Track", track_id=track_id)
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
                        track, mbt_by_id[mbt_id], disc_by_number, self._detail.barcode, force=True
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
            _, failed = controller.add.add_entities_with_fallback("AlbumAlias", alias_rows)
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
                rows = _plan_album_credit(
                    controller,
                    album,
                    credit,
                    known_roles,
                    next_sort_order_by_role,
                    planned_album_pairs,
                )
                album_credit_rows.extend(rows)
                tick()
            _, failed = controller.add.add_entities_with_fallback(
                "AlbumRoleAssociation", album_credit_rows
            )
            failed_writes.extend(
                f"Album credit (artist {row['artist_id']}, role {row['role_id']})" for row in failed
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
                    rows = _plan_track_credit(
                        controller, track, credit, known_roles, planned_by_track
                    )
                    track_credit_rows.extend(rows)
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
            _, failed = controller.add.add_entities_with_fallback("PlaceAssociation", place_rows)
            failed_writes.extend(
                f"Recording location for track {row['entity_id']}" for row in failed
            )

        if not self.is_cancelled:
            self.finished.emit(failed_writes)
