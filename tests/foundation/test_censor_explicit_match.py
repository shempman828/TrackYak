"""Tests for censor.text_contains_explicit_words() (docs/specs/
explicit_content_detection.md). Maps to AC5, AC6, and AC14 -- word-boundary
matching, case-insensitivity, phrase support, and the hot-reload-on-file-
change behavior this feature reuses from the existing censor.py cache.

Each test points _WORDLIST_PATH (and the config/ override) at throwaway
tmp_path files, so these tests don't depend on the real word lists.
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
    monkeypatch.setattr(censor, "_USER_WORDLIST_PATH", tmp_path / "config" / "explicit_words.txt")
    censor._cache["mtime"] = None
    censor._cache["pattern"] = None
    yield
    censor._cache["mtime"] = None
    censor._cache["pattern"] = None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("just a clean lyric line", False),
        ("this shit is real", True),
        ("take a class, polish the brass", False),  # substring of a listed word
        ("What the FUCK", True),
        ("I saw you kiss the sky last night", True),  # phrase entry
        ("", False),
        (None, False),
    ],
)
def test_whole_word_case_insensitive_matching(text, expected):
    assert censor.text_contains_explicit_words(text) is expected


def test_missing_wordlist_file_returns_false(tmp_path, monkeypatch):
    missing = tmp_path / "does_not_exist.txt"
    monkeypatch.setattr(censor, "_WORDLIST_PATH", missing)
    censor._cache["mtime"] = None
    censor._cache["pattern"] = None
    assert censor.text_contains_explicit_words("shit happens") is False


def test_wordlist_hot_reloads_on_change(tmp_path, monkeypatch):
    monkeypatch.setattr(censor, "_RECHECK_INTERVAL_S", 0.0)
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


def test_wordlist_mtime_check_is_throttled(monkeypatch):
    """Repeated calls within the recheck interval must not stat the word list again."""
    assert censor.text_contains_explicit_words("shit") is True
    calls = []
    real_path = censor._WORDLIST_PATH

    class _CountingPath(type(real_path)):
        def stat(self, *a, **k):
            calls.append(1)
            return super().stat(*a, **k)

    counting = _CountingPath(real_path)
    censor._cache["path"] = counting
    monkeypatch.setattr(censor, "_WORDLIST_PATH", counting)
    for _ in range(50):
        censor.text_contains_explicit_words("shit")
    assert calls == []


def test_user_wordlist_in_config_overrides_bundled_default(tmp_path, monkeypatch):
    monkeypatch.setattr(censor, "_RECHECK_INTERVAL_S", 0.0)
    assert censor.text_contains_explicit_words("newword here") is False

    user_list = tmp_path / "config" / "explicit_words.txt"
    user_list.parent.mkdir()
    user_list.write_text("newword\n")

    assert censor.text_contains_explicit_words("newword here") is True
    assert censor.text_contains_explicit_words("shit") is False  # the default list no longer applies
