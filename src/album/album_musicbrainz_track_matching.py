"""
album_musicbrainz_track_matching.py

Track matching (no DB writes) for AlbumMusicBrainzReviewDialog --
title-dominant combined score across every disc-compatible (MB track,
local track) pair, blind-matching the best-scoring pairs first; then a
title-similarity guess for a single-medium release covers whatever's
left; then a manual QComboBox for anything still unresolved.

Mixed into AlbumMusicBrainzReviewDialog. Expects the host class to
provide: self.detail, self.album, self._manual_combos, and to be a
QWidget subclass (for QComboBox signal handling).
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from src.musicbrainz.musicbrainz_release import MBReleaseTrack

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


class AlbumMusicBrainzTrackMatchingMixin:
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
