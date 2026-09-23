"""Regression test for #261: fuzzy matching should give extra weight to
initialised names (e.g. "J. Lennon" vs "John Lennon") instead of scoring
them as barely-similar strings, and those pairs must actually reach the
scoring step -- prefix-only blocking previously put them in different
buckets so they were never compared at all.
"""

from types import SimpleNamespace
from typing import ClassVar

from PySide6.QtWidgets import QPushButton
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.artist.artist_fuzzy_match import ArtistFuzzyMatchWorker, FuzzyMatchDialog, _blocking_keys, _tokens_match_with_initials, artist_name_similarity
from src.common.dialogs import fuzzy_match_dialog
from src.common.dialogs.fuzzy_match_dialog import BaseMergeWorker
from src.common.dismissed_duplicates import dismiss_pair, load_dismissed_pairs
from src.db.db_helpers.merge import MergeDB
from src.db.db_tables.artist import Artist
from src.db.db_tables.base import Base

# ---- test_artist_fuzzy_match_initials.py -------------------------------------
DUPLICATE_THRESHOLD = 0.85


def test_initial_vs_full_first_name_scores_above_duplicate_threshold():
    ratio = artist_name_similarity("J. Lennon", "John Lennon")
    assert ratio >= DUPLICATE_THRESHOLD


def test_multiple_initials_score_above_duplicate_threshold():
    ratio = artist_name_similarity("J. R. R. Tolkien", "John Ronald Reuel Tolkien")
    assert ratio >= DUPLICATE_THRESHOLD


def test_initial_matching_is_symmetric():
    assert artist_name_similarity("J. Lennon", "John Lennon") == artist_name_similarity("John Lennon", "J. Lennon")


def test_different_surname_is_not_boosted_by_shared_initial():
    # Same leading initial, different surname -- must not be inflated just
    # because the first token happens to be a single letter.
    ratio = artist_name_similarity("J. Lennon", "J. Smith")
    assert ratio < DUPLICATE_THRESHOLD


def test_tokens_match_with_initials_requires_equal_token_count():
    assert not _tokens_match_with_initials(["cher"], ["c", "cher"])


def test_tokens_match_with_initials_accepts_either_side_as_initial():
    assert _tokens_match_with_initials(["j", "lennon"], ["john", "lennon"])
    assert _tokens_match_with_initials(["john", "lennon"], ["j", "lennon"])


def test_blocking_keys_share_last_token_for_initialised_names():
    # This is what makes the pair reachable at all: prefix blocking alone
    # ("j l" vs "joh") would never put these two artists in the same
    # comparison bucket.
    assert _blocking_keys("J. Lennon") & _blocking_keys("John Lennon")


# ---- test_artist_fuzzy_match_mbid_conflict.py --------------------------------
# Regression test: artist_name is no longer DB-unique (see
# publisher_musicbrainz_import.py / album_musicbrainz_review_import.py --
# MB-import resolvers now create a second same-named Artist rather than
# merge two people confirmed distinct by different MBIDs). The duplicate-
# artist fuzzy scanner must not immediately turn around and suggest
# re-merging exactly the pair the import resolver just kept apart.
def _artist(artist_id, name, mbid=None):
    return SimpleNamespace(artist_id=artist_id, artist_name=name, MBID=mbid)


def test_conflicting_mbids_are_never_suggested_as_a_match(qapp):
    artists = [_artist(1, "John Smith", mbid="mbid-a"), _artist(2, "John Smith", mbid="mbid-b")]
    worker = ArtistFuzzyMatchWorker(artists, threshold=0.85)

    matches = worker._find_matches()

    assert matches == []


def test_identical_name_with_one_missing_mbid_is_still_suggested(qapp):
    # Only one side has an MBID -- not a confirmed conflict, still a
    # plausible duplicate worth flagging for a human to review.
    artists = [_artist(1, "John Smith", mbid="mbid-a"), _artist(2, "John Smith", mbid=None)]
    worker = ArtistFuzzyMatchWorker(artists, threshold=0.85)

    matches = worker._find_matches()

    assert len(matches) == 1


def test_identical_mbid_pair_is_still_suggested(qapp):
    # Same MBID on both sides isn't the conflict case this guard targets;
    # unaffected, should score normally.
    artists = [_artist(1, "John Smith", mbid="same-mbid"), _artist(2, "John Smith", mbid="same-mbid")]
    worker = ArtistFuzzyMatchWorker(artists, threshold=0.85)

    matches = worker._find_matches()

    assert len(matches) == 1


# ---- test_artist_fuzzy_match_ampersand.py ----------------------------------
# Regression test: the "Merge Artists" dialog put raw artist names straight
# into QRadioButton text, so Qt consumed the '&' as a mnemonic prefix and a
# name like "Simon & Garfunkel" rendered as "Simon  Garfunkel". (The esc_amp
# helper itself is unit-tested in tests/common/test_qt_text.py.)
def _named_artist(artist_id, name):
    return SimpleNamespace(artist_id=artist_id, artist_name=name, role_count=1, MBID=None)


