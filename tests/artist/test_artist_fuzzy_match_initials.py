"""Regression test for #261: fuzzy matching should give extra weight to
initialised names (e.g. "J. Lennon" vs "John Lennon") instead of scoring
them as barely-similar strings, and those pairs must actually reach the
scoring step -- prefix-only blocking previously put them in different
buckets so they were never compared at all.
"""

from src.artist.artist_fuzzy_match import (
    _blocking_keys,
    _tokens_match_with_initials,
    artist_name_similarity,
)

DUPLICATE_THRESHOLD = 0.85


def test_initial_vs_full_first_name_scores_above_duplicate_threshold():
    ratio = artist_name_similarity("J. Lennon", "John Lennon")
    assert ratio >= DUPLICATE_THRESHOLD


def test_multiple_initials_score_above_duplicate_threshold():
    ratio = artist_name_similarity("J. R. R. Tolkien", "John Ronald Reuel Tolkien")
    assert ratio >= DUPLICATE_THRESHOLD


def test_initial_matching_is_symmetric():
    assert artist_name_similarity("J. Lennon", "John Lennon") == artist_name_similarity(
        "John Lennon", "J. Lennon"
    )


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
