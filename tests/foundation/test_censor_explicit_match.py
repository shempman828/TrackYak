"""Tests for censor.text_contains_explicit_words() (docs/specs/
explicit_content_detection.md). Maps to AC5, AC6, and AC14 -- word-boundary
matching, case-insensitivity, phrase support, and the hot-reload-on-file-
change behavior this feature reuses from the existing censor.py cache.

Each test points _WORDLIST_PATH at a throwaway tmp_path file instead of the
real assets/explicit_words.txt, so these tests don't depend on -- or break
when someone edits -- the real, user-maintained word list.
"""

import os
import time

import pytest

from src.foundation import censor


@pytest.fixture(autouse=True)
def _isolated_wordlist(tmp_path, monkeypatch):
    wordlist = tmp_path / "explicit_words.txt"
    wordlist.write_text("# comment\nfuck\nshit\nass\nkiss the sky\n")
    monkeypatch.setattr(censor, "_WORDLIST_PATH", wordlist)
    censor._cache["mtime"] = None
    censor._cache["pattern"] = None
    yield
    censor._cache["mtime"] = None
    censor._cache["pattern"] = None


def test_no_match_returns_false():
    assert censor.text_contains_explicit_words("just a clean lyric line") is False


def test_whole_word_match_returns_true():
    assert censor.text_contains_explicit_words("this shit is real") is True


# AC5 -------------------------------------------------------------------------
def test_substring_does_not_false_positive():
    assert (
        censor.text_contains_explicit_words("take a class, polish the brass")
        is False
    )


def test_match_is_case_insensitive():
    assert censor.text_contains_explicit_words("What the FUCK") is True


# AC6 -------------------------------------------------------------------------
def test_phrase_entry_matches():
    assert (
        censor.text_contains_explicit_words("I saw you kiss the sky last night")
        is True
    )


def test_empty_or_none_text_returns_false():
    assert censor.text_contains_explicit_words("") is False
    assert censor.text_contains_explicit_words(None) is False


def test_missing_wordlist_file_returns_false(tmp_path, monkeypatch):
    missing = tmp_path / "does_not_exist.txt"
    monkeypatch.setattr(censor, "_WORDLIST_PATH", missing)
    censor._cache["mtime"] = None
    censor._cache["pattern"] = None
    assert censor.text_contains_explicit_words("shit happens") is False


# AC14 ------------------------------------------------------------------------
def test_wordlist_hot_reloads_on_change(tmp_path, monkeypatch):
    wordlist = tmp_path / "explicit_words.txt"
    wordlist.write_text("shit\n")
    monkeypatch.setattr(censor, "_WORDLIST_PATH", wordlist)
    censor._cache["mtime"] = None
    censor._cache["pattern"] = None

    assert censor.text_contains_explicit_words("newword here") is False

    wordlist.write_text("shit\nnewword\n")
    # Force a distinct mtime -- some filesystems have 1s mtime resolution,
    # which could otherwise make this test flaky.
    future = time.time() + 2
    os.utime(wordlist, (future, future))

    assert censor.text_contains_explicit_words("newword here") is True