def test_merge_dialog_radio_text_keeps_ampersand(qapp):
    a = _named_artist(1, "Simon & Garfunkel")
    b = _named_artist(2, "Simon and Garfunkel")
    dialog = FuzzyMatchDialog([(a, b, 95)], controller=None)
    try:
        radio_a = dialog.match_widgets[0][1]
        # QRadioButton.text() returns the doubled form; Qt renders it as a
        # single literal '&'. Before the fix this was "Simon  Garfunkel...".
        assert radio_a.text() == "Simon && Garfunkel (1 roles)"
    finally:
        dialog.deleteLater()


# ---- test_artist_fuzzy_match_dismiss.py --------------------------------------
# Acceptance criteria for docs/specs/dismiss_duplicate_suggestions.md:
# AC1 (dismiss removes the row immediately and persists it), AC5 (a
# dismissed pair is filtered out of a later scan's dialog), AC9 (dismissal
# is order-independent), AC10 (dismissing one pair doesn't suppress a
# different pair sharing one of the same entities).


def test_dismiss_button_removes_row_and_persists_the_pair(qapp, tmp_path, monkeypatch):
    dismissed_path = tmp_path / "dismissed_duplicates.json"
    monkeypatch.setattr(fuzzy_match_dialog, "DEFAULT_DISMISSED_DUPLICATES_PATH", dismissed_path)

    a = _named_artist(1, "Foo")
    b = _named_artist(2, "Fooo")
    dialog = FuzzyMatchDialog([(a, b, 95)], controller=None)
    try:
        assert len(dialog.match_widgets) == 1
        dismiss_buttons = [btn for btn in dialog.findChildren(QPushButton) if btn not in (dialog.btn_merge, dialog.btn_cancel)]
        assert len(dismiss_buttons) == 1

        dismiss_buttons[0].click()

        assert dialog.match_widgets == []
        assert load_dismissed_pairs(dismissed_path, "Artist") == {(1, 2)}
    finally:
        dialog.deleteLater()


def test_dismissed_pair_is_excluded_from_a_later_scans_dialog(qapp, tmp_path, monkeypatch):
    dismissed_path = tmp_path / "dismissed_duplicates.json"
    monkeypatch.setattr(fuzzy_match_dialog, "DEFAULT_DISMISSED_DUPLICATES_PATH", dismissed_path)
    dismiss_pair(dismissed_path, "Artist", 1, 2)

    a = _named_artist(1, "Foo")
    b = _named_artist(2, "Fooo")
    # A later scan can produce the same pair with IDs in the opposite order.
    dialog = FuzzyMatchDialog([(b, a, 95)], controller=None)
    try:
        assert dialog.matches == []
        assert dialog.match_widgets == []
    finally:
        dialog.deleteLater()


def test_dismissing_one_pair_does_not_suppress_a_different_pair(qapp, tmp_path, monkeypatch):
    dismissed_path = tmp_path / "dismissed_duplicates.json"
    monkeypatch.setattr(fuzzy_match_dialog, "DEFAULT_DISMISSED_DUPLICATES_PATH", dismissed_path)
    dismiss_pair(dismissed_path, "Artist", 1, 2)

    a = _named_artist(1, "Foo")
    c = _named_artist(3, "Foox")
    dialog = FuzzyMatchDialog([(a, c, 90)], controller=None)
    try:
        assert len(dialog.matches) == 1
    finally:
        dialog.deleteLater()


# ---- docs/specs/artist_duplicate_reconciliation.md acceptance criteria -----
# AC1/2: no-conflict pairs skip the modal, conflicting pairs show it.
# AC4/5/6: Skip This Pair / Skip All Remaining / Cancel Merge outcomes.
# AC3/7/8: a chosen field actually lands on the merged survivor, and the
# discarded artist's original name is still recorded as an alias regardless.


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _real_artist_pair(session, source_bio="Session guitarist.", target_bio=None, source_name="Lew", target_name="Lewis"):
    source = Artist(artist_name=source_name, biography=source_bio)
    target = Artist(artist_name=target_name, biography=target_bio)
    session.add_all([source, target])
    session.commit()
    return source, target


class _StubPairConflictDialog:
    """Test double for PairConflictDialog: skips rendering/exec'ing a real
    modal and instead returns a scripted (outcome, resolved_fields) for
    each pair, consumed in construction order."""

    USE_SELECTED = "use_selected"
    SKIP_PAIR = "skip_pair"
    SKIP_ALL = "skip_all"
    CANCEL = "cancel"

    queue: ClassVar[list[tuple]] = []
    constructed = 0

    def __init__(self, source_entity, target_entity, id_attr, source_label, target_label, pair_index, pair_total, parent=None):
        type(self).constructed += 1
        self.outcome, self._fields = type(self).queue.pop(0)

    def exec(self):
        pass

    def resolved_fields(self):
        return self._fields


def _reset_stub_dialog(monkeypatch, queue):
    monkeypatch.setattr(fuzzy_match_dialog, "PairConflictDialog", _StubPairConflictDialog)
    _StubPairConflictDialog.queue = list(queue)
    _StubPairConflictDialog.constructed = 0


def test_no_conflict_pair_skips_the_resolution_modal(qapp, monkeypatch):
    session = _make_session()
    source, target = _real_artist_pair(session, source_name="Same", target_name="Same", source_bio="Same bio", target_bio="Same bio")
    _reset_stub_dialog(monkeypatch, queue=[])

    dialog = FuzzyMatchDialog([(source, target, 90)], controller=None)
    try:
        resolved = dialog._resolve_conflicts([(source, target)])
        assert resolved == [(source, target, {})]
        assert _StubPairConflictDialog.constructed == 0
    finally:
        dialog.deleteLater()


def test_conflicting_pair_shows_the_resolution_modal(qapp, monkeypatch):
    session = _make_session()
    source, target = _real_artist_pair(session)
    _reset_stub_dialog(monkeypatch, queue=[(_StubPairConflictDialog.USE_SELECTED, {"biography": "Session guitarist."})])

    dialog = FuzzyMatchDialog([(source, target, 90)], controller=None)
    try:
        resolved = dialog._resolve_conflicts([(source, target)])
        assert _StubPairConflictDialog.constructed == 1
        assert resolved == [(source, target, {"biography": "Session guitarist."})]
    finally:
        dialog.deleteLater()


def test_skip_this_pair_keeps_canonical_fields(qapp, monkeypatch):
    session = _make_session()
    source, target = _real_artist_pair(session)
    _reset_stub_dialog(monkeypatch, queue=[(_StubPairConflictDialog.SKIP_PAIR, {})])

    dialog = FuzzyMatchDialog([(source, target, 90)], controller=None)
    try:
        resolved = dialog._resolve_conflicts([(source, target)])
        assert resolved == [(source, target, {})]
    finally:
        dialog.deleteLater()


def test_skip_all_remaining_applies_to_every_later_pair_without_more_modals(qapp, monkeypatch):
    session = _make_session()
    pair1 = _real_artist_pair(session, source_name="Lew", target_name="Lewis")
    pair2 = _real_artist_pair(session, source_name="Bob", target_name="Bobby")
    pair3 = _real_artist_pair(session, source_name="Sam", target_name="Samuel")
    jobs = [pair1, pair2, pair3]
    _reset_stub_dialog(monkeypatch, queue=[(_StubPairConflictDialog.SKIP_ALL, {})])

    dialog = FuzzyMatchDialog([(pair1[0], pair1[1], 90)], controller=None)
    try:
        resolved = dialog._resolve_conflicts(jobs)
        assert _StubPairConflictDialog.constructed == 1  # only the first pair opened a modal
        assert resolved == [(*pair1, {}), (*pair2, {}), (*pair3, {})]
    finally:
        dialog.deleteLater()


def test_cancel_merge_aborts_the_whole_batch(qapp, monkeypatch):
    session = _make_session()
    pair1 = _real_artist_pair(session, source_name="Lew", target_name="Lewis")
    pair2 = _real_artist_pair(session, source_name="Bob", target_name="Bobby")
    _reset_stub_dialog(monkeypatch, queue=[(_StubPairConflictDialog.CANCEL, {})])

    dialog = FuzzyMatchDialog([(pair1[0], pair1[1], 90)], controller=None)
    try:
        resolved = dialog._resolve_conflicts([pair1, pair2])
        assert resolved is None
        assert _StubPairConflictDialog.constructed == 1
    finally:
        dialog.deleteLater()


def test_resolved_field_lands_on_the_merged_survivor(qapp):
    session = _make_session()
    source, target = _real_artist_pair(session, source_bio="Session guitarist.", target_bio=None)

    class _StubController:
        merge = MergeDB(session)

    worker = BaseMergeWorker(_StubController(), "Artist", "artist_id", "artist_name", [(source, target, {"biography": "Session guitarist.", "artist_name": "Lew"})])
    worker.run()

    survivor = session.get(Artist, target.artist_id)
    assert survivor.biography == "Session guitarist."
    assert survivor.artist_name == "Lew"
    assert session.get(Artist, source.artist_id) is None


def test_discarded_artist_name_is_still_aliased_regardless_of_resolved_fields(qapp):
    # merge_entities() itself preserves the merged-away artist's name as an
    # ArtistAlias on the survivor (src/db/db_helpers/merge.py,
    # _ALIAS_ON_MERGE_REGISTRY) -- this already happens for every Artist
    # merge and is unaffected by which fields the reconciliation step chose.
    session = _make_session()
    source, target = _real_artist_pair(session, source_name="Lew", target_name="Lewis")

    class _StubController:
        merge = MergeDB(session)

    worker = BaseMergeWorker(_StubController(), "Artist", "artist_id", "artist_name", [(source, target, {"biography": "Session guitarist."})])
    worker.run()

    survivor = session.get(Artist, target.artist_id)
    assert survivor.artist_name == "Lewis"
    assert "Lew" in [a.alias_name for a in survivor.aliases]
